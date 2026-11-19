import { localViewerFiles, localViewerLocus, ViewerFileError, type LocalTrack, type LocalViewerFiles } from "../../../shared/genome-viewer.js";
import type { StringMap } from "../../../shared/types.js";
import { genomeViewerLabels } from "./genome-viewer-labels.js";
import { escapeAttribute, escapeHTML } from "./dom.js";
import { disablePopupLookups, localPopupHTML, type IgvLocalTrack } from "./genome-viewer-privacy.js";

interface IgvBrowser {
    loadTrack(track: LocalTrack): Promise<IgvLocalTrack | undefined>;
    on(event: "trackclick", handler: typeof localPopupHTML): void;
    search(locus: string): Promise<boolean>;
    zoomIn(): void;
    zoomOut(): void;
}

interface IgvRuntime {
    createBrowser(host: HTMLElement, options: LocalViewerFiles & Record<string, unknown>): Promise<IgvBrowser>;
    removeBrowser(browser: IgvBrowser): void;
}

declare global {
    interface Window {
        igv: IgvRuntime;
    }
}

let browser: IgvBrowser | undefined;
const root = document.querySelector<HTMLElement>("#genome-viewer");
if (!root) {
    throw new Error("Missing genome viewer root.");
}
let strings: StringMap = {};
const text = (key: string) => strings[key] ?? key;

try {
    const labels = await genomeViewerLabels();
    strings = labels.strings;
    document.documentElement.lang = labels.locale;
    document.documentElement.dir = labels.direction;
    document.documentElement.toggleAttribute("data-viewer-embedded", window.parent !== window);
    document.title = `${text("title")} · WGS Extract`;
    root.innerHTML = `
      <header class="page-header">
        <a class="genome-viewer-back" href="/">${escapeHTML(text("back"))}</a>
        <h1>${escapeHTML(text("title"))}</h1>
        <p>${escapeHTML(text("privacy"))}</p>
      </header>
      <form data-files class="viewer-files">
        <label>${escapeHTML(text("reference"))}
          <input type="file" name="reference" multiple accept=".fa,.fasta,.fna,.gz,.fai,.gzi" aria-describedby="reference-help">
        </label>
        <p id="reference-help">${escapeHTML(text("referenceHelp"))}</p>
        <label>${escapeHTML(text("tracks"))}
          <input type="file" name="tracks" multiple accept=".bam,.cram,.gz,.bgz,.bai,.csi,.crai,.tbi" aria-describedby="tracks-help">
        </label>
        <p id="tracks-help">${escapeHTML(text("tracksHelp"))}</p>
        <label>${escapeHTML(text("locus"))}
          <input type="text" name="locus" placeholder="chr1:1000-2000" autocomplete="off" spellcheck="false">
        </label>
        <div><button type="submit" class="action-button primary">${escapeHTML(text("open"))}</button></div>
      </form>
      <p data-viewer-status role="status" aria-live="polite">${escapeHTML(text("empty"))}</p>
      <form data-locus class="viewer-navigation" hidden>
        <label>${escapeHTML(text("locus"))}<input name="locus" autocomplete="off" spellcheck="false"></label>
        <button type="submit">${escapeHTML(text("go"))}</button>
        <div class="viewer-zoom">
          <button type="button" data-zoom="in" aria-label="${escapeAttribute(text("zoomIn"))}">+</button>
          <button type="button" data-zoom="out" aria-label="${escapeAttribute(text("zoomOut"))}">−</button>
        </div>
      </form>
      <div data-igv class="viewer-tracks" role="region" aria-label="${escapeAttribute(text("title"))}" hidden></div>
      <footer><p>${escapeHTML(text("limits"))}</p><p><a href="/vendor/igv/LICENSE">igv.js 3.8.10 · MIT</a></p></footer>`;
    const filesForm = required<HTMLFormElement>("[data-files]");
    const navigation = required<HTMLFormElement>("[data-locus]");
    const host = required<HTMLElement>("[data-igv]");
    // igv's context menus contain BLAT actions that transmit read sequences. This slice exposes only local inspection.
    host.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        event.stopImmediatePropagation();
    }, true);
    host.addEventListener("click", (event) => {
        if (event.composedPath().some((element) => element instanceof HTMLAnchorElement)) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }, true);
    filesForm.addEventListener("submit", (event) => {
        event.preventDefault();
        void openFiles(filesForm, navigation, host);
    });
    navigation.addEventListener("submit", (event) => {
        event.preventDefault();
        void goToLocus(navigation);
    });
    navigation.querySelectorAll<HTMLButtonElement>("[data-zoom]").forEach((button) => {
        button.addEventListener("click", () => button.dataset.zoom === "in" ? browser?.zoomIn() : browser?.zoomOut());
    });
    window.addEventListener("pagehide", dispose);
} catch (error) {
    root.textContent = error instanceof Error ? error.message : String(error);
}

