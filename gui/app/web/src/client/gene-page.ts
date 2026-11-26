import type { GeneDocument, GeneFilter } from "../../../shared/gene-page.js";
import { geneExportName, geneFilters } from "../../../shared/gene-page.js";
import { firstPathValue } from "../../../shared/rendering.js";
import { api } from "./api.js";
import { escapeAttribute, escapeHTML } from "./dom.js";
import { requestGeneIobio } from "./gene-iobio.js";
import { createGenePrerequisitesController, type GenePageInputs, type GenePageProblem, type PendingGenePrerequisites } from "./gene-page-prerequisites.js";
import { openGenomeViewer } from "./genome-viewer-launcher.js";
import { renderGeneDocument, type GeneText } from "./gene-page-view.js";
import { createModalFocusBinder } from "./modal-focus.js";
import { chooseLocalPath } from "./path-picker.js";
import { state } from "./state.js";
import { renderCheckingNotice, renderPrerequisitesModal } from "./view/prerequisites.js";

type GenePageResult =
    | { document: GeneDocument; exportPath?: string; exportRows?: number }
    | GenePageProblem;

let pane: HTMLElement | undefined;

/** The gene page covers #app like the viewer; its data comes from `wgsextract info gene` on this computer. */
export function openGenePage(opener: HTMLElement) {
    if (pane) return;
    const app = document.querySelector<HTMLElement>("#app");
    if (!app) return;
    const strings = state.labels.geneStrings ?? {};
    const text: GeneText = (key, values = {}) => Object.entries(values)
        .reduce((result, [name, value]) => result.replaceAll(`%{${name}}`, String(value)), strings[key] ?? key);
    const field = (id: string, label: string, help: string, value: string, attributes = "") => `
        <label for="gene-${id}">${escapeHTML(label)}</label>
        <span class="input-button-row"><input id="gene-${id}" name="${id}" value="${escapeAttribute(value)}" ${attributes} aria-describedby="gene-${id}-help">${id === "gene" || id === "minDepth" ? "" : `<button type="button" class="secondary-button" data-gene-pick="${id}">${escapeHTML(state.labels.chooseButtonTitle ?? "")}</button>`}</span>
        <p id="gene-${id}-help" class="field-help">${escapeHTML(help)}</p>`;
    const container = document.createElement("section");
    container.className = "gene-page-shell";
    container.innerHTML = `
      <button type="button" class="genome-viewer-close" data-gene-close>${escapeHTML(text("back"))}</button>
      <main class="gene-page">
        <header class="page-header"><h1>${escapeHTML(text("title"))}</h1><p>${escapeHTML(text("privacy"))}</p></header>
        <form data-gene-form class="gene-form">
          ${field("gene", text("symbol"), text("symbolHelp"), "", 'required maxlength="64" autocomplete="off" spellcheck="false" dir="ltr"')}
          ${field("alignment", text("alignment"), text("alignmentHelp"), firstPathValue(state.fieldValues.bam_path ?? ""), 'autocomplete="off" spellcheck="false" dir="ltr"')}
          ${field("vcf", text("vcf"), text("vcfHelp"), firstPathValue(state.fieldValues.vcf_path ?? ""), 'autocomplete="off" spellcheck="false" dir="ltr"')}
          ${field("minDepth", text("minDepth"), text("minDepthHelp"), "", 'type="number" min="0" step="1" inputmode="numeric"')}
          <div><button type="submit" class="action-button primary">${escapeHTML(text("show"))}</button></div>
        </form>
        <p data-gene-status role="status" aria-live="polite"></p>
        <section data-gene-install class="gene-install-panel" hidden>
          <h3 data-gene-install-title></h3>
          <pre data-gene-install-log class="gene-install-log"></pre>
        </section>
        <div data-gene-result></div>
      </main>
      <div data-gene-prerequisites></div>`;
    const form = required<HTMLFormElement>(container, "[data-gene-form]");
    const status = required<HTMLElement>(container, "[data-gene-status]");
    const installPanel = required<HTMLElement>(container, "[data-gene-install]");
    const installTitle = required<HTMLElement>(container, "[data-gene-install-title]");
    const installLog = required<HTMLElement>(container, "[data-gene-install-log]");
    const output = required<HTMLElement>(container, "[data-gene-result]");
    const prereqSlot = required<HTMLElement>(container, "[data-gene-prerequisites]");
    const bindModalFocus = createModalFocusBinder("[data-prerequisites-dialog]", "[data-prerequisites-install], [data-prerequisites-continue]");
    let current: { inputs: GenePageInputs; doc: GeneDocument; transcript: number; filter: GeneFilter | "all" } | undefined;
    let request: AbortController | undefined;
    let checkingPrerequisites = false;
    let pendingPrerequisites: PendingGenePrerequisites | null = null;
    const report = (key: string, values: Record<string, string> = {}, isError = false) => {
        status.textContent = key ? text(key, values) : "";
        status.classList.toggle("inline-error", isError);
        status.setAttribute("role", isError ? "alert" : "status");
    };
    const problem = (result: GenePageProblem) =>
        report(`problem.${result.problem}`, { file: result.fileName ?? "", message: result.message ?? "" }, true);
    const render = () => {
        if (!current) return;
        output.innerHTML = renderGeneDocument(current.doc, text, current.transcript, current.filter, Boolean(current.inputs.alignment || current.inputs.vcf));
    };
    const renderPrerequisites = () => {
        prereqSlot.innerHTML = pendingPrerequisites
            ? renderPrerequisitesModal(pendingPrerequisites.missing, pendingPrerequisites.stillMissing)
            : checkingPrerequisites ? renderCheckingNotice() : "";
        bindModalFocus(container, () => prerequisites.cancel());
    };
    const inputs = (): GenePageInputs => {
        const value = (name: string) => (form.elements.namedItem(name) as HTMLInputElement | null)?.value.trim() ?? "";
        const depth = value("minDepth");
        return { gene: value("gene"), alignment: value("alignment"), vcf: value("vcf"), minDepth: depth === "" ? null : Number(depth) };
    };
    const call = async (body: GenePageInputs & { export?: string }): Promise<GenePageResult | undefined> => {
        request?.abort();
        const controller = new AbortController();
        request = controller;
        try {
            return await api<GenePageResult>("/api/gene/open", { body, signal: controller.signal });
        } catch {
            if (!controller.signal.aborted) problem({ problem: "unavailable" });
            return undefined;
        } finally {
            if (request === controller) request = undefined;
        }
    };
    const loadGene = async (asked: GenePageInputs) => {
        installPanel.hidden = true;
        report("loading");
        output.setAttribute("aria-busy", "true");
        const result = await call(asked);
        output.setAttribute("aria-busy", "false");
        if (!result) return;
        if ("problem" in result) {
            problem(result);
            return;
        }
        current = { inputs: asked, doc: result.document, transcript: 0, filter: "all" };
        report("ready", { gene: result.document.gene.symbol });
        render();
    };
    const prerequisites = createGenePrerequisitesController({
        onChecking: (checking) => {
            checkingPrerequisites = checking;
            renderPrerequisites();
        },
        onPending: (pending) => {
            pendingPrerequisites = pending;
            if (pending) report("");
            renderPrerequisites();
        },
        onInstallStart: (title) => {
            report("");
            installTitle.textContent = title;
            installLog.textContent = "";
            installPanel.hidden = false;
        },
        onInstallOutput: (chunk) => {
            installLog.textContent += chunk;
            installLog.scrollTop = installLog.scrollHeight;
        },
        onInstallEnd: (succeeded, error) => {
            if (succeeded) {
                installPanel.hidden = true;
            } else if (error) {
                status.textContent = error;
            }
        },
        onProblem: (result) => problem(result),
        onReady: (asked) => loadGene(asked),
    });
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        request?.abort();
        installPanel.hidden = true;
        report("");
        void prerequisites.run(inputs());
    });
    container.addEventListener("change", (event) => {
        const target = event.target;
        if (!current || !(target instanceof HTMLSelectElement)) return;
        if (target.matches("[data-gene-transcript]")) current.transcript = Number(target.value);
        if (target.matches("[data-gene-filter]")) current.filter = ["all", ...geneFilters].includes(target.value) ? target.value as GeneFilter | "all" : "all";
        render();
        container.querySelector<HTMLElement>(target.matches("[data-gene-filter]") ? "[data-gene-filter]" : "[data-gene-transcript]")?.focus();
    });
    container.addEventListener("click", (event) => {
        const target = event.target instanceof Element ? event.target.closest<HTMLElement>("button") : null;
        if (!target) return;
        if (target.matches("[data-prerequisites-cancel]")) {
            prerequisites.cancel();
        } else if (target.matches("[data-prerequisites-continue]")) {
            void prerequisites.continue();
        } else if (target.matches("[data-prerequisites-install]")) {
            void prerequisites.install();
        } else if (target.matches("[data-gene-close]")) {
            prerequisites.dispose();
            request?.abort();
            container.remove();
            pane = undefined;
            app.style.display = "";
            app.inert = false;
            app.removeAttribute("aria-hidden");
            (app.querySelector<HTMLElement>("[data-gene-page-open]") ?? opener).focus();
        } else if (target.dataset.genePick) {
            const name = target.dataset.genePick;
            const input = form.elements.namedItem(name) as HTMLInputElement | null;
            void chooseLocalPath({ id: name, label: text(name), pathType: "file" }, input?.value ?? "").then((chosen) => {
                if (chosen && input) input.value = chosen;
            });
        } else if (target.matches("[data-gene-iobio]") && current) {
            requestGeneIobio(current.doc, current.inputs, text, target);
        } else if (target.matches("[data-gene-viewer]") && current) {
            const locus = current.doc.gene.locus;
            openGenomeViewer(state.localizationCode, state.labels, target, current.inputs.alignment || current.inputs.vcf, locus, { host: container, backLabel: text("backToGene") });
        } else if (target.matches("[data-gene-export]") && current) {
            const exporting = current;
            const exportStatus = container.querySelector<HTMLElement>("[data-gene-export-status]");
            if (exportStatus) exportStatus.textContent = text("exporting");
            void call({ ...exporting.inputs, export: geneExportName(exporting.filter) }).then((result) => {
                const note = container.querySelector<HTMLElement>("[data-gene-export-status]");
                if (!result || !note) return;
                if ("problem" in result) {
                    note.textContent = text(`problem.${result.problem}`, { file: result.fileName ?? "", message: result.message ?? "" });
                    return;
                }
                if (!result.exportPath) return;
                note.innerHTML = `${escapeHTML(text("exported", { rows: result.exportRows ?? 0, file: result.exportPath }))} <button type="button" class="secondary-button" data-gene-reveal="${escapeAttribute(result.exportPath)}">${escapeHTML(text("reveal"))}</button>`;
            });
        } else if (target.dataset.geneReveal) {
            void api("/api/result/open", { body: { path: target.dataset.geneReveal, reveal: true } }).catch(() => problem({ problem: "unavailable" }));
        }
    });
    document.body.append(container);
    pane = container;
    app.style.display = "none";
    app.inert = true;
    app.setAttribute("aria-hidden", "true");
    required<HTMLInputElement>(container, "#gene-gene").focus();
}

function required<T extends Element>(root: Element, selector: string): T {
    const element = root.querySelector<T>(selector);
    if (!element) throw new Error(`Missing gene page element: ${selector}`);
    return element;
}
