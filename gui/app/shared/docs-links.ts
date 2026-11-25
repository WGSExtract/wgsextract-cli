/**
 * Canonical rendered-HTML documentation URLs (https://wgse.bio/wgsextract-cli/...)
 * and safe inline linkification for error banners, result notices, and terminal logs.
 */

export const SITE_BASE_URL = "https://wgse.bio/wgsextract-cli";
export const CLI_DOCS_BASE_URL = `${SITE_BASE_URL}/docs`;
export const APP_DOCS_BASE_URL = `${SITE_BASE_URL}/app/docs`;
export const APP_DEVDOCS_BASE_URL = `${SITE_BASE_URL}/app/devdocs`;

const htmlEscapes: Record<string, string> = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
};

function escapeHTML(value: string): string {
    return value.replace(/[&<>"']/g, (ch) => htmlEscapes[ch] ?? ch);
}

/** Map a repository doc path (docs/*.md, gui/docs/*.md, gui/devdocs/*.md) to its rendered wgse.bio HTML URL. */
export function docUrl(docPath: string, fragment?: string): string {
    const hashIndex = docPath.indexOf("#");
    const rawPath = hashIndex >= 0 ? docPath.slice(0, hashIndex) : docPath;
    const frag = fragment ?? (hashIndex >= 0 ? docPath.slice(hashIndex + 1) : "");
    const clean = rawPath.trim().replace(/^\.?\//, "");
    let base = CLI_DOCS_BASE_URL;
    let rel = clean;
    if (clean.startsWith("gui/devdocs/")) {
        base = APP_DEVDOCS_BASE_URL;
        rel = clean.slice("gui/devdocs/".length);
    }
    else if (clean.startsWith("gui/docs/")) {
        base = APP_DOCS_BASE_URL;
        rel = clean.slice("gui/docs/".length);
    }
    else if (clean.startsWith("docs/")) {
        base = CLI_DOCS_BASE_URL;
        rel = clean.slice("docs/".length);
    }
    const htmlRel = rel === "README.md"
        ? "index.html"
        : rel.endsWith(".md")
            ? `${rel.slice(0, -3)}.html`
            : rel.endsWith(".html")
                ? rel
                : `${rel}.html`;
    return frag ? `${base}/${htmlRel}#${frag}` : `${base}/${htmlRel}`;
}

// Matches either an explicit wgse.bio URL or a bare repo doc reference (docs/*.md, gui/docs/*.md, gui/devdocs/*.md).
const docLinkPattern = /(?:https:\/\/wgse\.bio\/[^\s<>()'"\]]+|(?<![A-Za-z0-9_./:-])(?:gui\/(?:docs|devdocs)|docs)\/[A-Za-z0-9_./-]+\.md(?:#[A-Za-z0-9_-]+)?)/g;

function trimTrailingPunctuation(raw: string): { core: string; trailing: string } {
    let end = raw.length;
    while (end > 0 && ".,;:!?".includes(raw[end - 1] ?? "")) {
        end -= 1;
    }
    return { core: raw.slice(0, end), trailing: raw.slice(end) };
}

/**
 * HTML-escapes plain text and turns wgse.bio URLs and bare repo doc paths (e.g. docs/privacy.md)
 * into clickable external links that open the rendered HTML docs in the user's browser.
 */
export function renderTextWithDocLinks(value: unknown): string {
    const text = String(value ?? "");
    if (!text) {
        return "";
    }
    let out = "";
    let lastIndex = 0;
    docLinkPattern.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = docLinkPattern.exec(text)) !== null) {
        const start = match.index;
        const { core, trailing } = trimTrailingPunctuation(match[0]);
        out += escapeHTML(text.slice(lastIndex, start));
        const href = core.startsWith("https://") ? core : docUrl(core);
        out += `<a class="doc-link" href="${escapeHTML(href)}" target="_blank" rel="noopener noreferrer">${escapeHTML(core)}</a>`;
        if (trailing) {
            out += escapeHTML(trailing);
        }
        lastIndex = start + match[0].length;
    }
    out += escapeHTML(text.slice(lastIndex));
    return out;
}
