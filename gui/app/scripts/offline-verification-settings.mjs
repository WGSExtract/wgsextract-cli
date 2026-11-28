import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { appSettingsPath } from "../dist/web/src/server/workspace-location.js";

export async function configureOfflineVerification(output) {
    const home = path.join(output, "home");
    Object.assign(process.env, {
        HOME: home, USERPROFILE: home, LOCALAPPDATA: home, XDG_DATA_HOME: home,
        WGSEXTRACT_GUI_APP_DATA_NAME: "verification",
    });
    const file = appSettingsPath();
    await mkdir(path.dirname(file), { recursive: true });
    await writeFile(file, '{"networkConsent":"deferred"}\n');
}
