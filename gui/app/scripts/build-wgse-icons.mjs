import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";

const source = new URL("../../resources/BuiltinIcons/wgse/", import.meta.url);
const target = new URL("../dist/web/src/client/", import.meta.url);
const root = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">';
const geometry = {
    circle: ["cx", "cy", "r"],
    ellipse: ["cx", "cy", "rx", "ry"],
    path: ["d"],
    rect: ["x", "y", "width", "height", "rx", "ry"],
};
const presentation = {
    fill: /^(?:none|currentColor)$/,
    stroke: /^(?:none|currentColor)$/,
    "fill-opacity": /^(?:0?(?:\.\d+)?|1)$/,
    "stroke-opacity": /^(?:0?(?:\.\d+)?|1)$/,
    "stroke-dasharray": /^[\d. ]+$/,
};

// The accepted subset is flat, inert SVG geometry, not arbitrary SVG markup.
function inlineIcon(svg, name) {
    const text = svg.trim();
    if (!text.startsWith(root) || !text.endsWith("</svg>")) {
        throw new Error(`${name}: use the standard WGS Extract svg root`);
    }
    const body = text.slice(root.length, -"</svg>".length).trim();
    const elements = body.match(/<(?:circle|ellipse|path|rect)(?: [a-z-]+="[^"<>]*")+ *\/>/g);
    if (!elements?.length || body.replaceAll(/\s/g, "") !== elements.join("").replaceAll(/\s/g, "")) {
        throw new Error(`${name}: only self-closing SVG geometry is allowed`);
    }
    for (const element of elements) {
        const tag = element.match(/^<\w+/)[0].slice(1);
        const seen = new Set();
        for (const [, key, value] of element.matchAll(/ ([a-z-]+)="([^"]*)"/g)) {
            const rule = Object.hasOwn(presentation, key) ? presentation[key]
                : !geometry[tag].includes(key) ? undefined
                    : key === "d" ? /^[Mm][MmZzLlHhVvCcSsQqTtAaEe\d.,+ -]+$/
                        : /^-?(?:\d+(?:\.\d+)?|\.\d+)$/;
            if (!rule?.test(value) || seen.has(key)) {
                throw new Error(`${name}: invalid ${tag} attribute ${key}="${value}"`);
            }
            seen.add(key);
        }
        if (geometry[tag].some((key) => !seen.has(key) && !(tag === "rect" && (key === "rx" || key === "ry")))) {
            throw new Error(`${name}: incomplete ${tag} geometry`);
        }
    }
    return `${root.slice(0, -1)} class="wgse-icon" aria-hidden="true" focusable="false">${elements.join("")}</svg>`;
}

const icons = {};
for (const filename of (await readdir(source)).sort()) {
    if (!filename.endsWith(".svg")) continue;
    const name = filename.slice(0, -4);
    if (!/^[a-z][a-z0-9-]*$/.test(name)) throw new Error(`${filename}: invalid icon name`);
    icons[name] = inlineIcon(await readFile(new URL(filename, source), "utf8"), filename);
}
await mkdir(target, { recursive: true });
await writeFile(new URL("wgse-icons.js", target), `export const wgseIcons = ${JSON.stringify(icons)};\n`);
