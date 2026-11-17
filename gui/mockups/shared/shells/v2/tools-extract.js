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
    slug: "y-chromosome", label: "Y chromosome", needsY: true, ops: ["extract/ydna-bam", "extract/ydna-vcf"], group: "Extract", keywords: "ydna yfull big y",
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

  /* --formats items: [value, label, aside, desc, on] */
  var KITS = [
    ["all", "GEDmatch (combined)", "1 file", "Every marker any company's chip reads, in one file that GEDmatch takes", true],
    ["myheritage_v2", "MyHeritage", "1 file", "Their current chip (v2, Illumina GSA); matches and ethnicity after upload", true],
    ["ftdna_v3", "FamilyTreeDNA", "1 file", "Family Finder transfer (v3 chip)"],
    ["ldna_v2", "Living DNA", "1 file", "Regional breakdown, strongest for Britain and Ireland"],
    ["23andme_v5", "23andMe format", "1 file", "For tools that read a 23andMe file; 23andMe itself takes no uploads"],
    ["ancestry_v2", "AncestryDNA format", "1 file", "For tools that read an AncestryDNA file; Ancestry takes no uploads"],
    ["nucleus", "Nucleus format", "1 file", "Nucleus's 2025 array export"],
    ["aadr_1240k", "AADR 1240K (research)", "PLINK and EIGENSTRAT", "Ancient-DNA researchers' marker set, GRCh37; for admixture tools"],
  ];

  T.define({
    slug: "microarray", label: "DNA-test files", ops: ["microarray"], group: "Extract",
    keywords: "microarray chip raw data kit gedmatch myheritage ftdna 23andme ancestry living dna nucleus aadr wgs to microarray",
    lesson: { href: "learn-files.html#kits", label: "DNA-test (microarray) files" },
    desc: "Turns a whole genome or a VCF into the raw-data files that DNA-testing companies give their customers, so relative-matching sites accept it.",
    learn: "Use it to find relatives in the databases of GEDmatch, MyHeritage, FamilyTreeDNA and Living DNA, which accept uploads of microarray (\u201cchip\u201d) raw data. A chip reads about 600,000\u2013700,000 chosen positions; your genome has nearly all of them, so WGS Extract reads those positions and writes them in each company's layout.\n\n" +
      "Positions your genome couldn't read confidently are left blank, as a chip would. A BAM or CRAM is best: every position is checked. A VCF lists only differences from the reference, so positions it doesn't list are unknown unless you say they're reference. Uploading shares your genotypes with that company; read its privacy terms first. Make DNA-test files on a person's page walks through the same choices.",
    accepts: ["reads", "vcf"], file: "reginald", button: "Make the files",
    checklist: { key: "kits", title: "Which files?", items: KITS.map(function (k) {
      return { value: k[0], label: k[1], aside: k[2], desc: k[3], on: !!k[4] };
    }) },
    fields: [
      f.param("strand_ambiguous", "A/T and C/G markers", "Their strand can't be checked", { labels: { keep: "Keep", "no-call": "Leave blank" } }),
      f.param("min_gq", "Minimum quality (GQ)", "Blank below this; empty uses the setting (20), 0 is off", { width: 90 }),
      f.param("min_dp", "Minimum depth", "Blank below this many reads; empty uses the setting (10)", { width: 90 }),
      f.param("call_reasons", "Explain blanks", "Also write a table of why each blank marker is blank"),
    ],
    moreTitle: "More options",
    more: [
      f.param("assume_hom_ref", "Unlisted means reference", "VCF only: treat positions the VCF doesn't list as reference. Unsafe unless the VCF lists every site; needs minimum quality and depth of 0", {
        disabled: function (c) {
          return c.file.kind === "vcf" ? null : "Only for a VCF";
        },
      }),
      f.param("exclude_filtered", "Skip filtered calls", "VCF records that failed a FILTER become blanks"),
      f.param("resolve_rsids", "Update marker names", "Use current dbSNP rsIDs; retired or ambiguous names become blanks"),
      f.param("quirks", "Strand as the company wrote it", "Corrected: forward strand. Vendor: copy a chip revision's own reverse-strand calls", { labels: { corrected: "Corrected", vendor: "Vendor" } }),
      f.param("table_format", "Also a genotype table", "rsID, chromosome, position and genotype, for your own analysis", { as: "select", unset: "No", labels: { tsv: "TSV", parquet: "Parquet" } }),
      f.param("aadr_sex", "AADR sex label", "Written into the AADR metadata only", { labels: { U: "Unknown", M: "Male", F: "Female" },
        when: function (c) {
          return c.v.kits.indexOf("aadr_1240k") >= 0;
        } }),
      f.threads(),
    ],
    requests: function (c, base) {
      return [base("microarray", { formats: c.v.kits.join(",") })];
    },
    run: function (c) {
      var n = c.v.kits.length;
      var vcf = c.file.kind === "vcf";
      var notes = [];
      if (vcf && !c.v.assume_hom_ref) notes.push({ tone: "info", icon: "info", id: "vcf-gaps", title: "Expect more blanks from a VCF",
        text: "A VCF lists only where " + c.name + " differs from the reference, so about two thirds of chip positions will be blank. A BAM or CRAM fills them in." });
      if (!vcf && c.file.depth && c.file.depth < 15) notes.push({ tone: "warn", icon: "triangle-alert", id: "low-depth", title: "Low depth: " + c.file.depth + "\u00d7",
        text: "Below 15\u00d7 many positions can't be read confidently and stay blank; matching sites may find fewer relatives." });
      if (c.v.kits.indexOf("aadr_1240k") >= 0) notes.push({ tone: "info", icon: "download", id: "aadr", title: "AADR uses its own marker list",
        text: "The first run fetches the AADR v66 marker list (ref aadr) into Reference data. Outputs are on GRCh37, whatever this file's build." });
      return {
        count: n + (n === 1 ? " file" : " files"),
        hours: vcf ? null : 0.2 * ((c.file.depth || 30) / 30), timeText: "minutes",
        disk: [[c.m.diskName(c.file), 0.02 * Math.max(n, 1)]],
        blocked: n ? null : "Choose at least one.",
        notes: notes,
        makes: "Saved in " + c.name + "\u2019s folder, under microarray/.",
      };
    },
  });

  WGSM.v2extract = { noY: noY };
})();
