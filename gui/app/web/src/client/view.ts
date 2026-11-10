import { displayWorkspacePath } from "../../../shared/workspace-paths.js";
import { applyDataSourcePayload, configValueKey, hydrateRows, isSectionVisible, rowContext, } from "../../../shared/rendering.js";
import { escapeAttribute, escapeHTML } from "./dom.js";
import { buildTagStyle, commandContext, configDataSourceContext, displayOption, localizedStatus, localizedTag, renderIcon, renderIconTitle, renderInlineError, renderLoadingBox, renderLoadingInline, renderTooltip, resolveText, tagStyle, } from "./model.js";
import { ensureDataSource } from "./operations.js";
import { state } from "./state.js";
export { renderActions, renderPrecheckBanner } from "./view/actions.js";
import { renderActions } from "./view/actions.js";
export { renderSetupStatusSection, renderStandardOptionsAccessory } from "./view/settings.js";
import { renderSetupStatusSection, renderStandardOptionsAccessory } from "./view/settings.js";
import { renderWorkspaceLocationSection } from "./view/workspace-location.js";
export { renderSetupGlobalStatusBar, renderSetupPromptDialog, setupHasNeverRun, setupNeedsAttention, setupPageID, setupPromptMessage, } from "./view/setup.js";
import { renderUpdateNavigationItem } from "./view/update.js";
import { isShownInUIMode, visibleControls, visibleOptions, visiblePages, visibleRows, visibleSections, type UIMode } from "../../../shared/ui-mode.js";
import type { BundleManifest, BundlePage, CommandContext, LocalizedOption, ControlOption, ControlSpec, ManifestLocation, PageSection, PendingConfirmation, RowColumnSpec, RowSpec, ConfigSetting } from "../../../shared/types.js";
function requiredManifest(): BundleManifest {
    if (!state.manifest) {
        throw new Error("Bundle manifest is not loaded.");
    }
    return state.manifest;
}
export function renderBundleHeader() {
    const manifest = requiredManifest();
    const iconPath = manifest.iconPath ? `/bundle-file?path=${encodeURIComponent(manifest.iconPath)}` : "";
    const icon = iconPath
        ? `<img class="bundle-icon" src="${iconPath}" alt="">`
        : `<div class="bundle-emoji" aria-hidden="true">${renderIcon(manifest.iconName, manifest.textIcon, "🧰")}</div>`;
    const appVersion = String(state.appVersion ?? "").trim();
    const appVersionLabel = state.labels.setupVersionLabel ?? "Version";
    return `
    <header class="bundle-header">
      ${icon}
      <h1>${escapeHTML(manifest.displayName)}${renderTooltip(manifest.summary)}</h1>
      ${appVersion ? `<p class="app-version" data-app-version>${escapeHTML(appVersionLabel)} ${escapeHTML(appVersion)}</p>` : ""}
    </header>
  `;
}
/** The Basic / Advanced switch at the top of the sidebar: a radio group, so exactly one mode is on. */
export function renderUIModeSwitch() {
    const modes: { mode: UIMode; label: string; tooltip: string }[] = [
        { mode: "basic", label: state.labels.uiModeBasicLabel ?? "Basic", tooltip: state.labels.uiModeBasicTooltip ?? "" },
        { mode: "advanced", label: state.labels.uiModeAdvancedLabel ?? "Advanced", tooltip: state.labels.uiModeAdvancedTooltip ?? "" },
    ];
    return `
    <div class="ui-mode-switch" role="radiogroup" aria-label="${escapeAttribute(state.labels.uiModeLabel ?? "Mode")}">
      ${modes
        .map(({ mode, label, tooltip }) => {
        const active = mode === state.uiMode;
        return `<button type="button" role="radio" class="ui-mode-option ${active ? "active" : ""}" aria-checked="${active}" tabindex="${active ? 0 : -1}"
          data-ui-mode="${mode}" title="${escapeAttribute(tooltip)}">${escapeHTML(label)}</button>`;
    })
        .join("")}
    </div>
  `;
}
export function renderLanguagePicker() {
    return `
    <label class="language-picker">
      <span>${escapeHTML(state.labels.languagePickerLabel)}</span>
      <select data-locale-picker>
        <option value="__system__" ${state.usingSystemDefaultLocale ? "selected" : ""}>${escapeHTML(systemLanguageOptionLabel())}</option>
        ${state.localizationOptions
        .map((option) => `<option value="${escapeAttribute(option.code)}" ${!state.usingSystemDefaultLocale && option.code === state.localizationCode ? "selected" : ""}>${escapeHTML(languageOptionLabel(option))}</option>`)
        .join("")}
      </select>
    </label>
  `;
}
function systemLanguageOptionLabel() {
    const currentOption = state.localizationOptions.find((option) => option.code === state.localizationCode);
    const currentName = currentOption ? languageOptionLabel(currentOption) : state.localizationCode;
    return currentName
        ? `${state.labels.languageSystemDefaultLabel ?? "Use system default"} — ${currentName}`
        : state.labels.languageSystemDefaultLabel ?? "Use system default";
}
function languageOptionLabel(option: LocalizedOption) {
    return option.isAITranslated
        ? `${option.displayName} - ${state.labels.languageAITranslatedLabel ?? "AI translated"}`
        : option.displayName;
}
export function renderNavigation() {
    const manifest = requiredManifest();
    const pages = visiblePages(manifest, state.uiMode);
    const primaryPages = pages.filter((page) => page.sidebarPlacement !== "bottom");
    const bottomPages = pages.filter((page) => page.sidebarPlacement === "bottom");
    const updateItem = renderUpdateNavigationItem();
    return `
    <div class="nav-primary">${renderNavigationGroups(primaryPages)}</div>
    ${bottomPages.length || updateItem ? `<div class="nav-bottom">${updateItem}${renderNavigationGroups(bottomPages, false)}</div>` : ""}
  `;
}
type NavigationGroup = { name: string; pages: BundlePage[] };
export function renderNavigationGroups(pages: BundlePage[], showGroupTitles = true) {
    const groups: NavigationGroup[] = [];
    for (const page of pages) {
        const groupName = String(page.sidebarGroup ?? "");
        const group = groups.find((candidate) => candidate.name === groupName);
        if (group) {
            group.pages.push(page);
        }
        else {
            groups.push({ name: groupName, pages: [page] });
        }
    }
    return groups
        .map((group) => `
      ${showGroupTitles && group.name ? `<h2>${escapeHTML(group.name)}</h2>` : ""}
      ${group.pages
        .map((page) => `
          <button class="nav-item ${page.id === state.activePageID ? "active" : ""}" data-page-id="${escapeAttribute(page.id)}">
            <span class="nav-icon" aria-hidden="true">${renderIcon(page.iconName, page.textIcon, "◦")}</span>
            <span>${escapeHTML(page.title)}</span>
          </button>`)
        .join("")}`)
        .join("");
}
export function renderPage(page: BundlePage) {
    return `
    <article>
      <header class="page-header">
        <h2>${renderIconTitle(page.title, page.iconName, page.textIcon, "📄")}</h2>
        <p>${escapeHTML(page.summary)}</p>
      </header>
      ${page.id === "settings" ? `${renderSetupStatusSection()}${state.uiMode === "advanced" ? renderWorkspaceLocationSection() : ""}${renderStandardOptionsAccessory()}` : ""}
      <div class="sections">
        ${visibleSections(page, state.uiMode).map((section) => renderSection(section, page)).join("")}
      </div>
    </article>
  `;
}
export function renderSection(section: PageSection, page: BundlePage) {
    const key = `section:${section.id}`;
    const location: ManifestLocation = { pageID: page.id, sectionID: String(section.id ?? "") };
    if (section.dataSource) {
        ensureDataSource(key, location, commandContext());
    }
    const sectionValues = state.dataSourcePayloads.get(key)?.values ?? {};
    const context = commandContext({}, sectionValues);
    // Checked after the section's own data source starts, so visibleWhen can read the values it returns.
    if (!isSectionVisible(section, context)) {
        return "";
    }
    const sectionActions = (section.actions ?? []).filter((action) => isShownInUIMode(action, state.uiMode));
    return `
    <section class="card${section.subsection === true ? " subsection" : ""}" aria-labelledby="section-${escapeAttribute(section.id)}">
      <header class="section-header">
        ${section.title
        ? `<h3 id="section-${escapeAttribute(section.id)}">${renderIconTitle(section.title, section.iconName, section.textIcon, "▦")}</h3>`
        : ""}
        ${section.subtitle ? `<p>${escapeHTML(section.subtitle)}</p>` : ""}
      </header>
      <div class="controls">
        ${visibleControls(section, state.uiMode).map((control) => renderControl(control, context, location)).join("")}
      </div>
      ${state.loadingDataSources.has(key) ? renderLoadingBox(state.labels.loadingTitle) : ""}
      ${state.dataSourceErrors.has(key)
        ? renderInlineError(state.dataSourceErrors.get(key), `<button type="button" data-retry-source="${escapeAttribute(key)}">${escapeHTML(state.labels.retryButtonTitle)}</button>`)
        : ""}
      ${sectionActions.length ? `<div class="action-row">${renderActions(sectionActions, context, location)}</div>` : ""}
    </section>
  `;
}
export function renderControl(control: ControlSpec, sectionContext: CommandContext, sectionLocation: ManifestLocation) {
    const key = `control:${control.id}`;
    const location: ManifestLocation = { ...sectionLocation, controlID: control.id };
    let renderedControl = control;
    if (control.dataSource) {
        ensureDataSource(key, location, commandContext());
        const payload = state.dataSourcePayloads.get(key);
        if (payload) {
            renderedControl = applyDataSourcePayload(control, payload);
        }
    }
    const error = state.dataSourceErrors.get(key);
    const loading = state.loadingDataSources.has(key);
    let body = "";
    switch (renderedControl.kind) {
        case "text":
        case "path":
            body = renderTextControl(renderedControl);
            break;
        case "dropdown":
            body = renderDropdownControl(renderedControl);
            break;
        case "toggle":
            body = renderToggleControl(renderedControl);
            break;
        case "checkboxGroup":
            body = renderCheckboxGroup(renderedControl);
            break;
        case "infoGrid":
            body = renderInfoGrid(renderedControl);
            break;
        case "libraryList":
            body = renderLibraryList(renderedControl, sectionContext, location);
            break;
        case "configEditor":
            body = renderConfigEditor(renderedControl, location);
            break;
        default:
            body = renderInlineError(`Unsupported control kind: ${renderedControl.kind}`);
    }
    return `
    <div class="control">
      ${body}
      ${loading && renderedControl.kind !== "libraryList" ? renderLoadingInline(state.labels.loadingTitle) : ""}
      ${error
        ? renderInlineError(error, `<button type="button" data-retry-source="${escapeAttribute(key)}">${escapeHTML(state.labels.retryButtonTitle)}</button>`)
        : ""}
    </div>
  `;
}
export function renderTextControl(control: ControlSpec) {
    const inputID = `control-${control.id}`;
    const pathAttributes = control.kind === "path" ? ` class="path-input" dir="ltr"` : "";
    const rawValue = state.fieldValues[control.id] ?? control.value ?? "";
    const input = `<input id="${escapeAttribute(inputID)}" type="text"${pathAttributes} value="${escapeAttribute(control.kind === "path" ? displayWorkspacePath(String(rawValue), state.bundleRootPath) : rawValue)}"
        placeholder="${escapeAttribute(control.placeholder ?? "")}" data-field-id="${escapeAttribute(control.id)}">`;
    return `
    <div class="form-row">
      <label class="row-label" for="${escapeAttribute(inputID)}">${escapeHTML(control.label)}${renderTooltip(control.tooltip)}</label>
      ${control.kind === "path"
        ? `<span class="input-button-row">${input}<button type="button" class="secondary-button" data-path-prompt="${escapeAttribute(control.id)}">${escapeHTML(state.labels.chooseButtonTitle)}</button></span>`
        : input}
    </div>
  `;
}
export function renderDropdownControl(control: ControlSpec) {
    const value = state.fieldValues[control.id] ?? control.value ?? control.options?.find((option) => option.selected)?.id ?? "";
    const options = visibleOptions(control.options, state.uiMode);
    // A list with nothing in it (no reference genome installed yet) says what to do instead of showing a blank menu.
    const emptyText = options.length === 0 && typeof control.emptyText === "string" ? control.emptyText : "";
    return `
    <label class="form-row">
      <span class="row-label">${escapeHTML(control.label)}${renderTooltip(control.tooltip)}</span>
      <select data-field-id="${escapeAttribute(control.id)}">
        ${emptyText
        ? `<option value="" selected disabled>${escapeHTML(emptyText)}</option>`
        : options
            .map((option) => `<option value="${escapeAttribute(option.id)}" ${option.id === String(value) ? "selected" : ""}>${escapeHTML(displayOption(option))}</option>`)
            .join("")}
      </select>
    </label>
  `;
}
export function renderToggleControl(control: ControlSpec) {
    const checked = (state.fieldValues[control.id] ?? control.value ?? "") === "true";
    return `
    <label class="toggle-row">
      <span class="row-label">${escapeHTML(control.label)}${renderTooltip(control.tooltip)}</span>
      <input type="checkbox" ${checked ? "checked" : ""} data-field-id="${escapeAttribute(control.id)}" data-toggle>
    </label>
  `;
}
export function renderCheckboxGroup(control: ControlSpec) {
    const selected = state.checkedOptions[control.id] ?? new Set((control.options ?? []).filter((option) => option.selected).map((option) => option.id));
    const options = visibleOptions(control.options, state.uiMode);
    // A lone checkbox named like its group needs no heading: its own name says it, with the tooltip beside it.
    const onlyOption = control.options?.length === 1 ? control.options[0] : undefined;
    const optionNamesGroup = onlyOption != null && sameLabel(displayOption(onlyOption), control.label);
    const optionSuffix = optionNamesGroup ? renderTooltip(control.tooltip) : "";
    const checkbox = (option: ControlOption) => `
              <label>
                <input type="checkbox" data-check-group="${escapeAttribute(control.id)}" value="${escapeAttribute(option.id)}" ${selected.has(option.id) ? "checked" : ""}>
                <span>${escapeHTML(displayOption(option))}${optionSuffix}</span>
              </label>`;
    const body = control.layout === "rows"
        ? renderOptionRows(options, checkbox)
        : groupedOptions(options)
            .map((group) => `
        <div class="option-group">
          ${group.title ? `<h4>${escapeHTML(group.title)}${renderTooltip(optionTooltip(group.options, "groupTooltip"))}</h4>` : ""}
          <div class="option-grid">${group.options.map(checkbox).join("")}</div>
        </div>`)
            .join("");
    return `
    <fieldset class="checkbox-group">
      ${optionNamesGroup ? "" : `<legend>${escapeHTML(control.label)}${renderTooltip(control.tooltip)}</legend>`}
      ${body}
    </fieldset>
  `;
}
/**
 * Rows layout: options sorted under category headings, one row per group with its name on the left and its checkboxes
 * in shared columns. Advanced-only options, whose names run longer, wrap onto a second line of the row.
 */
