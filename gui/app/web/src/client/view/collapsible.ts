import { escapeAttribute } from "../dom.js";
import { state } from "../state.js";

/**
 * A card that starts closed, showing only its title, until the user opens it. It stays as the user left it
 * while the page re-renders.
 */
export function renderCollapsibleCard(id: string, extraClass: string, titleHTML: string, bodyHTML: string, subtitleHTML = "") {
    return `
    <details class="card collapsible-card${extraClass ? ` ${extraClass}` : ""}" data-collapsible-section="${escapeAttribute(id)}"${state.openSections.has(id) ? " open" : ""}>
      <summary class="section-header">
        <h3>${titleHTML}</h3>
        ${subtitleHTML}
      </summary>
      ${bodyHTML}
    </details>
  `;
}

/** Records which collapsible cards are open, so a re-render keeps them so. */
export function bindCollapsibleSections(app: HTMLElement) {
    app.querySelectorAll<HTMLDetailsElement>("[data-collapsible-section]").forEach((details) => {
        details.addEventListener("toggle", () => {
            const id = details.dataset.collapsibleSection ?? "";
            if (details.open) {
                state.openSections.add(id);
            }
            else {
                state.openSections.delete(id);
            }
        });
    });
}
