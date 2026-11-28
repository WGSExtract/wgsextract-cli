import type { NetworkCost } from "../../../../shared/network-settings.js";
import type { LocalQuery } from "./query.js";

const connectionCostScript = `
$ErrorActionPreference = 'Stop'
$profile = [Windows.Networking.Connectivity.NetworkInformation,Windows,ContentType=WindowsRuntime]::GetInternetConnectionProfile()
if ($null -ne $profile) {
    $cost = $profile.GetConnectionCost()
    Write-Output "$($cost.NetworkCostType) $($cost.Roaming) $($cost.OverDataLimit) $($cost.ApproachingDataLimit) $($cost.BackgroundDataUsageRestricted)"
}
`;

export async function windowsNetworkCost(query: LocalQuery): Promise<NetworkCost> {
    const [cost, ...restrictions] = (await query("powershell.exe", [
        "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", connectionCostScript,
    ])).trim().split(/\s+/);
    if (cost === "Fixed" || cost === "Variable" || restrictions.includes("True")) return "constrained";
    return cost === "Unrestricted" ? "unconstrained" : "unknown";
}
