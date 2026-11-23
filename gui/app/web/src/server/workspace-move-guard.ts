import { lstat, readlink, realpath } from "node:fs/promises";
import path from "node:path";
import { formatLinkedMoveFolder } from "../../../shared/workspace-move-message.js";
import { errnoCode, HttpError } from "./errors.js";

export class LinkedMoveFolderError extends HttpError {
    constructor(readonly link: string, readonly target: string | null) {
        super(400, formatLinkedMoveFolder({ link, target }));
    }
}

export async function refuseLinkedMoveFolders(...folders: string[]) {
    // A trailing slash or "/." can make lstat follow the last symlink. Don't
    // resolve ".." lexically, which changes its meaning after a link.
    const trailingSeparators = process.platform === "win32" ? /(?:[\\/]\.?)+$/ : /(?:\/\.?)+$/;
    for (const folder of folders) {
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
            const resolved = await realpath(link).catch(() => null);
            const target = resolved && (rawTarget !== null || path.relative(link, resolved) !== "") ? resolved : rawTarget;
            throw new LinkedMoveFolderError(link, target);
        }
    }
}
