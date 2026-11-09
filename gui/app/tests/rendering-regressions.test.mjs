import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  checkedOptionsForContext, hydrateRows, initialCheckedOptions, initialFieldValues,
  parseFlatToml, renderedCommand,
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

const wgsPages = ["microarray", "extract", "fastq", "info-bam", "ancestry", "vcf", "settings", "library", "genomes"]
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
    "microarray", "--input", "sample.bam", "--ref", "ref.fa", "--formats", "23andme_v35,23andme_v5,all",
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

  test("WGSExtract AADR 2M extraction is an opt-in and its metadata ships with the library", () => {
    const page = wgsPages.find((item) => item.id === "microarray");
    const formats = page.sections.flatMap((section) => section.controls ?? [])
      .find((control) => control.id === "microarray_formats");
    assert.ok(formats.options.some((option) => option.id === "aadr_2m"), "2M is selectable");
    assert.deepEqual(wgsCommand("microarray", "microarray-generate", {}, {
      microarray_formats: ["aadr_2m"],
    }), [
      "microarray", "--input", "sample.bam", "--ref", "ref.fa", "--formats", "aadr_2m",
    ]);
    const library = wgsPages.find((item) => item.id === "library");
    const libraryActions = library.sections.flatMap((section) => section.actions ?? []);
    assert.ok(
      !libraryActions.some((action) => action.command.arguments.includes("aadr")),
      "ref bootstrap installs the AADR 2M metadata; no Library button does",
    );
    assert.ok(!wgsCommand("microarray", "microarray-generate").includes("aadr_2m"));
  });
  assert.deepEqual(args.slice(5), [
    "--formats", "aadr_1240k,aadr_ho,myheritage_gsa_2025",
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
  assert.ok(!wgsCommand("extract", "vcf-snp", {}, choices).includes("--scatter"));
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
  assert.equal(action.command.executable, "{{bundleRoot}}/scripts/fastqc-reads.sh");
  assert.deepEqual(wgsCommand("fastq", "fastqc", { fastq_r1: "reads R1.fq.gz" }), ["reads R1.fq.gz"]);
  assert.deepEqual(wgsCommand("fastq", "fastqc", { fastq_r1: "r1.fq.gz", fastq_r2: "r2.fq.gz", out_dir: "qc out" }, {
    fastqc_multiqc: ["--multiqc"],
  }), ["r1.fq.gz", "--r2", "r2.fq.gz", "--outdir", "qc out", "--multiqc"]);
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
  assert.deepEqual(section.dataSource, { path: "scripts/coverage-state.py", arguments: ["{{bam_path}}", "{{out_dir}}"] });
  assert.deepEqual(section.actions.slice(0, 5).map((action) => action.id), [
    "basic-info", "detailed-info", "coverage-sample", "calculate-coverage", "calculate-coverage-shown",
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
  const genomes = wgsPages.find((item) => item.id === "genomes");
  const actions = [
    ...library.sections.find((section) => section.id === "databases-tools").actions,
    ...genomes.sections.find((section) => section.id === "test-genome-data").actions,
  ];
  const shownWhen = (action, value) => (action.visibleWhen ?? [])
    .filter((rule) => rule.equals === value).map((rule) => rule.placeholder);
  const installs = actions.filter((action) => /-(download|install)$/.test(action.id));
  assert.ok(installs.length >= 8);
  for (const install of installs) {
    const [placeholder] = shownWhen(install, "false");
    assert.ok(placeholder, `${install.id} hides once installed`);
    const remove = actions.find((action) => shownWhen(action, "true").includes(placeholder)
      && action.role === "destructive");
    assert.ok(remove, `${install.id} has a delete for ${placeholder}`);
    assert.ok(remove.confirm?.message, `${remove.id} asks first`);
    assert.ok(remove.command.arguments.includes("--delete") || remove.command.arguments.includes("delete"),
      `${remove.id} runs a delete`);
  }
  assert.ok(!JSON.stringify(wgsPages).includes("vep_cache_path"), "the VEP cache lives in the library");
});
