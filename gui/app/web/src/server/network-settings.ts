import { effectiveNetworkMode, networkAllowed, networkPreferences, type NetworkPreference, type NetworkSettings } from "../../../shared/network-settings.js";
import { HttpError } from "./errors.js";
import { systemNetworkCost } from "./network-cost/detector.js";
import { readAppSettings, updateAppSettings } from "./workspace-location.js";

function isPreference(value: unknown): value is NetworkPreference {
    return networkPreferences.some((mode) => mode === value);
}

/**
 * Allow check for updates is saved as `checkForUpdatesAtLaunch`, the key releases up to v0.9.0 wrote for their
 * launch check, so their choice carries over. v0.9.0's Privacy mode turned the check off; it reads as unchecked and
 * the next save writes that instead.
 */
function allowUpdateChecks(saved: Record<string, unknown>) {
    return saved.privacyMode !== true && saved.checkForUpdatesAtLaunch !== false;
}

function currentFormat(saved: Record<string, unknown>, checkForUpdatesAtLaunch = allowUpdateChecks(saved)) {
    const { privacyMode: _privacyMode, ...rest } = saved;
    return { ...rest, checkForUpdatesAtLaunch };
}

function settingsFrom(saved: Record<string, unknown>): Omit<NetworkSettings, "detectedCost"> {
    const consent = saved.networkConsent;
    return {
        consent: consent === "accepted" || consent === "deferred" ? consent : "pending",
        allowUpdateChecks: allowUpdateChecks(saved),
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
        // Installations set up before the first-launch question keep Privacy mode's choice for the update check.
        ...currentFormat(saved),
        networkConsent: "accepted",
    } : null);
}

export async function saveNetworkSettings(body: Record<string, unknown>) {
    const { consent, allowUpdateChecks: allow, mode } = body;
    if ((consent !== undefined && consent !== "accepted" && consent !== "deferred") ||
        (allow !== undefined && typeof allow !== "boolean") ||
        (mode !== undefined && !isPreference(mode))) {
        throw new HttpError(400, "Invalid network settings.");
    }
    let recordedChoice = false;
    const saved = await updateAppSettings((previous) => {
        const current = settingsFrom(previous);
        if (current.consent === "pending") {
            if (!consent) throw new HttpError(409, "Choose your network preferences first.");
            recordedChoice = true;
            // OK allows update checks; Not now turns them off.
            return { ...currentFormat(previous, consent === "accepted"), networkConsent: consent };
        }
        if (consent) return null; // A stale dialog in another window cannot replace the saved choice.
        return {
            ...currentFormat(previous, allow ?? current.allowUpdateChecks),
            ...(mode !== undefined ? { networkMode: mode } : {}),
        };
    });
    return { ...settingsFrom(saved), detectedCost: await systemNetworkCost.detect(), recordedChoice };
}

/** Running setup yourself answers the first-launch question that Not now put off; update checks stay as set. */
export async function acceptNetworkConsentForSetup() {
    await updateAppSettings((saved) => settingsFrom(saved).consent === "deferred"
        ? { ...currentFormat(saved), networkConsent: "accepted" }
        : null);
}

export async function requireNetworkChoice(activity: Parameters<typeof networkAllowed>[1] = "explicit") {
    const saved = settingsFrom(await readAppSettings());
    const detectedCost = activity === "startup-setup" || activity === "startup-download"
        ? await systemNetworkCost.detect() : "unknown";
    if (!networkAllowed({ ...saved, detectedCost }, activity)) {
        throw new HttpError(403, "This operation is not allowed by the Network mode setting.");
    }
}
