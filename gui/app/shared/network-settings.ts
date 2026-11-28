export const networkModes = ["full", "low", "off"] as const;
export const networkPreferences = ["automatic", ...networkModes] as const;
export type NetworkMode = typeof networkModes[number];
export type NetworkPreference = typeof networkPreferences[number];
export type NetworkCost = "constrained" | "unconstrained" | "unknown";

export type NetworkSettings = {
    consent: "pending" | "accepted" | "deferred";
    privacyMode: boolean;
    checkForUpdatesAtLaunch: boolean;
    mode: NetworkPreference;
    detectedCost: NetworkCost;
};

export function effectiveNetworkMode(settings: NetworkSettings): NetworkMode {
    if (settings.consent === "pending") return "off";
    if (settings.mode !== "automatic") return settings.mode;
    return settings.detectedCost === "unconstrained" ? "full" : "low";
}

export function networkAllowed(settings: NetworkSettings, activity: "explicit" | "external" | "startup-setup" | "startup-update" | "startup-download") {
    if (settings.consent === "pending") return false;
    if (activity === "explicit") return true;
    const mode = effectiveNetworkMode(settings);
    if (mode === "off") return false;
    if (activity === "external") return true;
    if (settings.privacyMode) return false;
    if (mode === "low" && activity !== "startup-update") return false;
    return activity !== "startup-setup"
        ? settings.checkForUpdatesAtLaunch
        : settings.consent === "accepted";
}
