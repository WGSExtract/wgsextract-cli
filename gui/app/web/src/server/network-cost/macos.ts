import type { NetworkCost } from "../../../../shared/network-settings.js";
import type { LocalQuery } from "./query.js";

export async function macOSNetworkCost(query: LocalQuery, nativeExecutable: string | undefined): Promise<NetworkCost> {
    if (!nativeExecutable) return "unknown";
    const [constrained, expensive] = (await query(nativeExecutable, ["--network-cost"])).trim().split(/\s+/);
    if (constrained === "true" || expensive === "true") return "constrained";
    if (constrained === "false" && expensive === "false") return "unconstrained";
    return "unknown";
}
