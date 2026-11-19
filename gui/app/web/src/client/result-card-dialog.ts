import type { Labels } from "../../../shared/types.js";
import { api } from "./api.js";
import { formatLabel } from "./model.js";

interface ResultCard {
    path: string;
    name: string;
    html: string;
}

let open: { close: () => void } | undefined;
let request = 0;

/**
 * Shows a run's result card (`<name>.card.html`, written by `wgsextract --card`) over the app.
 * Like the genome viewer it is a sibling of #app, so the app's renders don't reload it. The card
 * is static HTML: its frame is sandboxed without scripts, forms, popups or navigation.
 */
export async function openResultCard(filePath: string, labels: Labels) {
    const ticket = ++request;
    let card: ResultCard;
    try {
        card = await api<ResultCard>("/api/result/card", { body: { path: filePath } });
    }
    catch (error) {
        console.error("Could not load the result card:", error);
        return;
    }
    // A later request (a newer run's card) wins over a slower earlier one.
    if (ticket !== request) {
        return;
    }
    closeResultCard();
    showCard(card, labels);
}

export function closeResultCard() {
    open?.close();
}

function showCard(card: ResultCard, labels: Labels) {
    const app = document.querySelector<HTMLElement>("#app");
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    const backdrop = element("div", "modal-backdrop result-card-backdrop");
    backdrop.setAttribute("role", "presentation");
    const dialog = element("section", "confirmation-modal result-card-modal");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "result-card-title");

    const header = element("header", "result-card-header");
    const heading = element("div", "result-card-heading");
    const title = element("h2");
    title.id = "result-card-title";
    title.textContent = labels.resultCardTitle ?? "Result card";
    const source = element("p", "result-card-source");
    source.textContent = formatLabel(labels.resultCardSourceFormat ?? "Shown from %{file}", { file: card.name });
    source.title = card.path;
    heading.append(title, source);
    const close = element("button", "action-button primary compact");
    close.setAttribute("type", "button");
    close.textContent = labels.resultCardCloseTitle ?? "Close";
    header.append(heading, close);

    const frame = element("iframe", "result-card-frame");
    frame.title = card.name;
    // Without allow-scripts nothing in the card can run; allow-same-origin only keeps the
    // frame in the page's renderer: an opaque-origin card frame showed blank in testing.
    frame.setAttribute("sandbox", "allow-same-origin");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.srcdoc = card.html;

    dialog.append(header, frame);
    backdrop.append(dialog);

    const onKey = (event: KeyboardEvent) => {
        if (event.key === "Escape") {
            event.preventDefault();
            finish();
        }
        // Only the Close button takes focus here; the frame is not focusable.
        if (event.key === "Tab") {
            event.preventDefault();
            close.focus();
        }
    };
    const finish = () => {
        backdrop.remove();
        document.removeEventListener("keydown", onKey, true);
        open = undefined;
        if (app) {
            app.inert = false;
            app.removeAttribute("aria-hidden");
        }
        if (opener?.isConnected) {
            opener.focus({ preventScroll: true });
        }
    };
    close.addEventListener("click", finish);
    backdrop.addEventListener("click", (event) => {
        if (event.target === backdrop) {
            finish();
        }
    });
    document.addEventListener("keydown", onKey, true);

    document.body.append(backdrop);
    if (app) {
        app.inert = true;
        app.setAttribute("aria-hidden", "true");
    }
    open = { close: finish };
    close.focus({ preventScroll: true });
}

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = ""): HTMLElementTagNameMap[K] {
    const node = document.createElement(tag);
    if (className) {
        node.className = className;
    }
    return node;
}
