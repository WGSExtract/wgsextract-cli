import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  checkedOptionsForContext, hydrateRows, initialCheckedOptions, initialFieldValues,
  optionDisabledReason, parseFlatToml, renderedCommand, withoutDisabledOptions,
} from "../dist/shared/rendering.js";

test("parses flat TOML inline comments outside quoted strings", () => {
  assert.deepEqual({ ...parseFlatToml('plain = value # comment\nquoted = "value # not comment" # comment\n') }, {
    plain: "value",
    quoted: "value # not comment",
  });
});

test("hydrates whitespace-only row metadata as empty", () => {
  const rows = hydrateRows({ rowTemplate: { id: "one", title: "One", status: "   " }, items: [{}] });

  assert.equal(rows[0].status, undefined);
});

const wgsPages = ["microarray", "extract", "fastq", "info-bam", "ancestry", "vcf", "settings", "library", "datasets"]
  .map((id) => JSON.parse(readFileSync(new URL(`../../examples/WGSExtract/pages/${id}.json`, import.meta.url), "utf8")));
const wgsManifest = { pages: wgsPages };

function wgsCommand(pageID, actionID, fieldValues = {}, checkedOptions = {}) {
  const page = wgsPages.find((item) => item.id === pageID);
  const action = page.sections.flatMap((section) => section.actions ?? []).find((item) => item.id === actionID);
  return renderedCommand(action.command, {
    bundleRootPath: "bundle",
    fieldValues: { ...initialFieldValues(wgsManifest), bam_path: "sample.bam", ref_fasta: "ref.fa", ...fieldValues },
    checkedOptions: checkedOptionsForContext({ ...initialCheckedOptions(wgsManifest), ...checkedOptions }),
  }).arguments;
}

test("WGSExtract new options do not change default argv or selected formats", () => {
  assert.deepEqual(wgsCommand("microarray", "microarray-generate"), [
    "microarray", "--input", "sample.bam", "--formats", "23andme_v35,23andme_v5,all", "--ref", "ref.fa",
  ]);
  for (const [page, action, flag] of [
    ["extract", "region-y", "--poz"], ["extract", "region-y", "--ybrowse"],
    ["extract", "unmapped", "--fastq"], ["extract", "vcf-gatk", "--scatter"],
    ["extract", "realign", "--keep-fastq"], ["ancestry", "run-yleaf", "--tree-links"],
    ["fastq", "multiqc", "--force"],
  ]) {
    assert.ok(!wgsCommand(page, action).includes(flag));
  }
});

test("WGSExtract variant calling: type checkboxes, gVCF and Merged drive one vcf call", () => {
  // Default: SNP and InDel checked, one file per type, and no gVCF even if config.toml asks for one.
  assert.deepEqual(wgsCommand("extract", "vcf-call"), [
    "vcf", "call", "--input", "sample.bam", "--types", "indel,snp", "--reference-evidence=none",
  ]);
  const all = wgsCommand("extract", "vcf-call", {}, {
    vcf_call_types: ["snp", "indel", "sv", "cnv"], vcf_merge: ["--merge"], vcf_gvcf: ["--reference-evidence=gvcf"],
  });
  assert.deepEqual(all.slice(0, 6), ["vcf", "call", "--input", "sample.bam", "--types", "cnv,indel,snp,sv"]);
  assert.ok(all.includes("--merge") && all.includes("--reference-evidence=gvcf"), all.join(" "));
  // gVCF also reaches the callers that write one; FreeBayes has none.
  const gvcf = { vcf_gvcf: ["--reference-evidence=gvcf"] };
  for (const action of ["vcf-call", "vcf-gatk", "vcf-deepvariant"]) {
    // The checked box comes after the explicit none, so it wins.
    const argv = wgsCommand("extract", action, {}, gvcf);
    assert.ok(argv.indexOf("--reference-evidence=gvcf") > argv.indexOf("--reference-evidence=none"), action);
    assert.ok(wgsCommand("extract", action).includes("--reference-evidence=none"), action);
  }
  assert.ok(!wgsCommand("extract", "vcf-freebayes", {}, gvcf).includes("--reference-evidence=gvcf"));
});

