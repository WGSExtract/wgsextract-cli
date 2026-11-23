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
    return capped && !m ? 0.25 : gb;
  }

  /* Which aligner runs, how, and what it costs, on this computer */
  function plan(c) {
    var M = c.M;
    var mac = M.appleSilicon;
    var windowsNative = M.windowsBwa && c.flag("windows-wgse-bwa");
    var native = (M.native || windowsNative) && c.v.native_aligners !== "off";
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
    var onGpu = native && (mac || M.nvidia && (!M.windowsBwa || c.v.prefer_native_gpu));
    var bwa = onGpu
      ? cost("wgse-bwa on " + gpu, M.windowsBwa ? null : mac ? 0.28 : 0.19, 6.75 + (c.v.dense_suffix_array ? 0.8 : 0), false)
      : cost(native ? "wgse-bwa on the CPU" : "bwa on the CPU", 0.7, 6 + (native && c.v.dense_suffix_array ? 0.8 : 0), false);
    if (bwa.rate !== null && native && c.v.dense_suffix_array) bwa.rate *= 0.923;
    if (onGpu && M.windowsBwa) {
      bwa.aligner += " if preflight passes";
      bwa.timeText = "varies · CUDA isn’t always faster";
    }
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
    return c.v.format === "BAM" && c.v.output_profile === "default" ? cram * 2.5 : cram;
  }

  var ALIGNER_FIELDS = [
    f.select("ref", "Target reference", "The build to move to; sent as the library’s FASTA", REFS.map(function (r) {
      return [r[0], r[1]];
    }), "hs38"),
    f.param("format", "Output", function (c) {
      return c.v.output_profile !== "default" ? "Set by the output profile below" : "CRAM is about a third of the size";
    }, { def: "CRAM", send: true }),
    f.param("aligner", "Aligner", function (c) {
      var p = plan(c);
      if (p.blocked) return p.blocked;
      if (p.needsIndex) return "Needs bwa-mem2’s index";
      return "Runs " + p.aligner + (p.standIn ? ", standing in for " + p.standIn : "") + "; writes " + p.records + " records";
    }, { as: "select", labels: { auto: "Auto" } }),
    f.param("fast", "Faster preset", "bwa-mem3’s speed preset: about twice as fast, but alignments differ from BWA’s", {
      when: function (c) {
        return !longRead(c);
      },
      disabled: function (c) {
        return c.m.unavailable("bwa-mem3");
      },
    }),
  ];

  var ALIGNER_MORE = [
    f.param("platform", "Sequencer", "Empty: read from the reads’ names and read groups (Illumina here)", {
      unset: "From the reads",
      labels: { illumina: "Illumina", dnbseq: "DNBSEQ (MGI, BGI)", ultima: "Ultima", pacbio: "PacBio", hifi: "PacBio HiFi", clr: "PacBio CLR", ont: "Oxford Nanopore", nanopore: "Nanopore" },
    }),
    f.param("output_profile", "Output profile", function (c) {
      return { default: "Keeps the format above", archive: "CRAM 3.1, smallest; GATK 4.7 or later reads it", calling: "CRAM 3.0, which every caller reads" }[c.v.output_profile];
    }, { def: "default", labels: { default: "Default", archive: "Archive", calling: "For calling" } }),
    f.param("native_aligners", "WGS Extract’s aligners", function (c) {
      if (c.M.windowsBwa) return "Optional wgse-bwa.exe on PATH or set in config.toml; stock BWA if missing or unusable";
      return c.M.appleSilicon ? "Builds for Apple Silicon that use the GPU, with the same records as upstream" : "wgse-bwa (on the NVIDIA GPU when there is one) and wgse-bwa-mem3, with the same records as upstream";
    }, {
      def: "auto", labels: { auto: "Use them", off: "Off" },
      when: function (c) {
        return c.M.native || c.M.windowsBwa;
      },
    }),
    f.param("dense_suffix_array", "Dense suffix array", function (c) {
      var p = plan(c);
      return p.dense ? "About 8% faster with wgse-bwa; holds 0.8 GB more in memory. Build it once with ref dense-sa" : "Only wgse-bwa reads it; " + (p.aligner || "this aligner") + " runs here, so it would be unused";
    }, {
      when: function (c) {
        return c.M.native || c.M.windowsBwa;
      },
    }),
    f.param("prefer_native_gpu", "Prefer native GPU", function (c) {
      return c.M.windowsBwa
        ? "Opt in to CUDA instead of native CPU alignment. Needs optional wgse-bwa, a supported NVIDIA driver, sufficient RAM and Windows commit (RAM plus page file). No Docker or CUDA Toolkit."
        : "Prefer the native GPU path instead of falling back to CPU when memory is tight.";
    }, {
      when: function (c) {
        return (c.M.native || c.M.windowsBwa) && (c.M.metal || c.M.nvidia);
      },
    }),
    f.param("gpu", "NVIDIA Parabricks", "BWA-MEM, sorting and duplicate marking on the GPU, in Docker", {
      disabled: function (c) {
        if (c.M.parabricks) return null;
        if (c.M.os === "windows") return "Parabricks needs Linux. Native Windows CUDA is a separate wgse-bwa option.";
        if (c.M.nvidia) return "Parabricks needs a GPU with 16 GB or more";
        return "Needs Linux with an NVIDIA GPU of 16 GB or more";
      },
    }),
    f.threads(),
    f.sortMemory(function (c) {
      var p = plan(c);
      return p.aligner && p.aligner.indexOf("bwa-mem2") >= 0 ? "Capped at 256 MB with bwa-mem2, to leave room for its index" : "Auto: a quarter of the RAM, at least 1 GB per thread";
    }),
  ];

  function alignerNotes(c, p) {
    var notes = [];
    if (c.M.windowsBwa && c.v.prefer_native_gpu) notes.push({ tone: "info", icon: "info", id: "windows-cuda",
      title: "Windows CUDA is optional",
      text: "Used only when the selected BWA path can run a validated wgse-bwa build and the NVIDIA driver, RAM and commit checks pass. Otherwise it uses the CPU. It is not automatically selected or necessarily faster." });
    if (p.blocked) notes.push({ tone: "warn", icon: "triangle-alert", id: "aligner", title: "Not on this computer", text: p.blocked + " Choose Auto, bwa or bwa-mem2." });
    if (p.needsIndex) {
      notes.push({ tone: "warn", icon: "download", id: "index", title: "bwa-mem2’s index for " + refLabel(c.v.ref) + " isn’t installed",
        text: "bwa-mem2 and bwa-mem3 read this index. Building it locally needs about 90 GB of memory, so WGS Extract downloads the published copy instead of building one automatically.",
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

  function alignerChecks(p) {
    return p.aligner ? [["Aligner", p.aligner + (p.standIn ? " (in " + p.standIn + "’s place)" : "") + ", writing " + p.records + " records", "ok", "Found"]] : [];
  }

  T.define({
    slug: "realign", label: "Realign", ops: ["realign"], group: "Align", keywords: "bwa bwa-mem2 bwa-mem3 minimap2 reference build",
    lesson: { href: "learn-reference.html#liftover", label: "Reference genomes & changing builds" },
    desc: "Remaps an aligned genome onto another reference build for tools and sites that expect it, while keeping the original file.",
    learn: "Genomes delivered on an older build such as GRCh37 often need GRCh38 for current tools, databases and DNA-test sites, or T2T for the hardest regions. Realigning leaves your DNA sequence unchanged and maps the same reads onto the new reference.\n\n" +
      "Every read is aligned again, so it takes hours, and you get a second BAM or CRAM beside the first. Once you’ve checked the new file, you can delete the old one yourself. Align FASTQ is the same job for reads that were never aligned; a VCF can’t be realigned, because it holds no reads.",
    accepts: ["reads"], file: "gordon", button: "Start realign", dryRun: true,
    fields: ALIGNER_FIELDS,
    moreTitle: "Aligner, resources and steps",
    more: ALIGNER_MORE.concat([
      f.param("keep_fastq", "Keep the extracted reads", function (c) {
        return "About " + c.m.gb(c.file.gb * 0.83) + " of FASTQ more; otherwise reads stream straight to the aligner";
      }),
      f.param("chunk_size", "Chunk size", "Long reads: input per chunk, so a stopped run resumes and temporary files stay small", { width: 100, when: longRead }),
      f.param("restart", "Start over", "Ignore steps an earlier run finished"),
    ]),
    requests: function (c, base) {
      return [base("realign", { ref: c.m.fasta(c.v.ref) })];
    },
    run: function (c) {
      var p = plan(c);
      var out = { notes: alignerNotes(c, p), checks: alignerChecks(p) };
      var file = c.file;
      var outName = file.stem + "_aligned." + (c.v.format === "BAM" && c.v.output_profile === "default" ? "bam" : "cram");
      var outDisk = c.m.diskName(file);
      var size = outputGB(c, file);
      /* the output, the sort's spills beside it, and kept reads */
      out.disk = [[outDisk, size + size * 1.2 + (c.v.keep_fastq ? file.gb * 0.83 : 0)]];
      /* The most fundamental reason first */
      out.blocked = [
        p.blocked,
        c.v.ref === file.ref ? "This file is already on " + refLabel(c.v.ref) + "." : null,
        !refInstalled(c, c.v.ref) ? "Download " + refLabel(c.v.ref) + " first." : null,
        p.needsIndex ? "Download bwa-mem2’s index first." : null,
      ].filter(Boolean)[0] || null;
      out.hours = p.aligner ? hoursFor(c, p, file) : null;
      out.timeText = p.timeText;
      out.memGB = p.mem;
      out.makes = "Makes " + outName + " in " + c.name + "’s folder; the original stays.";
      out.jobName = "Realign " + c.name + " to " + c.v.ref;
      return out;
    },
  });

  T.define({
    slug: "align", label: "Align FASTQ", ops: ["align"], group: "Align", keywords: "fastq bwa bwa-mem2 bwa-mem3 minimap2",
    lesson: { href: "learn-files.html#alignment", label: "From reads to aligned files" },
    desc: "Maps raw reads from a sequencer onto a reference genome to make the BAM or CRAM that downstream tools read, keeping the FASTQ files.",
    learn: "Start here when a lab sends only FASTQ files, or when you want to align from the raw reads onto a reference of your choice. Almost every other tool needs aligned reads, so this step usually comes first.\n\n" +
      "It’s the longest job here: hours for a whole genome, less with bwa-mem3 or WGS Extract’s GPU builds. bwa, bwa-mem2 and bwa-mem3 are for short reads and give the same kind of result; minimap2 is for long reads. If you already have a BAM or CRAM, use Realign instead.",
    accepts: ["fastq"], file: "fastq", button: "Start aligning", dryRun: false,
    fields: ALIGNER_FIELDS.concat([f.param("sample", "Sample name", "Written into the read group; sites and tools show it", { def: "60820188474839" })]),
    moreTitle: "Aligner and resources",
    more: ALIGNER_MORE,
    requests: function (c, base) {
      var M = c.m;
      return [base("align", { r1: M.realPath(M.path(c.file, c.file.r1)), r2: M.realPath(M.path(c.file, c.file.r2)), ref: M.fasta(c.v.ref), outdir: M.realPath(c.M.genomesPath + "/") })];
    },
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
      out.makes = "Makes " + c.file.stem + "_aligned." + (c.v.format === "BAM" && c.v.output_profile === "default" ? "bam" : "cram") + " on " + c.M.genomesDisk + ".";
      out.jobName = "Align " + c.name + "’s reads";
      return out;
    },
  });

  WGSM.v2align = { refInstalled: refInstalled, refLabel: refLabel };
})();
