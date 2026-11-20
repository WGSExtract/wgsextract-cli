import { localViewerFiles, localViewerLocus, ViewerFileError, type LocalTrack, type LocalViewerFiles } from "../../../shared/genome-viewer.js";
import { releaseLibraryViewer, requestLibraryViewer } from "./genome-viewer-library.js";
import type { StringMap } from "../../../shared/types.js";
import { genomeViewerLabels } from "./genome-viewer-labels.js";
import { escapeAttribute, escapeHTML } from "./dom.js";
import { disablePopupLookups, localPopupHTML, type IgvLocalTrack } from "./genome-viewer-privacy.js";
import { validatedBedGraphTrack } from "../../../shared/viewer-track-records.js";
import { viewerGeneLookup, viewerNavigationLocus, type ViewerGene } from "../../../shared/viewer-genes.js";
import { viewerCytobandSource } from "../../../shared/viewer-cytobands.js";

interface IgvBrowser {
    genome: { cytobandSource?: ReturnType<typeof viewerCytobandSource> };
    loadTrack(track: LocalTrack<File | string> | { name: string; type: "annotation"; features: ViewerGene[]; searchable: false }): Promise<IgvLocalTrack | undefined>;
    on(event: "trackclick", handler: typeof localPopupHTML): void;
    search(locus: string): Promise<boolean>;
    zoomIn(): void;
    zoomOut(): void;
}

interface IgvRuntime {
    createBrowser(host: HTMLElement, options: LocalViewerFiles<File | string> & Record<string, unknown>): Promise<IgvBrowser>;
    removeBrowser(browser: IgvBrowser): void;
}

declare global {
    interface Window {
        igv: IgvRuntime;
    }
}

let browser: IgvBrowser | undefined;
let trackProblem: ViewerFileError | undefined;
let geneLookup = new Map<string, string>();
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
      <form data-library class="viewer-files">
        <label>${escapeHTML(text("libraryPath"))}
          <input type="text" name="library-path" dir="ltr" autocomplete="off" spellcheck="false" required aria-describedby="library-help">
        </label>
        <p id="library-help">${escapeHTML(text("libraryHelp"))}</p>
        <label>${escapeHTML(text("extraTracks"))}
          <textarea name="extra-tracks" rows="3" dir="ltr" autocomplete="off" spellcheck="false" aria-describedby="extra-tracks-help"></textarea>
        </label>
        <p id="extra-tracks-help">${escapeHTML(text("extraTracksHelp"))}</p>
        <label>${escapeHTML(text("locus"))}<input name="locus" placeholder="chr1:1000-2000" autocomplete="off" spellcheck="false"></label>
        <div><button type="submit" class="action-button primary">${escapeHTML(text("openLibrary"))}</button></div>
      </form>
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
      <p data-gene-status role="status" aria-live="polite" hidden></p>
      <p data-cytoband-status role="status" aria-live="polite" hidden></p>
      <form data-locus class="viewer-navigation" hidden>
        <label>${escapeHTML(text("navigation"))}<input name="locus" autocomplete="off" spellcheck="false" maxlength="128" aria-describedby="gene-help"></label>
        <p id="gene-help">${escapeHTML(text("geneHelp"))}</p>
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
    const libraryForm = required<HTMLFormElement>("[data-library]");
    libraryForm.addEventListener("submit", (event) => {
        event.preventDefault();
        void openLibrary(libraryForm, navigation, host);
    });
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
    if (labels.inputPath) {
        required<HTMLInputElement>('[name="library-path"]').value = labels.inputPath;
        required<HTMLInputElement>('[data-library] [name="locus"]').value = labels.locus ?? "";
        void openLibrary(libraryForm, navigation, host);
    }
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

function readyStatus() {
    if (trackProblem) status(trackProblem.code, true, trackProblem.fileName);
    else status("ready");
}

async function openFiles(form: HTMLFormElement, navigation: HTMLFormElement, host: HTMLElement) {
    try {
        const files = localViewerFiles(
            [...(required<HTMLInputElement>('[name="reference"]').files ?? [])],
            [...(required<HTMLInputElement>('[name="tracks"]').files ?? [])],
        );
        const locus = localViewerLocus(form.querySelector<HTMLInputElement>('[name="locus"]')?.value ?? "");
        await releaseLibraryViewer();
        await openTracks(files, locus, navigation, host);
    } catch (error) {
        status(error instanceof ViewerFileError ? error.code : "loadFailed", true, error instanceof ViewerFileError ? error.fileName : "");
    }
}

