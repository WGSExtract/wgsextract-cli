import type { ResultDocumentSpec } from "./result-document.js";
import type { LooseRecord, StateValue, StringMap, ValueMap } from "./value-types.js";

export interface ControlOption {
    id: string;
    title?: string;
    label?: string;
    iconName?: string;
    textIcon?: string;
    selected?: boolean;
    /** A checkbox option can't be chosen while any of these match, and commands see it unchecked. */
    disabledWhen?: ConditionSpec[];
    disabledTooltip?: string;
    [key: string]: unknown;
}

export interface ConfigSetting {
    id: string;
    key?: string;
    label?: string;
    value?: StateValue;
    options?: ControlOption[];
    dataSource?: LooseRecord;
    kind?: string;
    placeholder?: string;
    tooltip?: string;
    /** A path setting whose folder's contents move with it when the user picks another folder. */
    moveContents?: boolean;
    [key: string]: unknown;
}

export interface ConfigBootstrapScriptSpec {
    path: string;
    arguments?: string[];
    environment?: StringMap;
    workingDirectory?: string;
}

export interface ConfigFileSpec {
    path: string;
    /** Where earlier versions kept the file; the app moves it to path and drops these from saved state. */
    previousPaths?: string[];
    bootstrap?: { mode?: string; script?: ConfigBootstrapScriptSpec };
    [key: string]: unknown;
}

export interface RowColumnSpec {
    id: string;
    [key: string]: unknown;
}

export interface RowTagSpec {
    id?: string;
    title?: string;
    [key: string]: unknown;
}

export interface RowSpec {
    id: string;
    title?: string | undefined;
    values?: ValueMap;
    status?: string | undefined;
    tags?: RowTagSpec[];
    tooltip?: string | undefined;
    [key: string]: unknown;
}

export interface RowTemplateSpec {
    id?: string;
    title?: string;
    values?: Record<string, string>;
    status?: string;
    tags?: RowTagSpec[];
    tooltip?: string;
    [key: string]: unknown;
}

export interface ControlSpec {
    id: string;
    kind: string;
    label?: string;
    value?: StateValue;
    multiple?: boolean;
    pathDisplay?: "absolute";
    pathDisplaySource?: string;
    options?: ControlOption[];
    settings?: ConfigSetting[];
    configFile?: ConfigFileSpec;
    items?: Array<LooseRecord>;
    rows?: RowSpec[];
    rowTemplate?: RowTemplateSpec;
    columns?: RowColumnSpec[];
    rowActions?: ActionSpec[];
    actions?: ActionSpec[];
    dataSource?: LooseRecord;
    [key: string]: unknown;
}

export interface CommandSpec {
    executable: string;
    arguments?: string[];
    optionalArguments?: string[][];
    [key: string]: unknown;
}

export interface ConditionSpec {
    placeholder: string;
    exists?: boolean;
    equals?: string;
    notEquals?: string;
    in?: string[];
    notIn?: string[];
    lessThan?: string;
    lessThanOrEqual?: string;
    greaterThan?: string;
    greaterThanOrEqual?: string;
    [key: string]: unknown;
}

export interface ConfirmationSpec {
    requiredText?: string;
    title?: string;
    message?: string;
    prompt?: string;
    cancelButtonTitle?: string;
    confirmButtonTitle?: string;
    [key: string]: unknown;
}

export interface ActionSpec {
    id?: string;
    title?: string;
    /** The title Advanced mode shows instead of `title`, when the action does more there. */
    advancedTitle?: string;
    command?: CommandSpec;
    /** Control id whose selected alignment or variant file opens in the built-in genome viewer instead of running a command. */
    viewerInput?: string;
    /** Optional 1-based button row inside the section's actions area. */
    row?: number;
    precheck?: LooseRecord;
    confirm?: ConfirmationSpec;
    role?: string;
    iconName?: string;
    textIcon?: string;
    visibleWhen?: ConditionSpec[];
    disabledWhen?: ConditionSpec[];
    disabledTooltip?: string;
    /** Runs the action as a job on the bundle's job server when it is available; `command` is the fallback. */
    serverOperation?: ServerOperationSpec;
    /** The command line the bundle's prerequisite check is given for this action, when its executable is a wrapper the check can't read. */
    prerequisiteArguments?: string[];
    /** The placeholder naming the job's main input, shown in the Jobs list; else the first file path control the command uses. */
    primaryInput?: string;
    /** Ask for the run's result card (the manifest's `resultDocument.cardArguments`, or a job's `card`) and show it when the run ends. */
    resultCard?: boolean;
    /** Output roles in order of preference: when the run ends, the first output it wrote with one of them opens in the system's default app. */
    openOutput?: string[];
    [key: string]: unknown;
}