test("WGSExtract Run Yleaf sends only native y-haplogroup flags (#538)", () => {
  assert.deepEqual(wgsCommand("ancestry", "run-yleaf"), ["lineage", "y-haplogroup", "--input", "sample.bam"]);
  assert.deepEqual(wgsCommand("ancestry", "run-yleaf", { yleaf_tree: "isogg" }, {
    yleaf_ancient: ["--ancient"], yleaf_no_ref_inference: ["--no-ref-inference"],
  }).slice(4), ["--tree", "isogg", "--ancient", "--no-ref-inference"]);
});

test("WGSExtract opt-in policies render flags and preserve manifest paths with spaces", () => {
  const args = wgsCommand("microarray", "microarray-generate", {
    microarray_min_gq: "20", microarray_min_dp: "5",
    aadr_sample_id: "research_sample", aadr_sex: "U",
    indel_format: "myheritage_gsa_2025", indel_manifest: "manifests/matching chip.csv",
  }, {
    microarray_formats: ["myheritage_gsa_2025", "aadr_1240k", "aadr_ho"],
    microarray_strand_ambiguous: ["no-call"], microarray_resolve_rsids: ["--resolve-rsids"],
    microarray_absent_no_call: ["--no-assume-hom-ref"],
    microarray_exclude_filtered: ["--exclude-filtered"], microarray_call_reasons: ["--call-reasons"],
  });

  test("WGSExtract AADR 2M is a Reference Data download that unlocks the 2M format", () => {
    const page = wgsPages.find((item) => item.id === "microarray");
    const section = page.sections.find((item) => item.id === "microarray-formats");
    assert.deepEqual(section.dataSource.arguments, ["ref", "status", "--values"], "the section reads library.aadrInstalled");
    const formats = section.controls.find((control) => control.id === "microarray_formats");
    const twoM = formats.options.find((option) => option.id === "aadr_2m");
    assert.ok(twoM.disabledTooltip, "a disabled 2M says why");
    const withPanel = (installed) => ({
      fieldValues: { "library.aadrInstalled": installed }, configValues: {}, rowValues: {},
      checkedOptions: { microarray_formats: "aadr_1240k,aadr_2m" },
    });
    assert.ok(optionDisabledReason(twoM, withPanel("false")), "2M is disabled without the panel");
    assert.ok(optionDisabledReason(twoM, withPanel("")), "2M is disabled until the status is known");
    assert.equal(optionDisabledReason(twoM, withPanel("true")), undefined, "2M is selectable once installed");
    assert.equal(withoutDisabledOptions(withPanel("false"), [formats]).checkedOptions.microarray_formats, "aadr_1240k");
    assert.equal(withoutDisabledOptions(withPanel("true"), [formats]).checkedOptions.microarray_formats, "aadr_1240k,aadr_2m");
    assert.deepEqual(wgsCommand("microarray", "microarray-generate", {}, {
      microarray_formats: ["aadr_2m"],
    }), [
      "microarray", "--input", "sample.bam", "--formats", "aadr_2m", "--ref", "ref.fa",
    ]);
    const library = wgsPages.find((item) => item.id === "library");
    const libraryActions = library.sections.flatMap((section) => [
      ...(section.actions ?? []),
      ...(section.controls ?? []).flatMap((control) => control.rowActions ?? []),
    ]);
    const aadrArgs = libraryActions
      .filter((action) => action.command.arguments.includes("aadr"))
      .map((action) => action.command.arguments.slice(0, 3));
    assert.deepEqual(aadrArgs, [["ref", "aadr", "--install"], ["ref", "aadr", "--delete"]]);
    assert.ok(!wgsCommand("microarray", "microarray-generate").includes("aadr_2m"));
  });
  assert.deepEqual(args.slice(3), [
    "--formats", "aadr_1240k,aadr_ho,myheritage_gsa_2025",
    "--ref", "ref.fa",
    "--min-gq", "20", "--min-dp", "5", "--strand-ambiguous", "no-call",
    "--resolve-rsids", "--no-assume-hom-ref", "--exclude-filtered", "--call-reasons",
    "--aadr-sample-id", "research_sample", "--aadr-sex", "U",
    "--indel-manifest", "myheritage_gsa_2025=manifests/matching chip.csv",
  ]);
  assert.ok(!wgsCommand("microarray", "microarray-generate", { indel_format: "myheritage_gsa_2025" }).includes("--indel-manifest"));
  assert.ok(!wgsCommand("microarray", "microarray-generate", { indel_manifest: "chip.csv" }).includes("--indel-manifest"));
});