function renderOptionRows(options: ControlOption[], checkbox: (option: ControlOption) => string) {
    const categories: { title: string; groups: OptionGroup[] }[] = [];
    for (const option of options) {
        const title = String(option.category ?? "");
        let category = categories.find((candidate) => candidate.title === title);
        if (!category) {
            category = { title, groups: [] };
            categories.push(category);
        }
        const groupTitle = String(option.group ?? "");
        const group = category.groups.find((candidate) => candidate.title === groupTitle);
        if (group) {
            group.options.push(option);
        }
        else {
            category.groups.push({ title: groupTitle, options: [option] });
        }
    }
    return categories
        .map((category) => `
      <div class="option-category">
        ${category.title ? `<h4>${escapeHTML(category.title)}${renderTooltip(optionTooltip(category.groups.flatMap((group) => group.options), "categoryTooltip"))}</h4>` : ""}
        ${category.groups
        .map((group) => {
        const common = group.options.filter((option) => option.advancedOnly !== true);
        const advanced = group.options.filter((option) => option.advancedOnly === true);
        return `
          <div class="option-row">
            <span class="option-row-title">${escapeHTML(group.title)}${renderTooltip(optionTooltip(group.options, "groupTooltip"))}</span>
            <div class="option-row-options">
              ${common.length ? `<div class="option-columns">${common.map(checkbox).join("")}</div>` : ""}
              ${advanced.length ? `<div class="option-columns wide">${advanced.map(checkbox).join("")}</div>` : ""}
            </div>
          </div>`;
    })
        .join("")}
      </div>`)
        .join("");
}
/** A group's or category's tooltip: the first one its options give. */
function optionTooltip(options: ControlOption[], key: "groupTooltip" | "categoryTooltip") {
    return options.map((option) => option[key]).find((tooltip) => typeof tooltip === "string" && tooltip !== "");
}
function sameLabel(left: string | undefined, right: string | undefined) {
    const normalize = (value: string | undefined) => (value ?? "").trim().replace(/\s+/g, " ").toLocaleLowerCase();
    return normalize(left) !== "" && normalize(left) === normalize(right);
}
type OptionGroup = { title: string; options: ControlOption[] };
function groupedOptions(options: ControlOption[]) {
    const groups: OptionGroup[] = [];
    for (const option of options) {
        const title = String(option.group ?? "");
        const group = groups.find((candidate) => candidate.title === title);
        if (group) {
            group.options.push(option);
        }
        else {
            groups.push({ title, options: [option] });
        }
    }
    return groups;
}
export function renderInfoGrid(control: ControlSpec) {
    return `
    <div>
      <div class="control-label">${escapeHTML(control.label)}${renderTooltip(control.tooltip)}</div>
      <div class="info-grid">
        ${(control.options ?? []).map((option) => `<div>${escapeHTML(displayOption(option))}</div>`).join("")}
      </div>
    </div>
  `;
}
export function renderLibraryList(control: ControlSpec, context: CommandContext, location: ManifestLocation) {
    const rows = visibleRows(hydrateRows(control), state.uiMode);
    const columns = (control.columns ?? []).filter((column) => isShownInUIMode(column, state.uiMode));
    const key = `control:${control.id}`;
    if (state.loadingDataSources.has(key) && !state.dataSourcePayloads.has(key)) {
        return `
      <div class="library-list">
        <div class="control-label">${escapeHTML(control.label)}${renderTooltip(control.tooltip)}</div>
        ${renderLoadingBox(state.labels.loadingTitle)}
      </div>
    `;
    }
    return `
    <div class="library-list">
      <div class="control-label">${escapeHTML(control.label)}${renderTooltip(control.tooltip)}</div>
      ${rows.length
        ? `<div class="table-wrap"><table>
              <thead><tr>
                ${columns.map((column) => `<th>${escapeHTML(column.title)}</th>`).join("")}
                ${(control.rowActions ?? []).length ? `<th>${escapeHTML(state.labels.actionsColumnTitle)}</th>` : ""}
              </tr></thead>
              <tbody>
                ${rows
            .map((row) => {
            const contextForRow = rowContext(context, row);
            return `<tr>
                      ${columns.map((column) => `<td>${renderRowCell(row, column)}</td>`).join("")}
                      ${(control.rowActions ?? []).length
                ? `<td><div class="row-actions">${renderActions(control.rowActions ?? [], contextForRow, location, true)}</div></td>`
                : ""}
                    </tr>`;
        })
            .join("")}
              </tbody>
            </table></div>`
        : `<p class="empty">${escapeHTML(state.labels.libraryEmptyTitle)}</p>`}
    </div>
  `;
}
export function renderConfigEditor(control: ControlSpec, location: ManifestLocation) {
    return `
    <div class="config-editor">
      ${control.configFile
        ? `<label class="form-row">
              <span class="row-label">${escapeHTML(control.label || state.labels.settingsFileLabel)}${renderTooltip(control.tooltip)}</span>
              <span class="input-button-row">
                <input type="text" class="mono path-input" dir="ltr" value="${escapeAttribute(displayWorkspacePath(state.configFilePaths[control.id] ?? control.configFile.path, state.bundleRootPath))}"
                  data-config-path="${escapeAttribute(control.id)}">
                <button type="button" class="secondary-button" data-load-config="${escapeAttribute(control.id)}">${escapeHTML(state.labels.loadButtonTitle)}</button>
              </span>
            </label>
            <button type="button" class="visually-hidden" data-save-config="${escapeAttribute(control.id)}">${escapeHTML(state.labels.saveButtonTitle)}</button>`
        : `<div class="control-label">${escapeHTML(control.label)}${renderTooltip(control.tooltip)}</div>`}
      ${(control.settings ?? []).map((setting) => renderConfigSetting(control, setting, location)).join("")}
    </div>
  `;
}
export function renderConfigSetting(control: ControlSpec, setting: ConfigSetting, controlLocation: ManifestLocation) {
    const key = `${control.id}.${setting.id}`;
    let options = setting.options ?? [];
    if (setting.dataSource) {
        const context = configDataSourceContext(control);
        ensureDataSource(`setting:${key}`, { ...controlLocation, settingID: setting.id }, context);
        options = state.dataSourcePayloads.get(`setting:${key}`)?.options ?? options;
    }
    const value = state.configValues[configValueKey(control, setting)] ?? setting.value ?? "";
    const common = `data-config-control="${escapeAttribute(control.id)}" data-config-setting="${escapeAttribute(setting.id)}"`;
    if (setting.kind === "dropdown") {
        return `
      <label class="form-row">
        <span class="row-label">${escapeHTML(setting.label)}${renderTooltip(setting.tooltip)}</span>
        <select ${common}>
          ${options
            .map((option) => `<option value="${escapeAttribute(option.id)}" ${option.id === String(value) ? "selected" : ""}>${escapeHTML(displayOption(option))}</option>`)
            .join("")}
        </select>
      </label>
    `;
    }
    if (setting.kind === "toggle") {
        return `
      <label class="toggle-row">
        <span class="row-label">${escapeHTML(setting.label)}${renderTooltip(setting.tooltip)}</span>
        <input type="checkbox" ${value === true || value === "true" ? "checked" : ""} ${common} data-toggle>
      </label>
    `;
    }
    const inputID = `setting-${control.id}-${setting.id}`;
    return `
    <div class="form-row">
      <label class="row-label" for="${escapeAttribute(inputID)}">${escapeHTML(setting.label)}${renderTooltip(setting.tooltip)}</label>
      ${setting.kind === "path"
        ? `<span class="input-button-row"><input id="${escapeAttribute(inputID)}" type="text" class="path-input" dir="ltr" value="${escapeAttribute(displayWorkspacePath(String(value), state.bundleRootPath))}" placeholder="${escapeAttribute(setting.placeholder ?? "")}" ${common}>
              <button type="button" class="secondary-button" data-config-path-prompt="${escapeAttribute(control.id)}:${escapeAttribute(setting.id)}">${escapeHTML(state.labels.chooseButtonTitle)}</button></span>`
        : `<input id="${escapeAttribute(inputID)}" type="text" value="${escapeAttribute(value)}" placeholder="${escapeAttribute(setting.placeholder ?? "")}" ${common}>`}
    </div>
  `;
}

