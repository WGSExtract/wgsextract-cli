import { contextPathList, contextValue } from "./rendering-context.js";
import type { CommandContext, CommandSpec, LooseRecord } from "./types.js";

const PLACEHOLDER_PATTERN = /\{\{([^}]+)\}\}/g;
const SINGLE_PLACEHOLDER_PATTERN = /^\{\{([^}]+)\}\}$/;

export function interpolate(value: unknown, context: CommandContext): string {
    return String(value ?? "").replace(PLACEHOLDER_PATTERN, (_, rawPlaceholder) => {
        const placeholder = rawPlaceholder.trim();
        return String(contextValue(context, placeholder) ?? "");
    });
}
export function placeholdersIn(values: unknown[]): string[] {
    const placeholders: string[] = [];
    for (const value of values) {
        for (const match of String(value ?? "").matchAll(PLACEHOLDER_PATTERN)) {
            const placeholder = match[1]?.trim();
            if (placeholder && !placeholders.includes(placeholder)) {
                placeholders.push(placeholder);
            }
        }
    }
    return placeholders;
}
export function missingPlaceholders(command: CommandSpec, context: CommandContext): string[] {
    return placeholdersIn([command.executable, ...(command.arguments ?? [])]).filter((placeholder) => {
        const value = String(contextValue(context, placeholder) ?? "").trim();
        return value.length === 0;
    });
}

function expandGroup(group: string[], context: CommandContext): string[] {
    const listPlaceholders = placeholdersIn(group).filter((placeholder) => contextPathList(context, placeholder) !== undefined);
    if (listPlaceholders.length === 1) {
        const listPlaceholder = listPlaceholders[0]!;
        const items = contextPathList(context, listPlaceholder) ?? [];
        if (items.length === 0) {
            return [];
        }
        const otherMissing = placeholdersIn(group).filter(
            (placeholder) => placeholder !== listPlaceholder && String(contextValue(context, placeholder) ?? "").trim().length === 0,
        );
        if (otherMissing.length > 0) {
            return [];
        }
        return items.flatMap((item) =>
            group.map((argument) =>
                argument.replace(PLACEHOLDER_PATTERN, (_, rawPlaceholder) => {
                    const placeholder = rawPlaceholder.trim();
                    return placeholder === listPlaceholder ? item : String(contextValue(context, placeholder) ?? "");
                }),
            ),
        );
    }
    if (missingRequiredPlaceholders(group, context).length > 0) {
        return [];
    }
    return group.map((argument) => interpolate(argument, context));
}

export function renderedCommand(command: CommandSpec, context: CommandContext): CommandSpec {
    const optionalArguments = (command.optionalArguments ?? []).flatMap((group) => expandGroup(group, context));
    const baseArguments = (command.arguments ?? []).flatMap((argument) => {
        const single = SINGLE_PLACEHOLDER_PATTERN.exec(String(argument ?? "").trim());
        if (single) {
            const list = contextPathList(context, single[1]!.trim());
            if (list !== undefined) {
                return list;
            }
        }
        return [interpolate(argument, context)];
    });
    return {
        executable: interpolate(command.executable, context),
        arguments: [...baseArguments, ...optionalArguments],
    };
}
export function displayCommand(command: CommandSpec, context: CommandContext): string {
    const rendered = renderedCommand(command, context);
    return commandLine(rendered.executable, rendered.arguments ?? []);
}
export function commandLine(executable: string, args: readonly string[]): string {
    return [executable, ...args].map(shellQuote).join(" ");
}
export function setupResultLine(result: LooseRecord): string {
    const status = result.status ?? (result.exitCode === 0 ? "ok" : "failed");
    const duration = formatDurationMs(result.durationMs);
    return `[${status}] ${result.label ?? result.id}${duration ? ` (${duration})` : ""}`;
}
export function formatDurationMs(value: unknown): string {
    const durationMs = Number(value);
    if (!Number.isFinite(durationMs) || durationMs < 0) {
        return "";
    }
    if (durationMs < 1000) {
        return `${(durationMs / 1000).toFixed(1)}s`;
    }
    const totalSeconds = Math.max(0, Math.round(durationMs / 1000));
    if (totalSeconds < 60) {
        return `${totalSeconds}s`;
    }
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    if (minutes < 60) {
        return seconds === 0 ? `${minutes}m` : `${minutes}m ${seconds}s`;
    }
    const hours = Math.floor(minutes / 60);
    const remainingMinutes = minutes % 60;
    return remainingMinutes === 0 ? `${hours}h` : `${hours}h ${remainingMinutes}m`;
}
export function shellQuote(value: unknown): string {
    const text = String(value ?? "");
    if (/^[A-Za-z0-9_./-]+$/.test(text)) {
        return text;
    }
    return `'${text.replaceAll("'", "'\\''")}'`;
}

function missingRequiredPlaceholders(values: unknown[], context: CommandContext): string[] {
    return placeholdersIn(values).filter((placeholder) => String(contextValue(context, placeholder) ?? "").trim().length === 0);
}
