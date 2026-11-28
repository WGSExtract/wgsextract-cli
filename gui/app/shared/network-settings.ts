export const networkModes = ["full", "low", "off"] as const;
export const networkPreferences = ["automatic", ...networkModes] as const;
export type NetworkMode = typeof networkModes[number];
export type NetworkPreference = typeof networkPreferences[number];
export type NetworkCost = "constrained" | "unconstrained" | "unknown";

export type NetworkSettings = {
    /** The first-launch answer. "deferred" (Not now) holds back automatic setup until the user runs setup. */
    consent: "pending" | "accepted" | "deferred";
    /** Allow check for updates: at launch and when setup runs. On by default; Not now turns it off. */
    allowUpdateChecks: boolean;
    mode: NetworkPreference;
    detectedCost: NetworkCost;
};

export function effectiveNetworkMode(settings: NetworkSettings): NetworkMode {
    if (settings.consent === "pending") return "off";
    if (settings.mode !== "automatic") return settings.mode;
    return settings.detectedCost === "unconstrained" ? "full" : "low";
}

/** Network mode Off is the one switch for all network activity; Allow check for updates turns off only those. */
export function networkAllowed(settings: NetworkSettings, activity: "explicit" | "external" | "startup-setup" | "startup-update" | "startup-download") {
    if (settings.consent === "pending") return false;
    if (activity === "explicit") return true;
    const mode = effectiveNetworkMode(settings);
    if (mode === "off") return false;
    if (activity === "external") return true;
    if (activity === "startup-setup") return settings.consent === "accepted" && mode === "full";
    // Low allows the small update check, not the automatic download.
    return settings.allowUpdateChecks && (mode === "full" || activity === "startup-update");
}
