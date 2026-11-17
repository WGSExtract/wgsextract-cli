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

  /* One request per ticked operation of a checklist */
  function each(key) {
    return function (c, base) {
      return c.v[key].map(function (op) {
        return base(op);
      });
    };
  }

  T.define({
    slug: "region", label: "Region or gene", ops: ["extract/custom"], group: "Extract", keywords: "gene brca bam subset",
    lesson: { href: "learn-files.html#bam", label: "Reads, regions & file formats" },
    desc: "Copies the reads for one gene or stretch of a chromosome into a small BAM, to share with a doctor or researcher or to look at in a genome browser.",
    learn: "Use it when someone asks for the evidence behind one finding: a doctor, a research study, or a forum helper looking at a gene. A few megabytes travel more easily than a whole genome, and reveal far less about you.\n\n" +
      "You can open the small BAM in a genome browser such as IGV to see the reads yourself. It holds only that region, so tools that need the whole genome, such as ancestry, won’t work on it. Mitochondrial DNA and Y chromosome have extracts of their own, made for lineage sites.",
    accepts: READS, file: "reginald", button: "Extract",
    fields: [f.region("Pick a gene, chromosomes or a stretch of one")],
    run: function (c) {
      var share = WGSM.v2region.share(c.v.region);
      return {
        hours: null, timeText: "minutes", disk: [[c.m.diskName(c.file), c.file.size * share * 1.05]],
        blocked: c.v.region ? null : "Pick a region: the whole genome is the file itself.",
        makes: "Makes a BAM with its index in " + c.name + "’s folder.",
      };
    },
  });

  T.define({
    slug: "mtdna", label: "Mitochondrial DNA", ops: ["extract/mito-fasta", "extract/mito-vcf", "extract/mt-bam"], group: "Extract", keywords: "mtdna fasta yfull mitochondria",
    lesson: { href: "learn-ancestry.html#lineage", label: "Mitochondrial & Y lineages" },
    desc: "The mitochondrial genome on its own, inherited from the mother. Sites such as YFull MTree and FamilyTreeDNA’s mtDNA tools accept the FASTA.",
    learn: "Use it to send your maternal line to sites that analyse it in detail, or to keep its 16,569 letters on their own. The FASTA is the full sequence; the VCF lists only its differences from the reference mtDNA.\n\n" +
      "A whole genome reads mtDNA very deeply, because every cell holds many copies, so the sequence is usually complete. Uploading shares that sequence with the site. For the haplogroup alone, mtDNA haplogroup works it out here, with nothing uploaded.",
    accepts: READS, file: "reginald", button: "Extract",
    checklist: { key: "formats", title: "Which files?", items: [
      { value: "extract/mito-fasta", label: "FASTA", aside: "17 kB", desc: "The full mtDNA sequence, for YFull MTree and mtDNA sites", on: true },
      { value: "extract/mito-vcf", label: "VCF", aside: "8 kB", desc: "Only the differences from the reference, with heteroplasmy levels" },
      { value: "extract/mt-bam", label: "BAM", aside: "≈ 50 MB", desc: "The reads, for another tool or a genome browser" },
    ] },
    fields: [],
    requests: each("formats"),
    run: function (c) {
      var picks = c.v.formats;
      return {
        count: picks.length + (picks.length === 1 ? " file" : " files"), hours: null, timeText: "minutes",
        blocked: picks.length ? null : "Choose at least one.", makes: "Saved in " + c.name + "’s folder.",
      };
    },
  });

  T.define({
    slug: "y-chromosome", label: "Y chromosome", ops: ["extract/ydna-bam", "extract/ydna-vcf"], group: "Extract", keywords: "ydna yfull big y",
    lesson: { href: "learn-ancestry.html#lineage", label: "What a paternal lineage tells you" },
    desc: "The Y chromosome on its own, passed from father to son. YFull and FamilyTreeDNA’s Big Y tools read the BAM; the VCF lists named Y SNPs.",
    learn: "Use it to send your paternal line to YFull or FamilyTreeDNA for detailed placement and matching with other men, without sharing the rest of the genome. It applies only to males; a woman’s paternal line needs her father’s or a brother’s genome.\n\n" +
      "The BAM keeps the reads, so those sites can find new SNPs themselves; the VCF is smaller and lists known ones. For the haplogroup alone, Y haplogroup works it out here, with nothing uploaded.",
    accepts: READS, file: "reginald", button: "Extract",
    checklist: { key: "formats", title: "Which files?", items: [
      { value: "extract/ydna-bam", label: "BAM", aside: "≈ 400 MB", desc: "Y reads, for YFull and other Y analysis services", on: true },
      { value: "extract/ydna-vcf", label: "VCF", aside: "≈ 2 MB", desc: "Calls at known Y SNPs, for haplogroup tools" },
    ] },
    fields: [
      f.param("poz", "Only the reliable part", "Keep reads where Y can be read reliably (the CombBED, McDonald and Poznik regions); GRCh37 and GRCh38 only", {
        def: true,
        disabled: function (c) {
          return c.file.build === "GRCh37" || c.file.build === "GRCh38" ? null : "GRCh37 and GRCh38 only";
        },
      }),
      f.param("ybrowse", "Named SNPs", "Call the installed yBrowse SNP positions so each has its name", {
        def: true,
        disabled: function (c) {
          return c.file.build === "GRCh38" ? null : "GRCh38 only; this file is " + c.file.build;
        },
      }),
    ],
    requests: each("formats"),
    run: function (c) {
      var n = c.v.formats.length;
      return {
        count: n + (n === 1 ? " file" : " files"), hours: null, timeText: "minutes",
        blocked: noY(c) ? "No Y chromosome in this genome." : n ? null : "Choose at least one.",
        notes: noY(c) ? [{ tone: "info", icon: "user", id: "no-y", title: "No Y chromosome", text: noY(c) }] : [],
        makes: "Saved in " + c.name + "’s folder.",
      };
    },
  });

  T.define({
    slug: "unmapped", label: "Unmapped reads", ops: ["extract/unmapped"], group: "Extract", keywords: "microbiome bacteria virus kraken",
    lesson: { href: "learn-sequencing.html#quality", label: "Unmapped reads & quality limits" },
    desc: "The reads that didn’t match the reference: bacteria and viruses in the sample, contamination, or human DNA missing from the reference.",
    learn: "Use it out of curiosity about what else was in the sample, mostly microbes from saliva, or to look for human sequence your reference lacks. Most genomes have a small share of such reads.\n\n" +
      "WGS Extract only extracts them: identifying what they are needs another tool, such as Kraken, which is what the FASTQ option is for. Realigning to a more complete reference, such as T2T, places some of them. Nothing here is a medical test for infection.",
    accepts: READS, file: "reginald", button: "Extract",
    fields: [f.param("fastq", "Also as FASTQ", "For tools that classify reads, such as Kraken")],
    run: function (c) {
      return {
        hours: null, timeText: "minutes", disk: [[c.m.diskName(c.file), c.v.fastq ? 2.4 : 1.1]],
        makes: "Makes " + c.file.stem + ".unmapped.bam in " + c.name + "’s folder.",
      };
    },
  });

  WGSM.v2extract = { noY: noY };
})();
