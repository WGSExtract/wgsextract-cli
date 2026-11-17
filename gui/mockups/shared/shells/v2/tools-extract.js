/* Extracts: part of a genome as its own small file. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var T = WGSM.v2tools;
  var f = T.f;
  var READS = ["reads"];

  function noY(c) {
    return c.file.sex === "female" ? c.name + " is female, so has no Y chromosome. Her father’s or a brother’s genome would show the paternal line." : null;
  }

  T.define({
    slug: "region", label: "Region or gene", op: "extract/custom", group: "Extract", keywords: "gene brca bam subset",
    desc: "Copies the reads for one gene or stretch of a chromosome into a small BAM, to share with a doctor or researcher or to look at in a genome browser.",
    accepts: READS, file: "reginald", button: "Extract",
    fields: [f.region("Pick a gene, chromosomes or a stretch of one")],
    run: function (c) {
      var share = WGSM.v2region.share(c.v.region);
      return {
        hours: null, timeText: "minutes", disk: [[c.m.diskName(c.file), c.file.size * share * 1.05]],
        command: ["wgsextract extract custom --input " + c.m.path(c.file)].concat(c.v.region ? ["--region " + c.v.region] : []),
        blocked: c.v.region ? null : "Pick a region: the whole genome is the file itself.",
        makes: "Makes a BAM with its index in " + c.name + "’s folder.",
      };
    },
  });

  T.define({
    slug: "mtdna", label: "Mitochondrial DNA", op: "extract/mito-fasta · extract/mito-vcf · extract/mt-bam", group: "Extract", keywords: "mtdna fasta yfull mitochondria",
    desc: "The mitochondrial genome on its own, inherited from the mother. Sites such as YFull MTree and FamilyTreeDNA’s mtDNA tools accept the FASTA.",
    accepts: READS, file: "reginald", button: "Extract",
    checklist: { key: "formats", title: "Which files?", items: [
      { value: "mito-fasta", label: "FASTA", aside: "17 kB", desc: "The full mtDNA sequence, for YFull MTree and mtDNA sites", on: true },
      { value: "mito-vcf", label: "VCF", aside: "8 kB", desc: "Only the differences from the reference, with heteroplasmy levels" },
      { value: "mt-bam", label: "BAM", aside: "≈ 50 MB", desc: "The reads, for another tool or a genome browser" },
    ] },
    fields: [],
    run: function (c) {
      var picks = c.v.formats;
      return {
        count: picks.length + (picks.length === 1 ? " file" : " files"), hours: null, timeText: "minutes",
        command: [picks.map(function (k) {
          return "wgsextract extract " + k + " --input " + c.m.path(c.file);
        }).join("\n")],
        blocked: picks.length ? null : "Choose at least one.", makes: "Saved in " + c.name + "’s folder.",
      };
    },
  });

  T.define({
    slug: "y-chromosome", label: "Y chromosome", op: "extract/ydna-bam · extract/ydna-vcf", group: "Extract", keywords: "ydna yfull big y",
    desc: "The Y chromosome on its own, passed from father to son. YFull and FamilyTreeDNA’s Big Y tools read the BAM; the VCF lists named Y SNPs.",
    accepts: READS, file: "reginald", button: "Extract",
    checklist: { key: "formats", title: "Which files?", items: [
      { value: "ydna-bam", label: "BAM", aside: "≈ 400 MB", desc: "Y reads, for YFull and other Y analysis services", on: true },
      { value: "ydna-vcf", label: "VCF", aside: "≈ 2 MB", desc: "Calls at known Y SNPs, for haplogroup tools" },
    ] },
    fields: [
      f.toggle("poz", "Only the reliable part", "Keep reads where Y can be read reliably (the CombBED, McDonald and Poznik regions); GRCh37 and GRCh38 only", "poz", true, {
        disabled: function (c) {
          return c.file.build === "GRCh37" || c.file.build === "GRCh38" ? null : "GRCh37 and GRCh38 only";
        },
      }),
      f.toggle("ybrowse", "Named SNPs", "Call the installed yBrowse SNP positions so each has its name", "ybrowse", true, {
        disabled: function (c) {
          return c.file.build === "GRCh38" ? null : "GRCh38 only; this file is " + c.file.build;
        },
      }),
    ],
    run: function (c) {
      var lines = c.v.formats.map(function (k) {
        var line = "wgsextract extract " + k + " --input " + c.m.path(c.file);
        if (k === "ydna-bam" && c.v.poz) line += " --poz";
        if (k === "ydna-vcf" && c.v.ybrowse && c.file.build === "GRCh38") line += " --ybrowse";
        return line;
      });
      return {
        count: lines.length + (lines.length === 1 ? " file" : " files"), hours: null, timeText: "minutes", command: [lines.join("\n")],
        blocked: noY(c) ? "No Y chromosome in this genome." : lines.length ? null : "Choose at least one.",
        notes: noY(c) ? [{ tone: "info", icon: "user", id: "no-y", title: "No Y chromosome", text: noY(c) }] : [],
        makes: "Saved in " + c.name + "’s folder.",
      };
    },
  });

  T.define({
    slug: "unmapped", label: "Unmapped reads", op: "extract/unmapped", group: "Extract", keywords: "microbiome bacteria virus kraken",
    desc: "The reads that didn’t match the reference: bacteria and viruses in the sample, contamination, or human DNA missing from the reference.",
    accepts: READS, file: "reginald", button: "Extract",
    fields: [f.toggle("fastq", "Also as FASTQ", "For tools that classify reads, such as Kraken", "fastq", false)],
    run: function (c) {
      return {
        hours: null, timeText: "minutes", disk: [[c.m.diskName(c.file), c.v.fastq ? 2.4 : 1.1]],
        command: ["wgsextract extract unmapped --input " + c.m.path(c.file)].concat(c.v.fastq ? ["--fastq"] : []),
        makes: "Makes " + c.file.stem + ".unmapped.bam in " + c.name + "’s folder.",
      };
    },
  });

  WGSM.v2extract = { noY: noY };
})();
