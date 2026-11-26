import { geneIobioBuild, geneIobioConsentVersion } from "../../../shared/gene-iobio.js";
import type { LooseRecord } from "../../../shared/types.js";
import { isViewerGeneSymbol } from "../../../shared/viewer-genes.js";
import { HttpError } from "./errors.js";
import { openSystemBrowser } from "./system-browser.js";
import { updateAppSettings } from "./workspace-location.js";

export async function openGeneIobio(body: LooseRecord, openBrowser = openSystemBrowser) {
    if (body.consentVersion !== geneIobioConsentVersion) {
        throw new HttpError(403, "Opening gene.iobio requires consent for this launch.");
    }
    const { gene } = body;
    const build = typeof body.build === "string" ? geneIobioBuild(body.build) : undefined;
    if (typeof gene !== "string" || !isViewerGeneSymbol(gene) || !build) {
        throw new HttpError(400, "gene.iobio needs a gene symbol and supported reference build.");
    }
    const url = new URL("https://gene.iobio.io/");
    url.search = new URLSearchParams({ gene, build }).toString();
    await updateAppSettings((settings) => ({
        ...settings,
        geneIobioConsent: { version: geneIobioConsentVersion, acceptedAt: new Date().toISOString() },
    }));
    // Do not put the selected gene in an error, log, or consent record.
    try {
        await openBrowser(url);
    } catch {
        throw new HttpError(500, "Could not open gene.iobio in the system browser.");
    }
    return { ok: true };
}
