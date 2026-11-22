import { lstat, readdir, readlink, realpath, rename, stat, symlink, unlink } from "node:fs/promises";
import path from "node:path";
import { errnoCode, HttpError } from "./errors.js";

/** A folder moving from `from` to `to`. Links may name it by its real path, `fromReal`, too (macOS's /var is /private/var). */
export interface FolderMove {
    from: string;
    fromReal: string;
    to: string;
}

/** A symlink in the moving folder, found before the move while what it leads to is still there. */
export interface PlannedLink {
    /** Where it is, relative to the folder. */
    relative: string;
    target: string;
    /** What it holds once moved, as {@link movedLinkTarget} says. */
    moved: string;
    /** Whether it leads to a folder, which Windows needs to know to make it. */
    directory: boolean;
}

/**
 * Where `target`, held by the link at `link`, leads as the OS follows it: its folder part resolved through any
 * symlinks, so a `..` after a folder link climbs from where that link leads, and its last part kept, so a link to a
 * link is not followed. Lexical where the folder part can't be resolved, as for a dangling link.
 */
async function currentTarget(link: string, target: string): Promise<string> {
    const unresolved = path.isAbsolute(target) ? target : `${path.dirname(link)}${path.sep}${target}`;
    const leaf = path.basename(unresolved);
    const [folder, name] = leaf === "." || leaf === ".." ? [unresolved, ""] : [path.dirname(unresolved), leaf];
    // Joined unnormalized, so the OS, not path.resolve, decides what each `..` climbs from.
    const real = await realpath(folder).catch(() => null);
    if (real == null) {
        return path.resolve(path.dirname(link), target);
    }
    return name ? path.join(real, name) : real;
}

/** Path separators a link target may use: Windows takes both. */
function separators() {
    return process.platform === "win32" ? /[\\/]/ : /\//;
}

/** Whether the relative `target`, followed from `directory` (relative to the moving folder), never climbs out of it. */
function staysInFolder(directory: string, target: string) {
    let depth = directory === "." ? 0 : directory.split(separators()).length;
    for (const part of target.split(separators())) {
        depth += part === ".." ? -1 : part && part !== "." ? 1 : 0;
        if (depth < 0) {
            return false;
        }
    }
    return true;
}

/** `candidate` relative to `root` by the platform's own path rules ("" for the root itself), or null when outside it. */
function within(root: string, candidate: string): string | null {
    const relative = path.relative(root, candidate);
    return relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative) ? null : relative;
}

/** `relative` in the moved folder. */
function inMovedFolder(move: FolderMove, relative: string) {
    return relative ? path.join(move.to, relative) : move.to;
}

/**
 * What the symlink at `relative` in the moving folder, holding `target`, should hold at its new place. A target
 * spelled within the folder keeps its spelling, relocated if absolute, so links it passes through still decide where
 * it leads. Any other leads where it did as the OS followed it before the move: to the same file at the new place if
 * that is in the folder, else where it did, an absolute target keeping its spelling unless a `..` makes the OS walk
 * through folders that may be gone.
 */
export async function movedLinkTarget(move: FolderMove, relative: string, target: string): Promise<string> {
    const absolute = path.isAbsolute(target);
    const climbs = target.split(separators()).includes("..");
    if (absolute && !climbs) {
        const spelled = within(move.from, target) ?? within(move.fromReal, target);
        if (spelled != null) {
            return inMovedFolder(move, spelled);
        }
    }
    const inFolder = !absolute && staysInFolder(path.dirname(relative), target);
    if (inFolder && !climbs) {
        return target;
    }
    const link = path.join(move.fromReal, relative);
    const leadsTo = await currentTarget(link, target);
    // A `..` climbs from where a link leads, not from where it is spelled; the spelling is kept only where both agree.
    if (inFolder && leadsTo === path.resolve(path.dirname(link), target)) {
        return target;
    }
    const inside = within(move.fromReal, leadsTo) ?? within(move.from, leadsTo);
    if (inside == null) {
        return absolute && !climbs ? target : leadsTo;
    }
    const movedTarget = inMovedFolder(move, inside);
    return absolute ? movedTarget : path.relative(path.dirname(inMovedFolder(move, relative)), movedTarget) || ".";
}

