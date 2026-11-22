import { lstat, readlink, realpath } from "node:fs/promises";
import path from "node:path";
import { errnoCode, HttpError } from "./errors.js";

export class LinkedMoveFolderError extends HttpError {
    constructor(readonly link: string, readonly target: string | null) {
        super(400, `Cannot move a folder from or to the link “${link}” (target: “${target ?? "unavailable"}”). Move the target yourself and update the setting, or choose a destination that is not a link.`);
    }
}

export async function refuseLinkedMoveFolders(...folders: string[]) {
    for (const folder of folders) {
        // A trailing slash or "/." can make lstat follow the last symlink. Don't
        // resolve ".." lexically, which changes its meaning after a link.
        const trailingSeparators = process.platform === "win32" ? /(?:[\\/]\.?)+$/ : /(?:\/\.?)+$/;
        const root = path.parse(folder).root;
        const trimmed = folder.replace(trailingSeparators, "");
        const link = trimmed.length < root.length ? root : trimmed;
        const info = await lstat(link).catch((error: unknown) => {
            if (errnoCode(error) === "ENOENT") return null;
            throw error;
        });
        // Bundled Node (libuv) also reports Windows junctions/reparse points here.
        if (info?.isSymbolicLink()) {
            const rawTarget = await readlink(link).catch((error: unknown) => {
                if (process.platform === "win32" && errnoCode(error) === "EINVAL") return null;
                throw error;
            });
            const target = rawTarget && (path.isAbsolute(rawTarget) ? rawTarget
                : await realpath(link).catch(() => `${path.dirname(link)}${path.sep}${rawTarget}`));
            throw new LinkedMoveFolderError(link, target);
        }
    }
}