export function renderRowCell(row: RowSpec, column: RowColumnSpec) {
    const value = column.id === "name" ? row.title ?? row.values?.[column.id] ?? row.id : column.id === "status" ? localizedStatus(row.status ?? row.values?.status ?? "") : row.values?.[column.id] ?? "";
    if (column.id === "build") {
        const style = buildTagStyle(value);
        return style ? `<div><span class="pill build ${style}">${escapeHTML(value)}</span></div>` : "<div></div>";
    }
    const tags = column.id === "name"
        ? [
            row.status ? `<span class="pill ${tagStyle(row.status)}">${escapeHTML(localizedStatus(row.status))}</span>` : "",
            ...(row.tags ?? []).map((tag) => `<span class="pill ${escapeAttribute(tag.style ?? "secondary")}">${escapeHTML(localizedTag(tag))}</span>`),
        ].join("")
        : "";
    return `<div>${escapeHTML(value)}${tags ? `<div class="pill-row">${tags}</div>` : ""}</div>`;
}

export function renderConfirmationDialog() {
    const pending: PendingConfirmation | null = state.pendingConfirmation;
    if (!pending) {
        return "";
    }
    const { action, context, input = "" } = pending;
    const confirmation = action.confirm;
    if (!confirmation) {
        return "";
    }
    const requiredText = resolveText(confirmation.requiredText ?? "", context);
    const canConfirm = !requiredText || input === requiredText;
    return `
    <div class="modal-backdrop" role="presentation">
      <section class="confirmation-modal" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
        <h2 id="confirm-title">${renderIconTitle(resolveText(confirmation.title ?? action.title, context), action.role === "destructive" ? "exclamationmark.triangle.fill" : action.iconName, action.role === "destructive" ? "⚠️" : action.textIcon, "?")}</h2>
        ${confirmation.message ? `<p>${escapeHTML(resolveText(confirmation.message, context))}</p>` : ""}
        ${requiredText
        ? `<label class="form-row stacked">
                <span>${escapeHTML(resolveText(confirmation.prompt ?? `Type "${requiredText}" to confirm.`, context))}</span>
                <input type="text" data-confirm-input value="${escapeAttribute(input)}" placeholder="${escapeAttribute(resolveText(requiredText, context))}" autofocus>
              </label>`
        : ""}
        <div class="modal-actions">
          <button type="button" class="secondary-button" data-confirm-cancel>${escapeHTML(confirmation.cancelButtonTitle ?? "Cancel")}</button>
          <button type="button" class="action-button ${action.role === "destructive" ? "danger" : "primary"}" data-confirm-run ${canConfirm ? "" : "disabled"}>${escapeHTML(confirmation.confirmButtonTitle ?? action.title)}</button>
        </div>
      </section>
    </div>
  `;
}
