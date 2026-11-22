import { lstat, mkdtemp, readdir, readlink, realpath, rename, rmdir, stat, symlink, unlink } from "node:fs/promises";
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

/** A link this move replaced, with what the new one holds: Windows may have made a junction holding an absolute path. */
export interface RelinkedLink extends PlannedLink {
    installed: string;
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
 * Whether each `..` in `target`, held by the link at `link`, climbs where its spelling says: from the folder named
 * before it, not from where a link it follows leads. Taken as so where the OS can't follow it, as for a dangling link.
 */
async function climbsByName(link: string, target: string) {
    const parts = target.split(separators());
    const prefix = parts.slice(0, parts.lastIndexOf("..") + 1).join(path.sep);
    const real = (folder: string) => realpath(folder).catch(() => null);
    // Unnormalized, so the OS decides what each `..` climbs from; normalized, as the spelling reads.
    const followed = await real(path.isAbsolute(prefix) ? prefix : `${path.dirname(link)}${path.sep}${prefix}`);
    return followed == null || followed === await real(path.resolve(path.dirname(link), prefix));
}

/**
 * What the symlink at `relative` in the moving folder, holding `target`, should hold at its new place. A target
 * spelled within the folder, whose every `..` climbs where its spelling says, keeps its spelling, relocated if
 * absolute, so links it passes through, such as `current -> v2`, still decide where it leads. Any other leads where it
 * did as the OS followed it before the move: to the same file at the new place if that is in the folder, else where it
 * did, an absolute target keeping its spelling unless a `..` makes the OS walk through folders that may be gone.
 */
export async function movedLinkTarget(move: FolderMove, relative: string, target: string): Promise<string> {
    const absolute = path.isAbsolute(target);
    const climbs = target.split(separators()).includes("..");
    const link = path.join(move.fromReal, relative);
    // A folder spelled with a `..` after a link is named only by its real path.
    const roots = move.from.split(separators()).includes("..") ? [move.fromReal] : [move.from, move.fromReal];
    const inside = (candidate: string) => roots.map((root) => within(root, candidate)).find((found) => found != null) ?? null;
    if (!climbs || await climbsByName(link, target)) {
        const spelled = absolute ? inside(target) : null;
        if (spelled != null) {
            return inMovedFolder(move, spelled);
        }
        if (!absolute && staysInFolder(path.dirname(relative), target)) {
            return target;
        }
    }
    const leadsTo = await currentTarget(link, target);
    const leadsInside = inside(leadsTo);
    if (leadsInside == null) {
        return absolute && !climbs ? target : leadsTo;
    }
    const movedTarget = inMovedFolder(move, leadsInside);
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
 * Points the link at `target` if it still holds `expected`, returning what the new link holds. The new link is made,
 * and the old set aside, in a folder of this move's own, so nothing else is overwritten, and a link that can't be made
 * loses nothing. An entry changed since the plan, by the CLI say, is put back untouched and fails the move; one
 * already gone is simply replaced.
 */
async function replaceLink(link: string, expected: string, target: string, directory: boolean, finalLink = link): Promise<string> {
    const scratch = await mkdtemp(path.join(path.dirname(link), ".relinking-"));
    const replacement = path.join(scratch, "new");
    const aside = path.join(scratch, "old");
    let setAside = false;
    try {
        await createLink(target, replacement, directory, finalLink);
        const installed = await readlink(replacement);
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
        if (setAside) {
            await unlink(aside);
        }
        return installed;
    }
    catch (error) {
        if (setAside) {
            await rename(aside, link);
        }
        throw error;
    }
    finally {
        await unlink(replacement).catch(() => undefined);
        // Left, holding the old entry, only if that could not be put back.
        await rmdir(scratch).catch((error: unknown) => {
            console.warn(`Could not remove ${scratch}: ${String(error)}`);
        });
    }
}

/**
 * After the folder was renamed to `move.to`, points each planned link that changes at its new target, recording in
 * `relinked` each one replaced, and only those, with what it now holds.
 */
export async function relinkMovedFolder(move: FolderMove, planned: Iterable<PlannedLink>, relinked: RelinkedLink[], signal: AbortSignal) {
    for (const link of planned) {
        signal.throwIfAborted();
        if (link.moved === link.target) {
            continue;
        }
        const installed = await replaceLink(path.join(move.to, link.relative), link.target, link.moved, link.directory);
        relinked.push({ ...link, installed });
    }
}

/** Puts back what {@link relinkMovedFolder} changed, as many as it can, before the folder is renamed back. */
export async function restoreLinks(move: FolderMove, relinked: RelinkedLink[]) {
    for (const { relative, target, installed, directory } of [...relinked].reverse()) {
        const original = path.join(move.from, relative);
        await replaceLink(path.join(move.to, relative), installed, target, directory, original).catch((error: unknown) => {
            console.warn(`Could not put back the link ${original}: ${String(error)}`);
        });
    }
}
