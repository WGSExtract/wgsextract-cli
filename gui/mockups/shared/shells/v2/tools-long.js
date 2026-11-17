/* Signal files, adaptive-sampling panels, and non-human read inputs. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var T = WGSM.v2tools;
  var f = T.f;
  var LESSON = { href: "learn-sequencing.html#platforms", label: "Short & long reads" };

  T.define({
    slug: "basecall", label: "Basecall Nanopore signal", ops: ["basecall"], group: "Long reads", noInput: true,
    keywords: "ont dorado pod5 ubam methylation", lesson: LESSON,
    desc: "Turn raw Nanopore POD5 signal into an unaligned BAM with Dorado, optionally retaining base-modification calls.",
    learn: "Use it when you have the original signal, or want to re-basecall with a newer or more accurate model. A FASTQ or an aligned BAM cannot replace POD5 signal.\n\nDorado must already be installed. Fast, high accuracy (hac) and super accuracy (sup) trade speed for accuracy. The unaligned BAM keeps MM/ML modification tags and can go straight into Align. A local model folder avoids fetching a model; named models and modification models may need a public download, which needs your opt-in below.",
    accepts: ["reads"], file: "reginald", button: "Basecall",
    fields: [
      f.param("input", "POD5 file or folder", "Example path: replace with your signal on this computer", { def: "{{bundleWorkspace}}/nanopore/run.pod5", send: true, width: 420 }),
      f.param("model", "Dorado model", "Local folder (replace this example), or fast, hac, sup, or a pinned model", {
        def: "{{bundleWorkspace}}/models/dorado/model", send: true, width: 420,
      }),
      f.param("modified_bases", "Base modifications", "Optional codes, for example 5mCG_5hmCG"),
      f.param("device", "Compute device", "auto, cpu, metal, cuda:all or cuda:0"),
      f.toggle("model_download", "Allow public model download", "Dorado may fetch a model from ONT using its model / chemistry identifier. No reads, positions or genotypes are uploaded", false, { ui: true }),
    ],
    more: [
      f.param("read_ids", "Read-ID file", "Only these read IDs; cannot combine with panel re-basecalling"),
      f.param("regions_bed", "Panel BED", "Re-basecall reads overlapping these targets; needs alignment too"),
      f.param("alignment", "Earlier alignment", "BAM/CRAM of the same signal run; paired with Panel BED"),
      f.param("ref", "Reference FASTA", "Needed to decode a CRAM alignment"),
      f.param("outdir", "Output folder", "Empty: beside the POD5 input"),
    ],
    run: function (c) {
      var device = c.v.device;
      var localModel = /[\\/]/.test(c.v.model);
      var blocked = !c.v.input || !c.v.model ? "Choose POD5 signal and a model." :
        (!localModel || c.v.modified_bases) && !c.v.model_download ? "Use a local model, or allow the public model download." :
        c.v.read_ids && (c.v.regions_bed || c.v.alignment) ? "Choose read IDs or a panel, not both." :
          !!c.v.regions_bed !== !!c.v.alignment ? "Panel BED and earlier alignment go together." :
            device === "metal" && !c.M.metal ? "Metal needs Apple silicon." :
              /^cuda/.test(device) && !c.M.nvidia ? "CUDA needs an NVIDIA GPU." : null;
      return { timeText: "depends on signal size, model & device", memGB: c.v.model === "sup" ? 16 : 8, blocked: blocked,
        notes: [{ tone: "info", icon: "wrench", title: "Dorado & a local model are required",
          text: "The server validates Dorado before running; WGS Extract does not install it. A local model folder must already exist. CPU basecalling can take days or months on a whole run." }],
        makes: "Makes an unaligned BAM, with MM/ML tags when modifications are requested." };
    },
    requests: function (c, base) {
      return [base("basecall", { model: /[\\/]/.test(c.v.model) ? c.m.realPath(c.v.model) : c.v.model })];
    },
  });

  T.define({
    slug: "panel-bed", label: "Gene-panel BED", ops: ["panel/bed"], group: "Long reads", noInput: true,
    keywords: "adaptive sampling minknow targets padding", lesson: LESSON,
    desc: "Turn gene names or a preset into padded, merged BED intervals for Nanopore adaptive sampling or targeted analysis.",
    learn: "Use it to tell a sequencer or another tool which regions matter. It uses bundled gene annotations, not your sample’s reads, and sends nothing to the network.\n\nPadding includes sequence around each gene. Overlapping targets are merged. A panel covering too much of the genome enriches poorly; inspect the reported target fraction before using it in MinKNOW.",
    accepts: ["reads"], file: "reginald", button: "Make BED",
    fields: [
      f.param("genes", "Gene symbols", "Comma-separated, for example CYP2D6,HLA-A"),
      f.param("presets", "Presets", "Comma-separated preset names; list the available ones below", { def: "pharmacogenes", send: true }),
      f.param("build", "Coordinate build", "Empty: hg38 unless a reference is supplied", { unset: "Auto", labels: { hg38: "GRCh38", hg19: "GRCh37", t2t: "T2T-CHM13" } }),
      f.param("pad_kb", "Padding each side (kb)", "100 means 100,000 bases on either side"),
      f.param("list_presets", "List presets only", "Show sources and genes; write no BED"),
    ],
    more: [f.param("gene_file", "Gene-list file", "One symbol per line"), f.param("outdir", "Output folder", "Empty: server working folder")],
    run: function (c) {
      return { timeText: "seconds",
        blocked: !c.v.list_presets && !c.v.genes && !c.v.presets && !c.v.gene_file ? "Choose genes, a preset or a gene-list file." : null,
        makes: c.v.list_presets ? "Lists presets and their gene sources; writes no files." : "Makes a padded, merged target BED and reports its share of the genome." };
    },
  });

  T.define({
    slug: "pet-align", label: "Dog or cat genome", ops: ["pet-align"], group: "Align", noInput: true,
    keywords: "pet canine feline fastq", lesson: { href: "learn-reference.html#builds", label: "Why the right reference matters" },
    desc: "Align a dog’s or cat’s FASTQ reads to its species’ reference, then call variants.",
    learn: "Use it only for dog or cat reads, never a person’s genome. Supply the matching species reference FASTA or a library containing it. Human references and human health annotations do not apply.\n\nThe output is an indexed alignment plus a VCF. This basic calling workflow is not a veterinary diagnosis.",
    accepts: ["fastq"], file: "fastq", button: "Align & call",
    fields: [
      f.param("species", "Species", "", { labels: { dog: "Dog", cat: "Cat" } }),
      f.param("r1", "Read 1 FASTQ", "Choose pet reads on this computer, not the human demo pair", { def: "{{bundleWorkspace}}/pet/reads_R1.fastq.gz", send: true, width: 420 }),
      f.param("r2", "Read 2 FASTQ", "Optional, for paired reads", { width: 420 }),
      f.param("ref", "Species reference", "Required here: a local species FASTA or reference library", { width: 420 }),
      f.param("format", "Alignment format", "", { labels: { BAM: "BAM", CRAM: "CRAM" } }),
    ],
    more: [f.threads(), f.sortMemory(), f.param("outdir", "Output folder", "Empty: beside the reads")],
    run: function (c) {
      return { timeText: "hours on a whole genome", memGB: 8,
        blocked: !c.v.r1 || !c.v.ref ? "Choose pet reads and the matching species reference." : null,
        makes: "Makes an indexed species alignment and a variant VCF. Input reads stay." };
    },
  });
})();
