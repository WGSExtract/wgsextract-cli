import { networkAllowed, type NetworkSettings } from "../../../shared/network-settings.js";
import { HttpError } from "./errors.js";
import { readAppSettings, updateAppSettings } from "./workspace-location.js";

function settingsFrom(saved: Record<string, unknown>): NetworkSettings {
    const consent = saved.networkConsent;
    return {
        consent: consent === "accepted" || consent === "deferred" ? consent : "pending",
        privacyMode: saved.privacyMode === true,
        checkForUpdatesAtLaunch: saved.checkForUpdatesAtLaunch === true,
    };
}

export async function readNetworkSettings() {
    return settingsFrom(await readAppSettings());
}

export async function migrateNetworkSettingsAfterCompletedSetup() {
    await updateAppSettings((saved) => settingsFrom(saved).consent === "pending" ? {
        ...saved,
        networkConsent: "accepted",
        // #1548 enabled the release-build check wherever Privacy mode was off.
        checkForUpdatesAtLaunch: saved.privacyMode !== true,
    } : null);
}

export async function saveNetworkSettings(body: Record<string, unknown>) {
    const { consent, privacyMode, checkForUpdatesAtLaunch } = body;
    if ((consent !== undefined && consent !== "accepted" && consent !== "deferred") ||
        (privacyMode !== undefined && typeof privacyMode !== "boolean") ||
        (checkForUpdatesAtLaunch !== undefined && typeof checkForUpdatesAtLaunch !== "boolean")) {
        throw new HttpError(400, "Invalid network settings.");
    }
    let recordedChoice = false;
    const saved = await updateAppSettings((previous) => {
        const current = settingsFrom(previous);
        if (current.consent === "pending") {
            if (!consent) throw new HttpError(409, "Choose your network preferences first.");
            recordedChoice = true;
            return { ...previous, networkConsent: consent, checkForUpdatesAtLaunch: consent === "accepted" };
        }
        if (consent) return null; // A stale dialog in another window cannot replace the saved choice.
        return {
            ...previous,
            ...(privacyMode !== undefined ? { privacyMode } : {}),
            ...(checkForUpdatesAtLaunch !== undefined ? { checkForUpdatesAtLaunch } : {}),
        };
    });
    return { ...settingsFrom(saved), recordedChoice };
}

export async function requireNetworkChoice(activity: Parameters<typeof networkAllowed>[1] = "explicit") {
    if (!networkAllowed(await readNetworkSettings(), activity)) {
        throw new HttpError(403, "This operation is not allowed by Network & Privacy settings.");
    }
}
