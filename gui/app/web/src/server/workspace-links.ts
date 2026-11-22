import { readdir, readlink, rename, stat, symlink, unlink } from "node:fs/promises";
import path from "node:path";
import { isSameOrInsidePath, relocatePath } from "../../../shared/workspace-paths.js";
import { errnoCode, HttpError } from "./errors.js";

/** A folder moving from `from` to `to`. Links may name it by its real path, `fromReal`, too (macOS's /var is /private/var). */
export interface FolderMove {
    from: string;
    fromReal: string;
    to: string;
}

/** A link rewritten in place, with what it held before, to put back if the move fails. */
export interface RelinkedLink {
    link: string;
    target: string;
    pointsAt: string;
    /** Where the link is once the failed move has put the folder back. */
    original: string;
}

/**
 * What the symlink at `link` in the moving folder, holding `target`, should hold at its new place. A link into the
 * folder points at the same file at its new place, and a relative one stays relative; a link out of it keeps
 * pointing at what it did.
 */
export function movedLinkTarget(link: string, target: string, move: FolderMove): string {
    if (path.isAbsolute(target)) {
        const root = [move.from, move.fromReal].find((candidate) => isSameOrInsidePath(candidate, target));
        return root ? relocatePath(target, root, move.to) : target;
    }
    const resolved = path.resolve(path.dirname(link), target);
    if (!isSameOrInsidePath(move.from, resolved)) {
        return resolved;
    }
    const movedDirectory = path.dirname(relocatePath(link, move.from, move.to));
    const movedTarget = path.resolve(relocatePath(resolved, move.from, move.to));
    return path.resolve(movedDirectory, target) === movedTarget ? target : path.relative(movedDirectory, movedTarget) || ".";
}

/**
 * Makes a symlink at `link` holding `target`. Windows needs to know whether a link is to a folder, from `pointsAt`,
 * an existing path the link leads to; a folder link it may not create falls back to a junction, which holds an
 * absolute path, resolved from `finalLink`, where the link ends up once its folder has moved.
 */
export async function createLink(target: string, link: string, pointsAt: string, finalLink = link) {
    if (process.platform !== "win32") {
        await symlink(target, link);
        return;
    }
    const directory = await stat(pointsAt).then((info) => info.isDirectory(), () => false);
    try {
        await symlink(target, link, directory ? "dir" : "file");
    }
    catch (error) {
        if (errnoCode(error) !== "EPERM") {
            throw error;
        }
        if (!directory) {
            throw new HttpError(409, `Could not make the link ${finalLink}: Windows lets only administrators, or accounts with Developer Mode on, make links to files.`);
        }
        await symlink(path.resolve(path.dirname(finalLink), target), link, "junction");
    }
}

/** Points the existing link at `target`, making the new link before removing the old, so a link that can't be made loses nothing. */
async function replaceLink(link: string, target: string, pointsAt: string, finalLink: string) {
    const replacement = `${link}.relinking-${process.pid}`;
    await createLink(target, replacement, pointsAt, finalLink);
    try {
        await unlink(link);
        await rename(replacement, link);
    }
    catch (error) {
        await unlink(replacement).catch(() => undefined);
        throw error;
    }
}

/**
 * After the folder was renamed to `move.to`, points the links in it as {@link movedLinkTarget} says, without
 * following any, recording each one changed in `relinked`.
 */
export async function relinkMovedFolder(move: FolderMove, relinked: RelinkedLink[], folder = move.to) {
    for (const entry of await readdir(folder, { withFileTypes: true })) {
        const link = path.join(folder, entry.name);
        if (entry.isDirectory()) {
            await relinkMovedFolder(move, relinked, link);
            continue;
        }
        if (!entry.isSymbolicLink()) {
            continue;
        }
        const target = await readlink(link);
        const original = relocatePath(link, move.to, move.from);
        const moved = movedLinkTarget(original, target, move);
        if (moved === target) {
            continue;
        }
        const pointsAt = path.resolve(folder, moved);
        relinked.push({ link, target, pointsAt, original });
        await replaceLink(link, moved, pointsAt, link);
    }
}

/** Puts back what {@link relinkMovedFolder} changed, as many as it can, before the folder is renamed back. */
export async function restoreLinks(relinked: RelinkedLink[]) {
    for (const { link, target, pointsAt, original } of [...relinked].reverse()) {
        await replaceLink(link, target, pointsAt, original).catch((error: unknown) => {
            console.warn(`Could not put back the link ${original}: ${String(error)}`);
        });
    }
}
