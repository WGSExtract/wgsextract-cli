import type { IconMap } from "./icon-map.js";
import type { Labels, LocalizedOption } from "./label-types.js";
import type { ActionSpec, BundleManifest, ControlOption, ExitCodeReference, MissingPrerequisite, RowSpec } from "./manifest-types.js";
import type { ResultDocument, ResultNotice } from "./result-document.js";
import type { UIMode } from "./ui-mode.js";
import type { LooseRecord, StringMap, ValueMap } from "./value-types.js";

export interface CommandContext {
    setupReady?: boolean;
    uiMode?: UIMode;
    fieldValues?: ValueMap;
    checkedOptions?: StringMap;
    configValues?: ValueMap;
    rowValues?: ValueMap;
    fileStateValues?: ValueMap;
    placeholderLabels?: Labels;
    multiPathControls?: string[];
    bundleRootPath?: string;
    usersWorkspacePath?: string;
    homePath?: string;
    lastResultDocumentPaths?: LastResultDocumentPaths;
    [key: string]: unknown;
}

/** Saved copies of the result documents of the last action, and the last failed action, run in the app. */
export interface LastResultDocumentPaths {
    last?: string;
    lastFailed?: string;
}

export interface ProcessRunOptions {
    cwd?: string;
    env?: Record<string, string | undefined>;
    elevatedEnv?: Record<string, string | undefined>;
    requiresAdmin?: boolean;
    signal?: AbortSignal;
    timeoutMs?: number;
    maxOutputBytes?: number;
    maxErrorBytes?: number;
    onStdout?: (text: string) => void | Promise<void>;
    onStderr?: (text: string) => void | Promise<void>;
    /** Called after requested termination, once tree shutdown and child exit are observed. */
    onTerminated?: () => void;
}

export type ProcessResult = {
    exitCode: number | null;
    signal?: string | null;
    stdout: string;
    stderr: string;
    stdoutTruncated?: boolean;
    stderrTruncated?: boolean;
};

export type RunProcess = (
    executable: string,
    args: string[],
    options: ProcessRunOptions,
) => Promise<ProcessResult>;

export interface UpdateState {
    supported: boolean;
    checked: boolean;
    popoverVisible: boolean;
    status: string;
    currentVersion: string;
    availableVersion: string;
    updateRid: number | null;
    bytesRid: number | null;
    downloadedBytes: number;
    contentLength: number | null;
    percent: number | null;
    message: string;
    body: string;
}

export interface DataSourcePayload {
    values?: ValueMap;
    displayPaths?: StringMap;
    options?: ControlOption[];
    rows?: RowSpec[];
    items?: Array<LooseRecord>;
    rowActions?: ActionSpec[];
    actions?: ActionSpec[];
    [key: string]: unknown;
}

export interface PrecheckResult {
    severity?: string;
    message?: string;
    [key: string]: unknown;
}

export interface SetupStepResult {
    id: string;
    status?: string;
    exitCode?: number | null;
    stdout?: string;
    stderr?: string;
    durationMs?: number;
    [key: string]: unknown;
}

export interface SetupRun {
    status?: string;
    results?: SetupStepResult[];
    currentStepID?: string | null;
    currentStepStartedAt?: string | null;
    currentStepElapsedMs?: number;
    error?: string;
    completedAt?: string;
    [key: string]: unknown;
}

export interface BundleStateSnapshot {
    selectedPageID?: string | null;
    iconSet?: unknown;
    colorTheme?: unknown;
    webUIFont?: unknown;
    uiMode?: unknown;
    setupRun?: SetupRun | null;
    [key: string]: unknown;
}

export interface ManifestResponse {
    manifest: BundleManifest;
    devReload?: boolean;
    stateWarning?: string;
    configWarnings?: string[];
    iconMap?: IconMap;
    labels: Labels;
    localizationCode: string;
    localizationOptions: LocalizedOption[];
    usingSystemDefaultLocale?: boolean;
    bundleState?: BundleStateSnapshot | null;
    /** The app-wide Privacy mode setting (settings.json), not a bundle's saved state. */
    networkSettings: import("./network-settings.js").NetworkSettings;
    appVersion?: string;
    workspaceLocation?: WorkspaceLocationInfo;
    workspaceMoveRevision?: string;
    bundleRootPath: string;
    usersWorkspacePath?: string;
    sourceRootPath: string;
    lastResultDocumentPaths?: LastResultDocumentPaths;
    fieldValues?: ValueMap;
    checkedOptions?: Record<string, string[]>;
    configValues?: ValueMap;
    configFilePaths?: StringMap;
    autoRunSetup?: boolean;
}

export interface ConfigLoadResponse {
    path: string;
    values: ValueMap;
    warnings: string[];
}

export interface ConfigSaveResponse {
    path: string;
    keyCount: number;
}

export interface PathPickResponse {
    cancelled?: boolean;
    path?: string;
    paths?: string[];
}

export interface FileStateResponse {
    values?: ValueMap;
}

export interface TerminalStatus {
    severity?: string;
    symbol?: string;
    title?: string;
    blurb?: string;
    detail?: string;
}

