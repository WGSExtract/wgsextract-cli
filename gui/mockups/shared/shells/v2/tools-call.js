/* Variant calling. Rates: bcftools SNPs 102 s and indels 155 s for 5M read
 * pairs (1.5 Gb) on 9 threads with wgse-mimalloc, 132 s and 193 s without
 * (docs/hardware.md, bcftools with mimalloc). Native DeepVariant on Apple
 * Silicon: 3 h 46 min for a 35x genome on 6 threads of an M1, inference at
 * 1 GiB + 12 MiB per batch item plus 1 GiB, ~17 GB of workspace
 * (apple-silicon-16gb.md); Google's CPU image 60 thread-hours
 * (resource_estimates.md). Where the docs give no rate, the time is the CLI
 * help's own words. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var T = WGSM.v2tools;
  var f = T.f;

  function regionShare(c) {
    return WGSM.v2region.share(c.v.region, c.file.build);
  }

  var EVIDENCE = function (cost) {
    return f.param("reference_evidence", "Matching sites too", "A gVCF also records sites that match the reference, needed when combining family genomes (" + cost + ")",
      { def: "none", labels: { none: "Variants only", gvcf: "Also a gVCF" } });
  };

  var READS = ["reads"];

  T.define({
    slug: "call-variants", label: "Variant calling", ops: ["vcf/call"], group: "Call variants", keywords: "bcftools vcf snp indel delly sv cnv",
    lesson: { href: "learn-variants.html", label: "Variants & confidence" },
    example: { href: "viewer.html?spot=smalldel", label: "a 4-letter deletion Helen passed to Reginald" },
    desc: "Call selected variant types in one run: bcftools for SNPs and indels, a caller matched to the reads for structural variants, and Delly for copy-number changes. Keep separate VCFs or merge them; a gVCF also records reference evidence.",
    learn: "Start here when you have a BAM or CRAM and want a VCF for annotation, health-related lookups or a small file to share. It handles a 30× genome well, though if your lab already sent a VCF you may not need to call variants again.\n\n" +
      "DeepVariant makes fewer mistakes, especially with indels, but takes far longer; GATK matches what many labs run; FreeBayes is a second opinion. Ask for a gVCF too if you’ll combine family members’ genomes later.",
    accepts: READS, file: "reginald", button: "Call variants",
    checklist: { key: "kinds", title: "What to call", items: [
      { value: "snp", label: "SNPs", desc: "Single-letter changes, about 4 million in a genome", on: true },
      { value: "indel", label: "Indels", desc: "Small insertions and deletions, about 0.8 million", on: true },
      { value: "sv", label: "Structural variants", desc: "Delly for short reads; pbsv or Sniffles for long reads" },
      { value: "cnv", label: "Copy-number changes", desc: "Delly compares read depth" },
    ] },
    fields: [f.region(), EVIDENCE("writes SNPs and indels together as bcftools.g.vcf.gz"), f.param("merge", "One merged VCF", "Write variants.vcf.gz, or variants.g.vcf.gz with reference evidence")],
    requests: function (c, base) {
      return [base("vcf/call", { types: c.v.kinds.join(",") })];
    },
    moreTitle: "Resources",
    more: [
      f.param("sv_caller", "Structural-variant caller", "Auto: match the reads", { unset: "Auto", labels: { delly: "Delly", pbsv: "pbsv", sniffles: "Sniffles" } }),
      f.param("ploidy", "SNP/indel ploidy", "Empty: inferred sex and build"),
      f.param("cnv_ploidy", "Copy-number baseline", "Empty: 2"),
      f.param("qc_min_depth", "Warn below depth", "Warns when the mean depth is lower; 0 turns it off. Empty: 15 (3 on Y and mtDNA)", { width: 90 }),
      f.threads(),
    ],
    run: function (c) {
      var kinds = c.v.kinds;
      var threads = c.v.threads || c.M.cores;
      var fast = c.M.tools["wgse-mimalloc"];
      var perGb = { snp: (fast ? 102 : 132) * 9 / 1.5 / 3600, indel: (fast ? 155 : 193) * 9 / 1.5 / 3600 };
      var hours = kinds.reduce(function (sum, k) {
        return sum + (perGb[k] || 0) * c.file.gb * regionShare(c) / threads;
      }, 0);
      if (c.v.reference_evidence === "gvcf" && kinds.length) hours += (perGb.snp + perGb.indel) * c.file.gb * regionShare(c) / threads;
      return {
        count: c.v.merge ? "1 merged VCF" : kinds.length + (kinds.length === 1 ? " called type" : " called types"),
        hours: kinds.indexOf("sv") >= 0 || kinds.indexOf("cnv") >= 0 ? null : hours, timeText: "minutes to hours",
        memGB: 0.35 * threads + 1, disk: [[c.m.diskName(c.file), 1.2 * regionShare(c)]],
        checks: [["bcftools", "1.21" + (fast ? ", with wgse-mimalloc" : ", system allocator"), "ok", "Found"]],
        blocked: kinds.length ? null : "Choose at least one variant type.",
        button: "Call with " + [kinds.some(function (k) { return k === "snp" || k === "indel"; }) ? "bcftools" : "", kinds.indexOf("sv") >= 0 ? c.v.sv_caller || (c.file.platform === "ont" ? "Sniffles" : "Delly") : "", kinds.indexOf("cnv") >= 0 ? "Delly CNV" : ""].filter(Boolean).join(", "),
        makes: "Saved in " + c.name + "’s folder.", jobName: "SNPs and indels for " + c.name,
      };
    },
  });

  /* DeepVariant engines on this computer: [value, label, small] */
  function dvEngines(c) {
    var M = c.M;
    if (M.os === "macos" && !M.appleSilicon) return [["docker", "Google’s image", "Google’s CPU image in Docker"]];
    if (M.os === "macos") {
      return [["native", "Native", "wgse-deepvariant: no Docker, inference on the Metal GPU"], ["docker", "Google’s image", "In Docker, x86-64 emulated on Apple Silicon: much slower"]];
    }
    if (M.platform === "linux-arm64") return [["native", "Native", "The native CPU build for ARM64; WGS model only"]];
    var list = [["native", "Native", "wgse-deepvariant: no Docker" + (M.nvidia ? ", inference on the NVIDIA GPU" : ", on the CPU")], ["docker", "Google’s image", "Google’s CPU image in Docker"]];
    if (M.parabricks) list.push(["parabricks", "Parabricks", "NVIDIA Parabricks on the GPU, in Docker"]);
    return list;
  }

  function dvThreadHours(c, engine) {
    var M = c.M;
    var depth = c.file.depth / 30;
    if (engine === "parabricks") return null;
    if (engine === "docker") return 60 * depth * (M.appleSilicon ? 3 : 1);
    if (M.appleSilicon) return 3.77 * 6 * (c.file.depth / 35);
    if (M.platform === "linux-arm64") return null;
    return (M.nvidia ? 12 : 28) * depth;
  }

  T.define({
    slug: "deepvariant", label: "DeepVariant", ops: ["vcf/deepvariant"], group: "Call variants", keywords: "google deep learning",
    lesson: { href: "learn-files.html#calling", label: "How variant calling works" },
    desc: "Google’s deep-learning caller gives the most accurate SNP and indel calls here, at a slower pace than bcftools.",
    learn: "Pick DeepVariant when accuracy matters more than speed, such as making a VCF to keep, checking health-related variants or comparing with a lab’s calls. Its trained neural network scores pileup images around each position and makes fewer mistakes than classic callers, especially with indels.\n\n" +
      "Its model must match the data: there are models for Illumina whole genomes, exomes, PacBio and Nanopore. It’s much slower than bcftools on a CPU and faster with a GPU. For a quick first VCF, bcftools is enough.",
    accepts: READS, file: "reginald", button: "Start DeepVariant", dryRun: true, requires: ["deepvariant"],
    fields: [
      f.show("Engine", "Chosen when DeepVariant was set up on this computer", function (c) {
        return dvEngines(c)[0][2];
      }),
      f.param("gpu", "Use the GPU", function (c) {
        return c.M.parabricks ? "Parabricks, or wgse-deepvariant’s inference, on the NVIDIA GPU" : "";
      }, {
        disabled: function (c) {
          return c.M.nvidia && c.M.os === "linux" ? null : c.M.metal ? "Apple Silicon’s GPU is used without asking" : "Needs Linux with an NVIDIA GPU";
        },
      }),
      f.param("model_type", "Model", "Read from the file: Illumina whole genome", {
        as: "select", unset: "From the reads", width: 280,
        labels: { WGS: "Whole genome (WGS)", WES: "Exome (WES)", PACBIO: "PacBio HiFi", HYBRID_PACBIO_ILLUMINA: "PacBio + Illumina", ULTIMA: "Ultima", ONT_R104: "Nanopore R10.4.1" },
      }),
      f.region(),
      EVIDENCE("adds about 5% to run time, or a quarter with Parabricks"),
    ],
    more: [
      f.show("Sex and PAR policy", "The native caller requires wgse-deepvariant 0.4.0 or later; inferred from sex and the verified reference build",
        "Haploid X/Y outside pseudoautosomal regions; diploid inside PAR. Reference blocks split at PAR boundaries."),
      f.param("batch_size", "Inference batch", "Smaller uses less memory; the same calls. Empty: 512 on Apple Silicon, 1024 elsewhere", { width: 90 }),
      f.threads(),
      f.param("restart", "Start over", "Ignore stages an earlier run finished"),
    ],
    run: function (c) {
      var engines = dvEngines(c);
      var engine = c.v.gpu && c.M.parabricks ? "parabricks" : engines[0][0];
      var threads = c.v.threads || c.M.cores;
      var th = dvThreadHours(c, engine);
      var hours = th === null ? null : th * regionShare(c) / threads;
      if (hours && c.v.reference_evidence === "gvcf") hours *= 1.05;
      var mem = engine === "parabricks" ? 25 : 1 + Number(c.v.batch_size || (c.M.metal ? 512 : 1024)) * 12 / 1024 + 1 + threads * (c.M.arch === "arm64" ? 2 : 1) * 0.5;
      if (c.v.reference_evidence === "gvcf" && engine === "native") mem = Math.max(mem, 20);
      var out = {
        hours: hours, timeText: engine === "parabricks" ? "about a minute per chromosome the size of chr20" : "hours on a 30x genome",
        memGB: mem, disk: [[c.m.diskName(c.file), 17.3 * regionShare(c) + 0.4]], notes: [],
        checks: [["Engine", engines.filter(function (e) {
          return e[0] === engine;
        })[0][2], "ok", "Found"]],
        makes: "Makes " + c.file.stem + ".deepvariant.vcf.gz in " + c.name + "’s folder.", jobName: "DeepVariant for " + c.name,
      };
      if (c.v.model_type === "ONT_R104" && c.M.arch !== "x86-64") out.blocked = c.m.unavailable("dv-ont") || "The Nanopore model needs an x86-64 computer.";
      if (c.v.model_type && c.v.model_type !== "WGS" && c.M.platform === "linux-arm64") out.blocked = c.m.unavailable("dv-models") + ".";
      if (engine === "docker" && c.M.appleSilicon) out.notes.push({ tone: "warn", icon: "clock", id: "docker", title: "Google’s image is x86-64", text: "Docker emulates x86-64 on Apple Silicon and runs several times slower than the native engine, which makes the same calls." });
      return out;
    },
  });

  T.define({
    slug: "gatk", label: "GATK HaplotypeCaller", ops: ["vcf/gatk"], group: "Call variants", keywords: "broad java",
    lesson: { href: "learn-files.html#calling", label: "How variant calling works" },
    desc: "The Broad Institute’s variant caller, which many labs use.",
    learn: "Many clinical and research labs build their VCFs with GATK, and some downstream tools expect its output. It rebuilds the sequence locally around each candidate change, which helps with indels.\n\n" +
      "It needs Java and is the slowest caller here. Its gVCFs are the usual route to calling a family together (joint calling). For one person, DeepVariant is usually more accurate and bcftools much faster.",
    accepts: READS, file: "reginald", button: "Start HaplotypeCaller", requires: ["gatk"],
    fields: [
      f.region(), EVIDENCE("adds little time; the VCF is genotyped from it"),
      f.param("scatter", "Split into intervals", "", {
        disabled: function (c) {
          return c.file.platform === "ultima" ? null : "Only for Ultima reads (flow mode)";
        },
      }),
      f.param("gpu", "NVIDIA Parabricks", "HaplotypeCaller on the GPU, in Docker", {
        disabled: function (c) {
          return c.M.parabricks ? null : "Needs Linux with an NVIDIA GPU of 16 GB or more";
        },
      }),
    ],
    run: function (c) {
      return {
        hours: null, timeText: c.v.gpu ? "about a minute per chromosome like chr20" : "hours on a 30x genome", memGB: c.v.gpu ? 18 : 6, disk: [[c.m.diskName(c.file), 0.6]],
        makes: "Makes " + c.file.stem + ".gatk.vcf.gz in " + c.name + "’s folder.",
      };
    },
  });

  T.define({
    slug: "freebayes", label: "FreeBayes", ops: ["vcf/freebayes"], group: "Call variants", keywords: "haplotype bayesian",
    lesson: { href: "learn-variants.html#confidence", label: "Evidence & confidence" },
    desc: "A haplotype-based caller that finds SNPs, indels and small complex changes together. Useful as a second opinion beside bcftools or DeepVariant.",
    learn: "Use it when you want an independent second set of calls: positions where FreeBayes and bcftools or DeepVariant disagree are the ones worth checking by eye. It considers short stretches of reads together, so it handles changes close to each other well.\n\n" +
      "On its own it’s slower than bcftools and less accurate than DeepVariant, so it’s rarely the first choice. Like the others, it makes a VCF that every annotation tool can read.",
    accepts: READS, file: "reginald", button: "Start FreeBayes", requires: ["freebayes"],
    fields: [f.region()],
    run: function (c) {
      return {
        hours: null, timeText: "hours on a 30x genome", disk: [[c.m.diskName(c.file), 1.2]],
        checks: [["FreeBayes", "1.3.8", "ok", "Found"]],
        makes: "Makes " + c.file.stem + ".freebayes.vcf.gz in " + c.name + "’s folder.",
      };
    },
  });

  T.define({
    slug: "clair3", label: "Clair3", ops: ["vcf/clair3"], group: "Call variants", keywords: "nanopore pacbio long read",
    lesson: { href: "learn-sequencing.html#platforms", label: "Short- and long-read technologies" },
    desc: "A deep-learning caller for Nanopore and PacBio long reads. Its model must match the basecaller that made the reads.",
    learn: "Short-read callers struggle on Oxford Nanopore and PacBio genomes, which have different error patterns. Clair3’s neural network learns each sequencing chemistry’s typical errors, so the model has to match how the reads were made.\n\n" +
      "Long reads reach repeats and duplicated genes that short reads can’t, so a long-read VCF covers more of the genome. For short Illumina reads, use bcftools, DeepVariant or GATK instead.",
    accepts: READS, file: "reginald", button: "Start Clair3", requires: ["clair3"],
    fields: [
      f.param("model", "Model", "Empty: chosen from the reads’ basecaller, such as r1041_e82_400bps_sup_v500", { width: 300 }),
      f.param("gpu", "Use GPU inference", "Explicit opt-in: Apple Silicon uses streaming MPS with the WGSE-built caller; Linux NVIDIA uses the Clair3 GPU image", {
        disabled: function (c) { return c.M.appleSilicon || c.M.nvidia && c.M.os === "linux" ? null : "Needs Apple Silicon MPS or Linux with NVIDIA and Docker"; },
      }),
      f.region(), EVIDENCE("about 2.4× CPU time, or 4.3× in the measured GPU run"),
    ],
    run: function (c) {
      var hours = 33 * regionShare(c) / (c.v.threads || c.M.cores);
      return {
        hours: c.v.reference_evidence === "gvcf" ? hours * 4 : hours, memGB: 16, disk: [[c.m.diskName(c.file), 30 * regionShare(c)]],
        notes: [{ tone: "info", icon: "info", id: "long", title: "For long reads", text: c.name + "’s file has Illumina short reads; use DeepVariant or bcftools for them." + (c.M.appleSilicon ? " On Apple Silicon Clair3 runs on the CPU only." : ""), action: { label: "DeepVariant", href: "tool-deepvariant.html" } }],
        blocked: "Clair3 is for Nanopore and PacBio reads, and isn’t installed.",
      };
    },
  });

  T.define({
    slug: "structural-variants", label: "Structural variants", ops: ["vcf/sv"], group: "Call variants", keywords: "delly sniffles pbsv large deletions",
    lesson: { href: "learn-variants.html#types", label: "Different types of variants" },
    example: { href: "viewer.html?spot=bigdel", label: "a deletion’s split reads and depth drop" },
    desc: "Finds large changes that SNP calling misses: deletions, duplications, inversions and moved pieces of DNA, from about 50 letters to millions.",
    learn: "A genome carries thousands of structural variants, and together they change more DNA letters than all its SNPs. Delly finds them in short reads from read pairs that sit oddly and reads split across a break; Sniffles and pbsv use long reads, which catch far more.\n\n" +
      "Most are common and harmless, so add population frequencies (gnomAD-SV) before looking for rare ones. Short-read calls include false positives and miss many insertions, so check the reads before trusting any single call. Copy-number changes gives a depth-based view of the larger deletions and duplications.",
    accepts: READS, file: "reginald", button: "Find structural variants", requires: ["delly"],
    fields: [
      f.param("caller", "Caller", "Empty: Delly for short reads, Sniffles or pbsv for long reads", { unset: "Auto", labels: { delly: "Delly", sniffles: "Sniffles", pbsv: "pbsv (PacBio only)" } }),
      f.region(),
    ],
    moreTitle: "Long reads",
    more: [
      f.param("long_read", "Long reads", "Read from the file: short Illumina reads"),
      f.param("tandem_repeats", "Tandem repeats", "A BED of repeats; recommended for long reads"),
    ],
    run: function (c) {
      var out = {
        hours: null, timeText: "hours on a 30x genome", memGB: 4, disk: [[c.m.diskName(c.file), 0.1]],
        checks: [["Delly", "1.2.6", "ok", "Found"]],
        makes: "Makes a structural-variant VCF in " + c.name + "’s folder.",
      };
      if (c.v.caller === "pbsv") out.blocked = "pbsv reads PacBio data only.";
      if (c.v.caller === "sniffles" && !c.v.long_read) out.warnings = [{ tone: "warn", icon: "triangle-alert", title: "Sniffles is for long reads", text: "On short reads it finds little; Delly suits them." }];
      return out;
    },
  });

  T.define({
    slug: "cnv", label: "Copy-number changes", ops: ["vcf/cnv"], group: "Call variants", keywords: "cnv delly copy number",
    lesson: { href: "learn-variants.html#types", label: "Copy number & larger changes" },
    example: { href: "viewer.html?spot=bigdel", label: "a 1.2 kb deletion Gordon passed to Reginald" },
    desc: "Delly measures read depth across the genome to spot stretches with extra or missing copies.",
    learn: "Larger deletions and duplications, usually thousands of letters or more, show up where read depth drops to about half or rises above the usual two copies. Even coverage matters, so a 30× whole genome suits it.\n\n" +
      "It complements Structural variants: depth is good at large copy changes, while read pairs and split reads find exact break points and inversions, which don’t change depth. Most CNVs appear in healthy people too, so check population frequency before reading much into one.",
    accepts: READS, file: "reginald", button: "Find copy-number changes", requires: ["delly"],
    fields: [f.param("ploidy", "Baseline copies", "Empty: 2, as for most of the genome", { width: 80 }), f.region()],
    run: function (c) {
      return {
        hours: null, timeText: "hours on a 30x genome", memGB: 4, disk: [[c.m.diskName(c.file), 0.1]],
        makes: "Makes a CNV VCF in " + c.name + "’s folder.",
      };
    },
  });

  T.define({
    slug: "phase", label: "Phase variants", ops: ["vcf/phase"], group: "Call variants", keywords: "whatshap longphase haplotag",
    lesson: { href: "learn-variants.html#phasing", label: "Phasing & chromosome copies" },
    desc: "Works out which variants came together from the same parent, using reads that span more than one. Needs the person’s reads and a VCF of their variants.",
    learn: "Knowing which chromosome copy carries each variant matters when two changes fall in the same gene: on the same copy, the other copy still works; on opposite copies, both copies are affected.\n\n" +
      "Reads only link variants close enough to share a read, producing phased blocks instead of whole chromosomes; long reads build much longer blocks. With both parents’ genomes, Family trio phases by inheritance instead, and imputation phases statistically from a reference panel.",
    accepts: READS, file: "reginald", button: "Phase variants",
    requests: function (c, base) {
      return [base("vcf/phase", { vcf_input: c.m.realPath(c.m.outdir(c.file) + c.file.stem + ".snps.vcf.gz") })];
    },
    fields: [
      f.param("phaser", "Phaser", "Empty: LongPhase for long reads when installed, else WhatsHap", { unset: "Auto", labels: { whatshap: "WhatsHap", longphase: "LongPhase" } }),
      f.param("haplotag", "Tag the reads", "Also write reads marked with the copy each came from"),
      f.region(),
    ],
    run: function (c) {
      var out = {
        hours: null, timeText: "hours on a 30x genome", disk: [[c.m.diskName(c.file), c.v.haplotag ? c.file.size : 0.3]],
        notes: [{ tone: "warn", icon: "variant", id: "needs-vcf", title: c.name + " has no VCF yet", text: "Phasing sorts existing calls into the two copies of each chromosome. Call variants first.", action: { label: "Call variants", href: "tool-call-variants.html" } }],
        blocked: "Call variants for " + c.name + " first.",
      };
      if (c.v.phaser === "longphase") out.blocked = "LongPhase is for Nanopore and PacBio reads.";
      return out;
    },
  });

  T.define({
    slug: "trio", label: "Family trio", ops: ["vcf/trio"], group: "Call variants", keywords: "de novo recessive compound heterozygous parents",
    lesson: { href: "learn-variants.html#confidence", label: "Inheritance & call confidence" },
    example: { href: "viewer.html?spot=denovo", label: "a new variant only Reginald has" },
    desc: "Compares a child with both parents to find variants new in the child (de novo) or inherited from both sides (recessive and compound heterozygous).",
    learn: "With genomes for a child and both biological parents, comparing all three traces which variants each parent passed on, and spots changes that are new in the child. The result keeps only the child’s variants that fit the inheritance pattern you pick.\n\n" +
      "Everyone has around 50 to 100 new mutations, almost all harmless, and many candidates turn out to be sequencing errors. Variants inherited from both sides in one gene matter mainly for rare recessive conditions. These results are for research and cannot diagnose a condition; a genetic counsellor can explain what a finding means.",
    accepts: READS, file: "reginald", button: "Analyse the trio",
    requests: function (c, base) {
      var M = c.m;
      var req = base("vcf/trio", { proband: M.realPath(M.path(c.file)), mother: M.realPath(M.path(M.files.helen)), father: M.realPath(M.path(M.files.gordon)) });
      delete req.params.input;
      return [req];
    },
    fields: [
      f.show("Mother", "Helen’s VCF", function () {
        return "helen-mcdonald.deepvariant.vcf.gz";
      }),
      f.show("Father", "Gordon’s BAM", function () {
        return "60820188479016.bam";
      }),
      f.param("mode", "Look for", "", { labels: { denovo: "New in the child", recessive: "Recessive", comphet: "Compound het.", all: "All" } }),
      f.param("min_depth", "Minimum depth", "From reads: in each person, at a reported site. Empty: 15", { width: 80 }),
      f.region(),
    ],
    run: function (c) {
      var out = {
        hours: null, timeText: "minutes on a 30x genome",
        makes: "A VCF of the matching variants, in " + c.name + "’s folder.",
      };
      if (c.v.mode === "denovo" || c.v.mode === "all") {
        out.warnings = [{ tone: "warn", icon: "triangle-alert", id: "denovo", title: "New variants need everyone’s reads", text: "Helen has only a VCF, which can’t tell “not there” from “not looked at”. Add her BAM or CRAM for de novo calls; recessive and compound heterozygous work now." }];
        out.blocked = "Add Helen’s reads, or look for recessive variants.";
      }
      return out;
    },
  });

  T.define({
    slug: "impute", label: "Imputation", ops: ["vcf/impute"], group: "Call variants", keywords: "glimpse beagle low pass kit",
    lesson: { href: "learn-variants.html#phasing", label: "Estimated versus measured genotypes" },
    desc: "Estimates missing genotypes from the 1000 Genomes haplotype panel, using GLIMPSE2 on low-coverage reads or Beagle on a VCF of genotypes such as chip calls.",
    learn: "Low-coverage genomes (about 0.5–8×) and DNA-test kits leave millions of sites unread; matching the sites they did read against the 3,202 people in the 1000 Genomes panel fills in many of the rest on GRCh38.\n\n" +
      "Imputed genotypes carry a probability, are least certain for rare variants, and cover only sites present in the panel. A 30× genome already reads nearly every position directly, and imputation does not replace sequencing.",
    accepts: ["vcf", "reads"], file: "reginald", button: "Impute",
    fields: [
      f.param("chromosomes", "Chromosomes", "Such as chr20 or chr1-22. Empty: every installed one", { width: 160 }),
      f.param("min_gp", "Minimum certainty", "Genotypes below this probability are left blank. Empty: 0.9 (GLIMPSE2), 0.95 (Beagle)", { width: 90 }),
    ],
    run: function (c) {
      var kit = c.file.kind === "vcf";
      /* a VCF goes to Beagle as vcf_input (requests.js); reads to GLIMPSE2 as input */
      var out = {
        hours: kit ? 0.75 : 2.8 * 8 / (c.v.threads || c.M.cores), memGB: kit ? 3.4 : 3.1, disk: [[c.M.systemDisk, 31]],
        checks: [[kit ? "Beagle" : "GLIMPSE2", kit ? "5.4" : "2.0.1", "ok", "Found"]],
        notes: c.flag("panel-1kg") ? [] : [{ tone: "warn", icon: "download", id: "panel", title: "The 1000 Genomes panel isn’t installed", text: "Phased haplotypes for chr1–22: about 30 GB.", action: { label: "Download 30 GB", set: "panel-1kg", toast: "Downloading the 1000 Genomes panel (30 GB). See Jobs." } }],
        makes: "Makes an imputed VCF in " + c.name + "’s folder.",
      };
      if (!c.flag("panel-1kg")) out.blocked = "Download the panel first.";
      if (!kit) out.warnings = [{ tone: "info", icon: "info", title: "At " + c.file.depth + "× there is little to fill in", text: "Imputation helps genomes under about 4× and chip genotypes; a full-depth genome already reads nearly every site." }];
      else out.warnings = [{ tone: "info", icon: "info", title: "A DNA-test kit has to be a VCF first", text: "Beagle reads chip genotypes as a VCF. A raw kit file such as Alex’s 23andMe text isn’t one, and the API has no converter yet." }];
      return out;
    },
  });
})();
