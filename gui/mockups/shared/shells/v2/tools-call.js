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

  function base(c, op) {
    return "wgsextract " + op + " --input " + c.m.path(c.file) + " --ref " + (c.file.ref || "hs38");
  }

  function regionShare(c) {
    return WGSM.v2region.share(c.v.region, c.file.build);
  }

  function opts(c, line, keys) {
    keys.forEach(function (k) {
      var value = c.v[k[0]];
      if (k[2] === "flag" && value) line.push(k[1]);
      else if (k[2] !== "flag" && value && value !== k[2]) line.push(k[1] + " " + value);
    });
    return line;
  }

  var EVIDENCE = function (cost) {
    return f.seg("evidence", "Matching sites too", "A gVCF also records where the genome matches the reference, which merging with family needs; " + cost,
      "reference_evidence", [["none", "Variants only"], ["gvcf", "Also a gVCF"]], "none");
  };

  var READS = ["reads"];

  T.define({
    slug: "call-variants", label: "Call SNPs and indels", op: "vcf/snp · vcf/indel", group: "Align and call", keywords: "bcftools vcf",
    desc: "Makes a VCF of the places where this genome differs from the reference: single-letter changes (SNPs) and small insertions and deletions (indels), with bcftools. Fast and good for most uses; DeepVariant is more accurate.",
    accepts: READS, file: "reginald", button: "Call variants",
    checklist: { key: "kinds", title: "What to call", items: [
      { value: "snp", label: "SNPs", desc: "Single-letter changes, about 4 million in a genome", on: true },
      { value: "indel", label: "Indels", desc: "Small insertions and deletions, about 0.8 million", on: true },
    ] },
    fields: [f.region(), EVIDENCE("a second pass over the reads")],
    moreTitle: "Resources",
    more: [
      f.toggle("parallel", "Call chromosomes in parallel", "Uses every thread; the calls are the same", "parallel", true),
      f.seg("mimalloc", "Faster memory allocator", "wgse-mimalloc: about a quarter faster on Apple Silicon, the same calls", "mimalloc", [["auto", "Auto"], ["off", "Off"]], "auto", {
        when: function (c) {
          return c.M.os === "macos";
        },
      }),
      f.number("minDepth", "Warn below depth", "Warns when the mean depth is lower; 0 turns it off", "qc_min_depth", 15, { width: 90 }),
      f.threads(),
    ],
    run: function (c) {
      var kinds = c.v.kinds;
      var threads = c.v.parallel ? c.v.threads || c.M.cores : 1;
      var fast = c.v.mimalloc === "auto" && c.M.tools["wgse-mimalloc"];
      var perGb = { snp: (fast ? 102 : 132) * 9 / 1.5 / 3600, indel: (fast ? 155 : 193) * 9 / 1.5 / 3600 };
      var hours = kinds.reduce(function (sum, k) {
        return sum + perGb[k] * c.file.gb * regionShare(c) / threads;
      }, 0);
      if (c.v.evidence === "gvcf" && kinds.length) hours += (perGb.snp + perGb.indel) * c.file.gb * regionShare(c) / threads;
      var lines = kinds.map(function (k) {
        return opts(c, [base(c, "vcf " + k)], [["region", "--region", ""], ["evidence", "--reference-evidence", "none"], ["mimalloc", "--mimalloc", "auto"]])
          .concat(c.v.parallel ? [] : ["--no-parallel"]).join(" \\\n  ");
      });
      return {
        count: kinds.length + (kinds.length === 1 ? " VCF" : " VCFs"),
        hours: hours, memGB: 0.35 * threads + 1, disk: [[c.m.diskName(c.file), 1.2 * regionShare(c)]],
        checks: [["bcftools", "1.21" + (fast ? ", with wgse-mimalloc" : ", system allocator"), "ok", "Found"]],
        command: [lines.join("\n")], blocked: kinds.length ? null : "Choose SNPs, indels or both.",
        makes: "Saved in " + c.name + "’s folder.", jobName: "SNPs and indels for " + c.name,
      };
    },
  });

  /* DeepVariant engines on this computer: [value, label, small] */
  function dvEngines(c) {
    var M = c.M;
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
    if (engine === "docker") return 60 * depth * (M.os === "macos" ? 3 : 1);
    if (M.os === "macos") return 3.77 * 6 * (c.file.depth / 35);
    if (M.platform === "linux-arm64") return null;
    return (M.nvidia ? 12 : 28) * depth;
  }

  T.define({
    slug: "deepvariant", label: "DeepVariant", op: "vcf/deepvariant", group: "Align and call", keywords: "google deep learning",
    desc: "Google’s deep-learning variant caller: the most accurate SNP and indel calls, and slower than bcftools.",
    accepts: READS, file: "reginald", button: "Start DeepVariant", dryRun: true, requires: ["deepvariant"],
    fields: [
      f.seg("engine", "Engine", function (c) {
        var e = dvEngines(c).filter(function (x) {
          return x[0] === c.v.engine;
        })[0] || dvEngines(c)[0];
        return e[2];
      }, "", [["native", "Native"], ["docker", "Google’s image"], ["parabricks", "Parabricks"]], "native"),
      f.select("model", "Model", "Read from the file: Illumina whole genome", "model_type",
        [["WGS", "Whole genome (WGS)"], ["WES", "Exome (WES)"], ["PACBIO", "PacBio HiFi"], ["HYBRID_PACBIO_ILLUMINA", "PacBio + Illumina"], ["ONT_R104", "Nanopore R10.4.1"]], "WGS", { width: 280 }),
      f.region(),
      EVIDENCE("about 5% more time (a quarter more with Parabricks)"),
    ],
    more: [
      f.select("batch", "Inference batch", "Smaller uses less memory; the same calls", "batch_size", [["512", "512 (auto on Apple Silicon)"], ["1024", "1024 (upstream default)"], ["256", "256"]], "512", { width: 260 }),
      f.threads(),
      f.toggle("force", "Start over", "Ignore stages an earlier run finished", "force", false),
    ],
    run: function (c) {
      var engines = dvEngines(c);
      var engine = engines.some(function (e) {
        return e[0] === c.v.engine;
      }) ? c.v.engine : engines[0][0];
      var threads = c.v.threads || c.M.cores;
      var th = dvThreadHours(c, engine);
      var hours = th === null ? null : th * regionShare(c) / threads;
      if (hours && c.v.evidence === "gvcf") hours *= 1.05;
      var mem = engine === "parabricks" ? 25 : 1 + Number(c.v.batch) * 12 / 1024 + 1 + threads * (c.M.arch === "arm64" ? 2 : 1) * 0.5;
      var out = {
        hours: hours, timeText: engine === "parabricks" ? "about a minute per chromosome the size of chr20" : "hours on a 30x genome",
        memGB: mem, disk: [[c.m.diskName(c.file), 17.3 * regionShare(c) + 0.4]], notes: [],
        checks: [["Engine", engines.filter(function (e) {
          return e[0] === engine;
        })[0][2], "ok", "Found"]],
        command: opts(c, [base(c, "vcf deepvariant")], [["model", "--model-type", "WGS"], ["region", "--region", ""], ["evidence", "--reference-evidence", "none"], ["batch", "--batch-size", "512"], ["force", "--force", "flag"]])
          .concat(engine === "parabricks" || (engine === "native" && c.M.nvidia) ? ["--gpu"] : []),
        makes: "Makes " + c.file.stem + ".deepvariant.vcf.gz in " + c.name + "’s folder.", jobName: "DeepVariant for " + c.name,
      };
      if (engine !== c.v.engine) out.notes.push({ tone: "info", icon: "info", id: "engine", title: "Using " + engines[0][1], text: "The engine you picked isn’t available on this computer." });
      if (c.v.model === "ONT_R104" && c.M.arch !== "x86-64") out.blocked = c.m.unavailable("dv-ont") || "The Nanopore model needs an x86-64 computer.";
      if (c.v.model !== "WGS" && c.M.platform === "linux-arm64") out.blocked = c.m.unavailable("dv-models") + ".";
      if (engine === "docker" && c.M.os === "macos") out.notes.push({ tone: "warn", icon: "clock", id: "docker", title: "Google’s image is x86-64", text: "On Apple Silicon Docker emulates it, several times slower than the native engine, which makes the same calls." });
      return out;
    },
  });

  T.define({
    slug: "gatk", label: "GATK HaplotypeCaller", op: "vcf/gatk", group: "Align and call", keywords: "broad java",
    desc: "The Broad Institute’s variant caller, which many labs use.",
    accepts: READS, file: "reginald", button: "Start HaplotypeCaller", requires: ["gatk"],
    fields: [
      f.region(), EVIDENCE("little extra time; the VCF is then genotyped from it"),
      f.toggle("scatter", "Split into intervals", "", "scatter", false, {
        disabled: function (c) {
          return c.file.platform === "ultima" ? null : "Only for Ultima reads (flow mode)";
        },
      }),
      f.toggle("gpu", "NVIDIA Parabricks", "HaplotypeCaller on the GPU, in Docker", "gpu", false, {
        disabled: function (c) {
          return c.M.parabricks ? null : "Needs Linux with an NVIDIA GPU of 16 GB or more";
        },
      }),
    ],
    run: function (c) {
      return {
        hours: null, timeText: c.v.gpu ? "about a minute per chromosome like chr20" : "hours on a 30x genome", memGB: c.v.gpu ? 18 : 6, disk: [[c.m.diskName(c.file), 0.6]],
        command: opts(c, [base(c, "vcf gatk")], [["region", "--region", ""], ["evidence", "--reference-evidence", "none"]]),
        makes: "Makes " + c.file.stem + ".gatk.vcf.gz in " + c.name + "’s folder.",
      };
    },
  });

  T.define({
    slug: "freebayes", label: "FreeBayes", op: "vcf/freebayes", group: "Align and call", keywords: "haplotype bayesian",
    desc: "A haplotype-based caller that finds SNPs, indels and small complex changes together. Useful as a second opinion beside bcftools or DeepVariant.",
    accepts: READS, file: "reginald", button: "Start FreeBayes", requires: ["freebayes"],
    fields: [f.region()],
    run: function (c) {
      return {
        hours: null, timeText: "hours on a 30x genome", disk: [[c.m.diskName(c.file), 1.2]],
        checks: [["FreeBayes", "1.3.8", "ok", "Found"]], command: opts(c, [base(c, "vcf freebayes")], [["region", "--region", ""]]),
        makes: "Makes " + c.file.stem + ".freebayes.vcf.gz in " + c.name + "’s folder.",
      };
    },
  });

  T.define({
    slug: "clair3", label: "Clair3", op: "vcf/clair3", group: "Align and call", keywords: "nanopore pacbio long read",
    desc: "A deep-learning caller for Nanopore and PacBio long reads. Its model must match the basecaller that made the reads.",
    accepts: READS, file: "reginald", button: "Start Clair3", requires: ["clair3"],
    fields: [
      f.text("model", "Model", "Default: chosen from the reads’ basecaller", "model", "r1041_e82_400bps_sup_v500", { mono: true, width: 300 }),
      f.region(), EVIDENCE("about 4 times the time on a CPU"),
    ],
    run: function (c) {
      var hours = 33 * regionShare(c) / (c.v.threads || c.M.cores);
      return {
        hours: c.v.evidence === "gvcf" ? hours * 4 : hours, memGB: 16, disk: [[c.m.diskName(c.file), 30 * regionShare(c)]],
        notes: [{ tone: "info", icon: "info", id: "long", title: "For long reads", text: c.name + "’s file has Illumina short reads; use DeepVariant or bcftools for them." + (c.M.os === "macos" ? " On Apple Silicon Clair3 runs on the CPU only." : ""), action: { label: "DeepVariant", href: "tool-deepvariant.html" } }],
        blocked: "Clair3 is for Nanopore and PacBio reads, and isn’t installed.",
        command: opts(c, [base(c, "vcf clair3")], [["model", "--model", ""], ["region", "--region", ""], ["evidence", "--reference-evidence", "none"]]),
      };
    },
  });

  T.define({
    slug: "structural-variants", label: "Structural variants", op: "vcf/sv", group: "Align and call", keywords: "delly sniffles pbsv large deletions",
    desc: "Finds large changes that SNP calling misses: deletions, duplications, inversions and moved pieces of DNA, from about 50 letters to millions.",
    accepts: READS, file: "reginald", button: "Find structural variants", requires: ["delly"],
    fields: [
      f.select("caller", "Caller", "Auto: Delly for short reads, Sniffles or pbsv for long reads", "caller", [["auto", "Auto: Delly"], ["delly", "Delly"], ["sniffles", "Sniffles"], ["pbsv", "pbsv (PacBio only)"]], "auto", { width: 220 }),
      f.region(),
    ],
    moreTitle: "Long reads",
    more: [
      f.toggle("long", "Long reads", "Read from the file: short Illumina reads", "long_read", false),
      f.text("repeats", "Tandem repeats", "A BED of repeats; recommended for long reads", "tandem_repeats", "None", { mono: true }),
    ],
    run: function (c) {
      var out = {
        hours: null, timeText: "hours on a 30x genome", memGB: 4, disk: [[c.m.diskName(c.file), 0.1]],
        checks: [["Delly", "1.2.6", "ok", "Found"]],
        command: opts(c, [base(c, "vcf sv")], [["caller", "--caller", "auto"], ["region", "--region", ""], ["long", "--long-read", "flag"], ["repeats", "--tandem-repeats", ""]]),
        makes: "Makes a structural-variant VCF in " + c.name + "’s folder.",
      };
      if (c.v.caller === "pbsv") out.blocked = "pbsv reads PacBio data only.";
      if (c.v.caller === "sniffles" && !c.v.long) out.warnings = [{ tone: "warn", icon: "triangle-alert", title: "Sniffles is for long reads", text: "On short reads it finds little; Delly suits them." }];
      return out;
    },
  });

  T.define({
    slug: "cnv", label: "Copy-number changes", op: "vcf/cnv", group: "Align and call", keywords: "cnv delly copy number",
    desc: "Finds stretches of the genome present in more or fewer copies than usual, from read depth, with Delly.",
    accepts: READS, file: "reginald", button: "Find copy-number changes", requires: ["delly"],
    fields: [f.number("ploidy", "Baseline copies", "2 for most of the genome", "ploidy", 2, { width: 80 }), f.region()],
    run: function (c) {
      return {
        hours: null, timeText: "hours on a 30x genome", memGB: 4, disk: [[c.m.diskName(c.file), 0.1]],
        command: opts(c, [base(c, "vcf cnv")], [["ploidy", "--ploidy", 2], ["region", "--region", ""]]),
        makes: "Makes a CNV VCF in " + c.name + "’s folder.",
      };
    },
  });

  T.define({
    slug: "phase", label: "Phase variants", op: "vcf/phase", group: "Align and call", keywords: "whatshap longphase haplotag",
    desc: "Works out which variants came together from the same parent, using reads that span more than one. Needs the person’s reads and a VCF of their variants.",
    accepts: READS, file: "reginald", button: "Phase variants",
    fields: [
      f.seg("phaser", "Phaser", "", "phaser", [["whatshap", "WhatsHap"], ["longphase", "LongPhase"]], "whatshap"),
      f.toggle("haplotag", "Tag the reads", "Also write reads marked with the copy each came from", "haplotag", false),
      f.region(),
    ],
    run: function (c) {
      var out = {
        hours: null, timeText: "hours on a 30x genome", disk: [[c.m.diskName(c.file), c.v.haplotag ? c.file.size : 0.3]],
        command: opts(c, [base(c, "vcf phase"), "--vcf-input " + c.file.stem + ".snps.vcf.gz"], [["phaser", "--phaser", "whatshap"], ["haplotag", "--haplotag", "flag"], ["region", "--region", ""]]),
        notes: [{ tone: "warn", icon: "variant", id: "needs-vcf", title: c.name + " has no VCF yet", text: "Phasing sorts existing calls into the two copies of each chromosome. Call variants first.", action: { label: "Call variants", href: "tool-call-variants.html" } }],
        blocked: "Call variants for " + c.name + " first.",
      };
      if (c.v.phaser === "longphase") out.blocked = "LongPhase is for Nanopore and PacBio reads.";
      return out;
    },
  });

  T.define({
    slug: "trio", label: "Family trio", op: "vcf/trio", group: "Align and call", keywords: "de novo recessive compound heterozygous parents",
    desc: "Looks at a child with both parents: variants new in the child (de novo), and ones inherited from both sides that could matter (recessive, compound heterozygous).",
    accepts: READS, file: "reginald", button: "Analyse the trio",
    fields: [
      f.show("Mother", "Helen", "mother", function () {
        return "helen-mcdonald.deepvariant.vcf.gz";
      }),
      f.show("Father", "Gordon", "father", function () {
        return "60820188479016.bam";
      }),
      f.seg("mode", "Look for", "", "mode", [["denovo", "New in the child"], ["recessive", "Recessive"], ["comphet", "Compound het."], ["all", "All"]], "denovo"),
      f.number("minDepth", "Minimum depth", "In each person, at a reported site", "min_depth", 15, { width: 80 }),
      f.region(),
    ],
    run: function (c) {
      var out = {
        hours: null, timeText: "minutes on a 30x genome",
        command: opts(c, ["wgsextract vcf trio --proband " + c.m.path(c.file), "--mother " + c.m.path(c.m.files.helen), "--father " + c.m.path(c.m.files.gordon)], [["mode", "--mode", "denovo"], ["minDepth", "--min-depth", 15], ["region", "--region", ""]]),
        makes: "A table of the variants found, in " + c.name + "’s folder.",
      };
      if (c.v.mode === "denovo" || c.v.mode === "all") {
        out.warnings = [{ tone: "warn", icon: "triangle-alert", id: "denovo", title: "New variants need everyone’s reads", text: "Helen has only a VCF, which can’t tell “not there” from “not looked at”. Add her BAM or CRAM for de novo calls; recessive and compound heterozygous work now." }];
        out.blocked = "Add Helen’s reads, or look for recessive variants.";
      }
      return out;
    },
  });

  T.define({
    slug: "impute", label: "Imputation", op: "vcf/impute", group: "Align and call", keywords: "glimpse beagle low pass kit",
    desc: "Fills in genotypes a low-coverage genome or a DNA-test kit didn’t read, from the haplotypes of the 1000 Genomes panel. GLIMPSE2 for low-pass reads, Beagle for kits.",
    accepts: ["kit", "reads"], file: "alexkit", button: "Impute",
    fields: [
      f.text("chromosomes", "Chromosomes", "Default: every installed one", "chromosomes", "chr1-22", { mono: true, width: 160 }),
      f.number("minGp", "Minimum certainty", "Genotypes below this probability are left blank", "min_gp", 0.95, { width: 90 }),
    ],
    run: function (c) {
      var kit = c.file.kind === "kit";
      var out = {
        hours: kit ? 0.75 : 2.8 * 8 / (c.v.threads || c.M.cores), memGB: kit ? 3.4 : 3.1, disk: [[c.M.systemDisk, 31]],
        checks: [[kit ? "Beagle" : "GLIMPSE2", kit ? "5.4" : "2.0.1", "ok", "Found"]],
        notes: c.flag("panel-1kg") ? [] : [{ tone: "warn", icon: "download", id: "panel", title: "The 1000 Genomes panel isn’t installed", text: "Phased haplotypes for chr1–22: about 30 GB.", action: { label: "Download 30 GB", set: "panel-1kg", toast: "Downloading the 1000 Genomes panel (30 GB). See Jobs." } }],
        command: opts(c, ["wgsextract vcf impute " + (kit ? "--vcf-input " : "--input ") + c.m.path(c.file)], [["chromosomes", "--chromosomes", ""], ["minGp", "--min-gp", kit ? 0.95 : 0.9]]),
        makes: "Makes an imputed VCF in " + c.name + "’s folder.",
      };
      if (!c.flag("panel-1kg")) out.blocked = "Download the panel first.";
      if (!kit) out.warnings = [{ tone: "info", icon: "info", title: "At " + c.file.depth + "× there is little to fill in", text: "Imputation helps genomes under about 4× and kits; a full-depth genome already reads nearly every site." }];
      return out;
    },
  });
})();
