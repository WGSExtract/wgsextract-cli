import { geneIobioBuild, geneIobioConsentVersion } from "../../../shared/gene-iobio.js";
import type { GeneDocument } from "../../../shared/gene-page.js";
import { resolveWorkspaceDisplayPath } from "../../../shared/workspace-paths.js";
import { api } from "./api.js";
import { escapeHTML } from "./dom.js";
import type { GeneText } from "./gene-page-view.js";
import { workspaceRoots } from "./model.js";

export function requestGeneIobio(
    doc: GeneDocument, files: { alignment: string; vcf: string }, text: GeneText, opener: HTMLElement,
) {
    const build = geneIobioBuild(doc.build);
    if (!build) return;
    const roots = workspaceRoots();
    const selected = ([
        [files.alignment, /\.(bam|cram)$/],
        [files.vcf, /\.vcf\.gz$/],
    ] as const).filter(([file]) => file).map(([file, supported]) => `
        <li><bdi>${escapeHTML(resolveWorkspaceDisplayPath(file, roots))}</bdi>
            ${supported.test(file) ? "" : `(${escapeHTML(text("iobio.unsupportedFile"))})`}</li>`);
    const dialog = document.createElement("dialog");
    dialog.className = "gene-iobio-dialog";
    dialog.setAttribute("aria-labelledby", "gene-iobio-title");
    dialog.setAttribute("aria-describedby", "gene-iobio-licence gene-iobio-privacy");
    dialog.innerHTML = `
      <h2 id="gene-iobio-title">${escapeHTML(text("iobio.title"))}</h2>
      <p id="gene-iobio-licence">${escapeHTML(text("iobio.licence"))}</p>
      <p id="gene-iobio-privacy">${escapeHTML(text("iobio.privacy", { gene: doc.gene.symbol, build }))}</p>
      ${selected.length ? `<p>${escapeHTML(text("iobio.loadFiles"))}</p>
      <ul class="gene-iobio-files">${selected.join("")}</ul>` : ""}
      <p class="inline-error" role="alert"></p>
      <div class="gene-iobio-actions">
        <button type="button" class="secondary-button" data-iobio-cancel autofocus>${escapeHTML(text("iobio.cancel"))}</button>
        <button type="button" class="action-button primary" data-iobio-open>${escapeHTML(text("iobio.open"))}</button>
      </div>`;
    dialog.addEventListener("close", () => {
        dialog.remove();
        opener.focus();
    }, { once: true });
    const cancel = dialog.querySelector<HTMLButtonElement>("[data-iobio-cancel]");
    const allow = dialog.querySelector<HTMLButtonElement>("[data-iobio-open]");
    const error = dialog.querySelector("[role=alert]");
    if (!cancel || !allow || !error) return;
    cancel.addEventListener("click", () => dialog.close());
    dialog.addEventListener("cancel", (event) => {
        if (allow.disabled) event.preventDefault();
    });
    allow.addEventListener("click", async () => {
        dialog.setAttribute("aria-busy", "true");
        allow.disabled = true;
        cancel.disabled = true;
        error.textContent = "";
        try {
            await api("/api/gene/iobio/open", { body: {
                gene: doc.gene.symbol, build: doc.build, consentVersion: geneIobioConsentVersion,
            } });
            dialog.close();
        } catch {
            dialog.removeAttribute("aria-busy");
            error.textContent = text("iobio.error");
            allow.disabled = false;
            cancel.disabled = false;
            allow.focus();
        }
    });
    document.body.append(dialog);
    dialog.showModal();
}
