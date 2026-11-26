export type NetworkSettings = {
    consent: "pending" | "accepted" | "deferred";
    privacyMode: boolean;
    checkForUpdatesAtLaunch: boolean;
};

export function networkAllowed(settings: NetworkSettings, activity: "explicit" | "startup-setup" | "startup-update") {
    if (settings.consent === "pending") return false;
    if (activity === "explicit") return true;
    if (settings.privacyMode) return false;
    // OS network-cost signals can restrict automatic transfers here without changing the saved choices.
    return activity === "startup-update"
        ? settings.checkForUpdatesAtLaunch
        : settings.consent === "accepted";
}