/**
 * Checks, before an action runs, for data it needs that isn't installed yet (devdocs/prerequisites.md). `command`
 * gets the action's command line appended and prints `{"missing": [MissingPrerequisite...]}`; each item's
 * `installArguments` are run with `command.executable` to install it.
 */
export interface PrerequisitesSpec {
    command: CommandSpec;
    /** The executables whose actions are checked, each with the arguments put before the action's own. */
    executables: Record<string, string[]>;
    /** A string-table key per item kind, formatting `%{name}` into what the dialog lists, e.g. "%{name} reference genome". */
    kindLabels?: StringMap;
}

/** One thing an action needs that isn't installed, as the bundle's prerequisite check reports it. */
export interface MissingPrerequisite {
    id: string;
    kind: string;
    name: string;
    /** Arguments for the check's executable that install it, or null when the user has to provide it. */
    installArguments: string[] | null;
}

/** An operation on the bundle's job server: `POST {url}/ops/{operation}` with `parameters` as the JSON body. */
export interface ServerOperationSpec {
    operation: string;
    /** String values are interpolated; those that end up empty are left out. Other JSON values are sent as written. */
    parameters?: LooseRecord;
}

/** A long-running local server, started on first use, that runs actions as cancellable jobs (devdocs/job-server.md). */
export interface JobServerSpec {
    /** Prints `{"url": ..., "token": ...}` as a line of JSON on stdout once it is listening. */
    command: CommandSpec;
    startupTimeoutSeconds?: number;
}

export interface PageSection {
    id?: string;
    title?: string;
    controls?: ControlSpec[];
    actions?: ActionSpec[];
    dataSource?: LooseRecord;
    visibleWhen?: ConditionSpec[];
    /** Starts closed, showing only its title, until the user opens it. */
    collapsed?: boolean;
    [key: string]: unknown;
}

export interface BundlePage {
    id: string;
    title: string;
    sections?: PageSection[];
    [key: string]: unknown;
}

export interface SetupStep {
    id: string;
    kind: string;
    label?: string;
    value?: string;
    arguments?: string[];
    workingDirectory?: string;
    environment?: StringMap;
    optional?: boolean;
    requiresAdmin?: boolean;
    platforms?: string[];
    toolVersion?: string;
    toolVersionFile?: string;
    installedVersionFile?: string;
    legacyPixiVersionFile?: string;
    [key: string]: unknown;
}

export interface SetupSpec {
    steps?: SetupStep[];
    [key: string]: unknown;
}

export interface BundleManifest {
    /**
     * A visible folder directly in the user's home for {{usersWorkspace}}, like ~/my-app: the settings file and the
     * folders the user manages, which they may move. Without it, {{usersWorkspace}} is the hidden {{bundleWorkspace}}.
     */
    defaultUsersWorkspace?: string;
    id?: string;
    displayName?: string;
    iconPath?: string;
    pages: BundlePage[];
    setup?: SetupSpec;
    uninstall?: SetupSpec;
    defaultLocalizationCode?: string;
    exitCodeReference?: ExitCodeReference[];
    jobServer?: JobServerSpec;
    resultDocument?: ResultDocumentSpec;
    prerequisites?: PrerequisitesSpec;
    [key: string]: unknown;
}

export interface ExitCodeReference {
    code: string | number;
    severity?: string;
    symbol?: string;
    title?: string;
    summary?: string;
    [key: string]: unknown;
}