async function openLibrary(form: HTMLFormElement, navigation: HTMLFormElement, host: HTMLElement) {
    setBusy(true);
    dispose();
    navigation.hidden = true;
    host.hidden = true;
    status("loading");
    try {
        const result = await requestLibraryViewer(
            required<HTMLInputElement>('[name="library-path"]').value,
            form.querySelector<HTMLInputElement>('[name="locus"]')?.value ?? "",
            (form.querySelector<HTMLTextAreaElement>('[name="extra-tracks"]')?.value ?? "").split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
        );
        if ("problem" in result) {
            status(result.problem, true, result.fileName ?? "");
            return;
        }
        await openTracks(result.files, result.locus, navigation, host);
    } catch {
        status("libraryUnavailable", true);
    } finally {
        setBusy(false);
    }
}

function setBusy(busy: boolean) {
    root?.querySelectorAll<HTMLInputElement | HTMLButtonElement | HTMLTextAreaElement>("form input, form button, form textarea").forEach((element) => { element.disabled = busy; });
}

async function openTracks(files: LocalViewerFiles<File | string>, locus: string, navigation: HTMLFormElement, host: HTMLElement) {
    try {
        setBusy(true);
        navigation.hidden = true;
        host.hidden = false;
        host.setAttribute("aria-busy", "true");
        status("loading");
        dispose();
        trackProblem = undefined;
        geneLookup = viewerGeneLookup(files.genes?.features ?? []);
        const geneStatus = required<HTMLElement>("[data-gene-status]");
        geneStatus.textContent = (files.genes?.notices ?? []).map((notice) => text(notice.code).replace("%{file}", notice.fileName)).join(" ");
        geneStatus.hidden = !geneStatus.textContent;
        const cytobandStatus = required<HTMLElement>("[data-cytoband-status]");
        cytobandStatus.textContent = (files.cytobands?.notices ?? []).map((notice) => text(notice.code)
            .replace("%{file}", notice.fileName).replace("%{command}", notice.command)).join(" ");
        cytobandStatus.hidden = !cytobandStatus.textContent;
        const cytobands = files.cytobands?.features ?? [];
        browser = await window.igv.createBrowser(host, {
            ...files,
            tracks: [],
            locus,
            loadDefaultGenomes: false,
            genomeList: [],
            queryParametersSupported: false,
            search: false,
            showNavigation: false,
            showIdeogram: cytobands.length > 0,
            showSVGButton: false,
        });
        // igv skips URL cytobands when wholeGenomeView is false. Attach its
        // in-memory source before search rebuilds the ideogram's viewports.
        if (cytobands.length) browser.genome.cytobandSource = viewerCytobandSource(cytobands);
        browser.on("trackclick", localPopupHTML);
        if (!await browser.search(locus)) {
            dispose();
            throw new ViewerFileError("locusNotFound");
        }
        if (files.genes?.features.length) {
            const loaded = await browser.loadTrack({ name: text("geneTrack"), type: "annotation", features: files.genes.features, searchable: false });
            if (!loaded) throw new Error("Gene spans could not be loaded.");
            disablePopupLookups(loaded);
        }
        for (const track of files.tracks) {
            const loaded = await browser.loadTrack(validatedBedGraphTrack(track, (error) => {
                trackProblem = error;
                status(error.code, true, error.fileName);
            }));
            if (!loaded) {
                throw new Error("Track could not be loaded.");
            }
            disablePopupLookups(loaded);
        }
        required<HTMLInputElement>('[data-locus] [name="locus"]').value = locus;
        navigation.hidden = false;
        readyStatus();
        if (typeof files.reference.fastaURL === "string") {
            const info = document.createElement("span");
            info.textContent = ` ${text("automaticReference").replace("%{file}", files.reference.name)}`;
            required<HTMLElement>("[data-viewer-status]").append(info);
        }
    } catch (error) {
        status(error instanceof ViewerFileError ? error.code : "loadFailed", true, error instanceof ViewerFileError ? error.fileName : "");
        if (!(error instanceof ViewerFileError)) {
            dispose();
            console.error("Genome viewer:", error);
        }
    } finally {
        setBusy(false);
        host.setAttribute("aria-busy", "false");
        host.hidden = !browser;
    }
}

async function goToLocus(form: HTMLFormElement) {
    try {
        const locus = viewerNavigationLocus(form.querySelector<HTMLInputElement>("input")?.value ?? "", geneLookup);
        if (!await browser?.search(locus)) {
            throw new ViewerFileError("locusNotFound");
        }
        required<HTMLInputElement>('[data-locus] [name="locus"]').value = locus;
        readyStatus();
    } catch (error) {
        status(error instanceof ViewerFileError ? error.code : "loadFailed", true);
    }
}

function dispose() {
    geneLookup.clear();
    const geneStatus = root?.querySelector<HTMLElement>("[data-gene-status]");
    if (geneStatus) geneStatus.hidden = true;
    const cytobandStatus = root?.querySelector<HTMLElement>("[data-cytoband-status]");
    if (cytobandStatus) cytobandStatus.hidden = true;
    if (browser) {
        window.igv.removeBrowser(browser);
        browser = undefined;
    }
}
