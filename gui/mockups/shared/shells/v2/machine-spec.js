/* The pretend computer as plain data, checked the same way in the browser
 * (machine.js, the mockup-settings editor) and by the shared mock server
 * (POST /v1/mock/machine). Environment-agnostic.
 *
 *   WGSMMock.machineSpec.normalize(raw) -> {profile, errors}
 *
 * A profile: {label, cpu, spec, os, arch, cores, bigCores, ramGB, gpu
 * ("metal" | "nvidia" | null), gpuGB, system: [name, freeGB, totalGB],
 * genomes: [name, freeGB, path, totalGB], volumes: [[name, freeGB, path,
 * totalGB]], missing: [Pixi environments not installed]} */
(function (root) {
  "use strict";
  var M = (root.WGSMMock = root.WGSMMock || {});
  var OS = ["macos", "linux", "windows"];
  var ARCH = ["arm64", "x86-64"];
  /* Optional Pixi tool sets (pixi.toml [environments]) */
  var ENVS = ["gatk", "pharmcat", "cyrius", "clair3", "modkit", "somalier", "verifybamid2", "t1k", "expansionhunter", "smn", "freebayes", "vep", "vep-rs",
    "deepvariant", "phasing", "imputation"];
  var MAX_VOLUMES = 6;

  function text(v, def, max) {
    var s = typeof v === "string" ? v.trim() : "";
    return (s || def).slice(0, max || 60);
  }

  function num(v, lo, hi, def) {
    var n = Number(v);
    if (v === "" || v === null || v === undefined || !isFinite(n)) return def;
    return Math.min(hi, Math.max(lo, Math.round(n)));
  }

  /* Where a computer's first disk is mounted */
  function systemPath(os) {
    return os === "windows" ? "C:/" : os === "linux" ? "/home" : "/";
  }

  /* A typical disk size a bit above what's free */
  function niceTotal(free) {
    var sizes = [256, 512, 1000, 2000, 4000, 8000, 16000, 32000];
    return sizes.filter(function (s) {
      return s >= free * 1.3;
    })[0] || Math.ceil(free * 1.5);
  }

  function absolute(os, p) {
    return os === "windows" ? /^[A-Za-z]:[\\/]/.test(p) : p.charAt(0) === "/";
  }

  function gpuText(p) {
    if (p.gpu === "metal") return null;
    if (p.gpu === "nvidia") return "NVIDIA " + p.gpuGB + " GB";
    return null;
  }

  M.machineSpec = {
    envs: ENVS,
    systemPath: systemPath,
    niceTotal: niceTotal,
    normalize: function (raw) {
      var r = raw && typeof raw === "object" ? raw : {};
      var errors = [];
      var os = OS.indexOf(r.os) >= 0 ? r.os : "linux";
      var arch = ARCH.indexOf(r.arch) >= 0 ? r.arch : "x86-64";
      if (os === "windows" && arch === "arm64") {
        errors.push("WGS Extract runs on Windows x86-64 only");
        arch = "x86-64";
      }
      var cores = num(r.cores, 1, 256, 8);
      var bigCores = num(r.bigCores, 1, cores, cores);
      if (Number(r.bigCores) > cores) errors.push("Performance cores can’t exceed total cores");
      var gpu = r.gpu === "metal" || r.gpu === "nvidia" ? r.gpu : null;
      if (gpu === "metal" && !(os === "macos" && arch === "arm64")) {
        errors.push("Metal requires macOS on Apple silicon");
        gpu = null;
      }
      if (gpu === "nvidia" && os === "macos") {
        errors.push("Macs don’t use NVIDIA GPUs");
        gpu = null;
      }
      var p = {
        label: text(r.label, "Custom computer", 40),
        cpu: text(r.cpu, os === "macos" ? (arch === "arm64" ? "Apple silicon" : "Intel Core") : arch === "arm64" ? "ARM64" : "x86-64", 40),
        os: os,
        arch: arch,
        cores: cores,
        bigCores: bigCores,
        ramGB: num(r.ramGB, 2, 4096, 16),
        gpu: gpu,
        gpuGB: gpu === "nvidia" ? num(r.gpuGB, 1, 192, 8) : 0,
        missing: (Array.isArray(r.missing) ? r.missing : []).filter(function (e, i, all) {
          return ENVS.indexOf(e) >= 0 && all.indexOf(e) === i;
        }),
      };
      var names = {};
      function volume(v, fallbackName, fallbackPath, kind) {
        var a = Array.isArray(v) ? v : [];
        var name = text(a[0], fallbackName, 30);
        var free = num(a[1], 0, 1e6, 100);
        var path = kind === "system" ? systemPath(os) : text(a[2], fallbackPath, 120).replace(/\\/g, "/");
        var total = num(kind === "system" ? a[2] : a[3], 1, 1e6, niceTotal(free));
        if (free > total) errors.push(name + ": free space can’t exceed total disk size");
        if (kind !== "system" && !absolute(os, path)) errors.push(name + ": “" + path + "” must be an absolute path on " + { macos: "macOS", linux: "Linux", windows: "Windows" }[os]);
        if (names[name.toLowerCase()]) errors.push("Duplicate volume name “" + name + "”");
        names[name.toLowerCase()] = true;
        return kind === "system" ? [name, free, total] : [name, free, path, total];
      }
      var sys = r.system || [];
      var gen = r.genomes || [];
      p.system = volume(sys, os === "windows" ? "C:" : os === "macos" ? "Macintosh HD" : "Home", null, "system");
      p.genomes = volume(gen, "Genomes", os === "windows" ? "D:/Genomes" : os === "macos" ? "/Volumes/Genomes" : "/mnt/genomes", "genomes");
      p.volumes = (Array.isArray(r.volumes) ? r.volumes : []).slice(0, MAX_VOLUMES).map(function (v, i) {
        return volume(v, "Volume " + (i + 1), os === "windows" ? "E:/" : "/mnt/volume" + (i + 1), "extra");
      });
      if ((r.volumes || []).length > MAX_VOLUMES) errors.push("At most " + MAX_VOLUMES + " extra volumes");
      p.spec = [p.cpu, p.cores + " cores", p.ramGB + " GB", gpuText(p)].filter(Boolean).join(" · ");
      return { profile: p, errors: errors };
    },
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
