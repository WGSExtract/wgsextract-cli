/* The computer the v2 mockup pretends to run on, chosen from the top bar
 * (a review control, not app UI), and the library's files. Each profile's
 * abilities follow the docs: docs/hardware.md (aligners, Metal, CUDA,
 * Parabricks), docs/windows.md (what native Windows lacks),
 * docs/linux-arm64.md (phase 1) and docs/apple-silicon-16gb.md. */
(function () {
  "use strict";
  var WGSM = window.WGSM;

  var PROFILES = {
    "mac-pro": { label: "MacBook Pro", spec: "Apple M2 Pro · 12 cores · 32 GB", os: "macos", arch: "arm64", cores: 12, bigCores: 8, ramGB: 32, gpu: "metal",
      system: ["Macintosh HD", 412], genomes: ["Genomes", 1126, "/Volumes/Genomes"], missing: ["gatk", "pharmcat", "cyrius", "clair3", "modkit", "vep"] },
    "mac-air": { label: "MacBook Air", spec: "Apple M1 · 8 cores · 16 GB", os: "macos", arch: "arm64", cores: 8, bigCores: 4, ramGB: 16, gpu: "metal",
      system: ["Macintosh HD", 180], genomes: ["Genomes", 1126, "/Volumes/Genomes"], missing: ["gatk", "pharmcat", "cyrius", "clair3", "modkit", "vep", "deepvariant", "t1k"] },
    "linux-gpu": { label: "Linux workstation", spec: "Ryzen 9 · 16 cores · 64 GB · RTX 3090 24 GB", os: "linux", arch: "x86-64", cores: 16, bigCores: 16, ramGB: 64, gpu: "nvidia", gpuGB: 24,
      system: ["Home", 900], genomes: ["Genomes", 3600, "/mnt/genomes"], missing: ["cyrius", "modkit"] },
    "linux-arm": { label: "Linux ARM64 server", spec: "Neoverse N2 · 16 cores · 64 GB", os: "linux", arch: "arm64", cores: 16, bigCores: 16, ramGB: 64, gpu: null,
      system: ["Home", 600], genomes: ["Genomes", 2000, "/mnt/genomes"], missing: ["clair3", "modkit", "vep"] },
    windows: { label: "Windows PC", spec: "Core i7 · 12 cores · 32 GB · RTX 4070 12 GB", os: "windows", arch: "x86-64", cores: 12, bigCores: 12, ramGB: 32, gpu: "nvidia", gpuGB: 12,
      system: ["C:", 380], genomes: ["Genomes", 1800, "D:/Genomes"], missing: ["cyrius"] },
    "windows-laptop": { label: "Windows laptop", spec: "Core i5 · 8 cores · 16 GB", os: "windows", arch: "x86-64", cores: 8, bigCores: 8, ramGB: 16, gpu: null,
      system: ["C:", 210], genomes: ["Genomes", 900, "D:/Genomes"], missing: ["gatk", "pharmcat", "vep-rs"] },
  };
  var DEFAULT = "mac-pro";

  /* Why a tool can't run on this computer, or null */
  var LIMITS = {
    windows: {
      deepvariant: "DeepVariant isn’t available on Windows",
      delly: "Delly isn’t available on Windows",
      freebayes: "FreeBayes isn’t available on Windows",
      t1k: "T1K (HLA typing) isn’t available on Windows",
      expansionhunter: "ExpansionHunter isn’t available on Windows",
      cyrius: "Cyrius (CYP2D6) isn’t available on Windows",
      smn: "SMNCopyNumberCaller isn’t available on Windows",
      verifybamid2: "VerifyBamID2 (contamination) isn’t available on Windows",
      somalier: "somalier (relatedness) isn’t available on Windows",
      "vep-ensembl": "Ensembl VEP isn’t available on Windows; vep-rs is",
      "bwa-mem3": "bwa-mem3 isn’t available on Windows",
      clair3: "Clair3 needs Linux or macOS",
    },
    "linux-arm64": {
      gatk: "GATK needs x86 AVX kernels, so it isn’t available on Linux ARM64",
      pharmcat: "PharmCAT isn’t qualified on Linux ARM64 yet",
      t1k: "T1K isn’t qualified on Linux ARM64 yet",
      expansionhunter: "ExpansionHunter isn’t qualified on Linux ARM64 yet",
      cyrius: "Cyrius isn’t qualified on Linux ARM64 yet",
      smn: "SMNCopyNumberCaller isn’t qualified on Linux ARM64 yet",
      "dv-models": "Only DeepVariant’s WGS model runs on Linux ARM64",
    },
    macos: {
      "dv-ont": "The Nanopore model needs an x86-64 computer",
    },
  };

  function profileKey() {
    var key = WGSM.v2state.doc.machine;
    return PROFILES[key] ? key : DEFAULT;
  }

  var key = profileKey();
  var P = PROFILES[key];
  var platform = P.os === "linux" ? "linux-" + P.arch : P.os;

  var MACHINE = {
    key: key,
    label: P.label,
    spec: P.spec,
    os: P.os,
    arch: P.arch,
    platform: platform,
    name: P.os === "macos" ? "This Mac" : "This PC",
    cores: P.cores,
    bigCores: P.bigCores,
    ramGB: P.ramGB,
    metal: P.gpu === "metal",
    nvidia: P.gpu === "nvidia",
    gpuGB: P.gpuGB || 0,
    /* Parabricks: Linux x86-64 with an NVIDIA GPU of 16 GB or more */
    parabricks: P.os === "linux" && P.arch === "x86-64" && P.gpu === "nvidia" && P.gpuGB >= 16,
    /* WGS Extract's own aligner builds: Metal on Apple Silicon; on Linux
     * x86-64 wgse-bwa (CUDA when there's an NVIDIA GPU) and wgse-bwa-mem3 */
    native: P.os === "macos" || (P.os === "linux" && P.arch === "x86-64"),
    disks: {},
    systemDisk: P.system[0],
    genomesDisk: P.genomes[0],
    genomesPath: P.genomes[2],
    downloads: P.os === "windows" ? "C:/Users/you/Downloads" : "~/Downloads",
    /* Optional Pixi tool sets (pixi.toml [environments]) not installed here */
    missing: P.missing,
    tools: { docker: P.os !== "windows", "wgse-mimalloc": P.os === "macos" },
  };
  MACHINE.disks[P.system[0]] = P.system[1];
  MACHINE.disks[P.genomes[0]] = P.genomes[1];

  /* The optional Pixi environment each tool comes in (pixi.toml) */
  var ENVS = {
    gatk: ["gatk", "GATK (with Java)"], pharmcat: ["pharmcat", "PharmCAT (with Java)"], cyrius: ["cyrius", "Cyrius"],
    clair3: ["clair3", "Clair3"], modkit: ["modkit", "modkit"], somalier: ["somalier", "somalier"],
    verifybamid2: ["verifybamid2", "VerifyBamID2"], t1k: ["t1k", "T1K"], expansionhunter: ["expansionhunter", "ExpansionHunter"],
    smn: ["smn", "SMNCopyNumberCaller"], freebayes: ["freebayes", "FreeBayes"], "vep-ensembl": ["vep", "Ensembl VEP"],
    "vep-rs": ["vep-rs", "vep-rs"], deepvariant: ["deepvariant", "DeepVariant"], phasing: ["phasing", "WhatsHap and LongPhase"],
    imputation: ["imputation", "GLIMPSE2 and Beagle"],
  };

  function unavailable(name) {
    var limits = LIMITS[platform] || {};
    var mac = P.os === "macos" ? LIMITS.macos : {};
    if (limits[name] || mac[name]) return limits[name] || mac[name];
    var env = ENVS[name];
    /* On Apple Silicon DeepVariant is the native wgse-deepvariant build */
    if (env && P.missing.indexOf(env[0]) >= 0) return env[1] + " isn’t installed";
    return null;
  }

  /* Files in the library. gb: read bases (Gb), what alignment time scales
   * by. disk: "system" or "genomes" */
  var FILES = {
    reginald: { person: "reginald", kind: "reads", tag: "CRAM", name: "reginald-mcdonald.GRCh38.30x.cram", stem: "reginald-mcdonald",
      dir: "{{bundleWorkspace}}/genomes/reginald-mcdonald", disk: "system", build: "GRCh38", ref: "hs38", depth: 31, gb: 96, size: 16.8, sex: "male", platform: "illumina" },
    gordon: { person: "gordon", kind: "reads", tag: "BAM", name: "60820188479016.bam", stem: "60820188479016",
      dir: "@genomes/Gordon", disk: "genomes", build: "GRCh37", ref: "hs37d5", depth: 38, gb: 118, size: 92.4, sex: "male", platform: "illumina" },
    alex: { person: "alex", kind: "reads", tag: "BAM", name: "NG1A2B3C4D.bam", stem: "NG1A2B3C4D",
      dir: "@downloads", disk: "system", build: "GRCh38", ref: "hs38d1", depth: 30, gb: 93, size: 71.2, sex: "female", platform: "illumina", needs: "alex-fixed" },
    alexkit: { person: "alex", kind: "kit", tag: "KIT", name: "genome_Alex_v5_Full_20241103.txt", stem: "genome_Alex_v5_Full_20241103",
      dir: "@downloads", disk: "system", build: "GRCh37", size: 0.025, sex: "female" },
    helen: { person: "helen", kind: "vcf", tag: "VCF", name: "helen-mcdonald.deepvariant.vcf.gz", stem: "helen-mcdonald",
      dir: "{{bundleWorkspace}}/genomes/helen-mcdonald", disk: "system", build: "GRCh38", ref: "hs38", size: 0.15, sex: "female" },
    fastq: { person: null, kind: "fastq", tag: "FASTQ", name: "60820188474839_SA_L001_R1/R2_001.fastq.gz", stem: "60820188474839",
      r1: "60820188474839_SA_L001_R1_001.fastq.gz", r2: "60820188474839_SA_L001_R2_001.fastq.gz",
      dir: "@downloads", disk: "system", depth: 30, gb: 93, size: 77, platform: "illumina" },
  };

  function native(p) {
    return MACHINE.os === "windows" ? p.replace(/\//g, "\\") : p;
  }

  function dir(file) {
    return file.dir.replace("@genomes", MACHINE.genomesPath).replace("@downloads", MACHINE.downloads);
  }

  function owner(file) {
    var S = WGSM.v2state;
    if (file.person) return S.first(S.person(file.person).name);
    return S.ownerName() || "Not assigned";
  }

  /* "about 2 h 10 min" from hours */
  function duration(hours) {
    var minutes = Math.max(1, Math.round(hours * 60));
    if (minutes < 60) return "about " + minutes + " min";
    if (hours >= 10) return "about " + Math.round(hours) + " h";
    var h = Math.floor(minutes / 60);
    var m = Math.round((minutes % 60) / 5) * 5;
    if (m === 60) {
      h += 1;
      m = 0;
    }
    return "about " + h + " h" + (m ? " " + m + " min" : "");
  }

  function gb(value) {
    if (value >= 1000) return (value / 1000).toFixed(1) + " TB";
    if (value >= 10) return Math.round(value) + " GB";
    if (value >= 1) return value.toFixed(1) + " GB";
    return Math.max(1, Math.round(value * 1000)) + " MB";
  }

  WGSM.v2machine = {
    profiles: PROFILES,
    machine: MACHINE,
    files: FILES,
    unavailable: unavailable,
    /* The Pixi environment that provides a tool, for install hints */
    env: function (name) {
      return ENVS[name] ? ENVS[name][0] : null;
    },
    owner: owner,
    duration: duration,
    gb: gb,
    native: native,
    diskName: function (file) {
      return file.disk === "genomes" ? MACHINE.genomesDisk : MACHINE.systemDisk;
    },
    /* Is this file usable now? (Alex's BAM needs fixing first) */
    ready: function (file) {
      return !file.needs || WGSM.v2state.flag(file.needs);
    },
    path: function (file, name) {
      return native(dir(file) + "/" + (name || file.r1 || file.name));
    },
    outdir: function (file) {
      return native(dir(file) + "/");
    },
    /* Choose the pretend computer; the page reloads to show it */
    choose: function (profile) {
      WGSM.v2state.doc.machine = profile;
      WGSM.v2state.save();
    },
    /* The static pages' Mac-specific words, for the chosen computer */
    localize: function () {
      if (key === DEFAULT) return;
      var pairs = [["Macintosh HD", MACHINE.systemDisk], ["/Volumes/Genomes", MACHINE.genomesPath]];
      if (MACHINE.os !== "macos") pairs.push(["This Mac", "This PC"], ["this Mac", "this PC"], ["⌘K", "Ctrl K"]);
      WGSM.v2state.replaceWords(pairs);
    },
  };
})();
