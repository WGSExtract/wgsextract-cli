import { geneIobioBuild } from "../../../shared/gene-iobio.js";
import { exonNumbers, filteredVariants, geneFilters, type GeneDocument, type GeneFilter, type GeneNotice, type GeneTranscript, type GeneVariant } from "../../../shared/gene-page.js";
import { escapeAttribute, escapeHTML } from "./dom.js";

export type GeneText = (key: string, values?: Record<string, string | number>) => string;

const blank = "–";
const number = (value: number, digits = 0) => value.toLocaleString(undefined, { maximumFractionDigits: digits });
const percent = (value: number) => value.toLocaleString(undefined, { style: "percent", maximumFractionDigits: 1 });
const cell = (value: string | number | null | undefined) => value == null || value === "" ? blank : escapeHTML(String(value).replaceAll("_", " "));

export function renderGeneDocument(doc: GeneDocument, text: GeneText, transcriptIndex: number, filter: GeneFilter | "all", canView: boolean): string {
    const transcript = doc.transcripts.items[transcriptIndex];
    return `
      <section class="gene-summary" aria-labelledby="gene-summary-title">
        <h2 id="gene-summary-title">${escapeHTML(text("summary", { gene: doc.gene.symbol, build: doc.build, locus: doc.gene.locus }))}</h2>
        <p>${escapeHTML(text("geneSource", { source: doc.gene.source }))}</p>
        ${canView ? `<button type="button" class="action-button secondary compact" data-gene-viewer>${escapeHTML(text("open"))}</button>` : ""}
        ${geneIobioBuild(doc.build) ? `<button type="button" class="action-button secondary compact" data-gene-iobio>${escapeHTML(text("iobio.review"))}</button>` : ""}
      </section>
      ${renderNotices(doc.notices, text)}
      ${renderTranscripts(doc, transcript, transcriptIndex, text)}
      ${renderCoverage(doc, transcript, text)}
      ${renderVariants(doc, filter, text)}`;
}

function renderNotices(notices: GeneNotice[], text: GeneText): string {
    if (!notices.length) return "";
    const items = notices.map((notice) => {
        const key = notice.annotation ? `notice.${notice.code}.${notice.annotation}` : `notice.${notice.code}`;
        const command = notice.command ? `<code dir="ltr">${escapeHTML(notice.command)}</code>` : "";
        const then = notice.annotation && notice.command ? ` ${escapeHTML(text("noticeThen"))}` : "";
        return `<li>${escapeHTML(text(key))} ${command}${then}</li>`;
    }).join("");
    return `<section class="gene-notices" aria-labelledby="gene-notices-title"><h3 id="gene-notices-title">${escapeHTML(text("noticesTitle"))}</h3><ul>${items}</ul></section>`;
}

function renderTranscripts(doc: GeneDocument, transcript: GeneTranscript | undefined, selected: number, text: GeneText): string {
    if (!transcript) return "";
    const options = doc.transcripts.items.map((item, index) => `<option value="${index}"${index === selected ? " selected" : ""}>${escapeHTML(text("transcriptOption", {
        accession: item.accession, strand: item.strand, exons: item.exons.length,
        coding: item.cdsStart == null ? text("nonCoding") : text("coding"),
    }))}</option>`).join("");
    return `
      <section class="gene-transcripts" aria-labelledby="gene-transcripts-title">
        <h3 id="gene-transcripts-title">${escapeHTML(text("transcriptsTitle"))}</h3>
        <label>${escapeHTML(text("transcript"))} <select data-gene-transcript>${options}</select></label>
        ${renderDiagram(doc, transcript, text)}
        <p class="gene-source">${escapeHTML(text("transcriptSource", { source: doc.transcripts.source }))}</p>
      </section>`;
}

