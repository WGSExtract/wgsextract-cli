import type { MissingPrerequisite } from "../../../../shared/types.js";
import { escapeHTML } from "../dom.js";
import { formatLabel, renderLoadingInline } from "../model.js";
import { state } from "../state.js";

/** What the dialog lists for a missing item, e.g. "hs37d5 reference genome", from the bundle's label for its kind. */
export function prerequisiteDescription(item: MissingPrerequisite): string {
    const label = state.manifest?.prerequisites?.kindLabels?.[item.kind];
    return label ? formatLabel(label, { name: item.name }) : item.name;
}

/** Asks what to do about data an action needs that isn't installed: install it, install it yourself, or cancel. */
export function renderPrerequisitesDialog(): string {
    const pending = state.pendingPrerequisites;
    if (!pending) {
        return state.checkingPrerequisites ? renderCheckingNotice() : "";
    }
    const labels = state.labels;
    const installable = pending.missing.every((item) => item.installArguments);
    const items = pending.missing.map((item) => `<li>${escapeHTML(prerequisiteDescription(item))}</li>`).join("");
    return `
    <div class="modal-backdrop" role="presentation">
      <section class="confirmation-modal prerequisites-modal" role="alertdialog" aria-modal="true" aria-labelledby="prerequisites-title" tabindex="-1" data-prerequisites-dialog>
        <h2 id="prerequisites-title">${escapeHTML(labels.prerequisitesTitle)}</h2>
        <p>${escapeHTML(labels.prerequisitesMessage)}</p>
        <ul class="prerequisites-list">${items}</ul>
        ${pending.stillMissing ? `<p class="prerequisites-still-missing" role="alert">${escapeHTML(labels.prerequisitesStillMissingMessage)}</p>` : ""}
        <p class="prerequisites-note">${escapeHTML(labels.prerequisitesManualNote)}</p>
        <div class="modal-actions">
          <button type="button" class="secondary-button" data-prerequisites-cancel>${escapeHTML(labels.prerequisitesCancelButtonTitle)}</button>
          <button type="button" class="secondary-button" data-prerequisites-continue>${escapeHTML(labels.prerequisitesContinueButtonTitle)}</button>
          ${installable ? `<button type="button" class="action-button primary" data-prerequisites-install autofocus>${escapeHTML(labels.prerequisitesInstallButtonTitle)}</button>` : ""}
        </div>
      </section>
    </div>
  `;
}

function renderCheckingNotice(): string {
    return `<div class="prerequisites-checking" role="status">${renderLoadingInline(state.labels.prerequisitesCheckingTitle)}</div>`;
}