test("WGSExtract region buttons write each checked format for their region", () => {
  assert.deepEqual(wgsCommand("extract", "region-ymt"), [
    "extract", "region", "--input", "sample.bam", "--target", "ymt", "--formats", "bam",
  ]);
  assert.deepEqual(wgsCommand("extract", "region-autox", { out_dir: "out" }, { extract_formats: ["vcf", "fasta", "bam"] }), [
    "extract", "region", "--input", "sample.bam", "--target", "autox", "--formats", "bam,fasta,vcf", "--outdir", "out",
  ]);
});

test("WGSExtract targeted opt-ins are restricted to the relevant commands", () => {
  const choices = {
    extract_poz: ["--poz"], extract_ybrowse: ["--ybrowse"], extract_unmapped_fastq: ["--fastq"],
    gatk_scatter: ["--scatter"], realign_keep_fastq: ["--keep-fastq"], lineage_tree_links: ["--tree-links"],
  };
  for (const [page, action, flag] of [
    ["extract", "region-y", "--poz"], ["extract", "region-y", "--ybrowse"],
    ["extract", "unmapped", "--fastq"], ["extract", "vcf-gatk", "--scatter"],
    ["extract", "realign", "--keep-fastq"], ["ancestry", "run-yleaf", "--tree-links"],
    ["ancestry", "run-haplogrep", "--tree-links"],
  ]) {
    assert.ok(wgsCommand(page, action, {}, choices).includes(flag));
  }
  assert.ok(!wgsCommand("extract", "custom", {}, choices).some((arg) => ["--poz", "--ybrowse", "--fastq"].includes(arg)));
  // --poz and --ybrowse apply only to Y-only; the CLI refuses them elsewhere.
  for (const action of ["region-mt", "region-ymt", "region-autox"]) {
    assert.ok(!wgsCommand("extract", action, {}, choices).some((arg) => ["--poz", "--ybrowse"].includes(arg)));
  }
  assert.ok(!wgsCommand("extract", "vcf-call", {}, choices).includes("--scatter"));
  assert.deepEqual(wgsCommand("fastq", "multiqc", {
    multiqc_input: "reports with spaces", out_dir: "new reports",
  }, { multiqc_force: ["--force"] }), [
    "qc", "multiqc", "--input", "reports with spaces", "--outdir", "new reports", "--force",
  ]);
  assert.deepEqual(wgsCommand("fastq", "multiqc-directory", {
    multiqc_directory: "directory with spaces", out_dir: "new reports",
  }), [
    "qc", "multiqc", "--input", "directory with spaces", "--outdir", "new reports",
  ]);
});

test("WGSExtract FastQC runs on R1, adds R2 when chosen and merges with MultiQC only when asked", () => {
  const page = wgsPages.find((item) => item.id === "fastq");
  const action = page.sections.flatMap((section) => section.actions ?? []).find((item) => item.id === "fastqc");
  assert.equal(action.command.executable, "{{bundleRoot}}/scripts/run-wgsextract.sh");
  assert.deepEqual(wgsCommand("fastq", "fastqc", { fastq_r1: "reads R1.fq.gz" }), ["qc", "fastqc", "--input", "reads R1.fq.gz"]);
  assert.deepEqual(wgsCommand("fastq", "fastqc", { fastq_r1: "r1.fq.gz", fastq_r2: "r2.fq.gz", out_dir: "qc out" }, {
    fastqc_multiqc: ["--multiqc"],
  }), ["qc", "fastqc", "--input", "r1.fq.gz", "--r2", "r2.fq.gz", "--outdir", "qc out", "--multiqc"]);
});

test("WGSExtract Run Haplogrep uses the Haplogrep installed with the CLI unless a path is set", () => {
  assert.deepEqual(wgsCommand("ancestry", "run-haplogrep"), ["lineage", "mt-haplogroup", "--input", "sample.bam"]);
  assert.deepEqual(wgsCommand("ancestry", "run-haplogrep", { haplogrep_path: "tools/haplogrep3" }).slice(4, 6), [
    "--haplogrep-path", "tools/haplogrep3",
  ]);
});

