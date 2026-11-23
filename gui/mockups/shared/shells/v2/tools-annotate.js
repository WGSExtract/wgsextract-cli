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

  function download(flag, label, size, extra) {
    return Object.assign({ tone: "warn", icon: "download", id: flag, title: label + " isn’t installed",
      text: "Public reference data; nothing about this genome is sent. It downloads only when you ask.",
      action: { label: "Download" + (size ? " " + size : ""), set: flag, toast: "Downloading " + label + ". See Jobs." } }, extra);
  }

  /* LOW,HIGH thresholds: arrays of numbers in the API */
  var CONFIDENCE = [
    f.param("confidence_depth", "Confidence: read depth", "LOW,HIGH: below LOW a call is low confidence, below HIGH moderate. Empty: 10,20", { width: 110 }),
    f.param("confidence_gq", "Confidence: genotype quality", "The same, by genotype quality. Empty: 20,30", { width: 110 }),
  ];

  T.define({
    slug: "vep", label: "VEP", ops: ["vep/run"], group: "Annotate", keywords: "variant effect predictor consequence gene transcript ensembl vep-rs",
    lesson: { href: "learn-health.html#annotation", label: "Annotation is context, not a diagnosis" },
    desc: "Runs the Variant Effect Predictor to label each variant with its gene, transcript and predicted effect on the protein.",
    learn: "After calling variants, VEP tells you what each one touches: which gene it falls in, and whether it changes a protein’s building blocks (missense), cuts the protein short, or sits between genes, as most do.\n\n" +
      "Consequence labels describe how the sequence changes without saying whether health is affected. ClinVar adds what labs have reported about known variants, Frequencies and scores adds population frequencies and model predictions, and Variant table puts them side by side for searching.\n\n" +
      "The selected engine needs a local cache for your build (including Ensembl VEP); WGS Extract does not quietly switch to an online lookup. vep-rs supports T2T-CHM13v2.0, but Ensembl VEP has no T2T cache here.\n\n" +
      "Starting from BAM/CRAM calls variants first. T2T and unknown builds need explicit ploidy or a ploidy file for that step; an existing VCF does not need it.",
    accepts: ["vcf", "reads"], file: "helen", button: "Annotate with VEP",
    fields: [
      f.param("engine", "Engine", function (c) {
        return c.v.engine === "ensembl" ? "Ensembl’s own VEP, the reference implementation: hours, and its own 26 GB cache" : "vep-rs: a fast Rust VEP, the default on every platform";
      }, { def: "vep-rs", labels: { "vep-rs": "vep-rs", ensembl: "Ensembl VEP" } }),
      f.param("preset", "What to add", function (c) {
        return c.v.preset === "fast" ? "Consequences, genes, MANE transcripts, SIFT and PolyPhen; no population frequencies or HGVS names" : "Everything VEP knows, including population frequencies and HGVS names";
      }, { labels: { everything: "Everything", fast: "Fast" } }),
      f.param("format", "Output", "", { labels: { vcf: "VCF", tab: "Table", json: "JSON" } }),
    ],
    more: [
      f.param("vep_assembly", "Assembly", "Empty: read from the VCF", { as: "select", unset: "From the VCF", labels: { "T2T-CHM13v2.0": "T2T-CHM13 v2.0" } }),
      f.param("ploidy", "Ploidy", "Explicit ploidy for calling from BAM or CRAM", { when: function (c) { return c.file.kind === "reads"; } }),
      f.param("ploidy_file", "Ploidy file", "Optional file specifying ploidy by region", { when: function (c) { return c.file.kind === "reads"; } }),
      f.region(),
    ],
    run: function (c) {
      var ensembl = c.v.engine === "ensembl";
      var build = c.v.vep_assembly || c.file.build;
      var t2t = /^(T2T|chm13)/i.test(build || "");
      var assembly = t2t ? "T2T-CHM13v2.0" : build;
      var cacheFlag = "vepcache-" + c.v.engine + (assembly === "GRCh38" ? "" : ":" + assembly);
      var minutes = ensembl ? 214 : 8.8;
      if (c.v.preset === "fast") minutes /= 2;
      var hours = (minutes / 60) * 6 / c.M.cores * WGSM.v2region.share(c.v.region);
      var out = {
        hours: hours, memGB: ensembl ? 1.4 : 7.5, disk: [[c.M.systemDisk, 0.6]], notes: [],
        makes: "Makes " + c.file.stem + ".vep." + (c.v.format === "tab" ? "tsv" : c.v.format === "json" ? "json" : "vcf.gz") + " in " + c.name + "’s folder.",
      };
      if (ensembl && c.m.unavailable("vep-ensembl")) out.blocked = c.m.unavailable("vep-ensembl") + ".";
      if (ensembl && t2t) out.blocked = "Ensembl VEP has no T2T-CHM13v2.0 cache; choose vep-rs.";
      if (c.file.kind === "reads" && !["GRCh37", "GRCh38"].includes(c.file.build) && !c.v.ploidy && !c.v.ploidy_file) {
        out.blocked = "Set the ploidy or a ploidy file before calling from T2T or an unknown build.";
      }
      if (!out.blocked && !c.flag(cacheFlag)) {
        out.notes.push(download(cacheFlag, (ensembl ? "Ensembl VEP’s " : "vep-rs’s ") + assembly + " cache", t2t ? "1 GB" : ensembl ? "27.6 GB" : "17 GB",
          { text: (t2t ? "1.2 GB on disk; no known-variant frequencies in this cache." : "A separate cache is needed for each build and engine.") +
            " Public data; nothing about this genome is sent." }));
        out.blocked = out.blocked || "Download the cache first.";
      }
      return out;
    },
  });

  T.define({
    slug: "clinvar", label: "ClinVar", ops: ["vcf/clinvar"], group: "Annotate", keywords: "pathogenic benign clinical significance health",
    lesson: { href: "learn-health.html#clinvar", label: "Reading ClinVar evidence" },
    desc: "Matches your variants against ClinVar, adding any reported classification from benign to pathogenic, the conditions submitters named and each call’s confidence.",
    learn: "ClinVar collects interpretations that labs and researchers have submitted for known variants, making it a straightforward way to check a familiar gene. Because it only covers previously submitted changes, a new or very rare variant won’t be listed; VEP and Frequencies and scores help with those.\n\n" +
      "Expect a few “pathogenic” entries. Most people carry some, usually for recessive conditions they don’t have, and some listings are old, disputed or based on little evidence; the review stars show how well supported each one is.\n\n" +
      "This isn’t a diagnosis or a clinical test. If something worries you, talk to a doctor or genetic counsellor, who can confirm it with a clinical test.",
    accepts: VCF, file: "helen", button: "Annotate with ClinVar",
    fields: [f.param("clinvar_file", "ClinVar file", "Empty: the release installed with Reference data (2026-11-02)", { width: 300 })],
    more: CONFIDENCE,
    run: function (c) {
      return {
        hours: null, timeText: "minutes",
        notes: [{ tone: "info", icon: "info", id: "caveat", title: "Not a diagnosis",
          text: "ClinVar records what labs have reported. Many entries conflict or are out of date, and most people carry a few variants marked pathogenic without being affected. Talk to a genetic counsellor about anything that worries you." }],
        makes: "Makes " + c.file.stem + ".clinvar.vcf.gz and adds a summary to " + c.name + "’s page.",
      };
    },
  });

  /* [value, label, size, about, operation, flag of its data, filter field] */
  var SCORES = [
    ["gnomad", "gnomAD frequencies", "≈ 26 GB", "How common each variant is in 800,000 people", "vcf/gnomad", "data-gnomad", "max_af"],
    ["revel", "REVEL", "≈ 0.6 GB", "How likely a missense change is to matter", "vcf/revel", "data-revel", "min_score"],
    ["alphamissense", "AlphaMissense", "", "DeepMind’s predicted effect of missense changes", "vcf/alphamissense", "data-alphamissense", "min_score"],
    ["cadd", "CADD", "≈ 85 GB", "How harmful any change may be, as a PHRED score", "vcf/cadd", "data-cadd", "min_score"],
    ["spliceai", "SpliceAI", "", "Whether a change may disturb splicing", "vcf/spliceai", "data-spliceai", null],
    ["gerp", "GERP++ conservation", "", "How strongly evolution has preserved a position", "vcf/gerp", "data-gerp", "min_score"],
    ["phylop", "phyloP conservation", "≈ 10 GB", "Conservation or faster-than-expected evolution at a position", "vcf/phylop", "data-phylop", "min_score"],
  ];

  T.define({
    slug: "scores", label: "Frequencies and scores", ops: ["vcf/gnomad", "vcf/revel", "vcf/alphamissense", "vcf/cadd", "vcf/spliceai", "vcf/gerp", "vcf/phylop"], group: "Annotate",
    lesson: { href: "learn-health.html#scores", label: "Prediction scores & population frequencies" },
    keywords: "gnomad revel cadd alphamissense spliceai gerp phylop conservation allele frequency pathogenicity",
    desc: "Annotates each variant with its gnomAD population frequency and model scores for protein impact, splicing and evolutionary conservation.",
    learn: "Clinicians reviewing a whole genome usually start with the rare variants that also score high on prediction models. gnomAD says how common each change is; REVEL and AlphaMissense rate changes to a protein’s building blocks, CADD rates any change, and SpliceAI looks at how a gene is cut and joined.\n\n" +
      "GERP++ and phyloP track evolutionary history across species: high positive scores suggest a position has changed less than expected, while negative phyloP scores suggest faster evolution. Conservation on its own does not mean a variant causes disease.\n\n" +
      "Every score here is a statistical prediction: a high number flags a variant as “possibly damaging”, and healthy people carry rare, high-scoring changes too. Pair them with VEP for the gene effect and ClinVar for lab reports. Some downloads are large; CADD is about 85 GB.",
    accepts: VCF, file: "helen", button: "Annotate",
    checklist: { key: "scores", title: "Which?", items: SCORES.map(function (s, i) {
      return { value: s[0], label: s[1], aside: s[2], desc: s[3], on: i < 2 };
    }) },
    fields: [
      f.param("max_af", "Keep only rarer than", "gnomAD: a frequency such as 0.01 (1%); empty keeps all", { width: 100 }),
      f.param("min_score", "Also a high-score copy", "At least this score, in an extra filtered VCF. Scales differ: REVEL / AlphaMissense 0–1, CADD PHRED, conservation has its own scale. Applies to every selected score; empty skips the extra copy", { width: 100 }),
    ],
    more: CONFIDENCE.concat([
      f.param("gerp_file", "Local GERP++ data", "Empty: use installed data", { when: function (c) { return c.v.scores.indexOf("gerp") >= 0; } }),
      f.param("phylop_file", "Local phyloP data", "Empty: use installed data", { when: function (c) { return c.v.scores.indexOf("phylop") >= 0; } }),
    ]),
    requests: function (c, base) {
      return SCORES.filter(function (s) {
        return c.v.scores.indexOf(s[0]) >= 0;
      }).map(function (s) {
        return base(s[4]);
      });
    },
    run: function (c) {
      var picks = c.v.scores;
      var out = { count: picks.length + (picks.length === 1 ? " source" : " sources"), hours: null, timeText: "minutes", notes: [] };
      SCORES.forEach(function (s) {
        var local = s[0] === "gerp" ? c.v.gerp_file : s[0] === "phylop" ? c.v.phylop_file : null;
        if (picks.indexOf(s[0]) >= 0 && !local && !c.flag(s[5])) out.notes.push(download(s[5], s[1], s[2].replace("≈ ", "")));
      });
      if (!picks.length) out.blocked = "Choose at least one.";
      else if (out.notes.length) out.blocked = "Download the data first.";
      out.makes = "Adds columns to " + c.name + "’s VCF; the original stays.";
      return out;
    },
  });

  T.define({
    slug: "variant-table", label: "Variant table", ops: ["vcf/table"], group: "Annotate", keywords: "spreadsheet sqlite tsv parquet search",
    lesson: { href: "learn-variants.html#genotype", label: "How to read variant genotypes" },
    desc: "Turns a VCF into a searchable table, one row per alternate allele, carrying through the genotype and available annotations.",
    learn: "By default, the table exports only alternate alleles the sample carries. When the VCF supplies them, rows include the phase set, alternate-read fraction, selected transcript ID, gnomAD's highest ancestry-group frequency and its homozygote count.\n\n" +
      "Read fraction is not population frequency, and a missing count is not zero. Older gnomAD reference tables may supply allele frequency without group maximum or homozygote count. Missing annotations stay blank. Use SQLite or Parquet (requires pyarrow) for a whole-genome table too large for Excel.",
    accepts: VCF, file: "helen", button: "Make the table",
    fields: [
      f.param("table_format", "Format", "Parquet is smaller and faster for large tables", { labels: { tsv: "TSV", parquet: "Parquet" } }),
      f.param("no_sqlite", "Only the table", "Skip building the SQLite database used to search by gene or position here and on the phone"),
      f.region(),
    ],
    more: [
      f.param("all_alleles", "Alleles not carried", "Also list alleles at the site the person doesn’t have"),
      f.param("pharmcat_json", "Medicine results", "PharmCAT’s phenotype.json, to add its results to rows in those genes"),
    ],
    run: function (c) {
      return { hours: null, timeText: "minutes", disk: [[c.M.systemDisk, 0.4 * WGSM.v2region.share(c.v.region)]],
        makes: "Makes " + c.file.stem + "_variants." + c.v.table_format + (c.v.no_sqlite ? "" : " and .sqlite") + " in " + c.name + "’s folder." };
    },
  });

  T.define({
    slug: "pharmacogenomics", label: "Pharmacogenomics", ops: ["vcf/pharmcat"], group: "Annotate", keywords: "pharmcat medicines drugs cpic cyp2c19",
    lesson: { href: "learn-health.html#medicines", label: "Genes, medicines & clinical limits" },
    desc: "PharmCAT reads the genes that affect how some medicines work, such as CYP2C19 and SLCO1B1, and reports each gene’s type with the published dosing guidance.",
    learn: "Use it if you’re curious how your genes may affect some medicines, for example CYP2C19 and the blood thinner clopidogrel, or SLCO1B1 and statins. PharmCAT names each gene’s star alleles and the published guideline that applies to them.\n\n" +
      "A home genome can miss variants, and genes such as CYP2D6 are hard to call from short reads alone (Gene typing calls CYP2D6 separately). Because this report isn’t a clinical test, never start, stop or change a medicine based on it; bring it to your doctor or pharmacist, who can order a clinical test if it matters.",
    accepts: VCF, file: "helen", button: "Run PharmCAT", requires: ["pharmcat"],
    fields: [
      f.param("absent_to_ref", "Missing positions match the reference", "For a whole-genome VCF that lists only differences (not a gVCF): a position it doesn’t mention matches the reference", { def: true }),
      f.param("outside_call", "Genes typed elsewhere", "Outside-call files (gene and diplotype), such as CYP2D6 from Gene typing; comma-separated", { width: 300 }),
    ],
    more: CONFIDENCE,
    run: function () {
      return {
        hours: null, timeText: "minutes",
        notes: [{ tone: "info", icon: "info", id: "caveat", title: "Not medical advice", text: "Don’t change a medicine because of this report. Take it to a doctor or pharmacist." }],
        makes: "One report covering every gene, in the person’s folder.",
      };
    },
  });

  var TYPING = [
    ["hla", "HLA", "HLA-A, -B, -C, -DRB1, -DQB1 and others, for transplant matching and research", "t1k", "typing/hla"],
    ["cyp2d6", "CYP2D6", "Star alleles for about a quarter of common medicines", "cyrius", "typing/cyp2d6"],
    ["smn", "SMN1 and SMN2", "Copy numbers, for spinal muscular atrophy carrier status", "smn", "typing/smn"],
    ["str", "Repeat expansions", "Repeats such as HTT and FMR1", "expansionhunter", "typing/str"],
  ];

  T.define({
    slug: "typing", label: "Gene typing", ops: ["typing/hla", "typing/cyp2d6", "typing/smn", "typing/str"], group: "Annotate",
    lesson: { href: "learn-health.html#medicines", label: "Why some genes need specialist analysis" },
    keywords: "hla cyp2d6 smn str expansionhunter t1k cyrius repeat",
    desc: "Reads genes too variable for ordinary variant calling: the HLA genes of the immune system, CYP2D6 for medicines, SMN1 and SMN2, and repeat expansions.",
    learn: "Ordinary variant callers struggle where genes vary enormously between people, sit beside near-identical copies or span long repeats. Specialised tools handle each group from the aligned reads: T1K for HLA, Cyrius for CYP2D6, and ExpansionHunter for repeats.\n\n" +
      "HLA types matter for transplant matching and some drug reactions, CYP2D6 for many medicines, SMN1 copies for spinal muscular atrophy carrier status, and repeat lengths for conditions such as Huntington’s disease. Treat these as research readings from a home genome; anything that matters for your health needs a clinical test.",
    accepts: ["reads"], file: "reginald", button: "Start typing",
    checklist: { key: "genes", title: "Which genes?", items: TYPING.map(function (t, i) {
      return { value: t[0], label: t[1], desc: t[2], on: i === 0 };
    }) },
    fields: [
      f.param("caller", "Repeat caller", "Empty: LongTR for long reads, ExpansionHunter for others", {
        as: "select", unset: "Auto", width: 240, labels: { expansionhunter: "ExpansionHunter", longtr: "LongTR", strkit: "STRkit" },
        when: function (c) {
          return c.v.genes.indexOf("str") >= 0;
        },
      }),
      f.param("sex", "Sex for repeats", function (c) {
        return "Empty: read from the genome (" + c.file.sex + ")";
      }, {
        unset: "From the genome", labels: { male: "Male", female: "Female" },
        when: function (c) {
          return c.v.genes.indexOf("str") >= 0;
        },
      }),
    ],
    requests: function (c, base) {
      return TYPING.filter(function (t) {
        return c.v.genes.indexOf(t[0]) >= 0;
      }).map(function (t) {
        return base(t[4]);
      });
    },
    run: function (c) {
      var out = { count: c.v.genes.length + (c.v.genes.length === 1 ? " gene set" : " gene sets"), hours: null, timeText: "minutes per gene set", notes: [] };
      TYPING.forEach(function (t) {
        if (c.v.genes.indexOf(t[0]) < 0) return;
        var why = c.no(t[3]);
        if (!why) return;
        var missing = /isn’t installed$/.test(why);
        out.notes.push({ tone: "warn", icon: missing ? "wrench" : "triangle-alert", id: "no-" + t[0], title: t[1] + ": " + why,
          text: t[3] === "cyrius" ? "Cyrius comes from its authors under a licence that doesn’t allow WGS Extract to bundle it; install it only if you need CYP2D6." : missing ? "An optional tool set: pixi install --locked -e " + c.m.env(t[3]) + "." : "",
          action: missing ? { label: "How to install", href: "install.html" } : null });
        out.blocked = "Untick what this computer can’t run, or install it.";
      });
      if (!c.v.genes.length) out.blocked = "Choose at least one.";
      out.makes = "Results appear on " + c.name + "’s page.";
      return out;
    },
  });
})();
