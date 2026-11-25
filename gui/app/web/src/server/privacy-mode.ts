import { HttpError } from "./errors.js";
import { withSettingsFileLock } from "./settings-lock.js";
import { appSettingsPath, readAppSettings, writeAppSettings } from "./workspace-location.js";

/**
 * Privacy mode stops the app's automatic network activity (the startup update check). It is an app-wide setting
 * in the app's settings.json, not a bundle's saved state, so loading another bundle or resetting a bundle's state
 * never turns it off.
 */
const privacyModeKey = "privacyMode";

export async function readPrivacyMode(): Promise<boolean> {
    return (await readAppSettings())[privacyModeKey] === true;
}

export async function savePrivacyMode(body: Record<string, unknown>): Promise<{ privacyMode: boolean }> {
    const enabled = body.enabled;
    if (typeof enabled !== "boolean") {
        throw new HttpError(400, "enabled must be a boolean");
    }
    await withSettingsFileLock(appSettingsPath(), async () => {
        await writeAppSettings({ ...await readAppSettings(), [privacyModeKey]: enabled });
    });
    return { privacyMode: enabled };
}