/** The transcript's exons along the gene, coloured by their coverage, with the variants as ticks. */
function renderDiagram(doc: GeneDocument, transcript: GeneTranscript, text: GeneText): string {
    const start = Math.min(doc.gene.start, transcript.start);
    const end = Math.max(doc.gene.end, transcript.end);
    const width = 1000;
    const x = (position: number) => ((position - start) / Math.max(1, end - start)) * width;
    const coverage = new Map(doc.coverage.exons.map((exon) => [`${exon.start}-${exon.end}`, exon]));
    const exons = transcript.exons.map(([first, last]) => {
        const stats = coverage.get(`${first}-${last}`);
        const kind = doc.coverage.status !== "ok" || !stats ? "unknown" : stats.below ? "below" : "callable";
        return `<rect class="gene-exon ${kind}" x="${x(first - 1).toFixed(1)}" y="14" width="${Math.max(2, x(last) - x(first - 1)).toFixed(1)}" height="20"></rect>`;
    }).join("");
    const ticks = doc.variants.rows.map((row) => typeof row.values.pos === "number"
        ? `<line class="gene-variant-tick${row.filters.length ? " flagged" : ""}" x1="${x(row.values.pos).toFixed(1)}" x2="${x(row.values.pos).toFixed(1)}" y1="0" y2="12"></line>` : "").join("");
    const label = text("diagram", { accession: transcript.accession });
    return `
      <svg class="gene-diagram" viewBox="0 0 ${width} 36" preserveAspectRatio="none" role="img" aria-label="${escapeAttribute(label)}">
        <line class="gene-axis" x1="${x(transcript.start - 1).toFixed(1)}" x2="${x(transcript.end).toFixed(1)}" y1="24" y2="24"></line>${exons}${ticks}
      </svg>
      <p class="gene-legend"><span class="callable">${escapeHTML(text("legendCallable"))}</span> <span class="below">${escapeHTML(text("legendBelow"))}</span> <span class="unknown">${escapeHTML(text("legendUnknown"))}</span> <span class="tick">${escapeHTML(text("legendVariant"))}</span></p>`;
}

function renderCoverage(doc: GeneDocument, transcript: GeneTranscript | undefined, text: GeneText): string {
    const { coverage } = doc;
    if (coverage.status !== "ok" || !coverage.gene) return "";
    const threshold = number(coverage.threshold, 1);
    const summary = text("coverageSummary", { mean: number(coverage.gene.mean, 1), fraction: percent(coverage.gene.callableFraction), threshold });
    if (!transcript) return `<section class="gene-coverage"><h3>${escapeHTML(text("coverageTitle"))}</h3><p>${escapeHTML(summary)}</p></section>`;
    const stats = new Map(coverage.exons.map((exon) => [`${exon.start}-${exon.end}`, exon]));
    const numbers = exonNumbers(transcript);
    const exons = transcript.exons.map(([start, end]) => ({ start, end, number: numbers.get(`${start}-${end}`) ?? 0, stats: stats.get(`${start}-${end}`) }))
        .sort((a, b) => a.number - b.number);
    const below = exons.filter((exon) => exon.stats?.below).length;
    const rows = exons.map((exon) => `<tr class="${exon.stats?.below ? "below" : ""}">
        <td>${exon.number}</td><td dir="ltr">${escapeHTML(`${doc.gene.chrom}:${number(exon.start)}-${number(exon.end)}`)}</td>
        <td>${exon.stats ? number(exon.stats.mean, 1) : blank}</td><td>${exon.stats ? number(exon.stats.min) : blank}</td>
        <td>${exon.stats ? percent(exon.stats.callableFraction) : blank}</td>
        <td>${escapeHTML(text(exon.stats?.below ? "below" : "callable"))}</td></tr>`).join("");
    return `
      <section class="gene-coverage" aria-labelledby="gene-coverage-title">
        <h3 id="gene-coverage-title">${escapeHTML(text("coverageTitle"))}</h3>
        <p>${escapeHTML(summary)} ${escapeHTML(text("exonsBelow", { count: below, total: exons.length, threshold }))}</p>
        <div class="table-scroll"><table class="gene-table">
          <thead><tr><th scope="col">${escapeHTML(text("col.exon"))}</th><th scope="col">${escapeHTML(text("col.position"))}</th><th scope="col">${escapeHTML(text("col.meanDepth"))}</th><th scope="col">${escapeHTML(text("col.lowestDepth"))}</th><th scope="col">${escapeHTML(text("col.callable", { threshold }))}</th><th scope="col">${escapeHTML(text("col.status"))}</th></tr></thead>
          <tbody>${rows}</tbody>
        </table></div>
      </section>`;
}

