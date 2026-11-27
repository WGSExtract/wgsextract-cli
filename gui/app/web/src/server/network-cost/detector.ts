import type { NetworkCost } from "../../../../shared/network-settings.js";
import { linuxNetworkCost } from "./linux.js";
import { macOSNetworkCost } from "./macos.js";
import { queryNetworkCost, type LocalQuery } from "./query.js";
import { windowsNetworkCost } from "./windows.js";

export function createNetworkCostDetector(platform: NodeJS.Platform, query: LocalQuery, nativeExecutable?: string) {
    const detect = (): Promise<NetworkCost> => {
        if (platform === "linux") return linuxNetworkCost(query);
        if (platform === "darwin") return macOSNetworkCost(query, nativeExecutable);
        if (platform === "win32") return windowsNetworkCost(query);
        return Promise.resolve("unknown");
    };
    let pending: Promise<NetworkCost> | undefined;
    return () => {
        // Share simultaneous manifest, settings and CLI probes, but never reuse a completed snapshot.
        pending ??= detect().catch((): NetworkCost => "unknown").finally(() => { pending = undefined; });
        return pending;
    };
}

export const systemNetworkCost = {
    detect: createNetworkCostDetector(process.platform, queryNetworkCost),
};