test("WGSExtract Realign re-aligns the BAM/CRAM chosen in Inputs", () => {
  assert.deepEqual(wgsCommand("extract", "realign", { realign_ref: "new.fa", fastq_r1: "ignored.fq" }), [
    "realign", "--input", "sample.bam", "--ref", "new.fa",
  ]);
});

test("WGSExtract Detailed Coverage adds the depth bins and, once run, only shows them again", async () => {
  const { isActionVisible } = await import("../dist/shared/rendering.js");
  const page = wgsPages.find((item) => item.id === "info-bam");
  const section = page.sections.find((item) => item.id === "info-commands");
  // No BAM chosen leaves --input out (Windows PowerShell 5.1 drops an empty argument): nothing is current.
  assert.deepEqual(section.dataSource.arguments, ["--json", "info", "calculate-coverage", "--status"]);
  assert.deepEqual(section.dataSource.optionalArguments, [["--input", "{{bam_path}}"], ["--outdir", "{{out_dir}}"]]);
  const { values } = await import("../../examples/WGSExtract/transforms/coverage-status.mjs");
  assert.deepEqual(values({ details: { current: true } }), { values: { coverage_done: "true" } });
  assert.deepEqual(values({ details: { current: false } }), { values: { coverage_done: "false" } });
  assert.deepEqual(section.actions.slice(0, 6).map((action) => action.id), [
    "basic-info", "detailed-info", "view-bam", "coverage-sample", "calculate-coverage", "calculate-coverage-shown",
  ]);
  const [run, shown] = ["calculate-coverage", "calculate-coverage-shown"].map((id) => section.actions.find((action) => action.id === id));
  for (const action of [run, shown]) {
    assert.deepEqual(wgsCommand("info-bam", action.id), ["info", "calculate-coverage", "--with-bins", "--input", "sample.bam"]);
    assert.equal(action.serverOperation.parameters.with_bins, true);
  }
  assert.equal(run.estimatedDurationMinutes, 120);
  assert.equal(shown.estimatedDurationMinutes, undefined);
  assert.notEqual(shown.tooltip, run.tooltip);

  const visible = (done) => section.actions
    .filter((action) => isActionVisible(action, { fieldValues: { coverage_done: done } }))
    .map((action) => action.id)
    .filter((id) => id.startsWith("calculate-coverage"));
  assert.deepEqual(visible("false"), ["calculate-coverage"]);
  assert.deepEqual(visible(""), ["calculate-coverage"]);
  assert.deepEqual(visible("true"), ["calculate-coverage-shown"]);
});

test("WGSExtract BAM Info Commands puts Viewer after Detailed Info in Basic mode and moves Coverage to a second row", async () => {
  globalThis.localStorage = { getItem() { return null; }, setItem() {} };
  globalThis.window = { innerHeight: 900 };
  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderSection } = await import("../dist/web/src/client/view.js");
  const page = wgsPages.find((item) => item.id === "info-bam");
  const section = page.sections.find((item) => item.id === "info-commands");
  const viewer = section.actions.find((action) => action.id === "view-bam");

  assert.equal(viewer.viewerInput, "bam_path");
  assert.notEqual(viewer.advancedOnly, true);
  assert.equal(viewer.row, undefined);
  for (const id of ["coverage-sample", "calculate-coverage", "calculate-coverage-shown", "coverage-by-bed"]) {
    assert.equal(section.actions.find((action) => action.id === id).row, 2, `${id} belongs on the second row`);
  }

  for (const uiMode of ["basic", "advanced"]) {
    Object.assign(state, createInitialState(), {
      manifest: { id: "wgs-extract", pages: wgsPages },
      uiMode,
      fieldValues: { bam_path: "/genomes/sample.bam" },
      labels: { actionMissingInputsFormat: "Missing: %{inputs}", actionUnavailableTitle: "Unavailable" },
    });
    if (uiMode === "advanced") {
      state.toggledSections.add("info-bam/info-commands");
    }
    const html = renderSection(section, page);
    const rows = [...html.matchAll(/<div class="action-row">([\s\S]*?)<\/div>/g)].map(([, body]) =>
      [...body.matchAll(/data-action-id="([^"]+)"/g)].map(([, id]) => id),
    );
    assert.deepEqual(rows[0], ["basic-info", "detailed-info", "view-bam"], `${uiMode} row 1`);
    assert.deepEqual(
      rows[1],
      uiMode === "basic" ? ["calculate-coverage"] : ["coverage-sample", "calculate-coverage", "coverage-by-bed"],
      `${uiMode} row 2`,
    );
    assert.match(html, /data-action-id="view-bam"\s+data-viewer-input="bam_path"/);
    assert.doesNotMatch(html, /data-action-id="view-bam"[^>]*disabled/);
  }

  state.fieldValues = { bam_path: "" };
  const disabledHtml = renderSection(section, page);
  assert.match(disabledHtml, /data-action-id="view-bam"[^>]*disabled/);
});

