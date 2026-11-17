import { escapeAttribute, escapeHTML } from "../dom.js";
import { formatLabel, renderIconTitle } from "../model.js";
import { state } from "../state.js";
import { renderCollapsibleCard } from "./collapsible.js";

/** The section ID the users workspace card opens and closes under. */
export const workspaceLocationSectionID = "users-workspace-location";

export function renderWorkspaceLocationSection() {
    const location = state.workspaceLocation;
    if (!location) {
        return "";
    }
    const busy = state.setupRun?.status === "running" || state.workspaceMove != null;
    const locked = busy ? "disabled" : "";
    const label = state.labels.workspaceLocationFolderLabel;
    const draft = state.workspaceLocationDraft ?? location.workspaceRoot;
    const changed = draft.trim() !== "" && draft.trim() !== location.workspaceRoot;
    return renderCollapsibleCard(workspaceLocationSectionID, "workspace-location-card",
        renderIconTitle(state.labels.workspaceLocationTitle, "folder", undefined, "📁"), `
      <div class="controls">
        <div class="form-row">
          <span class="row-label">${escapeHTML(state.labels.workspaceLocationCurrentLabel)}</span>
          <code data-workspace-location-current>${escapeHTML(location.workspaceRoot)}</code>
        </div>
        <label class="form-row">
          <span class="row-label">${escapeHTML(label)}</span>
          <span>
            <span class="input-button-row workspace-location-row">
              <input type="text" data-workspace-location-input aria-label="${escapeAttribute(label)}" placeholder="${escapeAttribute(location.defaultRoot)}" value="${escapeAttribute(draft)}" ${locked}>
              <button type="button" class="secondary-button" data-workspace-location-choose ${locked}>${escapeHTML(state.labels.chooseButtonTitle)}</button>
              <button type="button" class="secondary-button" data-workspace-location-move ${locked || !changed ? "disabled" : ""}>${escapeHTML(state.labels.workspaceLocationMoveTitle)}</button>
              <button type="button" class="secondary-button" data-workspace-location-reset ${locked || location.usingDefault ? "disabled" : ""}>${escapeHTML(state.labels.workspaceLocationUseDefaultTitle)}</button>
            </span>
            <span class="field-note">${escapeHTML(state.labels.workspaceLocationNote)}</span>
          </span>
        </label>
        ${state.workspaceMove && !state.workspaceMove.setting ? renderWorkspaceMoveStatus() : ""}
      </div>`);
}

/** Bound folder controls can live on any page, including in Basic mode. */
export function renderSettingFolderMoveDialog() {
    if (!state.workspaceMove?.setting) {
        return "";
    }
    return `<div class="modal-backdrop" role="presentation">
      <section class="confirmation-modal" role="dialog" aria-modal="true" aria-labelledby="setting-move-title">
        <h3 id="setting-move-title">${escapeHTML(state.labels.workspaceLocationMoveTitle)}</h3>
        ${renderWorkspaceMoveStatus()}
      </section>
    </div>`;
}

function renderWorkspaceMoveStatus() {
    const move = state.workspaceMove;
    if (!move) {
        return "";
    }
    if (move.running) {
        return `<p class="field-note" data-workspace-location-moving>${escapeHTML(formatLabel(state.labels.workspaceLocationMovingFormat, { percent: move.percent }))}</p>`;
    }
    const destination = move.folder ?? state.workspaceLocation?.defaultRoot ?? "";
    const question = move.setting
        ? formatLabel(state.labels.workspaceLocationFolderMoveConfirm, { from: move.setting.from, path: destination })
        : formatLabel(state.labels.workspaceLocationMoveConfirm, { path: destination });
    return `
        <div class="workspace-location-confirm">
          <p class="field-note">${escapeHTML(question)}</p>
          <span class="input-button-row">
            <button type="button" class="action-button primary" data-workspace-location-confirm>${escapeHTML(state.labels.workspaceLocationMoveTitle)}</button>
            ${move.setting ? `<button type="button" class="secondary-button" data-workspace-location-setting-only>${escapeHTML(state.labels.workspaceLocationSettingOnlyTitle)}</button>` : ""}
            <button type="button" class="secondary-button" data-workspace-location-cancel>${escapeHTML(state.labels.workspaceLocationCancelTitle)}</button>
          </span>
        </div>
  `;
}
