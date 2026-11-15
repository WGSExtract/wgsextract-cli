// Maps `wgsextract --json info calculate-coverage --status` to the BAM page's Info Commands
// values: `coverage_done` is "true" when Detailed Coverage's reports are newer than the BAM/CRAM,
// so a run shows them again instead of rescanning.

export function values(document) {
    return { values: { coverage_done: String(document?.details?.current === true) } };
}
