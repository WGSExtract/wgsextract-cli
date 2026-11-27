import type { NetworkCost } from "../../../../shared/network-settings.js";
import type { LocalQuery } from "./query.js";

export async function linuxNetworkCost(query: LocalQuery): Promise<NetworkCost> {
    const output = await query("nmcli", ["--terse", "--fields", "GENERAL.TYPE,GENERAL.STATE,GENERAL.METERED", "device", "show"]);
    let known = false;
    let unknown = false;
    for (const device of output.trim().split(/\r?\n\s*\r?\n/)) {
        const fields = new Map(device.split(/\r?\n/).map((line) => {
            const colon = line.indexOf(":");
            return [line.slice(0, colon), line.slice(colon + 1)];
        }));
        if (fields.get("GENERAL.TYPE") === "loopback" || !/^100\b/.test(fields.get("GENERAL.STATE") ?? "")) continue;
        const metered = fields.get("GENERAL.METERED");
        if (metered === "yes" || metered === "yes (guessed)") return "constrained";
        if (metered === "no" || metered === "no (guessed)") known = true;
        else unknown = true;
    }
    return known && !unknown ? "unconstrained" : "unknown";
}
