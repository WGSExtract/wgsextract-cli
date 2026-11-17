/* Mock server core, part 3: configuration from plain data, the pretend
 * computer's disks, the reference data it knows and the demo's starting
 * jobs. The browser builds the input from shared/shells/v2/machine.js and
 * state.js; the Node server loads those same files. Environment-agnostic. */
(function (root) {
  "use strict";
  var M = root.WGSMMock;
  var WORKSPACE = "{{bundleWorkspace}}";

  /* input: {profiles, machineKey, files, people: [{id, name, note}]} */
  M.buildConfig = function (input) {
    var people = {};
    (input.people || []).forEach(function (p) {
      people[p.id] = { name: p.name, note: p.note || "" };
    });
    return {
      profiles: input.profiles,
      machineKey: input.machineKey && input.machineKey in input.profiles ? input.machineKey : Object.keys(input.profiles)[0],
      files: input.files,
      people: people,
      references: input.references || REFERENCES,
    };
  };

  function niceTotal(free) {
    var sizes = [256, 512, 1000, 2000, 4000, 8000, 16000];
    return sizes.filter(function (s) {
      return s >= free * 1.3;
    })[0] || Math.ceil(free * 1.5);
  }

  /* The pretend computer: a machine.js profile as plain data */
  M.machineFrom = function (config, key) {
    var k = key && config.profiles[key] ? key : config.machineKey;
    var P = config.profiles[k];
    var systemPath = P.os === "windows" ? "C:/" : P.os === "linux" ? "/home" : "/";
    return {
      key: k,
      label: P.label,
      spec: P.spec,
      os: P.os,
      arch: P.arch,
      cores: P.cores,
      bigCores: P.bigCores,
      ramGB: P.ramGB,
      gpu: P.gpu ? { kind: P.gpu, memory_gb: P.gpuGB || null } : null,
      workspace: WORKSPACE,
      genomesPath: P.genomes[2],
      downloads: P.os === "windows" ? "C:/Users/you/Downloads" : "~/Downloads",
      disks: [
        { id: "system", name: P.system[0], path: systemPath, base: P.system[1], total: niceTotal(P.system[1]) },
        { id: "genomes", name: P.genomes[0], path: P.genomes[2], base: P.genomes[1], total: niceTotal(P.genomes[1]) },
      ],
    };
  };

  /* The disk a path (or a disk id or name) is on */
  M.diskFor = function (machine, where) {
    var w = String(where || "");
    var named = machine.disks.filter(function (d) {
      return d.id === w || d.name === w;
    })[0];
    if (named) return named;
    var best = null;
    machine.disks.forEach(function (d) {
      if (d.path !== "/" && w.indexOf(d.path) === 0 && (!best || d.path.length > best.path.length)) best = d;
    });
    return best || machine.disks[0];
  };

  /* Library files with real paths on this machine */
  M.resolveFiles = function (config, machine) {
    var out = {};
    Object.keys(config.files).forEach(function (k) {
      var f = Object.assign({}, config.files[k]);
      var dir = f.dir.replace("@genomes", machine.genomesPath).replace("@downloads", machine.downloads);
      f.path = dir + "/" + (f.r1 || f.name);
      out[k] = f;
    });
    return out;
  };

  /* Reference data: installed from the start, or once a flag is set */
  var REFERENCES = [
    { id: "hs38", name: "hs38", build: "GRCh38", kind: "genome", gb: 3.1, installed: true, use: "Most labs since 2018" },
    { id: "hs37d5", name: "hs37d5", build: "GRCh37", kind: "genome", gb: 3.0, installed: true, use: "1000 Genomes; Dante before 2019" },
    { id: "T2Tv20", name: "T2T-CHM13 v2.0", build: "T2T", kind: "genome", gb: 0.9, use: "The complete genome, including all of Y; long reads" },
    { id: "hs38d1", name: "hs38d1", build: "GRCh38", kind: "genome", gb: 1.0, use: "GRCh38 with decoys" },
    { id: "hs38DH", name: "hs38DH", build: "GRCh38", kind: "genome", gb: 1.0, use: "1000 Genomes high-coverage files; ancient DNA" },
    { id: "hg38", name: "hg38", build: "GRCh38", kind: "genome", gb: 0.9, use: "Files from YSEQ" },
    { id: "hg19", name: "hg19", build: "GRCh37", kind: "genome", gb: 0.9, use: "Early Dante files (2018)" },
    { id: "hs37", name: "hs37", build: "GRCh37", kind: "genome", gb: 0.9, use: "Older 1000 Genomes files without decoys" },
    { id: "panel-1kg", name: "1000 Genomes panel", kind: "panel", gb: 30, flag: "panel-1kg", use: "Phasing and imputation" },
    { id: "vep-cache", name: "VEP cache (GRCh38)", kind: "annotation", gb: 26, flag: "vep-cache", use: "Variant effects" },
    { id: "clinvar", name: "ClinVar", kind: "annotation", gb: 0.18, flag: "clinvar", use: "Clinical significance" },
    { id: "gnomad", name: "gnomAD frequencies", kind: "annotation", gb: 34, flag: "gnomad", use: "Population frequencies" },
  ];

  /* The demo's jobs at the start (see versions/v2/jobs.html). make(op,
   * params, fields) builds a job record from a plan; fields fix its times
   * and outcome. Times are on the day the simulation starts. */
  M.seedJobs = function (now, files, make) {
    var day = M.fmt.dayStart(now);
    function at(h, m, s) {
      return day + ((h * 60 + m) * 60 + (s || 0)) * 1000;
    }
    function done(start, end, extra) {
      return Object.assign({ created: start, started: start, finished: end, status: "succeeded", pre: true }, extra);
    }
    var helenDay = at(14, 2) - 3 * 86400000;
    var downloadGB = 22.4;
    return [
      make("microarray", { input: files.helen.path, outdir: files.helen.path.replace(/\/[^/]*$/, "/") },
        done(helenDay, helenDay + 11 * 60000, { mock: { label: "DNA-test files for Helen", metrics: { kits: 2, sites: "GEDmatch and MyHeritage" } } })),
      make("lineage/mt-haplogroup", { input: files.reginald.path },
        done(at(6, 8, 2), at(6, 9), { mock: { label: "mtDNA haplogroup for Reginald", metrics: { haplogroup: "K1a1b1a" } } })),
      make("lineage/y-haplogroup", { input: files.reginald.path },
        done(at(6, 9), at(6, 11), { mock: { label: "Y haplogroup for Reginald", metrics: { haplogroup: "J-ZS241" } } })),
      make("qc/ancestry", { input: files.reginald.path },
        done(at(6, 11), at(6, 14), { mock: { label: "Ancestry for Reginald", metrics: { components: 5, summary: "mostly European and Middle Eastern" } } })),
      make("ref/index", { ref: WORKSPACE + "/reference/genomes/hs37d5.fa.gz", aligner: "bwa-mem2" },
        { created: at(7, 2), started: at(7, 2), finished: at(7, 2, 14), status: "interrupted", stopWork: Math.round(0.62 * downloadGB * 1e9),
          note: "This Mac went to sleep during the download.",
          mock: { label: "Download reference hs37d5 indexes", person: "gordon", gb: downloadGB, steps: [["bwa index", 5.4], ["bwa-mem2 index", 17]] } }),
      make("vcf/snp", { input: files.alex.path, ref: "hs38d1" },
        { created: at(7, 40), started: at(7, 40), finished: at(7, 40, 12), status: "failed", stopWork: 0,
          error: { kind: "failure", message: files.alex.name + " has no index (.bai), so it can’t be read by region." },
          mock: { label: "Call SNPs for Alex" } }),
      make("realign", { input: files.gordon.path, ref: WORKSPACE + "/reference/genomes/hs38.fa.gz", outdir: files.gordon.path.replace(/\/[^/]*$/, "/"), format: "CRAM" },
        { created: at(8, 14, 40), started: at(8, 15), status: "running",
          mock: { label: "Realign Gordon to hs38", person: "gordon", hours: 213.2 / 60, tempGB: 41, flags: ["realign-done"], outputs: [{ path: files.gordon.path.replace(/\.bam$/, "_aligned.cram"), gb: 34, kind: "cram", library: true }] } }),
      make("lineage/y-str", { input: files.reginald.path },
        { created: at(9, 31), status: "queued", mock: { label: "Y-STR markers for Reginald" } }),
    ];
  };

  M.seedStart = function (real) {
    var d = new Date(real);
    d.setHours(9, 56, 10, 0);
    return d.getTime();
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
