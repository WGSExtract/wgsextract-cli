/* Inspect individual files, or assemble reports for a whole sample. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var T = WGSM.v2tools;
  var f = T.f;
  var O = WGSM.v2ops;
  var QUALITY = { href: "learn-sequencing.html#quality", label: "Read quality & its limits" };

  T.define({
    slug: "file-info", label: "File info & reference", ops: ["info", "bam/identify"], group: "Quality", keywords: "identify integrity header md5",
    lesson: QUALITY,
    desc: "Inspect an alignment’s reference build, integrity and sequencing metrics, or identify its reference from sequence names and lengths.",
    learn: "When a BAM or CRAM arrives from a lab, checking its header and basic metrics first confirms what it contains before other tools run; a reference mismatch skews later results.\n\nIdentify reference hashes only the header’s sequence names and lengths to match a genome build, without checksumming the read data itself. Turn on Detailed metrics for a deeper scan beyond the header.",
    accepts: ["reads"], file: "reginald", button: "Inspect", allowUnready: true,
    fields: [
      O.action("inspection", [["info", "File info"], ["bam/identify", "Identify reference"]], "info"),
      f.param("detailed", "Detailed metrics", "Do the more expensive sequencing checks", { when: function (c) { return c.v.inspection === "info"; } }),
      f.param("cache", "Save metrics cache", "Existing cached info is still read when this is off", { when: function (c) { return c.v.inspection === "info"; } }),
    ],
    requests: O.selected("inspection"),
    run: function (c) {
      return { timeText: c.v.detailed && c.v.inspection === "info" ? "minutes" : "seconds",
        makes: "Shows the detected reference and file metrics." };
    },
  });

  T.define({
    slug: "coverage-sample", label: "Quick coverage estimate", ops: ["info/coverage-sample"], group: "Quality", keywords: "breadth depth sample windows",
    lesson: { href: "learn-sequencing.html#depth", label: "Depth & breadth of coverage" },
    desc: "Samples about 1,000 windows across the alignment to estimate coverage instead of scanning the whole genome.",
    learn: "Gives a fast first look at how much of each chromosome was read, though window sampling can miss local gaps; run Coverage per chromosome when you need exact values.\n\nA high average does not guarantee every medically interesting gene is well covered.",
    accepts: ["reads"], file: "reginald", button: "Estimate coverage",
    fields: [f.region(), f.param("table_format", "Also a table", "Empty: no extra table", { as: "select", unset: "No table" })],
    run: function () { return { timeText: "minutes", makes: "Makes an estimated coverage report, labelled as sampled." }; },
  });

  T.define({
    slug: "chip-info", label: "Identify a DNA-test chip", ops: ["info/chip"], group: "Quality", keywords: "microarray vendor revision raw data",
    lesson: { href: "learn-files.html#kits", label: "DNA-test files & chip revisions" },
    desc: "Identify the company, chip and revision behind a consumer DNA-test raw-data file.",
    learn: "Consumer testing companies have changed microarrays several times, and each revision tests a different marker set; inspecting an older raw-data download tells you which chip version produced it. You can also list the built-in chip catalog on its own without opening a sample file.",
    accepts: ["kit"], file: "alexkit", button: "Identify",
    fields: [f.param("list_revisions", "List known revisions instead", "Show the chip catalog without inspecting this file")],
    requests: function (c, base) {
      return [c.v.list_revisions ? { op: "info/chip", params: { list_revisions: true } } : base("info/chip")];
    },
    run: function () { return { timeText: "seconds", makes: "Shows chip details; the raw-data file stays unchanged." }; },
  });

  T.define({
    slug: "comprehensive", label: "Whole-sample analysis", ops: ["analyze/comprehensive"], group: "Reports", keywords: "comprehensive pipeline significant haplogroups",
    lesson: { href: "learn-health.html#annotation", label: "Context, not a diagnosis" },
    desc: "Run the sample’s info, maternal and paternal haplogroups, variant calling, annotation and significant-variant filtering as one resumable analysis.",
    learn: "Chains the main single-sample steps into one resumable first pass, either calling SNPs and indels from the reads or analysing existing VCF paths you supply. Only installed tools and data can be used; check Reference data and Install first.\n\nHere “significant” only flags variants that pass the workflow’s review filters and never establishes a diagnosis. Check the output for skipped steps or warnings that leave gaps in what was checked; completed steps are reused automatically unless you turn on Restart.",
    accepts: ["reads"], file: "reginald", button: "Analyse sample",
    fields: [
      f.param("vcf_inputs", "Existing VCFs", "Optional comma-separated file paths; otherwise call SNPs and indels", { width: 420 }),
      f.param("skip_calling", "Skip variant calling", "Use supplied VCFs, or analyse only the alignment"),
    ],
    more: [f.param("restart", "Restart completed steps", "Recompute instead of reusing saved results"), f.threads()],
    run: function (c) {
      return { timeText: "hours on a whole genome", memGB: 8,
        makes: "Writes haplogroups, annotations and significant-variant VCFs to the sample’s folder." };
    },
  });

  T.define({
    slug: "batch-file", label: "Prepare a sample batch", ops: ["analyze/batch-gen"], group: "Reports", noInput: true,
    lesson: { href: "learn-files.html#bam", label: "Recognising read and variant files" },
    desc: "Scan a folder for BAMs, CRAMs and VCFs and write a CSV listing the samples for a comprehensive analysis.",
    learn: "Groups sequencing and variant files in a folder by filename stem to prepare a multi-sample table: for example, sample1.bam and sample1_sv.vcf.gz belong to sample1. Review the CSV before using it, especially when filenames are ambiguous.\n\nWriting the CSV neither launches analyses nor generates an executable script.",
    accepts: ["reads"], file: "reginald", button: "Make batch list",
    fields: [
      f.param("directory", "Folder to scan", "Includes subfolders, on the connected computer", { def: "{{bundleWorkspace}}/genomes", send: true, width: 420 }),
      f.param("output", "CSV to write", "", { def: "{{bundleWorkspace}}/batch.csv", send: true, width: 420 }),
    ],
    run: function (c) {
      return { timeText: "seconds", blocked: !c.v.directory || !c.v.output ? "Choose the scan folder and output CSV." : null,
        makes: "Writes a CSV with name, input and vcf columns. No analyses start." };
    },
  });

  T.define({
    slug: "multiqc", label: "Combined quality report", ops: ["qc/multiqc", "qc/multiqc-metrics"], group: "Quality", noInput: true,
    keywords: "multiqc fastqc manifests metrics", lesson: QUALITY,
    desc: "Gather quality results from a folder into one MultiQC report, or export WGS Extract metrics for MultiQC.",
    learn: "Once quality checks have run on several files, MultiQC compiles existing FastQC reports, bcftools stats and WGS Extract result manifests into one HTML summary without rerunning those checks.\n\nChoosing Export metrics only writes custom-content JSON files that MultiQC can read elsewhere, so MultiQC need not be installed here.",
    accepts: ["reads"], file: "reginald", button: "Build report",
    fields: [
      O.action("quality_output", [["qc/multiqc", "MultiQC HTML report"], ["qc/multiqc-metrics", "Export metrics only"]], "qc/multiqc"),
      f.param("input", "Results folder", "A folder containing reports and result manifests", { def: "{{bundleWorkspace}}/genomes", send: true, width: 420 }),
      f.param("force", "Replace an existing report", "Allow overwriting MultiQC’s previous report"),
    ],
    requests: O.selected("quality_output"),
    run: function (c) {
      return { timeText: "minutes", memGB: 2, blocked: !c.v.input ? "Choose the results folder." : null,
        makes: c.v.quality_output === "qc/multiqc" ? "Makes multiqc_report.html. MultiQC must already be installed." : "Makes wgsextract_*_mqc.json custom-content files." };
    },
  });
})();
