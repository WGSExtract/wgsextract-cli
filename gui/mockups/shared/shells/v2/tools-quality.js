/* Quality checks. Times are the CLI help's own words where no rate is
 * documented ("minutes on a 30x genome"). */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var T = WGSM.v2tools;
  var f = T.f;
  var READS = ["reads"];

  T.define({
    slug: "coverage", label: "Coverage per chromosome", ops: ["info/calculate-coverage"], group: "Quality", keywords: "depth breadth bins targets bed",
    lesson: { href: "learn-sequencing.html#depth", label: "What 30× actually means" },
    desc: "Measures how many reads cover each place in the genome: the average per chromosome, how much is covered at all, and depth on targets such as a gene panel.",
    learn: "Use it to check a genome before relying on it: whether the depth is what the lab promised, whether any chromosome is thin, and whether X and Y depth fit the expected sex. About 30× on average, with nearly everything covered, is a good whole genome.\n\n" +
      "Low or patchy coverage explains missing markers in DNA-test files and uncertain calls later. With a BED file of genes, it reports depth on just those, which is how labs judge a gene panel or exome.",
    accepts: READS, file: "reginald", button: "Measure coverage",
    fields: [
      f.param("with_bins", "Depth bands", "How much of each chromosome has 0, 1–3, 4–7 and 8 or more reads", { def: true }),
      f.region("Without targets, the whole file is measured"),
      f.param("by", "Targets", "A BED of genes or a test panel: depth per target"),
    ],
    more: [
      f.param("min_depth", "Low-depth limit", "With targets: list those below this depth", { width: 90 }),
      f.param("table_format", "Also save tables", "As .tsv or .parquet beside the input", { unset: "No", labels: { tsv: "TSV", parquet: "Parquet" } }),
    ],
    run: function (c) {
      return { hours: null, timeText: "minutes on a 30x genome", makes: "The numbers appear on " + c.name + "’s page." };
    },
  });

  T.define({
    slug: "read-quality", label: "Read quality", ops: ["qc/fastqc", "qc/fastp"], group: "Quality", keywords: "fastqc fastp multiqc adapters",
    lesson: { href: "learn-sequencing.html#quality", label: "Base quality & mapping quality" },
    desc: "Checks raw reads before aligning: base quality along the read, adapters left on, duplication and GC content. Use it when a lab’s reads look wrong.",
    learn: "Use it on FASTQ files before aligning, or when a genome looks odd: few reads mapped, uneven coverage, strange calls. FastQC draws the standard charts; fastp gives a quicker summary and can trim adapters.\n\n" +
      "Most labs’ reads pass easily, and a few warnings are normal for whole genomes. If quality really is poor, contact the lab before spending hours aligning. Coverage and Contamination check the aligned genome instead.",
    accepts: ["fastq", "reads"], file: "fastq", button: "Check the reads",
    fields: [
      f.seg("program", "Program", function (c) {
        return c.v.program === "fastp" ? "fastp: fast, a JSON and HTML report (FASTQ only)" : "FastQC: the familiar report; slower";
      }, [["fastqc", "FastQC"], ["fastp", "fastp"]], "fastqc", { ui: true }),
      f.param("multiqc", "Combined report", "One MultiQC page for both reads", {
        def: true,
        when: function (c) {
          return c.v.program === "fastqc" && c.file.kind === "fastq";
        },
      }),
    ],
    requests: function (c, base) {
      var M = c.m;
      var fq = c.file.kind === "fastq";
      var r1 = fq ? M.realPath(M.path(c.file, c.file.r1)) : null;
      var r2 = fq ? M.realPath(M.path(c.file, c.file.r2)) : null;
      if (c.v.program === "fastp") return [base("qc/fastp", { r1: r1, r2: r2 })];
      return [base("qc/fastqc", fq ? { input: r1, r2: r2 } : {})];
    },
    run: function (c) {
      var fq = c.file.kind === "fastq";
      return {
        hours: null, timeText: c.v.program === "fastp" ? "minutes on a 30x genome" : "hours on a 30x genome", makes: "Opens the report when it’s done.",
        blocked: c.v.program === "fastp" && !fq ? "fastp reads FASTQ files; pick FastQC for a BAM or CRAM." : null,
      };
    },
  });

  T.define({
    slug: "contamination", label: "Contamination", ops: ["qc/contamination"], group: "Quality", keywords: "verifybamid2 freemix",
    lesson: { href: "learn-sequencing.html#quality", label: "Read evidence & contamination" },
    desc: "Estimates how much of the sample is another person’s DNA. Above about 2%, results such as rare variants become unreliable.",
    learn: "Use it once on any genome you plan to rely on, especially if something looks odd. Another person’s reads don’t make anything fail; they quietly turn some of your genotypes into mixed ones, which spoils DNA-test files, haplogroups and rare-variant findings.\n\n" +
      "VerifyBamID2 compares the reads at common SNPs with population frequencies; a clean sample shows close to 0%. If it’s high, ask the lab. Relatedness catches the other kind of mix-up: a file that isn’t who it should be.",
    accepts: READS, file: "reginald", button: "Check contamination", requires: ["verifybamid2"],
    fields: [f.param("sites", "Marker set", function (c) {
      return c.v.sites === "10k" ? "Faster and noisier; for low depth" : "1000 Genomes markers; the default";
    }, { labels: { "100k": "100k markers", "10k": "10k" } })],
    more: [f.param("svd_prefix", "Own marker set", "A VerifyBamID2 resource, for T2T for example; replaces the marker set")],
    run: function (c) {
      return { hours: null, timeText: "minutes on a 30x genome", makes: "The estimate appears on " + c.name + "’s page." };
    },
  });

  T.define({
    slug: "relatedness", label: "Relatedness", ops: ["qc/relate"], group: "Quality", keywords: "somalier family sample swap kinship",
    lesson: { href: "learn-ancestry.html#relatives", label: "Relatedness & family comparisons" },
    desc: "Compares genomes to each other: parent and child, siblings, the same person twice, or unrelated. A pair that should be related but isn’t is a likely sample swap.",
    learn: "Use it when you have genomes for several family members, to confirm relationships before relying on them and to catch a mislabelled file. It also infers each sample’s sex from X and Y and checks it against what was recorded.\n\n" +
      "somalier compares a set of common sites, so it’s quick. It tells the same person, parent and child, siblings and second-degree relatives apart, and more distant degrees only roughly. It isn’t a genealogy matching service: to find cousins in other families, upload DNA-test files to matching sites.",
    accepts: READS, file: "reginald", noInput: true, button: "Compare", requires: ["somalier"],
    checklist: { key: "people", title: "Whose genomes?", items: [
      { value: "reginald", label: "Reginald", aside: "CRAM", desc: "reginald-mcdonald.GRCh38.30x.cram", on: true },
      { value: "gordon", label: "Gordon", aside: "BAM", desc: "60820188479016.bam · GRCh37 is fine; builds are matched", on: true },
      { value: "helen", label: "Helen", aside: "VCF", desc: "helen-mcdonald.deepvariant.vcf.gz", on: true },
      { value: "alex", label: "Alex", aside: "BAM", desc: "NG1A2B3C4D.bam · needs its index first" },
    ] },
    fields: [
      f.seg("expected", "Expected family", "Who is whose parent, from the library, so pairs that disagree are flagged", [["library", "From the library"], ["none", "Don’t check"]], "library", { ui: true }),
      f.param("ancestry", "Predict ancestry", "A continental group per person, as a cross-check", { def: true }),
      f.param("table_format", "Table", "", { labels: { tsv: "TSV", parquet: "Parquet" } }),
    ],
    requests: function (c, base) {
      var M = c.m;
      var extra = {
        inputs: c.v.people.map(function (id) {
          return M.realPath(M.path(M.files[id]));
        }),
      };
      if (c.v.expected === "library") extra.ped = M.realPath("{{bundleWorkspace}}/library/family.ped");
      return [base("qc/relate", extra)];
    },
    extra: function () {
      return '<section class="v2-section" data-annot-id="last"><h2>Last time</h2><div class="v2-sheet v2-scroll"><table class="v2-table" aria-label="Relatedness from the last run">' +
        "<thead><tr><th>Pair</th><th>Found</th><th class=\"v2-num\">Relatedness</th><th>Expected</th></tr></thead><tbody>" +
        '<tr><td class="v2-table__main">Reginald · Gordon</td><td>Parent and child</td><td class="v2-num">0.49</td><td><span class="v2-status"><span class="v2-pip" data-state="ok"></span>As expected</span></td></tr>' +
        '<tr><td class="v2-table__main">Reginald · Helen</td><td>Parent and child</td><td class="v2-num">0.51</td><td><span class="v2-status"><span class="v2-pip" data-state="ok"></span>As expected</span></td></tr>' +
        '<tr><td class="v2-table__main">Gordon · Helen</td><td>Unrelated</td><td class="v2-num">0.01</td><td><span class="v2-status"><span class="v2-pip" data-state="ok"></span>As expected</span></td></tr>' +
        '</tbody></table></div><p class="v2-section__note">Nov 13, from the same three files. 1.0 is the same person, 0.5 a parent, child or full sibling.</p></section>';
    },
    run: function (c) {
      var M = c.m;
      var people = c.v.people;
      var out = {
        count: people.length + (people.length === 1 ? " genome" : " genomes"), hours: null, timeText: "minutes",
        makes: "Results appear on each person’s page.", jobName: "Compare " + people.map(function (id) {
          return WGSM.v2state.first(WGSM.v2state.person(id).name);
        }).join(", "),
      };
      if (people.length < 2) out.blocked = "Choose at least two genomes.";
      if (people.indexOf("alex") >= 0 && !M.ready(M.files.alex)) out.blocked = "Alex’s BAM needs its index first.";
      return out;
    },
  });

  T.define({
    slug: "vcf-quality", label: "VCF quality", ops: ["qc/vcf"], group: "Quality", keywords: "ti/tv heterozygosity stats",
    lesson: { href: "learn-variants.html#confidence", label: "What makes a call trustworthy?" },
    desc: "Summarises a VCF: how many SNPs and indels, the ratio of transitions to transversions, heterozygosity and quality. Unusual numbers point to a problem with the calls.",
    learn: "Use it after calling variants, or on a VCF a lab sent, to see whether the calls look like a typical human genome: for a 30× genome, about 4 to 5 million variants, a ratio of transitions to transversions near 2, and somewhat more heterozygous than homozygous variants.\n\n" +
      "Numbers far from these point to contamination, low coverage, the wrong reference or a caller set up wrongly. It doesn’t judge individual variants; it’s a sanity check before annotation.",
    accepts: ["vcf"], file: "helen", button: "Check the VCF",
    fields: [],
    run: function (c) {
      return { hours: null, timeText: "minutes", makes: "The summary appears on " + c.name + "’s page." };
    },
  });
})();
