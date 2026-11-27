import type { ActionSpec, BundleManifest, BundlePage, ConfigSetting, ControlOption, ControlSpec, PageSection, RowTagSpec, StringMap } from "./types.js";
import { linkedMoveFolderFormat, linkedMoveTargetUnavailable } from "./workspace-move-message.js";

export function parseTomlStrings(text: string): StringMap {
    const values: StringMap = {};
    const lines = text.split(/\r?\n/);
    let index = 0;
    while (index < lines.length) {
        const rawLine = lines[index] ?? "";
        const lineNumber = index + 1;
        const line = rawLine.trim();
        index += 1;
        if (!line || line.startsWith("#")) {
            continue;
        }
        if (line.startsWith("[") && line.endsWith("]")) {
            throw new Error(`Invalid localization TOML at line ${lineNumber}: ${rawLine}`);
        }
        const equals = findUnescapedEquals(line);
        if (equals < 0) {
            throw new Error(`Invalid localization TOML at line ${lineNumber}: ${rawLine}`);
        }
        const key = unquoteKey(line.slice(0, equals).trim());
        let rawValue = line.slice(equals + 1).trimStart();
        if (rawValue.startsWith('"""')) {
            rawValue = rawValue.slice(3);
            const collected: string[] = [];
            const sameLineEnd = rawValue.indexOf('"""');
            if (sameLineEnd >= 0) {
                collected.push(rawValue.slice(0, sameLineEnd));
            }
            else {
                collected.push(rawValue);
                let foundEnd = false;
                while (index < lines.length) {
                    const nextLine = lines[index] ?? "";
                    index += 1;
                    const end = nextLine.indexOf('"""');
                    if (end >= 0) {
                        collected.push(nextLine.slice(0, end));
                        foundEnd = true;
                        break;
                    }
                    collected.push(nextLine);
                }
                if (!foundEnd) {
                    throw new Error(`Unterminated multiline localization string: ${key}`);
                }
            }
            if (collected[0] === "") {
                collected.shift();
            }
            if (collected[collected.length - 1] === "") {
                collected.pop();
            }
            values[key] = collected.join("\n");
            continue;
        }
        if (!rawValue.startsWith('"')) {
            throw new Error(`Invalid localization TOML at line ${lineNumber}: ${rawLine}`);
        }
        const closing = findClosingQuote(rawValue);
        if (closing < 0) {
            throw new Error(`Invalid localization TOML at line ${lineNumber}: ${rawLine}`);
        }
        const trailing = rawValue.slice(closing + 1).trim();
        if (trailing && !trailing.startsWith("#")) {
            throw new Error(`Invalid localization TOML at line ${lineNumber}: ${rawLine}`);
        }
        values[key] = unescapeTomlString(rawValue.slice(1, closing));
    }
    return values;
}
export function parseTomlStringValue(text: string, requestedKey: string): string | undefined {
    for (const rawLine of text.split(/\r?\n/)) {
        const line = rawLine.trim();
        if (!line || line.startsWith("#") || line.startsWith("[") && line.endsWith("]")) {
            continue;
        }
        const equals = findUnescapedEquals(line);
        if (equals < 0) {
            continue;
        }
        const key = unquoteKey(line.slice(0, equals).trim());
        if (key !== requestedKey) {
            continue;
        }
        const rawValue = line.slice(equals + 1).trimStart();
        const booleanMatch = /^(true|false)(?:\s*(?:#.*)?)$/.exec(rawValue);
        if (booleanMatch) {
            return booleanMatch[1];
        }
        if (!rawValue.startsWith('"') || rawValue.startsWith('"""')) {
            return undefined;
        }
        const closing = findClosingQuote(rawValue);
        if (closing < 0) {
            return undefined;
        }
        const trailing = rawValue.slice(closing + 1).trim();
        if (trailing && !trailing.startsWith("#")) {
            return undefined;
        }
        return unescapeTomlString(rawValue.slice(1, closing));
    }
    return undefined;
}
export function mergeTables(...tables: Array<StringMap | undefined | null>): StringMap {
    return Object.assign({}, ...tables.filter(Boolean));
}
export function localizationLabels(table: StringMap = {}) {
    return {
        standardOptionsSectionTitle: table["app.standardOptions.title"] ?? "Standard UI Options",
        languageSectionTitle: table["language.setting.title"] ?? "Interface Language",
        languagePickerLabel: table["language.setting.label"] ?? "Language",
        languageSearchPlaceholder: table["language.setting.searchPlaceholder"] ?? "Search languages",
        languageSystemDefaultLabel: table["language.setting.systemDefault"] ?? "Use system default",
        languageAITranslatedLabel: table["language.setting.aiTranslated"] ?? "AI translated",
        iconSetPickerLabel: table["app.iconSet.label"] ?? "Icons",
        iconSetSwiftSymbolsLabel: table["app.iconSet.sfSymbols"] ?? "SF Symbols",
        iconSetBootstrapIconsLabel: table["app.iconSet.bootstrapIcons"] ?? "Bootstrap Icons",
        iconSetEmojiLabel: table["app.iconSet.emoji"] ?? "Emoji",
        colorThemePickerLabel: table["app.colorTheme.label"] ?? "Theme",
        colorThemeSystemLabel: table["app.colorTheme.system"] ?? "System",
        colorThemeLightLabel: table["app.colorTheme.light"] ?? "Light",
        colorThemeDarkLabel: table["app.colorTheme.dark"] ?? "Dark",
        webUIFontPickerLabel: table["app.webUIFont.label"] ?? "Web Font",
        webUIFontSystemLabel: table["app.webUIFont.system"] ?? "Current priority",
        webUIFontSFProLabel: table["app.webUIFont.sfPro"] ?? "SF Pro when available",
        privacyModeLabel: table["app.privacyMode.label"] ?? "Privacy mode",
        privacyModeTooltip: table["app.privacyMode.tooltip"] ?? "Stop the app's automatic network activity. When on, the app does not check GitHub for updates at startup; use File > Check for Updates... to check yourself.",
        privacyModeBadgeTitle: table["app.privacyMode.badge.title"] ?? "PRIVACY",
        privacyModeBadgeTooltip: table["app.privacyMode.badge.tooltip"] ?? "Privacy mode is on. Change it under Network & Privacy in Settings.",
        networkTitle: table["app.network.title"] ?? "Network & Privacy",
        networkConsentQuestion: table["app.network.consentQuestion"] ?? "Do you want to access the network to download the bootstrap library (and use default network features)?",
        networkConsentDetail: table["app.network.consentDetail"] ?? "OK lets setup download tools and public reference data if needed, and enables update checks at launch. You can change this in Settings.",
        networkLaunchCheck: table["app.network.launchCheck"] ?? "Check for updates at launch",
        networkNotNow: table["app.network.notNow"] ?? "Not now",
        networkSaveError: table["app.network.saveError"] ?? "Could not save network settings. Try again.",
        networkSettingsDetail: table["app.network.settingsDetail"] ?? "Check GitHub for app updates at launch, or pause automatic connections with Privacy mode. Manual checks and downloads still run when you request them.",
        uiModeLabel: table["app.uiMode.label"] ?? "Mode",
        uiModeBasicLabel: table["app.uiMode.basic"] ?? "Basic",
        uiModeAdvancedLabel: table["app.uiMode.advanced"] ?? "Advanced",
        uiModeBasicTooltip: table["app.uiMode.basic.tooltip"] ?? "Show the common tasks, with recommended settings chosen for you.",
        uiModeAdvancedTooltip: table["app.uiMode.advanced.tooltip"] ?? "Show every feature and option.",
        layoutDirection: layoutDirection(table["language.layoutDirection"]),
        certificationStrings: Object.fromEntries(Object.entries(table).filter(([key]) => key.startsWith("certification.")).map(([key, value]) => [key.slice("certification.".length), value])),
        viewerTitle: table["viewer.title"] ?? "Genome viewer",
        viewerStrings: Object.fromEntries(Object.entries(table).filter(([key]) => key.startsWith("viewer.")).map(([key, value]) => [key.slice("viewer.".length), value])),
        geneTitle: table["gene.title"] ?? "Gene page",
        geneStrings: Object.fromEntries(Object.entries(table).filter(([key]) => key.startsWith("gene.")).map(([key, value]) => [key.slice("gene.".length), value])),
        terminalMainTabTitle: table["app.terminal.mainTab.title"] ?? "Jobs",
        jobsEmptyLabel: table["app.jobs.empty"] ?? "No jobs have run in this session yet.",
        jobsColumnJobLabel: table["app.jobs.column.job"] ?? "Job",
        jobsColumnStatusLabel: table["app.jobs.column.status"] ?? "Status",
        jobsColumnStartedLabel: table["app.jobs.column.started"] ?? "Started",
        jobsColumnFileLabel: table["app.jobs.column.file"] ?? "File",
        jobsStatusRunningLabel: table["app.jobs.status.running"] ?? "Running",
        jobsStatusSucceededLabel: table["app.jobs.status.succeeded"] ?? "Succeeded",
        jobsStatusWarningLabel: table["app.jobs.status.warning"] ?? "Finished with warnings",
        jobsStatusFailedLabel: table["app.jobs.status.failed"] ?? "Failed",
        jobsStatusCancelledLabel: table["app.jobs.status.cancelled"] ?? "Cancelled",
        jobsMessagesLabel: table["app.jobs.messages"] ?? "Messages",
        terminalCommandOutputLabel: table["app.terminal.commandOutput.label"] ?? "Command output",
        terminalShowOutputLabel: table["app.terminal.showOutput.label"] ?? "Show Command Output",
        terminalHideOutputLabel: table["app.terminal.hideOutput.label"] ?? "Hide Command Output",
        sidebarShowLabel: table["app.sidebar.show.label"] ?? "Show Sidebar",
        sidebarHideLabel: table["app.sidebar.hide.label"] ?? "Hide Sidebar",
        openBundleWorkspaceTitle: table["actions.settings.settings-paths.open-bundle-workspace.title"] ?? "Open Bundle Workspace",
        openBundleWorkspaceTooltip: table["actions.settings.settings-paths.open-bundle-workspace.tooltip"] ??
            "Open the writable bundle workspace folder.",
        workspaceLocationTitle: table["app.workspaceLocation.title"] ?? "Users Workspace Location",
        workspaceLocationFolderLabel: table["app.workspaceLocation.folder.label"] ?? "Users Workspace Folder",
        workspaceLocationCurrentLabel: table["app.workspaceLocation.current.label"] ?? "Current Users Workspace",
        workspaceLocationUseDefaultTitle: table["app.workspaceLocation.useDefault.title"] ?? "Use Default",
        workspaceLocationNote: table["app.workspaceLocation.note"] ??
            "Holds the settings file and the folders you manage. Moving takes it and everything in it to this folder, so every path shown under {{usersWorkspace}} follows. Folders you set elsewhere stay where they are.",
        workspaceLocationMoveTitle: table["app.workspaceLocation.move.title"] ?? "Move",
        workspaceLocationMoveConfirm: table["app.workspaceLocation.move.confirm"] ??
            "Move the users workspace and everything in it to %{path}? Running actions are stopped.",
        workspaceLocationMovingFormat: table["app.workspaceLocation.moving.format"] ?? "Moving: %{percent}%",
        workspaceLocationCancelTitle: table["app.workspaceLocation.cancel.title"] ?? "Cancel",
        workspaceLocationFolderMoveConfirm: table["app.workspaceLocation.folderMove.confirm"] ??
            "Move what is in %{from} to %{path}? Running actions are stopped. Change Setting Only leaves it where it is.",
        workspaceLocationSettingOnlyTitle: table["app.workspaceLocation.settingOnly.title"] ?? "Change Setting Only",
        workspaceMoveBusy: table["app.workspaceLocation.error.busy"] ?? "The workspace is in use. Finish running commands, saves or other moves, then try again.",
        workspaceMoveInProgress: table["app.workspaceLocation.error.inProgress"] ?? "The workspace is being moved. Try again when the move has finished.",
        workspaceMoveSettingsLocked: table["app.workspaceLocation.error.settingsLocked"] ?? "The settings file is locked. Finish saving settings, then try the move again.",
        workspaceMoveStale: table["app.workspaceLocation.error.stale"] ?? "Another window moved the workspace. Reopen the app before using it.",
        workspaceMoveProtectedFolder: table["app.workspaceLocation.error.protectedFolder"] ?? "This folder contains app data and cannot be moved.",
        workspaceLocationLinkedFolderFormat: table["app.workspaceLocation.linkedFolder.format"] ?? linkedMoveFolderFormat,
        workspaceLocationLinkTargetUnavailable: table["app.workspaceLocation.linkTargetUnavailable"] ?? linkedMoveTargetUnavailable,
        terminalCopyTextLabel: table["app.terminal.copyText.label"] ?? "Copy terminal text",
        terminalCopiedTextLabel: table["app.terminal.copiedText.label"] ?? "Copied!",
        setupTitle: table["app.setup.status.title"] ?? "Setup",
        setupRunButtonTitle: table["app.setup.runButton.title"] ?? "Run Setup",
        setupRerunButtonTitle: table["app.setup.rerunButton.title"] ?? "Rerun Setup",
        setupPromptBodyFormat: table["app.setup.prompt.bodyFormat"] ??
            "Do you want to run setup? %{app} will probably not work properly without running setup.",
        setupPromptAppNameFallback: table["app.setup.prompt.appNameFallback"] ?? "This app",
        setupInitialInstallSizeFormat: table["app.setup.initialInstallSize.format"] ??
            "Initial setup will install about %{size} GB.",
        setupDiskSpaceCheckingTitle: table["app.setup.diskSpace.checking"] ?? "Checking available disk space...",
        setupDiskSpaceCheckFailedFormat: table["app.setup.diskSpace.checkFailedFormat"] ??
            "Could not check available disk space: %{error}",
        setupRunningTitle: table["app.setup.status.running"] ?? "Running setup...",
        setupNoStepsTitle: table["app.setup.status.none"] ?? "No setup steps are defined for this bundle.",
        setupStatusReadyTitle: table["app.setup.status.ready"] ?? "Review and run this bundle's setup steps.",
        setupStatusOkTitle: table["app.setup.status.ok"] ?? "Setup completed successfully.",
        setupStatusFailedTitle: table["app.setup.status.failed"] ?? "Setup failed. Review command output for details.",
        setupToolLabel: table["app.setup.tool.label"] ?? "Tool",
        setupVersionLabel: table["app.setup.version.label"] ?? "Version",
        setupStepPendingTitle: table["app.setup.step.pending"] ?? "Pending",
        setupStepRunningTitle: table["app.setup.step.running"] ?? "Running",
        setupStepOkTitle: table["app.setup.step.ok"] ?? "OK",
        setupStepWarningTitle: table["app.setup.step.warning"] ?? "Warning",
        setupStepFailedTitle: table["app.setup.step.failed"] ?? "Failed",
        setupStepKindScriptLabel: table["app.setup.step.kind.script"] ?? "Script",
        setupStepKindPathToolLabel: table["app.setup.step.kind.pathTool"] ?? "Tool on PATH",
        setupStepKindHomebrewPackageLabel: table["app.setup.step.kind.homebrewPackage"] ?? "Homebrew package",
        setupStepKindPixiInstallLabel: table["app.setup.step.kind.pixiInstall"] ?? "Pixi install",
        setupStepKindPixiRunLabel: table["app.setup.step.kind.pixiRun"] ?? "Pixi command",
        chooseButtonTitle: table["app.pathPicker.chooseButton.title"] ?? "Choose...",
        pathPickerErrorTitle: table["app.pathPicker.error.title"] ?? "Could not choose path",
        settingsFileLabel: table["app.settingsFile.label"] ?? "Settings File",
        loadButtonTitle: table["app.loadButton.title"] ?? "Load",
        saveButtonTitle: table["app.saveButton.title"] ?? "Save",
        actionsColumnTitle: table["app.actionsColumn.title"] ?? "Actions",
        loadingTitle: table["app.loading.title"] ?? "Loading...",
        refreshingTitle: table["app.refreshing.title"] ?? "Refreshing...",
        retryButtonTitle: table["app.retryButton.title"] ?? "Retry",
        sectionExpandLabel: table["app.section.expand.label"] ?? "Expand Section",
        sectionCollapseLabel: table["app.section.collapse.label"] ?? "Collapse Section",
        loadWebUITitle: table["app.error.loadWebUI.title"] ?? "Could not load Web UI",
        libraryEmptyTitle: table["app.library.empty"] ?? "No library items are defined.",
        actionMissingInputsFormat: table["app.action.missingInputs.format"] ?? "Missing: %{inputs}",
        actionUnavailableTitle: table["app.action.unavailable.title"] ?? "This action is not available.",
        terminalCloseTabLabelFormat: table["app.terminal.closeTab.labelFormat"] ?? "Close %{title}",
        terminalExitCodeTitleFormat: table["app.terminal.exitCode.titleFormat"] ?? "Exit code %{code}",
        terminalExitDetailFormat: table["app.terminal.exitCode.detailFormat"] ?? "%{command} exited with code %{code}.",
        terminalNonzeroExitSummary: table["app.terminal.nonzeroExit.summary"] ??
            "The command exited with a non-zero status. Check the command output for details.",
        terminalProcessErrorTitle: table["app.terminal.processError.title"] ?? "Command failed",
        terminalJobSubmissionUnknown: table["app.terminal.jobSubmissionUnknown"] ?? "The server may still be running this job. Check its status before trying again.",
        terminalProcessErrorSummary: table["app.terminal.processError.summary"] ?? "The command could not complete.",
        configLoadedFormat: table["app.config.loaded.format"] ?? "Loaded settings from %{path}",
        configLoadErrorFormat: table["app.config.loadError.format"] ?? "Could not load %{label}: %{error}",
        configSavedFormat: table["app.config.saved.format"] ?? "Saved %{count} setting(s) to %{path}",
        configSaveErrorFormat: table["app.config.saveError.format"] ?? "Could not save %{label}: %{error}",
        actionPrecheckDiskSpaceTitle: table["app.action.precheck.diskSpace.title"] ?? "Not enough free disk space",
        actionPrecheckDiskSpaceMessageFormat: table["app.action.precheck.diskSpace.messageFormat"] ??
            "Need %{required} GB free at %{path}, only %{available} GB available.",
        actionPrecheckDiskSpaceInfoTitle: table["app.action.precheck.diskSpace.infoTitle"] ?? "Disk space estimate",
        actionPrecheckDiskSpaceInfoFormat: table["app.action.precheck.diskSpace.infoFormat"] ??
            "Estimated %{required} GB needed at %{path} (%{available} GB free).",
        resultTitle: table["app.result.title"] ?? "Result",
        resultStatusOkTitle: table["app.result.status.ok"] ?? "Completed",
        resultStatusFailedTitle: table["app.result.status.failed"] ?? "Failed",
        resultStatusCancelledTitle: table["app.result.status.cancelled"] ?? "Cancelled",
        resultWarningsTitle: table["app.result.warnings.title"] ?? "Warnings",
        resultMetricsTitle: table["app.result.metrics.title"] ?? "Key metrics",
        resultOutputsTitle: table["app.result.outputs.title"] ?? "Output files",
        resultOpenTitle: table["app.result.open.title"] ?? "Open",
        resultRevealTitle: table["app.result.reveal.title"] ?? "Show in Folder",
        resultManifestLabel: table["app.result.manifest.label"] ?? "Result manifest",
        resultDocumentTitle: table["app.result.document.title"] ?? "Full result (JSON)",
        resultUsageTitle: table["app.result.usage.title"] ?? "Check the selected options",
        resultMissingDependencyTitle: table["app.result.missingDependency.title"] ?? "A required tool is not installed",
        resultOpenSetupTitle: table["app.result.openSetup.title"] ?? "Open Setup",
        resultCardTitle: table["app.result.card.title"] ?? "Result card",
        resultCardSourceFormat: table["app.result.card.sourceFormat"] ?? "Shown from %{file}",
        resultCardViewTitle: table["app.result.card.view.title"] ?? "View Card",
        resultCardCloseTitle: table["app.result.card.close.title"] ?? "Close",
        resultCardRerunTitle: table["app.result.card.rerun.title"] ?? "Force Run Again",
        alreadyRunTitle: table["app.alreadyRun.title"] ?? "Command already run",
        alreadyRunMessage: table["app.alreadyRun.message"] ?? "This command has already been run and its output files are newer than the input:",
        alreadyRunQuestion: table["app.alreadyRun.question"] ?? "Would you like to force run it again?",
        alreadyRunForceButtonTitle: table["app.alreadyRun.force"] ?? "Force Run Again",
        alreadyRunCancelButtonTitle: table["app.alreadyRun.cancel"] ?? "Cancel",
        alreadyRunMoreFilesFormat: table["app.alreadyRun.moreFiles"] ?? "and %{count} more",
        prerequisitesTitle: table["app.prerequisites.title"] ?? "Something this command needs is missing",
        prerequisitesMessage: table["app.prerequisites.message"] ?? "This command needs data or tools that aren't installed yet:",
        prerequisitesStillMissingMessage: table["app.prerequisites.stillMissing"] ??
            "It still isn't installed. Install it, or install it yourself and click Continue again.",
        prerequisitesManualNote: table["app.prerequisites.manualNote"] ??
            "To install it yourself, use the library or Tools pages, then click Continue.",
        prerequisitesInstallButtonTitle: table["app.prerequisites.install"] ?? "Install and Continue",
        prerequisitesContinueButtonTitle: table["app.prerequisites.continue"] ?? "Continue",
        prerequisitesCancelButtonTitle: table["app.prerequisites.cancel"] ?? "Cancel",
        prerequisitesCheckingTitle: table["app.prerequisites.checking"] ?? "Checking for required data and tools…",
        prerequisitesInstallTitleFormat: table["app.prerequisites.installTitleFormat"] ?? "Install %{name}",
        libraryStatusLabels: {
            installed: table["library.status.installed"] ?? "installed",
            unindexed: table["library.status.unindexed"] ?? "unindexed",
            incomplete: table["library.status.incomplete"] ?? "incomplete",
            missing: table["library.status.missing"] ?? "missing",
            unavailable: table["library.status.unavailable"] ?? "not available",
            "update-available": table["library.status.updateAvailable"] ?? "update available",
        },
        libraryTagLabels: {
            recommended: table["library.tags.recommended"] ?? "Recommended",
            mandatory: table["library.tags.mandatory"] ?? "Mandatory",
            optional: table["library.tags.optional"] ?? "Optional",
        },
    };
}
export function localizeManifest(rawManifest: BundleManifest, table: StringMap = {}): BundleManifest {
    const manifest = localizeFields(structuredClone(rawManifest), table, ["displayName", "summary"]);
    manifest.setup = manifest.setup ?? { steps: [] };
    manifest.setup.steps = (manifest.setup.steps ?? []).map((step) => localizeFields(step, table, ["label"]));
    manifest.exitCodeReference = (manifest.exitCodeReference ?? []).map((entry) => localizeFields(entry, table, ["title", "summary"]));
    manifest.pages = (manifest.pages ?? []).map((page) => localizePage(page, table));
    if (manifest.prerequisites?.kindLabels) {
        manifest.prerequisites.kindLabels = mapValues(manifest.prerequisites.kindLabels, (value) => localizedText(String(value), table));
    }
    return manifest;
}
function localizePage(page: BundlePage, table: StringMap): BundlePage {
    return {
        ...localizeFields(page, table, ["title", "summary", "sidebarGroup"]),
        sections: (page.sections ?? []).map((section) => localizeSection(section, table)),
    };
}
function localizeSection(section: PageSection, table: StringMap): PageSection {
    return {
        ...localizeFields(section, table, ["title", "subtitle"]),
        controls: (section.controls ?? []).map((control) => localizeControl(control, table)),
        actions: (section.actions ?? []).map((action) => localizeAction(action, table)),
    };
}
function localizeControl(control: ControlSpec, table: StringMap): ControlSpec {
    const localizedControl: ControlSpec = {
        ...localizeFields(control, table, ["label", "placeholder", "tooltip", "emptyText"]),
        options: (control.options ?? []).map((option) => localizeOption(option, table)),
        columns: (control.columns ?? []).map((column) => localizeFields(column, table, ["title"])),
        rows: (control.rows ?? []).map((row) => localizeRow(row, table)),
        items: (control.items ?? []).map((item) => ({
            ...item,
            values: mapValues(recordValue(item.values) ?? item, (value) => localizedText(String(value), table)),
        })),
        rowActions: (control.rowActions ?? []).map((action) => localizeAction(action, table)),
        settings: (control.settings ?? []).map((setting) => localizeSetting(setting, table)),
    };
    if (control.rowTemplate) {
        localizedControl.rowTemplate = localizeRow(control.rowTemplate, table);
    }
    return localizedControl;
}
function localizeRow<Row extends { tags?: RowTagSpec[] | undefined }>(row: Row, table: StringMap): Row {
    return {
        ...localizeFields(row, table, ["title", "status", "tooltip"]),
        tags: (row.tags ?? []).map((tag) => localizeFields(tag, table, ["title"])),
    };
}
function localizeAction(action: ActionSpec, table: StringMap): ActionSpec {
    const localizedAction = localizeFields(action, table, ["title", "advancedTitle", "tooltip", "disabledTooltip"]);
    if (action.confirm) {
        localizedAction.confirm = localizeFields(action.confirm, table, ["title", "message", "confirmButtonTitle", "cancelButtonTitle", "requiredText", "prompt"]);
    }
    return localizedAction;
}
function localizeSetting(setting: ConfigSetting, table: StringMap): ConfigSetting {
    return {
        ...localizeFields(setting, table, ["label", "placeholder", "tooltip"]),
        options: (setting.options ?? []).map((option) => localizeOption(option, table)),
    };
}
function localizeOption(option: ControlOption, table: StringMap): ControlOption {
    return localizeFields(option, table, ["title", "group", "category", "groupTooltip", "categoryTooltip", "disabledTooltip"]);
}
/** Replaces each string field that names a string-table key with its translation. */
function localizeFields<Record extends object>(record: Record, table: StringMap, fields: readonly string[]): Record {
    const next = { ...record } as { [key: string]: unknown };
    for (const field of fields) {
        const value = next[field];
        if (typeof value === "string") {
            next[field] = localizedText(value, table);
        }
    }
    return next as Record;
}
function localizedText(value: string, table: StringMap): string {
    return table[value] ?? value;
}
function recordValue(value: unknown): { [key: string]: unknown } | undefined {
    return value != null && typeof value === "object" && !Array.isArray(value) ? value as { [key: string]: unknown } : undefined;
}
function mapValues(values: { [key: string]: unknown }, transform: (value: unknown) => string): StringMap {
    return Object.fromEntries(Object.entries(values).map(([key, value]) => [key, transform(value)]));
}
function layoutDirection(value: unknown) {
    const normalized = String(value ?? "").trim().toLowerCase();
    return ["rtl", "right-to-left", "righttoleft"].includes(normalized) ? "rtl" : "ltr";
}
function findUnescapedEquals(line: string) {
    let quoted = false;
    let escaped = false;
    for (let i = 0; i < line.length; i += 1) {
        const char = line[i];
        if (escaped) {
            escaped = false;
        }
        else if (char === "\\") {
            escaped = true;
        }
        else if (char === '"') {
            quoted = !quoted;
        }
        else if (char === "=" && !quoted) {
            return i;
        }
    }
    return -1;
}
function findClosingQuote(value: string) {
    let escaped = false;
    for (let i = 1; i < value.length; i += 1) {
        const char = value[i];
        if (escaped) {
            escaped = false;
        }
        else if (char === "\\") {
            escaped = true;
        }
        else if (char === '"') {
            return i;
        }
    }
    return -1;
}
function unquoteKey(key: string) {
    return key.startsWith('"') && key.endsWith('"') ? unescapeTomlString(key.slice(1, -1)) : key;
}
function unescapeTomlString(value: string) {
    return value.replace(/\\([nrt"\\])/g, (_, escaped: string) => {
        switch (escaped) {
            case "n":
                return "\n";
            case "r":
                return "\r";
            case "t":
                return "\t";
            case '"':
                return '"';
            case "\\":
                return "\\";
            default:
                return escaped;
        }
    });
}