function renderVariants(doc: GeneDocument, filter: GeneFilter | "all", text: GeneText): string {
    const { variants } = doc;
    if (variants.status !== "ok") return "";
    const shown = filteredVariants(variants.rows, filter);
    const af = percent(variants.rareAF);
    const options = (["all", ...geneFilters] as const).map((name) => `<option value="${name}"${name === filter ? " selected" : ""}>${escapeHTML(text(`filter.${name}`, { af }))} (${filteredVariants(variants.rows, name).length})</option>`).join("");
    const heads = ["position", "change", "consequence", "impact", "hgvs", "zygosity", "depth", "clinvar", "gnomad", "acmg", "quality"]
        .map((name) => `<th scope="col">${escapeHTML(text(`col.${name}`))}</th>`).join("");
    const body = shown.length ? shown.map((row) => renderVariant(row)).join("") : `<tr><td colspan="11">${escapeHTML(text("noVariants"))}</td></tr>`;
    return `
      <section class="gene-variants" aria-labelledby="gene-variants-title">
        <h3 id="gene-variants-title">${escapeHTML(text("variantsTitle", { gene: doc.gene.symbol }))}</h3>
        <div class="gene-variant-tools">
          <label>${escapeHTML(text("filter"))} <select data-gene-filter>${options}</select></label>
          <button type="button" class="action-button secondary compact" data-gene-export>${escapeHTML(text("export"))}</button>
          <span data-gene-export-status role="status" aria-live="polite"></span>
        </div>
        <p>${escapeHTML(text("variantsCount", { shown: shown.length, total: variants.total }))}${variants.truncated ? ` ${escapeHTML(text("truncated", { count: variants.rows.length }))}` : ""}</p>
        ${variants.annotations.includes("acmg") ? `<p class="gene-research-note">${escapeHTML(text("acmgNote"))}</p>` : ""}
        <div class="table-scroll"><table class="gene-table"><thead><tr>${heads}</tr></thead><tbody>${body}</tbody></table></div>
        ${renderProvenance(doc, text)}
      </section>`;
}

function renderVariant(row: GeneVariant): string {
    const v = row.values;
    const hgvs = [v.hgvs_p, v.hgvs_c].filter((value) => typeof value === "string" && value).join(" ");
    const af = typeof v.gnomad_af === "number" ? v.gnomad_af.toPrecision(2) : v.gnomad_af;
    return `<tr class="${row.filters.length ? "flagged" : ""}">
      <td dir="ltr">${escapeHTML(`${v.chrom ?? ""}:${typeof v.pos === "number" ? number(v.pos) : ""}`)}</td>
      <td dir="ltr">${escapeHTML(`${v.ref ?? ""}>${v.alt ?? ""}`)}</td><td>${cell(v.consequence)}</td><td>${cell(v.impact)}</td>
      <td dir="ltr">${cell(hgvs)}</td><td>${cell(v.zygosity)}</td><td>${cell(v.depth)}</td><td>${cell(v.clinvar_significance)}</td>
      <td>${cell(af)}</td><td>${cell(v.acmg_class)}</td><td>${cell(v.filter)}</td></tr>`;
}

function renderProvenance(doc: GeneDocument, text: GeneText): string {
    const items = doc.variants.provenance.map((source) => `<li><strong>${escapeHTML(source.annotation)}</strong>${source.header.length
        ? `<ul>${source.header.map((line) => `<li><code dir="ltr">${escapeHTML(line)}</code></li>`).join("")}</ul>`
        : ` ${escapeHTML(text("provenanceUnknown"))}`}</li>`).join("");
    return `<details class="gene-provenance"><summary>${escapeHTML(text("provenanceTitle"))}</summary>
      <p>${escapeHTML(text("provenanceFile", { file: doc.variants.file }))}</p><ul>${items}</ul></details>`;
}