test("WGSExtract Advanced CLI actions pass only the options that are set", () => {
  assert.deepEqual(wgsCommand("info-bam", "bam-merge", { merge_input_1: "lane1.bam", merge_input_2: "lane2.bam" }), [
    "bam", "merge", "lane1.bam", "lane2.bam",
  ]);
  assert.deepEqual(wgsCommand("info-bam", "bam-merge", { merge_input_1: "a.bam", merge_input_2: "b.bam", merge_input_3: "c.bam", merge_sample: "HG002" }, { merge_cram: ["--cram"] }), [
    "bam", "merge", "a.bam", "b.bam", "c.bam", "--cram", "--sample", "HG002",
  ]);
  assert.deepEqual(wgsCommand("extract", "typing-str", { str_sex: "female" }), ["typing", "str", "--input", "sample.bam", "--sex", "female"]);
  assert.deepEqual(wgsCommand("extract", "align", { fastq_r1: "r1.fq", align_platform: "ont" }, { align_gpu: ["--gpu"] }), [
    "align", "--r1", "r1.fq", "--ref", "ref.fa", "--format", "BAM", "--platform", "ont", "--gpu",
  ]);
  assert.deepEqual(wgsCommand("ancestry", "run-haplogrep", { haplogrep_tree: "mitotree-rcrs", qc_min_depth: "5" }), [
    "lineage", "mt-haplogroup", "--input", "sample.bam", "--tree", "mitotree-rcrs", "--qc-min-depth", "5",
  ]);
});

test("WGSExtract Library install buttons become confirmed deletes once installed", () => {
  const library = wgsPages.find((item) => item.id === "library");
  const datasets = wgsPages.find((item) => item.id === "datasets");
  const refDataTable = library.sections.find((section) => section.id === "databases-tools")
    .controls.find((control) => control.id === "reference_data");
  const rowActions = refDataTable.rowActions;
  const rowMatch = (action, placeholder) => (action.visibleWhen ?? []).find((rule) => rule.placeholder === placeholder)?.equals;
  const deletableRows = ["gene-map", "dbsnp-hg38", "dbsnp-hg19", "annotation-vcf", "aadr-2m", "ybrowse", "vep", "spliceai", "alphamissense", "pharmgkb"];
  for (const rowID of deletableRows) {
    const install = rowActions.find((action) => rowMatch(action, "row.id") === rowID && rowMatch(action, "row.status") === "missing");
    assert.ok(install, `${rowID} has an install/download row action`);
    const remove = rowActions.find((action) => rowMatch(action, "row.id") === rowID && rowMatch(action, "row.status") === "installed" && action.role === "destructive");
    assert.ok(remove, `${rowID} has a destructive delete row action once installed`);
    assert.ok(remove.confirm?.message, `${remove.id} asks first`);
    assert.ok(remove.command.arguments.includes("--delete") || remove.command.arguments.includes("delete"), `${remove.id} runs a delete`);
  }
  // Required bootstrap has no delete button once installed.
  assert.equal(rowActions.some((action) => rowMatch(action, "row.id") === "bootstrap" && action.role === "destructive"), false);
  // Test dataset on Datasets page still toggles between install and confirmed delete.
  const testActions = datasets.sections.find((section) => section.id === "test-dataset").actions;
  const testDelete = testActions.find((action) => action.id === "test-dataset-delete");
  assert.ok(testDelete?.confirm?.message && testDelete.role === "destructive");
  assert.ok(!JSON.stringify(wgsPages).includes("vep_cache_path"), "the VEP cache lives in the library");
});
