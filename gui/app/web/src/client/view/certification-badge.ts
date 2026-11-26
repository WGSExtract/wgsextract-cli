import { actionCertification, certificationEvidenceUrl } from "../../../../shared/command-certification.js";
import type { ActionSpec, StringMap } from "../../../../shared/types.js";
import { escapeAttribute, escapeHTML } from "../dom.js";

const color = { Certified: "success", Validated: "primary", Tested: "", Experimental: "warning" };

export function renderCertificationBadge(action: ActionSpec, focusKey: string, strings: StringMap = {}): string {
    const certification = actionCertification(action);
    if (!certification) return "";
    const { path, level } = certification;
    const label = strings[`${level}.label`] ?? level;
    const tooltip = strings[`${level}.tooltip`] ?? "";
    const href = certificationEvidenceUrl(path);
    if (!href) return "";
    const name = action.title ? `${action.title}: ${label}` : label;
    return `<a class="pill certification-badge ${color[level]}" href="${escapeAttribute(href.href)}" target="_blank" rel="noopener noreferrer" aria-label="${escapeAttribute(name)}" data-certification-command="${escapeAttribute(path)}" data-certification-focus="${escapeAttribute(focusKey)}" data-tooltip="${escapeAttribute(tooltip)}">${escapeHTML(label)}</a>`;
}
