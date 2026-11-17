/* Annotation. VEP numbers from docs/vep-engines.md (whole genome, 6
 * threads): vep-rs 8.8 min, 7.5 GB, 17 GB cache (≈17 GB download); its
 * no-variants cache 2.9 min, 5.7 GB on disk (1.6 GB download); Ensembl VEP
 * 3 h 34 min, 1.4 GB, 26 GB cache (27.6 GB download). Score data sizes are
 * shared/data/catalog.js's. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var T = WGSM.v2tools;
  var f = T.f;
  var VCF = ["vcf"];

  function vcfIn(c, op, extra) {
    return ["wgsextract " + op + " --vcf-input " + c.m.path(c.file)].concat(extra || []);
  }

  function download(flag, label, size, extra) {
    return Object.assign({ tone: "warn", icon: "download", id: flag, title: label + " isn’t installed",
      text: "Public reference data; nothing about this genome is sent. It downloads only when you ask.",
      action: { label: "Download" + (size ? " " + size : ""), set: flag, toast: "Downloading " + label + ". See Jobs." } }, extra);
  }

  var CONFIDENCE = [
    f.text("confDepth", "Confidence: read depth", "Below LOW a call is low confidence, below HIGH moderate", "confidence_depth", "10,20", { mono: true, width: 110 }),
    f.text("confGq", "Confidence: genotype quality", "The same, by genotype quality", "confidence_gq", "20,30", { mono: true, width: 110 }),
  ];

  function confidence(c, line) {
    if (c.v.confDepth && c.v.confDepth !== "10,20") line.push("--confidence-depth " + c.v.confDepth);
    if (c.v.confGq && c.v.confGq !== "20,30") line.push("--confidence-gq " + c.v.confGq);
    return line;
  }

  T.define({
    slug: "vep", label: "VEP", op: "vep/run", group: "Annotate", keywords: "variant effect predictor consequence gene transcript ensembl vep-rs",
    desc: "The Variant Effect Predictor: for each variant, which gene and transcript it falls in and what it may do to the protein.",
    accepts: VCF, file: "helen", button: "Annotate with VEP",
    fields: [
      f.seg("engine", "Engine", function (c) {
        return c.v.engine === "ensembl" ? "Ensembl’s own VEP, the reference implementation: hours, and its own 26 GB cache" : "vep-rs: a fast Rust VEP, the default on every platform";
      }, "engine", [["vep-rs", "vep-rs"], ["ensembl", "Ensembl VEP"]], "vep-rs"),
      f.seg("preset", "What to add", function (c) {
        return c.v.preset === "fast" ? "Consequences, genes, MANE transcripts, SIFT and PolyPhen; no population frequencies or HGVS names" : "Everything VEP knows, including population frequencies and HGVS names";
      }, "preset", [["everything", "Everything"], ["fast", "Fast"]], "everything"),
      f.seg("format", "Output", "", "format", [["vcf", "VCF"], ["tab", "Table"], ["json", "JSON"]], "vcf"),
    ],
    more: [
      f.select("assembly", "Assembly", "Read from the VCF", "assembly", [["auto", "From the VCF"], ["GRCh37", "GRCh37"], ["GRCh38", "GRCh38"], ["T2T-CHM13v2.0", "T2T-CHM13 v2.0"]], "auto", { width: 220 }),
      f.region(),
    ],
    run: function (c) {
      var ensembl = c.v.engine === "ensembl";
      var cacheFlag = ensembl ? "vepcache-ensembl" : "vepcache-vep-rs";
      var minutes = ensembl ? 214 : 8.8;
      if (c.v.preset === "fast") minutes /= 2;
      var threads = c.v.threads || c.M.cores;
      var hours = (minutes / 60) * 6 / threads * WGSM.v2region.share(c.v.region);
      var out = {
        hours: hours, memGB: ensembl ? 1.4 : 7.5, disk: [[c.M.systemDisk, 0.6]], notes: [],
        command: ["wgsextract vep run --input " + c.m.path(c.file), "--engine " + c.v.engine + " --preset " + c.v.preset + " --format " + c.v.format]
          .concat(c.v.assembly !== "auto" ? ["--assembly " + c.v.assembly] : [], c.v.region ? ["--region " + c.v.region] : []),
        makes: "Makes " + c.file.stem + ".vep." + (c.v.format === "tab" ? "tsv" : c.v.format === "json" ? "json" : "vcf.gz") + " in " + c.name + "’s folder.",
      };
      if (ensembl && c.m.unavailable("vep-ensembl")) out.blocked = c.m.unavailable("vep-ensembl") + ".";
      if (!c.flag(cacheFlag)) {
        out.notes.push(download(cacheFlag, ensembl ? "Ensembl VEP’s GRCh38 cache" : "vep-rs’s GRCh38 cache", ensembl ? "27.6 GB" : "17 GB",
          { text: ensembl ? "26 GB on disk. Public data; nothing about this genome is sent." : "17 GB on disk, built after downloading (about 80 min on 2 cores, 11 GB of memory). Public data; nothing about this genome is sent." }));
        out.blocked = out.blocked || "Download the cache first.";
      }
      return out;
    },
  });

  T.define({
    slug: "clinvar", label: "ClinVar", op: "vcf/clinvar", group: "Annotate", keywords: "pathogenic benign clinical significance health",
    desc: "Marks variants that ClinVar lists with a reported effect on health, from benign to pathogenic, with the conditions submitters named, and how confident each call is.",
    accepts: VCF, file: "helen", button: "Annotate with ClinVar",
    fields: [f.select("release", "ClinVar release", "Installed with Reference data", "clinvar_file", [["installed", "2026-11-02, installed"]], "installed", { width: 240 })],
    more: CONFIDENCE,
    run: function (c) {
      return {
        hours: null, timeText: "minutes",
        notes: [{ tone: "info", icon: "info", id: "caveat", title: "Not a diagnosis",
          text: "ClinVar records what labs have reported. Many entries conflict or are out of date, and most people carry a few variants marked pathogenic without being affected. Talk to a genetic counsellor about anything that worries you." }],
        command: confidence(c, vcfIn(c, "vcf clinvar")), makes: "Makes " + c.file.stem + ".clinvar.vcf.gz and adds a summary to " + c.name + "’s page.",
      };
    },
  });

  var SCORES = [
    ["gnomad", "gnomAD frequencies", "≈ 26 GB", "How common each variant is in 800,000 people", "vcf gnomad", "--max-af"],
    ["revel", "REVEL", "≈ 0.6 GB", "How likely a missense change is to matter", "vcf revel", "--min-score"],
    ["alphamissense", "AlphaMissense", "", "DeepMind’s predicted effect of missense changes", "vcf alphamissense", "--min-score"],
    ["cadd", "CADD", "≈ 85 GB", "How harmful any change may be, as a PHRED score", "vcf cadd", "--min-score"],
    ["spliceai", "SpliceAI", "", "Whether a change may disturb splicing", "vcf spliceai", null],
  ];

  T.define({
    slug: "scores", label: "Frequencies and scores", op: "vcf/gnomad · vcf/revel · vcf/alphamissense · vcf/cadd · vcf/spliceai", group: "Annotate",
    keywords: "gnomad revel cadd alphamissense spliceai allele frequency pathogenicity",
    desc: "Adds what large databases and prediction models say about each variant: how common it is, and how likely it is to change a protein or its splicing. Rare plus likely harmful is what clinicians look at first.",
    accepts: VCF, file: "helen", button: "Annotate",
    checklist: { key: "scores", title: "Which?", items: SCORES.map(function (s, i) {
      return { value: s[0], label: s[1], aside: s[2], desc: s[3], on: i < 2 };
    }) },
    fields: [
      f.text("maxAf", "Keep only rarer than", "gnomAD: a frequency such as 0.01 (1%); empty keeps all", "max_af", "", { mono: true, width: 100 }),
      f.text("minScore", "Keep only scores above", "REVEL or AlphaMissense 0.5, CADD 20; empty keeps all", "min_score", "", { mono: true, width: 100 }),
    ],
    more: CONFIDENCE,
    run: function (c) {
      var picks = c.v.scores;
      var out = { count: picks.length + (picks.length === 1 ? " source" : " sources"), hours: null, timeText: "minutes", notes: [], command: [] };
      SCORES.forEach(function (s) {
        if (picks.indexOf(s[0]) < 0) return;
        if (!c.flag("data-" + s[0])) out.notes.push(download("data-" + s[0], s[1], s[2].replace("≈ ", "")));
        var line = vcfIn(c, s[4]);
        if (s[0] === "gnomad" && c.v.maxAf) line.push("--max-af " + c.v.maxAf);
        else if (s[5] === "--min-score" && c.v.minScore) line.push("--min-score " + c.v.minScore);
        out.command.push(confidence(c, line).join(" \\\n  "));
      });
      out.command = [out.command.join("\n")];
      if (!picks.length) out.blocked = "Choose at least one.";
      else if (out.notes.length) out.blocked = "Download the data first.";
      out.makes = "Adds columns to " + c.name + "’s VCF; the original stays.";
      return out;
    },
  });

  T.define({
    slug: "variant-table", label: "Variant table", op: "vcf/table", group: "Annotate", keywords: "spreadsheet sqlite tsv parquet search",
    desc: "Turns a VCF into a table you can open in a spreadsheet or search by gene, one row per variant, with the annotations already added.",
    accepts: VCF, file: "helen", button: "Make the table",
    fields: [
      f.seg("format", "Format", "Parquet is smaller and faster for large tables", "format", [["tsv", "TSV"], ["parquet", "Parquet"]], "tsv"),
      f.toggle("sqlite", "Searchable database", "Also a SQLite file, for searching by gene or position here and on the phone", "no_sqlite", true),
      f.region(),
    ],
    more: [
      f.toggle("allAlleles", "Alleles not carried", "Also list alleles at the site the person doesn’t have", "all_alleles", false),
      f.select("pharmcat", "Medicine results", "Add PharmCAT’s results to rows in those genes", "pharmcat_json", [["none", "None yet"]], "none", { width: 200 }),
    ],
    run: function (c) {
      var line = vcfIn(c, "vcf table");
      if (c.v.format !== "tsv") line.push("--format " + c.v.format);
      if (!c.v.sqlite) line.push("--no-sqlite");
      if (c.v.region) line.push("--region " + c.v.region);
      if (c.v.allAlleles) line.push("--all-alleles");
      return { hours: null, timeText: "minutes", disk: [[c.M.systemDisk, 0.4 * WGSM.v2region.share(c.v.region)]], command: line,
        makes: "Makes " + c.file.stem + "_variants." + c.v.format + (c.v.sqlite ? " and .sqlite" : "") + " in " + c.name + "’s folder." };
    },
  });

  T.define({
    slug: "pharmacogenomics", label: "Pharmacogenomics", op: "vcf/pharmcat", group: "Annotate", keywords: "pharmcat medicines drugs cpic cyp2c19",
    desc: "PharmCAT reads the genes that affect how some medicines work, such as CYP2C19 and SLCO1B1, and reports each gene’s type with the published dosing guidance.",
    accepts: VCF, file: "helen", button: "Run PharmCAT", requires: ["pharmcat"],
    fields: [
      f.toggle("absent", "Missing positions match the reference", "For a whole-genome VCF that lists only differences (not a gVCF): a position it doesn’t mention matches the reference", "absent_to_ref", true),
      f.select("outside", "Genes typed elsewhere", "CYP2D6 and HLA results from typing, which PharmCAT can’t read from a VCF", "outside_call", [["none", "None"], ["cyp2d6", "CYP2D6 from typing (needs reads)"]], "none", { width: 280 }),
    ],
    more: CONFIDENCE,
    run: function (c) {
      var line = vcfIn(c, "vcf pharmcat");
      if (c.v.absent) line.push("--absent-to-ref");
      if (c.v.outside !== "none") line.push("--outside-call cyp2d6.tsv");
      return {
        hours: null, timeText: "minutes",
        notes: [{ tone: "info", icon: "info", id: "caveat", title: "Not medical advice", text: "Don’t change a medicine because of this report. Take it to a doctor or pharmacist." }],
        command: confidence(c, line), makes: "A report per gene in " + c.name + "’s folder.",
      };
    },
  });

  var TYPING = [
    ["hla", "HLA", "HLA-A, -B, -C, -DRB1, -DQB1 and others, for transplant matching and research", "t1k", "typing hla"],
    ["cyp2d6", "CYP2D6", "Star alleles for about a quarter of common medicines", "cyrius", "typing cyp2d6"],
    ["smn", "SMN1 and SMN2", "Copy numbers, for spinal muscular atrophy carrier status", "smn", "typing smn"],
    ["str", "Repeat expansions", "Repeats such as HTT and FMR1", "expansionhunter", "typing str"],
  ];

  T.define({
    slug: "typing", label: "Gene typing", op: "typing/hla · typing/cyp2d6 · typing/smn · typing/str", group: "Annotate",
    keywords: "hla cyp2d6 smn str expansionhunter t1k cyrius repeat",
    desc: "Reads genes too variable for ordinary variant calling: the HLA genes of the immune system, CYP2D6 for medicines, SMN1 and SMN2, and repeat expansions.",
    accepts: ["reads"], file: "reginald", button: "Start typing",
    checklist: { key: "genes", title: "Which genes?", items: TYPING.map(function (t, i) {
      return { value: t[0], label: t[1], desc: t[2], on: i === 0 };
    }) },
    fields: [
      f.select("caller", "Repeat caller", "Auto: LongTR for long reads, ExpansionHunter for others", "caller", [["auto", "Auto: ExpansionHunter"], ["expansionhunter", "ExpansionHunter"], ["longtr", "LongTR"], ["strkit", "STRkit"]], "auto", {
        width: 240,
        when: function (c) {
          return c.v.genes.indexOf("str") >= 0;
        },
      }),
      f.seg("sex", "Sex for repeats", function (c) {
        return "Read from the genome: " + c.file.sex;
      }, "sex", [["auto", "From the genome"], ["male", "Male"], ["female", "Female"]], "auto", {
        when: function (c) {
          return c.v.genes.indexOf("str") >= 0;
        },
      }),
    ],
    run: function (c) {
      var out = { count: c.v.genes.length + (c.v.genes.length === 1 ? " gene set" : " gene sets"), hours: null, timeText: "minutes per gene set", notes: [], command: [] };
      var lines = [];
      TYPING.forEach(function (t) {
        if (c.v.genes.indexOf(t[0]) < 0) return;
        var why = c.no(t[3]);
        if (why) {
          var missing = /isn’t installed$/.test(why);
          out.notes.push({ tone: "warn", icon: missing ? "wrench" : "triangle-alert", id: "no-" + t[0], title: t[1] + ": " + why,
            text: t[0] === "cyrius" || t[3] === "cyrius" ? "Cyrius comes from its authors under a licence that doesn’t allow WGS Extract to bundle it; install it only if you need CYP2D6." : missing ? "An optional tool set: pixi install --locked -e " + c.m.env(t[3]) + "." : "",
            action: missing ? { label: "How to install", href: "install.html" } : null });
          out.blocked = "Untick what this computer can’t run, or install it.";
        }
        var line = "wgsextract " + t[4] + " --input " + c.m.path(c.file);
        if (t[0] === "str" && c.v.caller !== "auto") line += " --caller " + c.v.caller;
        if (t[0] === "str" && c.v.sex !== "auto") line += " --sex " + c.v.sex;
        lines.push(line);
      });
      out.command = [lines.join("\n")];
      if (!c.v.genes.length) out.blocked = "Choose at least one.";
      out.makes = "Results appear on " + c.name + "’s page.";
      return out;
    },
  });
})();
