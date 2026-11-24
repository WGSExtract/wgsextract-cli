// Shared helper to format multi-line row-name tooltips consistently across
// Reference Data, Reference Genomes, and Tools tables.

/** Format a concise multi-line tooltip covering summary, source, org, and license. */
export function formatProvenanceTooltip({ summary = "", source = "", organization = "", license = "", extras = [] }) {
    const lines = [];
    if (summary) lines.push(summary);
    if (source) lines.push(`Source: ${source}`);
    if (organization) lines.push(`Organization: ${organization}`);
    if (license) lines.push(`License: ${license}`);
    for (const [label, value] of extras) {
        if (value) lines.push(`${label}: ${value}`);
    }
    return lines.join("\n");
}
