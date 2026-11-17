/* Align and Realign. Which aligner runs follows docs/hardware.md on an Apple
 * Silicon Mac with WGS Extract's native aligners: auto picks bwa-mem2 only
 * when the reference has bwa-mem2's index, else bwa; with that index
 * wgse-bwa-mem3 stands in for either when it fits in RAM, writing the same
 * records; wgse-bwa runs on the Metal GPU.
 *
 * Speed, thread-hours per Gb of reads, plus 15% for extraction and sorting
 * (docs/resource_estimates.md, "Whole-genome host requirement"): bwa 0.70 on
 * the CPU; wgse-bwa on Metal 0.28 (the CPU path was 2.5x slower on a whole
 * genome); wgse-bwa-mem3 1.8x and wgse-bwa-mem2 1.2x as fast as wgse-bwa
 * (hardware.md, Apple Silicon); --fast about twice bwa-mem3; upstream
 * bwa-mem2 0.74x bwa's time. Memory: aligner (bwa ~6 GB, wgse-bwa +0.75,
 * wgse-bwa-mem2 0.5 and bwa-mem3 ~11, upstream bwa-mem2 ~17 for its index),
 * plus sort buffers (sort threads x per-thread memory, bwa-mem2 capped at
 * 256M) and ~4 GB for samblaster and collate. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var T = WGSM.v2tools;
  var f = T.f;

  var REFS = [
    ["hs38", "hs38 (GRCh38)", true],
    ["T2Tv20", "T2T-CHM13 v2.0", false],
    ["hs38d1", "hs38d1 (GRCh38 + decoys)", false],
    ["hs37d5", "hs37d5 (GRCh37)", true],
  ];

  function refInstalled(c, ref) {
    var known = REFS.filter(function (r) {
      return r[0] === ref;
    })[0];
    return (known && known[2]) || c.flag("ref-" + ref);
  }

  function refLabel(ref) {
    return REFS.filter(function (r) {
      return r[0] === ref;
    })[0][1];
  }

  function longRead(c) {
    return ["pacbio", "hifi", "clr", "ont", "nanopore"].indexOf(c.v.platform) >= 0;
  }

  function sortPerThread(c, capped) {
    var m = c.v.memory;
    var gb = m === "2G" ? 2 : m === "768M" ? 0.75 : m === "512M" ? 0.5 : m === "256M" ? 0.25 : 1;
    return capped && m === "auto" ? 0.25 : gb;
  }

  /* Which aligner runs, how, and what it costs, on this computer */
  function plan(c) {
    var M = c.M;
    var mac = M.os === "macos";
    var native = M.native && c.v.native !== "off";
    var index = c.flag("mem2-" + c.v.ref);
    var threads = c.v.threads || M.cores;
    var sortThreads = c.v.threads && c.v.threads !== M.cores ? threads : M.bigCores;
    var gpu = mac ? "the GPU" : "the NVIDIA GPU";
    function cost(aligner, rate, memAligner, capped) {
      return { aligner: aligner, rate: rate, mem: memAligner + sortThreads * sortPerThread(c, capped) + 4 };
    }
    if (c.v.gpu && M.parabricks) {
      var pb = cost("Parabricks fq2bam on the NVIDIA GPU", null, 0, false);
      pb.mem = 41;
      pb.records = "BWA-MEM’s";
      pb.timeText = "tens of minutes";
      return pb;
    }
    if (longRead(c) || c.v.aligner === "minimap2") {
      var mm = cost(native && mac ? "wgse-minimap2 on the CPU" : "minimap2", null, 12, false);
      mm.records = "minimap2’s";
      return mm;
    }
    var mem3Name = native ? "wgse-bwa-mem3" + (mac ? " on the GPU" : M.nvidia ? " on the NVIDIA GPU (experimental)" : " on the CPU") : "bwa-mem3";
    var mem3 = cost(mem3Name, mac ? 0.155 : 0.15, 11, false);
    var noMem3 = c.m.unavailable("bwa-mem3");
    if (c.v.fast || c.v.aligner === "bwa-mem3") {
      if (noMem3) return { blocked: noMem3 + "." };
      if (!index) return { needsIndex: true };
      if (c.v.fast) {
        mem3.rate = mem3.rate / 2;
        mem3.aligner += ", --fast";
        mem3.records = "its own (not BWA’s)";
      } else {
        mem3.records = "bwa’s exact";
      }
      return mem3;
    }
    var fitsMem3 = index && !noMem3 && (native || M.os === "linux") && mem3.mem <= M.ramGB - 2;
    var wantMem2 = c.v.aligner === "bwa-mem2" || (c.v.aligner === "auto" && index);
    if (c.v.aligner === "bwa-mem2" && !index) return { needsIndex: true };
    if (fitsMem3) {
      mem3.records = wantMem2 ? "bwa-mem2’s" : "bwa’s exact";
      mem3.standIn = wantMem2 ? "bwa-mem2" : "bwa";
      return mem3;
    }
    var arm = M.platform === "linux-arm64" ? 1.2 : 1;
    if (wantMem2) {
      var two = native && mac ? cost("wgse-bwa-mem2 on the GPU", 0.23, 11, true) : cost("bwa-mem2 on the CPU", 0.52 * arm, 17, true);
      two.records = "bwa-mem2’s";
      return two;
    }
    var onGpu = native && (mac || M.nvidia);
    var bwa = onGpu
      ? cost("wgse-bwa on " + gpu, mac ? 0.28 : 0.19, 6.75 + (c.v.dense ? 0.8 : 0), false)
      : cost(native ? "wgse-bwa on the CPU" : "bwa on the CPU", 0.7, 6 + (native && c.v.dense ? 0.8 : 0), false);
    if (native && c.v.dense) bwa.rate *= 0.923;
    bwa.records = "bwa’s exact";
    bwa.dense = native;
    return bwa;
  }

  function hoursFor(c, p, file) {
    if (p.rate === null) return longRead(c) && !p.timeText ? 1.4 * file.size / (c.v.threads || c.M.cores) : null;
    return (p.rate * file.gb * 1.15) / (c.v.threads || c.M.cores);
  }

  /* Output size: CRAM ~0.29 GB per x of depth, BAM ~2.5x that */
  function outputGB(c, file) {
    var cram = file.depth * 0.29;
    return c.v.format === "BAM" && c.v.profile === "default" ? cram * 2.5 : cram;
  }

  var ALIGNER_FIELDS = [
    f.select("ref", "Target reference", "The build to move to", "ref", REFS.map(function (r) {
      return [r[0], r[1]];
    }), "hs38"),
    f.seg("format", "Output", function (c) {
      return c.v.profile !== "default" ? "Set by the output profile below" : "CRAM is about a third of the size";
    }, "format", [["BAM", "BAM"], ["CRAM", "CRAM"]], "CRAM"),
    f.select("aligner", "Aligner", function (c) {
      var p = plan(c);
      if (p.blocked) return p.blocked;
      if (p.needsIndex) return "Needs bwa-mem2’s index";
      return "Runs " + p.aligner + (p.standIn ? ", standing in for " + p.standIn : "") + "; writes " + p.records + " records";
    }, "aligner", [["auto", "Auto"], ["bwa", "bwa"], ["bwa-mem2", "bwa-mem2"], ["bwa-mem3", "bwa-mem3"], ["minimap2", "minimap2"]], "auto"),
    f.toggle("fast", "Faster preset", "bwa-mem3’s speed preset: about twice as fast, but alignments differ from BWA’s", "fast", false, {
      when: function (c) {
        return !longRead(c);
      },
      disabled: function (c) {
        return c.m.unavailable("bwa-mem3");
      },
    }),
  ];

  var ALIGNER_MORE = [
    f.select("platform", "Sequencer", "Read from the file: Illumina", "platform",
      [["illumina", "Illumina"], ["dnbseq", "DNBSEQ (MGI, BGI)"], ["ultima", "Ultima"], ["hifi", "PacBio HiFi"], ["clr", "PacBio CLR"], ["ont", "Oxford Nanopore"]], "illumina", { width: 240 }),
    f.seg("profile", "Output profile", function (c) {
      return { default: "Keeps the format above", archive: "CRAM 3.1, smallest; GATK 4.7 or later reads it", calling: "CRAM 3.0, which every caller reads" }[c.v.profile];
    }, "output_profile", [["default", "Default"], ["archive", "Archive"], ["calling", "For calling"]], "default"),
    f.seg("native", "WGS Extract’s aligners", function (c) {
      return c.M.os === "macos" ? "Builds for Apple Silicon that use the GPU, with the same records as upstream" : "wgse-bwa (on the NVIDIA GPU when there is one) and wgse-bwa-mem3, with the same records as upstream";
    }, "native_aligners", [["auto", "Use them"], ["off", "Off"]], "auto", {
      when: function (c) {
        return c.M.native;
      },
    }),
    f.toggle("dense", "Dense suffix array", function (c) {
      var p = plan(c);
      return p.dense ? "About 8% faster with wgse-bwa; holds 0.8 GB more in memory. Build it once with ref dense-sa" : "Only wgse-bwa reads it; " + (p.aligner || "this aligner") + " runs here, so it would be unused";
    }, "dense_suffix_array", false, {
      when: function (c) {
        return c.M.native;
      },
    }),
    f.toggle("preferGpu", "Use the GPU even when tight", "If no GPU plan fits in memory, use it anyway rather than the slower CPU; may swap heavily", "prefer_native_gpu", false, {
      when: function (c) {
        return c.M.native && (c.M.metal || c.M.nvidia);
      },
    }),
    f.toggle("gpu", "NVIDIA Parabricks", "BWA-MEM, sorting and duplicate marking on the GPU, in Docker", "gpu", false, {
      disabled: function (c) {
        if (c.M.parabricks) return null;
        if (c.M.nvidia && c.M.os === "windows") return "Parabricks needs Linux; this GPU is used on Linux only";
        if (c.M.nvidia) return "Parabricks needs a GPU with 16 GB or more";
        return "Needs Linux with an NVIDIA GPU of 16 GB or more";
      },
    }),
    f.threads(),
    f.sortMemory(function (c) {
      var p = plan(c);
      return p.aligner && p.aligner.indexOf("bwa-mem2") >= 0 ? "Capped at 256 MB with bwa-mem2, to leave room for its index" : "Auto: a quarter of the RAM, at least 1 GB per thread";
    }),
    f.text("chunk", "Chunk size", "Long reads: input per chunk, so a stopped run resumes and temporary files stay small", "chunk_size", "4G", {
      width: 100,
      when: longRead,
    }),
  ];

  function alignerNotes(c, p) {
    var notes = [];
    if (p.blocked) notes.push({ tone: "warn", icon: "triangle-alert", id: "aligner", title: "Not on this computer", text: p.blocked + " Choose Auto, bwa or bwa-mem2." });
    if (p.needsIndex) {
      notes.push({ tone: "warn", icon: "download", id: "index", title: "bwa-mem2’s index for " + refLabel(c.v.ref) + " isn’t installed",
        text: "bwa-mem2 and bwa-mem3 read it. WGS Extract never builds it automatically: building needs about 90 GB of memory, so the published one is downloaded.",
        action: { label: "Download 17 GB", set: "mem2-" + c.v.ref, toast: "Downloading bwa-mem2’s index for " + c.v.ref + " (17 GB). See Jobs." } });
    } else if (!c.flag("mem2-" + c.v.ref) && !longRead(c) && c.v.aligner === "auto" && !c.v.gpu) {
      var faster = Object.assign({}, c, { flag: function (name) {
        return name === "mem2-" + c.v.ref || c.flag(name);
      } });
      var alt = plan(faster);
      var altHours = hoursFor(c, alt, c.file);
      var nowHours = hoursFor(c, p, c.file);
      if (altHours && nowHours && altHours < nowHours * 0.9) notes.push({ tone: "info", icon: "zap", id: "faster", title: "Could be faster: " + c.m.duration(altHours) + " with " + alt.aligner.split(" ")[0],
        text: "With bwa-mem2’s index for " + refLabel(c.v.ref) + " (a 17 GB download, once), " + alt.aligner.split(" ")[0] + " runs" + (alt.standIn ? " in bwa’s place and writes the same records" : "") + ".",
        action: { label: "Download 17 GB", set: "mem2-" + c.v.ref, toast: "Downloading bwa-mem2’s index for " + c.v.ref + " (17 GB). See Jobs." } });
    }
    if (!refInstalled(c, c.v.ref)) {
      notes.push({ tone: "warn", icon: "reference", id: "ref", title: refLabel(c.v.ref) + " isn’t installed",
        text: "The reference genome itself, 0.9 GB. It downloads only when you ask.",
        action: { label: "Download 0.9 GB", set: "ref-" + c.v.ref, toast: "Downloading " + refLabel(c.v.ref) + ". See Jobs." } });
    }
    if (c.v.fast && !longRead(c)) {
      notes.push({ tone: "warn", icon: "triangle-alert", id: "fast", title: "Not BWA’s alignments",
        text: "On 5M read pairs, 3.5% of reads were placed differently, mostly ones BWA wasn’t sure of, and 2.1% more got a different mapping quality. Fine for genealogy; for comparing with other BWA files, leave it off." });
    }
    return notes;
  }

  function alignerCommand(c, base, p) {
    var line = [base];
    if (c.v.aligner !== "auto") line.push("--aligner " + c.v.aligner);
    if (c.v.fast && !longRead(c)) line.push("--fast");
    if (c.v.platform !== "illumina") line.push("--platform " + c.v.platform);
    if (c.v.profile !== "default") line.push("--output-profile " + c.v.profile);
    else line.push("--format " + c.v.format);
    if (c.v.native === "off" && c.M.native) line.push("--native-aligners off");
    if (c.v.gpu && c.M.parabricks) line.push("--gpu");
    if (c.v.dense) line.push("--dense-suffix-array");
    if (c.v.preferGpu) line.push("--prefer-native-gpu");
    if (c.v.threads && c.v.threads !== c.M.cores) line.push("--threads " + c.v.threads);
    if (c.v.memory !== "auto") line.push("--memory " + c.v.memory);
    if (longRead(c) && c.v.chunk && c.v.chunk !== "4G") line.push("--chunk-size " + c.v.chunk);
    return line;
  }

  function alignerChecks(p) {
    return p.aligner ? [["Aligner", p.aligner + (p.standIn ? " (in " + p.standIn + "’s place)" : "") + ", writing " + p.records + " records", "ok", "Found"]] : [];
  }

  T.define({
    slug: "realign", label: "Realign", op: "realign", group: "Align and call", keywords: "bwa bwa-mem2 bwa-mem3 minimap2 reference build",
    desc: "Aligns a genome again to another reference, so it can be used with tools and sites that expect that build. The original file is kept.",
    learn: "Use it when a genome was delivered on an older build, usually GRCh37, and you want the tools, databases and DNA-test sites that expect GRCh38, or T2T for the hardest regions. Your DNA doesn’t change: the same reads are placed again on the new reference.\n\n" +
      "Every read is aligned again, so it takes hours, and you get a second BAM or CRAM beside the first. Once the new file is checked, Convert can remove the old one. Align FASTQ is the same job for reads that were never aligned; a VCF can’t be realigned, because it holds no reads.",
    accepts: ["reads"], file: "gordon", button: "Start realign", dryRun: true,
    fields: ALIGNER_FIELDS,
    moreTitle: "Aligner, resources and steps",
    more: ALIGNER_MORE.concat([
      f.toggle("keep", "Keep the extracted reads", function (c) {
        return "About " + c.m.gb(c.file.gb * 0.83) + " of FASTQ more; otherwise reads stream straight to the aligner";
      }, "keep_fastq", false),
      f.seg("tmp", "Temporary files on", "Sorting spills here", "tmp_dir", [["out", "Beside the output"], ["system", "System disk"]], "out"),
      f.toggle("force", "Start over", "Ignore steps an earlier run finished", "force", false),
    ]),
    run: function (c) {
      var p = plan(c);
      var out = { notes: alignerNotes(c, p), checks: alignerChecks(p) };
      var file = c.file;
      var outName = file.stem + "_aligned." + (c.v.format === "BAM" && c.v.profile === "default" ? "bam" : "cram");
      var outDisk = c.m.diskName(file);
      var size = outputGB(c, file);
      var tmpDisk = c.v.tmp === "out" ? outDisk : c.M.systemDisk;
      out.disk = [];
      var spill = size * 1.2;
      var onOut = size + (c.v.keep ? file.gb * 0.83 : 0) + (tmpDisk === outDisk ? spill : 0);
      out.disk.push([outDisk, onOut]);
      if (tmpDisk !== outDisk) out.disk.push([tmpDisk, spill]);
      var running = c.fileKey === "gordon" && c.v.ref === "hs38" && !c.flag("realign-cancelled");
      if (running) {
        out.notes.unshift({ tone: "info", icon: "realign", id: "running", title: "Already running: 47%, 1 h 52 min left",
          text: "Gordon’s realign to hs38 started at 08:15 with these settings.", action: { label: "See Jobs", href: "jobs.html" } });
      }
      /* The most fundamental reason first */
      out.blocked = [
        p.blocked,
        c.v.ref === file.ref ? "This file is already on " + refLabel(c.v.ref) + "." : null,
        !refInstalled(c, c.v.ref) ? "Download " + refLabel(c.v.ref) + " first." : null,
        p.needsIndex ? "Download bwa-mem2’s index first." : null,
        running ? "Already running." : null,
      ].filter(Boolean)[0] || null;
      out.hours = p.aligner ? hoursFor(c, p, file) : null;
      out.timeText = p.timeText;
      out.memGB = p.mem;
      var cmd = alignerCommand(c, "wgsextract realign --input " + c.m.path(file), p);
      cmd.splice(1, 0, "--ref " + c.v.ref + " --outdir " + c.m.outdir(file));
      if (c.v.keep) cmd.push("--keep-fastq");
      if (c.v.tmp !== "out") cmd.push("--tmp-dir " + c.m.native(c.M.os === "windows" ? "C:/Temp" : "/tmp"));
      if (c.v.force) cmd.push("--force");
      out.command = cmd;
      out.makes = "Makes " + outName + " in " + c.name + "’s folder; the original stays.";
      out.jobName = "Realign " + c.name + " to " + c.v.ref;
      return out;
    },
  });

  T.define({
    slug: "align", label: "Align FASTQ", op: "align", group: "Align and call", keywords: "fastq bwa bwa-mem2 bwa-mem3 minimap2",
    desc: "Places raw reads from a sequencer on a reference genome, making the BAM or CRAM that every other tool reads. The FASTQ files are kept.",
    learn: "Use it when a lab sent only FASTQ files, or when you’d rather start from the raw reads on a reference of your choice. Nothing else can read FASTQ, so this step comes first.\n\n" +
      "It’s the longest job here: hours for a whole genome, less with bwa-mem3 or WGS Extract’s GPU builds. bwa, bwa-mem2 and bwa-mem3 are for short reads and give the same kind of result; minimap2 is for long reads. If you already have a BAM or CRAM, use Realign instead.",
    accepts: ["fastq"], file: "fastq", button: "Start aligning", dryRun: false,
    fields: ALIGNER_FIELDS.concat([f.text("sample", "Sample name", "Written into the file; sites and tools show it", "sample", "60820188474839")]),
    moreTitle: "Aligner and resources",
    more: ALIGNER_MORE,
    run: function (c) {
      var p = plan(c);
      var out = { notes: alignerNotes(c, p), checks: alignerChecks(p) };
      if (!c.m.files.fastq.person && !WGSM.v2state.ownerName()) {
        out.notes.unshift({ tone: "info", icon: "user", id: "owner", title: "These reads aren’t assigned to anyone yet",
          text: "Choose whose they are, so the new file joins that person and their results use it.", action: { label: "Choose a person", href: "home.html" } });
      }
      var size = outputGB(c, c.file);
      out.disk = [[c.M.genomesDisk, size * 2.2]];
      out.hours = p.aligner ? hoursFor(c, p, c.file) : null;
      out.timeText = p.timeText;
      out.memGB = p.mem;
      out.blocked = [
        p.blocked,
        !refInstalled(c, c.v.ref) ? "Download " + refLabel(c.v.ref) + " first." : null,
        p.needsIndex ? "Download bwa-mem2’s index first." : null,
      ].filter(Boolean)[0] || null;
      var cmd = alignerCommand(c, "wgsextract align --r1 " + c.m.path(c.file, c.file.r1), p);
      cmd.splice(1, 0, "--r2 " + c.m.path(c.file, c.file.r2), "--ref " + c.v.ref + " --outdir " + c.m.native(c.M.genomesPath + "/"));
      if (c.v.sample) cmd.push("--sample " + c.v.sample);
      out.command = cmd;
      out.makes = "Makes " + (c.v.sample || "the sample") + "." + (c.v.format === "BAM" && c.v.profile === "default" ? "bam" : "cram") + " on " + c.M.genomesDisk + ".";
      out.jobName = "Align " + c.name + "’s reads";
      return out;
    },
  });

  WGSM.v2align = { refInstalled: refInstalled, refLabel: refLabel };
})();
