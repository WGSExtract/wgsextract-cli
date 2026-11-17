/* The computer and library the v2 tool pages estimate for. Illustrative,
 * like shared/data/genomes.js: an Apple Silicon Mac with 12 cores (8
 * performance, 4 efficiency) and 32 GB, WGS Extract's native aligners and
 * wgse-mimalloc installed, Java missing. Rates come from docs/hardware.md and
 * docs/resource_estimates.md; each is noted where it is used. */
(function () {
  "use strict";
  var WGSM = window.WGSM;

  var MACHINE = {
    name: "This Mac",
    cores: 12,
    bigCores: 8,
    ramGB: 32,
    metal: true,
    nvidia: false,
    disks: { "Macintosh HD": 412, Genomes: 1126 },
    tools: {
      java: false,
      docker: true,
      cyrius: false,
      "wgse-bwa": true,
      "wgse-bwa-mem2": true,
      "wgse-bwa-mem3": true,
      "wgse-minimap2": true,
      "wgse-deepvariant": true,
      "wgse-mimalloc": true,
      freebayes: true,
      delly: true,
      whatshap: true,
      longphase: false,
      clair3: false,
      glimpse2: true,
      modkit: false,
    },
  };

  /* Files in the library. gb: read bases (Gb), what alignment time scales by */
  var FILES = {
    reginald: { person: "reginald", kind: "reads", tag: "CRAM", name: "reginald-mcdonald.GRCh38.30x.cram", stem: "reginald-mcdonald",
      dir: "{{bundleWorkspace}}/genomes/reginald-mcdonald", disk: "Macintosh HD", build: "GRCh38", ref: "hs38", depth: 31, gb: 96, size: 16.8, sex: "male", platform: "illumina" },
    gordon: { person: "gordon", kind: "reads", tag: "BAM", name: "60820188479016.bam", stem: "60820188479016",
      dir: "/Volumes/Genomes/Gordon", disk: "Genomes", build: "GRCh37", ref: "hs37d5", depth: 38, gb: 118, size: 92.4, sex: "male", platform: "illumina" },
    alex: { person: "alex", kind: "reads", tag: "BAM", name: "NG1A2B3C4D.bam", stem: "NG1A2B3C4D",
      dir: "~/Downloads", disk: "Macintosh HD", build: "GRCh38", ref: "hs38d1", depth: 30, gb: 93, size: 71.2, sex: "female", platform: "illumina",
      needs: "alex-fixed" },
    alexkit: { person: "alex", kind: "kit", tag: "KIT", name: "genome_Alex_v5_Full_20241103.txt", stem: "genome_Alex_v5_Full_20241103",
      dir: "~/Downloads", disk: "Macintosh HD", build: "GRCh37", size: 0.025, sex: "female" },
    helen: { person: "helen", kind: "vcf", tag: "VCF", name: "helen-mcdonald.deepvariant.vcf.gz", stem: "helen-mcdonald",
      dir: "{{bundleWorkspace}}/genomes/helen-mcdonald", disk: "Macintosh HD", build: "GRCh38", ref: "hs38", size: 0.15, sex: "female" },
    fastq: { person: null, kind: "fastq", tag: "FASTQ", name: "60820188474839_SA_L001_R1/R2_001.fastq.gz", stem: "60820188474839",
      r1: "~/Downloads/60820188474839_SA_L001_R1_001.fastq.gz", r2: "~/Downloads/60820188474839_SA_L001_R2_001.fastq.gz",
      dir: "~/Downloads", disk: "Macintosh HD", depth: 30, gb: 93, size: 77, platform: "illumina" },
  };

  function owner(file) {
    var S = WGSM.v2state;
    if (file.person) return S.first(S.person(file.person).name);
    return S.ownerName() || "Not assigned";
  }

  /* "about 2 h 10 min" from hours */
  function duration(hours) {
    if (hours === null || hours === undefined) return "time not estimated";
    var minutes = Math.max(1, Math.round(hours * 60));
    if (minutes < 60) return "about " + minutes + " min";
    var h = Math.floor(minutes / 60);
    var m = minutes % 60;
    if (h >= 10) return "about " + Math.round(hours) + " h";
    m = Math.round(m / 5) * 5;
    return "about " + h + " h" + (m ? " " + m + " min" : "");
  }

  function gb(value) {
    if (value >= 1000) return (value / 1000).toFixed(1) + " TB";
    if (value >= 10) return Math.round(value) + " GB";
    if (value >= 1) return value.toFixed(1) + " GB";
    return Math.max(1, Math.round(value * 1000)) + " MB";
  }

  WGSM.v2machine = {
    machine: MACHINE,
    files: FILES,
    owner: owner,
    duration: duration,
    gb: gb,
    /* Is this file usable now? (Alex's BAM needs fixing first) */
    ready: function (file) {
      return !file.needs || WGSM.v2state.flag(file.needs);
    },
    path: function (file) {
      return file.dir + "/" + file.name;
    },
    /* Output folder for a file's person */
    outdir: function (file) {
      if (file.person === "gordon") return "/Volumes/Genomes/Gordon/";
      if (file.person) return "{{bundleWorkspace}}/genomes/" + (file.person === "alex" ? "alex-moreno" : file.stem) + "/";
      return "~/Downloads/";
    },
  };
})();
