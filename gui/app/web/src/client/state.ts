import type { UpdateState, WebUIState } from "../../../shared/types.js";

export function createInitialState(): WebUIState {
    const appMetadata = window as Window & {
        WGSEXTRACT_GUI_APPLICATION_NAME?: string;
        WGSEXTRACT_GUI_APPLICATION_VERSION?: string;
        WGSEXTRACT_GUI_AUTO_UPDATE?: boolean;
        WGSEXTRACT_GUI_AUTO_ACCEPT_UPDATE?: boolean;
        WGSEXTRACT_GUI_AUTO_UPDATE_DELAY_SECONDS?: number;
        WGSEXTRACT_GUI_AUTO_UPDATE_ACTION_DELAY_SECONDS?: number;
    };
    return {
        manifest: null,
        applicationName: appMetadata.WGSEXTRACT_GUI_APPLICATION_NAME ?? "",
        applicationVersion: appMetadata.WGSEXTRACT_GUI_APPLICATION_VERSION ?? "",
        autoUpdate: Boolean(appMetadata.WGSEXTRACT_GUI_AUTO_UPDATE),
        autoAcceptUpdate: Boolean(appMetadata.WGSEXTRACT_GUI_AUTO_ACCEPT_UPDATE),
        autoUpdateDelaySeconds: Number(appMetadata.WGSEXTRACT_GUI_AUTO_UPDATE_DELAY_SECONDS) || 0,
        autoUpdateActionDelaySeconds: Number(appMetadata.WGSEXTRACT_GUI_AUTO_UPDATE_ACTION_DELAY_SECONDS) || 0,
        update: createInitialUpdateState(),
        iconMap: {},
        labels: {},
        localizationCode: "",
        localizationOptions: [],
        usingSystemDefaultLocale: true,
        iconSet: "platform",
        colorTheme: "system",
        webUIFont: "system",
        uiMode: "basic",
        bundleRootPath: "",
        lastResultDocumentPaths: {},
        sourceRootPath: "",
        appVersion: "",
        workspaceLocation: null,
        workspaceLocationDraft: null,
        workspaceMove: null,
        activePageID: "",
        fieldValues: {},
        checkedOptions: {},
        configValues: {},
        configFilePaths: {},
        dataSourcePayloads: new Map(),
        dataSourceErrors: new Map(),
        loadingDataSources: new Set(),
        fileStateValues: new Map(),
        loadingFileStates: new Set(),
        actionPrechecks: new Map(),
        actionPrecheckErrors: new Map(),
        actionResultNotices: new Map(),
        loadingActionPrechecks: new Set(),
        exitCodeReference: new Map(),
        setupRun: null,
        setupPreflight: null,
        setupPreflightError: "",
        setupPreflightKey: "",
        loadingSetupPreflight: false,
        setupPromptVisible: false,
        setupPromptDismissed: false,
        terminalCopyFeedback: false,
        terminalEntries: [],
        activeTerminalIndex: 0,
        aboutDialogVisible: false,
        isTerminalVisible: true,
        isSidebarVisible: localStorage.getItem("guiForCLI.sidebarVisible") !== "false",
        pendingConfirmation: null,
        pendingPrerequisites: null,
        checkingPrerequisites: false,
        sidebarWidth: Number(localStorage.getItem("guiForCLI.sidebarWidth")) || 220,
        terminalHeight: Number(localStorage.getItem("guiForCLI.terminalHeight")) ||
            Math.round(window.innerHeight * 0.2),
    };
}

function createInitialUpdateState(): UpdateState {
    return {
        supported: false,
        checked: false,
        popoverVisible: false,
        status: "idle",
        currentVersion: "",
        availableVersion: "",
        updateRid: null,
        bytesRid: null,
        downloadedBytes: 0,
        contentLength: null,
        percent: null,
        message: "",
        body: "",
    };
}

export const state: WebUIState = createInitialState();
