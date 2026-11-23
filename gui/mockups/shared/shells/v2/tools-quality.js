/* Quality checks. Times are the CLI help's own words where no rate is
 * documented ("minutes on a 30x genome"). */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var T = WGSM.v2tools;
  var f = T.f;
  var READS = ["reads"];

  T.define({
    slug: "coverage", label: "Detailed Coverage", ops: ["info/calculate-coverage"], group: "Quality", keywords: "depth breadth bins targets bed",
    lesson: { href: "learn-sequencing.html#depth", label: "What 30× actually means" },
    desc: "Measures how many reads cover each place in the genome: the average per chromosome, how much is covered at all, and depth on targets such as a gene panel.",
    learn: "Check a new genome here before relying on its calls: you can see if average depth matches what the lab promised, spot thin chromosomes, and confirm that X and Y depth fit the expected sex. About 30× on average, with nearly everything covered, is a good whole genome.\n\n" +
      "The matching reference’s N gaps are left out of breadth and depth bins. The result card shows the bins, and Detailed Info then includes the breadth column. Low or patchy coverage explains missing markers in DNA-test files and uncertain calls later. A BED file narrows the report to target regions.",
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
      return { hours: null, timeText: c.v.region || c.v.by ? "depends on the selected targets" : "1–3 hours on a whole genome",
        disk: c.v.region || c.v.by ? [] : [[c.m.diskName(c.file), 2]],
        makes: "Shows depth bins in the result card and adds breadth to Detailed Info for " + c.name + "." };
    },
  });

  T.define({
    slug: "read-quality", label: "Read quality", ops: ["qc/fastqc", "qc/fastp"], group: "Quality", keywords: "fastqc fastp multiqc adapters",
    lesson: { href: "learn-sequencing.html#quality", label: "Base quality & mapping quality" },
    desc: "Inspects raw reads before alignment for base-quality drop-offs, leftover adapters, duplication and GC content.",
    learn: "Run this on newly delivered FASTQ files, or whenever an aligned genome shows low mapping rates, uneven coverage or puzzling calls. FastQC draws the standard charts; fastp produces a quicker summary and can trim adapters.\n\n" +
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
    learn: "Worth running once on any genome you plan to rely on, especially when calls look unusual. Stray DNA from another sample will not make tools error out, yet it blends into your reads and creates false mixed genotypes that skew DNA-test files, haplogroups and rare-variant findings.\n\n" +
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
    learn: "With genomes for two or more family members, comparing them confirms expected relationships before further analysis and catches a mislabelled file. Sex is also inferred from X and Y coverage and checked against what was recorded.\n\n" +
      "somalier genotypes a small set of common sites in minutes, separating identical samples, parent-child pairs, full siblings and second-degree relatives clearly while estimating more distant relationships only roughly. To search for cousins outside your own files, generate DNA-test files and upload them to a matching site.",
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
      if (c.v.expected === "library") extra.ped = M.realPath("{{usersWorkspace}}/library/family.ped");
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
    learn: "After calling variants or receiving a VCF from a lab, check whether the totals resemble a typical 30× human genome: roughly 4 to 5 million variants, a transition-to-transversion ratio near 2, and somewhat more heterozygous than homozygous sites.\n\n" +
      "Numbers far from these point to contamination, low coverage, the wrong reference or a caller set up wrongly. These file-wide totals catch broad problems before annotation without scoring individual variants.",
    accepts: ["vcf"], file: "helen", button: "Check the VCF",
    fields: [],
    run: function (c) {
      return { hours: null, timeText: "minutes", makes: "The summary appears on " + c.name + "’s page." };
    },
  });
})();
