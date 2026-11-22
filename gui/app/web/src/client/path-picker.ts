import type { PathPickResponse } from "../../../shared/types.js";
import { api } from "./api.js";
import { errorMessage } from "./model.js";
import { pathPickerDefaultPath, type PathPickerSpec } from "./path-picker-defaults.js";
import { scheduleRender } from "./rerender.js";
import { state } from "./state.js";
import { appendTerminal } from "./terminal.js";

export async function chooseLocalPath(spec: PathPickerSpec, currentValue: unknown) {
    try {
        const result = await api<PathPickResponse>("/api/path/pick", {
            method: "POST",
            body: {
                kind: pathPickerKind(spec),
                title: pathPickerTitle(spec),
                defaultPath: pathPickerDefaultPath(spec, currentValue, state),
            },
        });
        return result.cancelled ? null : result.path ?? null;
    }
    catch (error) {
        appendTerminal("error", pathPickerTitle(spec), errorMessage(error));
        scheduleRender();
        return null;
    }
}

function pathPickerTitle(spec: PathPickerSpec) {
    return spec?.label ? `${state.labels.chooseButtonTitle} ${spec.label}` : state.labels.chooseButtonTitle;
}
function pathPickerKind(spec: PathPickerSpec) {
    const explicitKind = String(spec?.pathType ?? spec?.pathKind ?? spec?.pathMode ?? "").toLowerCase();
    if (explicitKind === "directory" || explicitKind === "folder") {
        return "directory";
    }
    if (explicitKind === "file" || explicitKind === "bundle") {
        return explicitKind;
    }
    const id = String(spec?.id ?? "").toLowerCase();
    const key = String(spec?.key ?? "").toLowerCase();
    const label = String(spec?.label ?? "").toLowerCase();
    const tooltip = String(spec?.tooltip ?? "").toLowerCase();
    const searchable = `${id} ${key} ${label} ${tooltip}`;
    if (id === "ref_path" || key === "reference_library") {
        return "directory";
    }
    if (/(^|[_\s-])(out|output)[_\s-]*(dir|directory)($|[_\s-])/.test(searchable) ||
        /(^|[_\s-])(dir|directory|folder|library|cache)($|[_\s-])/.test(searchable)) {
        return "directory";
    }
    return "file";
}
