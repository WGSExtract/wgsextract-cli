import { effectiveNetworkMode, networkAllowed, networkPreferences, type NetworkPreference, type NetworkSettings } from "../../../shared/network-settings.js";
import { HttpError } from "./errors.js";
import { systemNetworkCost } from "./network-cost/detector.js";
import { readAppSettings, updateAppSettings } from "./workspace-location.js";

function isPreference(value: unknown): value is NetworkPreference {
    return networkPreferences.some((mode) => mode === value);
}

function settingsFrom(saved: Record<string, unknown>): Omit<NetworkSettings, "detectedCost"> {
    const consent = saved.networkConsent;
    return {
        consent: consent === "accepted" || consent === "deferred" ? consent : "pending",
        privacyMode: saved.privacyMode === true,
        checkForUpdatesAtLaunch: saved.checkForUpdatesAtLaunch === true,
        mode: isPreference(saved.networkMode) ? saved.networkMode : "automatic",
    };
}

export async function readNetworkSettings(): Promise<NetworkSettings> {
    const [saved, detectedCost] = await Promise.all([readAppSettings(), systemNetworkCost.detect()]);
    return { ...settingsFrom(saved), detectedCost };
}

export async function readEffectiveNetworkMode() {
    const saved = settingsFrom(await readAppSettings());
    const detectedCost = saved.consent !== "pending" && saved.mode === "automatic"
        ? await systemNetworkCost.detect() : "unknown";
    return effectiveNetworkMode({ ...saved, detectedCost });
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
    const { consent, privacyMode, checkForUpdatesAtLaunch, mode } = body;
    if ((consent !== undefined && consent !== "accepted" && consent !== "deferred") ||
        (privacyMode !== undefined && typeof privacyMode !== "boolean") ||
        (checkForUpdatesAtLaunch !== undefined && typeof checkForUpdatesAtLaunch !== "boolean") ||
        (mode !== undefined && !isPreference(mode))) {
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
            ...(mode !== undefined ? { networkMode: mode } : {}),
        };
    });
    return { ...settingsFrom(saved), detectedCost: await systemNetworkCost.detect(), recordedChoice };
}

export async function requireNetworkChoice(activity: Parameters<typeof networkAllowed>[1] = "explicit") {
    const saved = settingsFrom(await readAppSettings());
    const detectedCost = activity === "startup-setup" || activity === "startup-download"
        ? await systemNetworkCost.detect() : "unknown";
    if (!networkAllowed({ ...saved, detectedCost }, activity)) {
        throw new HttpError(403, "This operation is not allowed by Network & Privacy settings.");
    }
}
