import { isIP } from "node:net";

/** Locally launched servers cannot redirect default actions or credentials off-host. */
export function localJobServerURL(value: string): string | undefined {
    try {
        const url = new URL(value);
        const host = url.hostname.replace(/^\[|\]$/g, "");
        const loopback = (isIP(host) === 4 && host.startsWith("127.")) || host === "::1";
        if (!loopback || !["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
            return undefined;
        }
        return url.href.replace(/\/+$/, "");
    } catch {
        return undefined;
    }
}
