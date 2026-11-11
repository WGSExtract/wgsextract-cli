import path from "node:path";

/**
 * Inside an AppImage, its AppRun points LD_LIBRARY_PATH, PATH, PYTHONHOME, GIO_MODULE_DIR and more into the
 * mounted image so the app loads the libraries it bundles. The programs the backend starts (setup's curl,
 * the CLI's Python, file pickers) are the system's: with those variables they load the image's older
 * libraries (an OpenSSL without the symbols the system's libssl needs, say) and fail. This drops every entry
 * pointing into the image, leaving the environment the user's session gave the app.
 */
export function leaveAppImageEnvironment(environment: NodeJS.ProcessEnv = process.env) {
    const appDir = environment.APPDIR;
    // AppRun sets APPDIR, also when it is run from an extracted image (--appimage-extract), where APPIMAGE is not set.
    if (!appDir || !path.posix.isAbsolute(appDir)) {
        return;
    }
    const root = path.posix.resolve(appDir);
    const inside = (entry: string) => {
        const resolved = path.posix.resolve(entry);
        return resolved === root || resolved.startsWith(`${root}/`);
    };
    for (const [key, value] of Object.entries(environment)) {
        if (!value?.includes(root)) {
            continue;
        }
        // AppRun also leaves empty entries, which mean the current folder in PATH and LD_LIBRARY_PATH.
        const kept = value.split(":").filter((entry) => entry !== "" && !inside(entry));
        if (kept.length > 0) {
            environment[key] = kept.join(":");
        }
        else {
            delete environment[key];
        }
    }
}
