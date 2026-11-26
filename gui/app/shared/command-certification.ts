import { commandCertifications } from "./generated/command-certifications.js";
import { SITE_BASE_URL } from "./docs-links.js";
import type { ActionSpec } from "./types.js";

export function certificationEvidenceUrl(path: string): URL | undefined {
    if (!Object.hasOwn(commandCertifications, path)) return undefined;
    return new URL(`${SITE_BASE_URL}/certification.html#command-${encodeURIComponent(path.replaceAll(" ", "-"))}`);
}

export function actionCertification(action: ActionSpec) {
    const command = action.command;
    let path = action.serverOperation?.operation.replaceAll("/", " ");
    if (command) {
        if (!/(?:^|[/\\])run-wgsextract\.(?:sh|ps1|bat)$/i.test(command.executable)) {
            return undefined;
        }
        const words = [];
        for (const argument of command.arguments ?? []) {
            if (argument.startsWith("-") || argument.includes("{{")) break;
            words.push(argument);
        }
        path = words.join(" ");
    }
    const level = path && Object.hasOwn(commandCertifications, path) ? commandCertifications[path] : undefined;
    return path && level ? { path, level } : undefined;
}