/** What the link at `relative` holds and will hold, and whether it leads to a folder. */
export async function planLink(move: FolderMove, relative: string, target: string): Promise<PlannedLink> {
    const link = path.join(move.fromReal, relative);
    const directory = await stat(link).then((info) => info.isDirectory(), () => false);
    return { relative, target, moved: await movedLinkTarget(move, relative, target), directory };
}

/** Windows lists any reparse point, such as a deduplicated file, as a link, but reads only symlinks and junctions as one. */
async function readLinkTarget(link: string): Promise<string | null> {
    return readlink(link).catch((error: unknown) => {
        if (process.platform === "win32" && errnoCode(error) === "EINVAL") {
            return null;
        }
        throw error;
    });
}

/**
 * Every symlink in the folder by its relative path, planned before the move; links are never followed. The folder is
 * read at its real path, as the OS finds it, even where its spelling has a `..` after a link.
 */
export async function planLinks(move: FolderMove, signal: AbortSignal, relative = "", planned = new Map<string, PlannedLink>()) {
    for (const entry of await readdir(path.join(move.fromReal, relative), { withFileTypes: true })) {
        signal.throwIfAborted();
        const entryRelative = path.join(relative, entry.name);
        const entryPath = path.join(move.fromReal, entryRelative);
        const target = entry.isSymbolicLink() ? await readLinkTarget(entryPath) : null;
        if (target != null) {
            planned.set(entryRelative, await planLink(move, entryRelative, target));
        }
        else if (entry.isDirectory() || (entry.isSymbolicLink() && (await lstat(entryPath)).isDirectory())) {
            await planLinks(move, signal, entryRelative, planned);
        }
    }
    return planned;
}

/**
 * Makes a symlink at `link` holding `target`. Windows needs to know whether it leads to a folder; a folder link it may
 * not create falls back to a junction, which holds an absolute path, resolved from `finalLink`, where the link ends up
 * once its folder has moved.
 */
export async function createLink(target: string, link: string, directory: boolean, finalLink = link) {
    if (process.platform !== "win32") {
        await symlink(target, link);
        return;
    }
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

/**
 * Points the link at `target` if it still holds `expected`, making the new link before setting the old one aside, so a
 * link that can't be made loses nothing. An entry changed since the plan, by the CLI say, is put back untouched and
 * fails the move; one already gone, as after an earlier attempt that couldn't finish, is simply replaced.
 */
async function replaceLink(link: string, expected: string, target: string, directory: boolean, finalLink = link) {
    const replacement = `${link}.relinking-${process.pid}`;
    const aside = `${link}.relinked-${process.pid}`;
    await createLink(target, replacement, directory, finalLink);
    let setAside = false;
    try {
        setAside = await rename(link, aside).then(() => true, (error: unknown) => {
            if (errnoCode(error) !== "ENOENT") {
                throw error;
            }
            return false;
        });
        if (setAside && await readlink(aside).catch(() => null) !== expected) {
            throw new HttpError(409, `${finalLink} changed during the move. Close anything using the folder and try again.`);
        }
        await rename(replacement, link);
    }
    catch (error) {
        if (setAside) {
            await rename(aside, link);
        }
        await unlink(replacement).catch(() => undefined);
        throw error;
    }
    if (setAside) {
        await unlink(aside);
    }
}

/** After the folder was renamed to `move.to`, points each planned link that changes at its new target, recording it in `relinked`. */
export async function relinkMovedFolder(move: FolderMove, planned: Iterable<PlannedLink>, relinked: PlannedLink[], signal: AbortSignal) {
    for (const link of planned) {
        signal.throwIfAborted();
        if (link.moved === link.target) {
            continue;
        }
        relinked.push(link);
        await replaceLink(path.join(move.to, link.relative), link.target, link.moved, link.directory);
    }
}

/** Puts back what {@link relinkMovedFolder} changed, as many as it can, before the folder is renamed back. */
export async function restoreLinks(move: FolderMove, relinked: PlannedLink[]) {
    for (const { relative, target, moved, directory } of [...relinked].reverse()) {
        const link = path.join(move.to, relative);
        if (await readlink(link).then((current) => current === target, () => false)) {
            continue;
        }
        const original = path.join(move.from, relative);
        await replaceLink(link, moved, target, directory, original).catch((error: unknown) => {
            console.warn(`Could not put back the link ${original}: ${String(error)}`);
        });
    }
}
