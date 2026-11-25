import { escapeHTML } from "../dom.js";
import { createModalFocusBinder } from "../modal-focus.js";
import { formatLabel } from "../model.js";
import { state } from "../state.js";

const maxListedOutputs = 6;

export const bindAlreadyRunFocus = createModalFocusBinder(
    "[data-already-run-dialog]",
    "[data-already-run-force], [data-already-run-cancel]",
);

/** Pop-up shown when an output-only command already has outputs newer than its inputs. */
export function renderAlreadyRunDialog(): string {
    const pending = state.pendingAlreadyRun;
    if (!pending) {
        return "";
    }
    const labels = state.labels;
    const shown = pending.outputs.slice(0, maxListedOutputs);
    const extra = pending.outputs.length - shown.length;
    const items = [
        ...shown.map((name) => `<li>${escapeHTML(name)}</li>`),
        ...(extra > 0 ? [`<li>${escapeHTML(formatLabel(labels.alreadyRunMoreFilesFormat ?? "and %{count} more", { count: extra }))}</li>`] : []),
    ].join("");
    return `
    <div class="modal-backdrop" role="presentation">
      <section class="confirmation-modal prerequisites-modal" role="alertdialog" aria-modal="true" aria-labelledby="already-run-title" tabindex="-1" data-already-run-dialog>
        <h2 id="already-run-title">${escapeHTML(labels.alreadyRunTitle ?? "Command already run")}</h2>
        <p>${escapeHTML(labels.alreadyRunMessage ?? "This command has already been run and its output files are newer than the input:")}</p>
        ${items ? `<ul class="prerequisites-list">${items}</ul>` : ""}
        <p>${escapeHTML(labels.alreadyRunQuestion ?? "Would you like to force run it again?")}</p>
        <div class="modal-actions">
          <button type="button" class="secondary-button" data-already-run-cancel>${escapeHTML(labels.alreadyRunCancelButtonTitle ?? "Cancel")}</button>
          <button type="button" class="action-button primary" data-already-run-force autofocus>${escapeHTML(labels.alreadyRunForceButtonTitle ?? "Force Run Again")}</button>
        </div>
      </section>
    </div>
  `;
}
