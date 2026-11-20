import { localViewerLocus, ViewerFileError, type LibraryViewerSession } from "../../../shared/genome-viewer.js";
import { escapeHTML } from "./dom.js";
import { releaseLibraryViewer } from "./genome-viewer-library.js";
import { requestRemoteViewer, type RemoteViewerStatus } from "./genome-viewer-remote.js";

type Text = (key: string) => string;

export function remoteViewerForm(text: Text) {
    return `<details data-remote class="viewer-remote">
      <summary>${escapeHTML(text("remoteTitle"))}</summary>
      <p>${escapeHTML(text("remoteHelp"))}</p>
      <div class="input-button-row">
        <button type="button" data-remote-import>${escapeHTML(text("remoteImport"))}</button>
        <button type="button" data-remote-forget hidden>${escapeHTML(text("remoteForget"))}</button>
      </div>
      <p data-remote-destination role="status" dir="auto">${escapeHTML(text("remoteNotConfigured"))}</p>
      <p data-remote-plaintext hidden>${escapeHTML(text("remotePlaintext"))}</p>
      <form data-remote-form class="viewer-remote-fields" hidden>
        <label>${escapeHTML(text("remotePath"))}<input name="remote-path" dir="ltr" autocomplete="off" spellcheck="false" maxlength="4096" required></label>
        <label>${escapeHTML(text("remoteIndex"))}<input name="remote-index" dir="ltr" autocomplete="off" spellcheck="false" maxlength="4096" required></label>
        <label>${escapeHTML(text("remoteReference"))}<input name="remote-reference" dir="ltr" autocomplete="off" spellcheck="false" maxlength="4096" required aria-describedby="remote-reference-help"></label>
        <p id="remote-reference-help">${escapeHTML(text("remoteReferenceHelp"))}</p>
        <label>${escapeHTML(text("locus"))}<input name="remote-locus" dir="ltr" autocomplete="off" spellcheck="false" placeholder="chr1:1000-2000" required maxlength="128"></label>
        <label class="viewer-remote-consent"><input type="checkbox" name="remote-consent" required> ${escapeHTML(text("remoteConsent"))}</label>
        <div><button type="submit" class="action-button primary">${escapeHTML(text("remoteOpen"))}</button></div>
      </form>
    </details>`;
}

export function bindRemoteViewer(root: HTMLElement, text: Text, showStatus: (key: string, isError?: boolean) => void,
    open: (session: LibraryViewerSession) => Promise<void>, clear: () => void, setBusy: (busy: boolean) => void) {
    const element = root.querySelector<HTMLElement>("[data-remote]");
    if (!element) return;
    const find = <T extends Element>(selector: string): T => {
        const value = element.querySelector<T>(selector);
        if (!value) throw new Error("Missing remote viewer control.");
        return value;
    };
    const form = find<HTMLFormElement>("[data-remote-form]");
    let connection: RemoteViewerStatus = {};
    let busy = false;
    const setConnection = (status: RemoteViewerStatus) => {
        if (connection.id && status.id !== connection.id) clear();
        connection = status;
        form.hidden = !status.id || !status.destination;
        find<HTMLInputElement>('[name="remote-consent"]').checked = false;
        find<HTMLElement>("[data-remote-destination]").textContent = status.destination
            ? text("remoteDestination").replace("%{destination}", status.destination) : text("remoteNotConfigured");
        find<HTMLElement>("[data-remote-forget]").hidden = !status.id;
        find<HTMLElement>("[data-remote-plaintext]").hidden = !status.destination?.startsWith("http:");
    };
    const run = async (action: () => Promise<void>) => {
        if (busy) return;
        busy = true;
        element.querySelectorAll<HTMLInputElement | HTMLButtonElement>("input, button").forEach((control) => { control.disabled = true; });
        try { await action(); }
        catch (error) { showStatus(error instanceof ViewerFileError ? error.code : "remoteUnavailable", true); }
        finally {
            busy = false;
            element.querySelectorAll<HTMLInputElement | HTMLButtonElement>("input, button").forEach((control) => { control.disabled = false; });
        }
    };
    element.addEventListener("toggle", () => {
        if (element instanceof HTMLDetailsElement && element.open) void run(async () => {
            const result = await requestRemoteViewer("status");
            if ("problem" in result && result.problem) showStatus(result.problem, true);
            else setConnection(result);
        });
    });
    find<HTMLButtonElement>("[data-remote-import]").addEventListener("click", () => {
        void run(async () => {
            const result = await requestRemoteViewer("import", { title: text("remoteImport") });
            if ("problem" in result && result.problem) { showStatus("remoteConfigurationInvalid", true); return; }
            setConnection(result);
        });
    });
    find<HTMLButtonElement>("[data-remote-forget]").addEventListener("click", () => {
        void run(async () => {
            const result = await requestRemoteViewer("forget");
            if ("problem" in result && result.problem) { showStatus(result.problem, true); return; }
            clear();
            setConnection({});
            showStatus("remoteNotConfigured");
        });
    });
    form.addEventListener("input", (event) => {
        const consent = find<HTMLInputElement>('[name="remote-consent"]');
        if (event.target !== consent) consent.checked = false;
    });
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!form.reportValidity() || !connection.id) return;
        void run(async () => {
            setBusy(true);
            try {
            showStatus("loading");
            const locus = localViewerLocus(find<HTMLInputElement>('[name="remote-locus"]').value);
            await releaseLibraryViewer();
            const result = await requestRemoteViewer("open", {
                id: connection.id, path: find<HTMLInputElement>('[name="remote-path"]').value,
                index: find<HTMLInputElement>('[name="remote-index"]').value,
                reference: find<HTMLInputElement>('[name="remote-reference"]').value,
                locus,
                confirmed: find<HTMLInputElement>('[name="remote-consent"]').checked,
            });
            if (!("files" in result)) {
                if (!result.cancelled) showStatus(result.problem ?? "remoteUnavailable", true);
                return;
            }
            await open(result);
            } finally { setBusy(false); }
        });
    });
}