function required<T extends Element>(selector: string): T {
    const element = root?.querySelector<T>(selector);
    if (!element) {
        throw new Error(`Missing viewer element: ${selector}`);
    }
    return element;
}

function status(key: string, isError = false, fileName = "") {
    const element = required<HTMLElement>("[data-viewer-status]");
    element.textContent = text(key).replace("%{file}", fileName);
    element.classList.toggle("inline-error", isError);
    element.setAttribute("role", isError ? "alert" : "status");
}

async function openFiles(form: HTMLFormElement, navigation: HTMLFormElement, host: HTMLElement) {
    const controls = [...form.querySelectorAll<HTMLInputElement | HTMLButtonElement>("input, button")];
    try {
        const files = localViewerFiles(
            [...(required<HTMLInputElement>('[name="reference"]').files ?? [])],
            [...(required<HTMLInputElement>('[name="tracks"]').files ?? [])],
        );
        const locus = localViewerLocus(form.querySelector<HTMLInputElement>('[name="locus"]')?.value ?? "");
        controls.forEach((element) => { element.disabled = true; });
        navigation.hidden = true;
        host.hidden = false;
        host.setAttribute("aria-busy", "true");
        status("loading");
        dispose();
        browser = await window.igv.createBrowser(host, {
            ...files,
            tracks: [],
            locus,
            loadDefaultGenomes: false,
            genomeList: [],
            queryParametersSupported: false,
            search: false,
            showNavigation: false,
            showIdeogram: false,
            showSVGButton: false,
        });
        browser.on("trackclick", localPopupHTML);
        if (!await browser.search(locus)) {
            dispose();
            throw new ViewerFileError("locusNotFound");
        }
        for (const track of files.tracks) {
            const loaded = await browser.loadTrack(track);
            if (!loaded) {
                throw new Error("Track could not be loaded.");
            }
            disablePopupLookups(loaded);
        }
        required<HTMLInputElement>('[data-locus] [name="locus"]').value = locus;
        navigation.hidden = false;
        status("ready");
    } catch (error) {
        status(error instanceof ViewerFileError ? error.code : "loadFailed", true, error instanceof ViewerFileError ? error.fileName : "");
        if (!(error instanceof ViewerFileError)) {
            dispose();
            console.error("Genome viewer:", error);
        }
    } finally {
        controls.forEach((element) => { element.disabled = false; });
        host.setAttribute("aria-busy", "false");
        host.hidden = !browser;
    }
}

async function goToLocus(form: HTMLFormElement) {
    try {
        const locus = localViewerLocus(form.querySelector<HTMLInputElement>("input")?.value ?? "");
        if (!await browser?.search(locus)) {
            throw new ViewerFileError("locusNotFound");
        }
        status("ready");
    } catch (error) {
        status(error instanceof ViewerFileError ? error.code : "loadFailed", true);
    }
}

function dispose() {
    if (browser) {
        window.igv.removeBrowser(browser);
        browser = undefined;
    }
}
