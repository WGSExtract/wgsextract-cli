import type { LabelTextKey } from "./types.js";

/** Every setup step kind the setup runner can run, with the label key that names it in the UI. */
export const setupStepKindLabelKeys = {
    pathTool: "setupStepKindPathToolLabel",
    homebrewPackage: "setupStepKindHomebrewPackageLabel",
    bundledScript: "setupStepKindScriptLabel",
    setupScript: "setupStepKindScriptLabel",
    pixiInstall: "setupStepKindPixiInstallLabel",
    pixiRun: "setupStepKindPixiRunLabel",
} as const satisfies Record<string, LabelTextKey>;

export type SetupStepKind = keyof typeof setupStepKindLabelKeys;

export const setupStepKinds = Object.keys(setupStepKindLabelKeys) as SetupStepKind[];

/** Kinds whose `value` is a script path inside the bundle. */
export const scriptSetupStepKinds: ReadonlySet<string> = new Set<SetupStepKind>(["bundledScript", "setupScript"]);

export function isSetupStepKind(kind: unknown): kind is SetupStepKind {
    return typeof kind === "string" && Object.hasOwn(setupStepKindLabelKeys, kind);
}
