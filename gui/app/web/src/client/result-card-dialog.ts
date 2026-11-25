import type { Labels } from "../../../shared/types.js";
import { api } from "./api.js";
import { captureFocus, restoreFocus } from "./focus-state.js";
import { errorMessage, formatLabel } from "./model.js";
import { scheduleRender } from "./rerender.js";
import { state } from "./state.js";
import { appendTerminal } from "./terminal.js";

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
export async function openResultCard(filePath: string, labels: Labels, options: { onRerun?: () => void } = {}) {
    const ticket = ++request;
    let card: ResultCard;
    try {
        card = await api<ResultCard>("/api/result/card", { body: { path: filePath } });
    }
    catch (error) {
        // A deleted card or a refused path shows in the terminal, as Open and Show in Folder do.
        appendTerminal("error", state.labels.terminalProcessErrorTitle, errorMessage(error));
        scheduleRender();
        return;
    }
    // A later request (a newer run's card) wins over a slower earlier one.
    if (ticket !== request) {
        return;
    }
    closeResultCard();
    showCard(card, labels, options.onRerun);
}

export function closeResultCard() {
    open?.close();
}

function showCard(card: ResultCard, labels: Labels, onRerun?: () => void) {
    const app = document.querySelector<HTMLElement>("#app");
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    // A render while the card is open replaces the opener; this finds its replacement.
    const openerSnapshot = app ? captureFocus(app) : null;

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
    const actions = element("div", "result-card-actions");
    let rerun: HTMLElement | undefined;
    if (onRerun) {
        rerun = element("button", "secondary-button compact");
        rerun.setAttribute("type", "button");
        rerun.dataset.resultCardRerun = "";
        rerun.textContent = labels.resultCardRerunTitle ?? "Force Run Again";
        actions.append(rerun);
    }
    const close = element("button", "action-button primary compact");
    close.setAttribute("type", "button");
    close.textContent = labels.resultCardCloseTitle ?? "Close";
    actions.append(close);
    header.append(heading, actions);

    const frame = element("iframe", "result-card-frame");
    frame.title = card.name;
    // Without allow-scripts nothing in the card can run; allow-same-origin only keeps the
    // frame in the page's renderer: an opaque-origin card frame showed blank in testing.
    frame.setAttribute("sandbox", "allow-same-origin");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.srcdoc = card.html;

    dialog.append(header, frame);
    backdrop.append(dialog);

    const headerButtons = rerun ? [rerun, close] : [close];
    // Focus moves between header buttons and the card's own controls (its Metrics summary), and wraps at
    // either end; with the card focused, the arrow keys scroll it. Escape closes from both.
    const onKey = (event: KeyboardEvent) => {
        if (event.key === "Escape") {
            event.preventDefault();
            finish();
        }
        else if (event.key === "Tab") {
            const index = headerButtons.indexOf(document.activeElement as HTMLElement);
            if (event.shiftKey) {
                if (index > 0) {
                    event.preventDefault();
                    headerButtons[index - 1]?.focus();
                }
                else {
                    event.preventDefault();
                    (lastCardStop() ?? frame).focus();
                }
            }
            else if (index >= 0 && index < headerButtons.length - 1) {
                event.preventDefault();
                headerButtons[index + 1]?.focus();
            }
            else if (index < 0) {
                event.preventDefault();
                headerButtons[0]?.focus();
            }
        }
    };
    const frameDocument = () => frame.contentDocument;
    const cardStops = () => [...(frameDocument()?.querySelectorAll<HTMLElement>("summary, a[href], button, [tabindex]:not([tabindex='-1'])") ?? [])];
    const lastCardStop = () => cardStops().at(-1);
    const onCardKey = (event: KeyboardEvent) => {
        if (event.key === "Escape") {
            event.preventDefault();
            finish();
            return;
        }
        if (event.key !== "Tab") {
            return;
        }
        const stops = cardStops();
        const active = frameDocument()?.activeElement;
        const atEdge = event.shiftKey ? !active || active === stops[0] || !stops.includes(active as HTMLElement) : active === stops.at(-1) || !stops.includes(active as HTMLElement);
        if (atEdge) {
            event.preventDefault();
            (event.shiftKey ? headerButtons.at(-1) : headerButtons[0])?.focus();
        }
    };
    frame.addEventListener("load", () => frameDocument()?.addEventListener("keydown", onCardKey));
    // Everything behind the card, the app and an open genome viewer alike, is inert until it closes.
    const behind = [...document.body.children].filter((node): node is HTMLElement => node instanceof HTMLElement && !node.inert);
    const finish = () => {
        backdrop.remove();
        document.removeEventListener("keydown", onKey, true);
        open = undefined;
        for (const node of behind) {
            node.inert = false;
            node.removeAttribute("aria-hidden");
        }
        if (opener?.isConnected) {
            opener.focus({ preventScroll: true });
        }
        else if (app) {
            restoreFocus(app, openerSnapshot);
        }
    };
    close.addEventListener("click", finish);
    rerun?.addEventListener("click", () => {
        finish();
        onRerun?.();
    });
    backdrop.addEventListener("click", (event) => {
        if (event.target === backdrop) {
            finish();
        }
    });
    document.addEventListener("keydown", onKey, true);

    for (const node of behind) {
        node.inert = true;
        node.setAttribute("aria-hidden", "true");
    }
    document.body.append(backdrop);
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