export interface TerminalEntry {
    id: string;
    kind: string;
    title: string;
    body: string;
    /** Set once old output has been dropped from `body` to cap scrollback. */
    truncated?: boolean;
    command?: string;
    status?: TerminalStatus;
    /** Present until submission is confirmed; null before the app receives the ID. */
    pendingJobID?: string | null;
    /** The action's result document, when its command printed one. */
    result?: ResultDocument;
    /** When a job (an action or setup run) started, in epoch milliseconds. */
    startedAt?: number;
    /** The main input file a job ran on, shown in the Jobs list. */
    primaryFile?: string;
}

/** Identifies a section, control, or config setting in the loaded manifest. */
export interface ManifestLocation {
    pageID: string;
    sectionID: string;
    controlID?: string;
    settingID?: string;
}

/** Identifies an action by where it is declared; the server renders its command from the manifest. */
export interface ActionReference extends ManifestLocation {
    actionID: string;
}

export interface PendingConfirmation {
    action: ActionSpec;
    reference: ActionReference;
    context: CommandContext;
    input: string;
}

/** An action held back because data it needs isn't installed. */
export interface PendingPrerequisites {
    action: ActionSpec;
    reference: ActionReference;
    context: CommandContext;
    missing: MissingPrerequisite[];
    /** Set when the user said it was installed, or an install finished, and it still isn't. */
    stillMissing: boolean;
    /** Preserved from Force Run Again so Continue / Install and Continue still forces execution. */
    skipExistingCheck?: boolean;
    forceRerun?: boolean;
}

/** An output-only action held back because its permanent outputs are already newer than its inputs. */
export interface PendingAlreadyRun {
    action: ActionSpec;
    reference: ActionReference;
    context: CommandContext;
    outputs: string[];
    finishedAt?: string;
}

export interface WebUIState {
    manifest: BundleManifest | null;
    applicationName: string;
    applicationVersion: string;
    autoUpdate: boolean;
    autoAcceptUpdate: boolean;
    autoUpdateDelaySeconds: number;
    autoUpdateActionDelaySeconds: number;
    update: UpdateState;
    iconMap: IconMap;
    labels: Labels;
    localizationCode: string;
    localizationOptions: LocalizedOption[];
    usingSystemDefaultLocale: boolean;
    iconSet: string;
    colorTheme: string;
    webUIFont: string;
    uiMode: UIMode;
    /** Stops the app's automatic network activity: no update check at startup. */
    networkSettings: import("./network-settings.js").NetworkSettings;
    bundleRootPath: string;
    usersWorkspacePath: string;
    homePath?: string;
    lastResultDocumentPaths: LastResultDocumentPaths;
    sourceRootPath: string;
    appVersion: string;
    workspaceLocation: WorkspaceLocationInfo | null;
    workspaceLocationDraft: string | null;
    /** A move of the users workspace (`folder` null for its default location) or, with `setting`, of one setting's folder. */
    workspaceMove: { folder: string | null; setting?: { controlID: string; key: string; from: string } | undefined; running: boolean; percent: number } | null;
    /** Collapsible sections the user opened (all start closed). */
    openSections: Set<string>;
    activePageID: string;
    fieldValues: ValueMap;
    checkedOptions: Record<string, Set<string>>;
    configValues: ValueMap;
    configFilePaths: StringMap;
    dataSourcePayloads: Map<string, DataSourcePayload>;
    dataSourceErrors: Map<string, string>;
    loadingDataSources: Set<string>;
    /** Collapsible sections the user opened or closed from their default, by "pageID/sectionID". */
    toggledSections: Set<string>;
    /** Keyword typed into a table's filter box, by "control:<id>". */
    libraryFilters: Map<string, string>;
    /** The Settings page's Setup steps: shown (true), hidden (false), or null to hide them once setup has run. */
    setupExpanded: boolean | null;
    fileStateValues: Map<string, ValueMap>;
    loadingFileStates: Set<string>;
    actionPrechecks: Map<string, PrecheckResult>;
    actionPrecheckErrors: Map<string, string>;
    /** What the last run of an action (by actionReferenceKey) asks the user to fix. */
    actionResultNotices: Map<string, ResultNotice>;
    loadingActionPrechecks: Set<string>;
    exitCodeReference: Map<number, ExitCodeReference>;
    setupRun: SetupRun | null;
    setupPreflight: PrecheckResult | null;
    setupPreflightError: string;
    setupPreflightKey: string;
    loadingSetupPreflight: boolean;
    setupPromptVisible: boolean;
    setupPromptDismissed: boolean;
    terminalCopyFeedback: boolean;
    terminalEntries: TerminalEntry[];
    /** Every job run this session, oldest first, including ones whose tab was closed. */
    jobHistory: TerminalEntry[];
    activeTerminalIndex: number;
    aboutDialogVisible: boolean;
    isTerminalVisible: boolean;
    isSidebarVisible: boolean;
    pendingConfirmation: PendingConfirmation | null;
    pendingPrerequisites: PendingPrerequisites | null;
    pendingAlreadyRun: PendingAlreadyRun | null;
    /** Set while an action's prerequisites are checked before it runs. */
    checkingPrerequisites: boolean;
    activeTerminalID?: string;
    sidebarWidth: number;
    terminalHeight: number;
    [key: string]: unknown;
}

/** Where {{usersWorkspace}} is, and where it goes by default. */
export interface WorkspaceLocationInfo {
    workspaceRoot: string;
    defaultRoot: string;
    usingDefault: boolean;
}
