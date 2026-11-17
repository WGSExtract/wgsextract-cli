/* Mock server core, part 9: generated files. A catalog of file templates
 * (a 30× Nebula BAM, a 23andMe kit, an ONT long-read BAM …) that the Add
 * files page turns into made-up files in the library, owned by made-up
 * people, to try tools on more than the demo's four people. The catalog is
 * editable in Mockup settings. Generated files behave like the seed files:
 * jobs find them by path, they take disk space, /v1/library lists them.
 * Environment-agnostic. */
(function (root) {
  "use strict";
  var M = root.WGSMMock;

  function hasOwn(table, key) {
    return Object.prototype.hasOwnProperty.call(table, key);
  }

  var FORMATS = { BAM: "reads", CRAM: "reads", FASTQ: "fastq", VCF: "vcf", KIT: "kit" };
  var BUILDS = { GRCh38: "hs38", GRCh37: "hs37d5", T2T: "T2Tv20" };
  var PLATFORMS = ["illumina", "ont", "pacbio", "mgi", ""];
  var SEXES = ["any", "male", "female"];
  var MAX_COUNT = 20;

  /* name: a file name pattern. {first} {last} (lower case), {First}
   * {Last}, {digits} (14), {code} (8 letters and digits), {date}
   * (YYYYMMDD), {depth} */
  var DEFAULT = [
    { id: "nebula-30x", label: "Nebula 30× whole genome", format: "BAM", source: "Nebula Genomics", build: "GRCh38", ref: "hs38d1", depth: 30, size_gb: 68, platform: "mgi", sex: "any", name: "NG{code}.bam", index: false },
    { id: "dante-30x", label: "Dante 30× whole genome", format: "BAM", source: "Dante Labs", build: "GRCh37", ref: "hs37d5", depth: 30, size_gb: 88, platform: "illumina", sex: "any", name: "{digits}.bam", index: true },
    { id: "seqcom-30x-cram", label: "Sequencing.com 30× CRAM", format: "CRAM", source: "Sequencing.com", build: "GRCh38", ref: "hs38", depth: 30, size_gb: 17, platform: "illumina", sex: "any", name: "{first}-{last}.GRCh38.30x.cram", index: true },
    { id: "lowpass-4x", label: "Low-pass 4× genome", format: "BAM", source: "Gencove", build: "GRCh38", ref: "hs38", depth: 4, size_gb: 9.5, platform: "illumina", sex: "any", name: "{code}_lowpass.bam", index: true },
    { id: "exome-100x", label: "Exome 100× (genes only)", format: "BAM", source: "Clinical exome", build: "GRCh38", ref: "hs38", depth: 100, size_gb: 7.8, platform: "illumina", sex: "any", name: "{Last}_{First}_exome.bam", index: true },
    { id: "bigy-700", label: "FTDNA Big Y-700 (Y only)", format: "BAM", source: "FamilyTreeDNA", build: "GRCh38", ref: "hg38", depth: 40, size_gb: 0.9, platform: "illumina", sex: "male", name: "{first}_{last}_BigY700.bam", index: false },
    { id: "ont-20x", label: "Oxford Nanopore 20× long reads", format: "BAM", source: "Self-sequenced (ONT)", build: "T2T", ref: "T2Tv20", depth: 20, size_gb: 52, platform: "ont", sex: "any", name: "{first}_{last}.ont.T2T.bam", index: true },
    { id: "hifi-30x", label: "PacBio HiFi 30× long reads", format: "BAM", source: "PacBio HiFi", build: "GRCh38", ref: "hs38", depth: 30, size_gb: 61, platform: "pacbio", sex: "any", name: "{code}.hifi_reads.GRCh38.bam", index: true },
    { id: "fastq-30x", label: "Raw reads, 30× (FASTQ pair)", format: "FASTQ", source: "Dante Labs", build: "", ref: "", depth: 30, size_gb: 77, platform: "illumina", sex: "any", name: "{digits}_SA_L001_R1/R2_001.fastq.gz", index: false },
    { id: "deepvariant-vcf", label: "Variant calls (VCF)", format: "VCF", source: "DeepVariant", build: "GRCh38", ref: "hs38", depth: 0, size_gb: 0.16, platform: "", sex: "any", name: "{first}-{last}.deepvariant.vcf.gz", index: true },
    { id: "23andme-v5", label: "23andMe v5 raw data", format: "KIT", source: "23andMe", build: "GRCh37", ref: "", depth: 0, size_gb: 0.025, platform: "", sex: "any", name: "genome_{First}_{Last}_v5_Full_{date}.txt", index: false },
    { id: "ancestry-v2", label: "AncestryDNA v2 raw data", format: "KIT", source: "AncestryDNA", build: "GRCh37", ref: "", depth: 0, size_gb: 0.018, platform: "", sex: "any", name: "AncestryDNA_{First}_{date}.txt", index: false },
  ];

  /* Made-up people, [first, last, sex] */
  var NAMES = [
    ["Dorothy", "Pemberton", "female"], ["Winston", "Abernathy", "male"], ["Priya", "Raman", "female"], ["Tomasz", "Kowalczyk", "male"],
    ["Grace", "Okafor", "female"], ["Desmond", "Fairweather", "male"], ["Lucía", "Fernández", "female"], ["Henrik", "Dahlberg", "male"],
    ["Mabel", "Thistlewood", "female"], ["Kenji", "Watanabe", "male"], ["Fatima", "Haddad", "female"], ["Rupert", "Ashcombe", "male"],
    ["Ingrid", "Solberg", "female"], ["Marcus", "Bellweather", "male"], ["Amara", "Nwosu", "female"], ["Cornelius", "Blackwood", "male"],
    ["Rosalind", "Featherstone", "female"], ["Mateo", "Villanueva", "male"], ["Agnes", "MacTavish", "female"], ["Bartholomew", "Quill", "male"],
    ["Nadia", "Petrova", "female"], ["Oscar", "Lindqvist", "male"], ["Imogen", "Hartley", "female"], ["Ezekiel", "Crumb", "male"],
  ];

  function num(v) {
    return typeof v === "number" ? v : v === "" || v === null || v === undefined ? NaN : Number(v);
  }

  /* -> {templates, errors: [message]} */
  function validate(list) {
    var errors = [];
    if (!Array.isArray(list)) return { templates: [], errors: ["templates must be a list."] };
    if (list.length > 60) errors.push("At most 60 templates.");
    var seen = {};
    var out = list.map(function (t, i) {
      t = t || {};
      var label = String(t.label || "").trim();
      var at = "Template " + (i + 1) + (label ? " (" + label + ")" : "") + ": ";
      var id = String(t.id || label).trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      if (!label) errors.push(at + "give it a name.");
      if (!id) id = "template-" + (i + 1);
      while (seen[id]) id += "-2";
      seen[id] = true;
      var format = String(t.format || "").toUpperCase();
      if (!hasOwn(FORMATS, format)) errors.push(at + "format must be one of " + Object.keys(FORMATS).join(", ") + ".");
      var build = String(t.build || "");
      if (build && !hasOwn(BUILDS, build)) errors.push(at + "build must be GRCh38, GRCh37, T2T or empty.");
      if (format !== "FASTQ" && !build) errors.push(at + "only FASTQ files have no build.");
      var depth = num(t.depth || 0);
      if (!(depth >= 0 && depth <= 2000)) errors.push(at + "depth must be 0–2000.");
      if (FORMATS[format] === "reads" || format === "FASTQ") {
        if (!(depth > 0)) errors.push(at + "reads need a depth above 0.");
      }
      var size = num(t.size_gb);
      if (!(size > 0 && size <= 5000)) errors.push(at + "size must be more than 0 and at most 5000 GB.");
      var platform = String(t.platform || "").toLowerCase();
      if (PLATFORMS.indexOf(platform) < 0) errors.push(at + "platform must be illumina, ont, pacbio, mgi or empty.");
      var sex = String(t.sex || "any");
      if (SEXES.indexOf(sex) < 0) errors.push(at + "sex must be any, male or female.");
      var name = String(t.name || "").trim();
      if (!name) errors.push(at + "give a file name pattern, such as {first}-{last}.bam.");
      else if (/[\\]|\.\.|^\//.test(name)) errors.push(at + "the file name can’t contain a folder.");
      return {
        id: id, label: label, format: format, source: String(t.source || "").trim(), build: build,
        ref: String(t.ref || (build ? BUILDS[build] : "")).trim(), depth: depth || 0, size_gb: size, platform: platform, sex: sex,
        name: name, index: !!t.index,
      };
    });
    return { templates: out, errors: errors };
  }

  function rand(n) {
    return Math.floor(Math.random() * n);
  }

  function fill(pattern, who, date) {
    var hex = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    function slug(s) {
      return s.normalize ? s.normalize("NFD").replace(/[\u0300-\u036f]/g, "") : s;
    }
    return pattern.replace(/\{(\w+)\}/g, function (m, k) {
      if (k === "first") return slug(who.first).toLowerCase();
      if (k === "last") return slug(who.last).toLowerCase();
      if (k === "First") return slug(who.first);
      if (k === "Last") return slug(who.last);
      if (k === "digits") return String(6e13 + rand(1e9) * 1e4 + rand(1e4));
      if (k === "code") return Array.apply(null, Array(8)).map(function () {
        return hex[rand(hex.length)];
      }).join("");
      if (k === "date") return date;
      if (k === "depth") return String(who.depth);
      return m;
    });
  }

  function issues(t) {
    var out = [];
    if (FORMATS[t.format] === "reads" && !t.index) out.push({ code: "no_index", text: "No index (." + (t.format === "CRAM" ? "crai" : "bai") + "), so it can’t be read by region yet." });
    if (t.format === "FASTQ") out.push({ code: "not_aligned", text: "Raw reads: align them before other tools can use them." });
    if (t.ref === "hg38" || t.ref === "hs38d1") out.push({ code: "reference", text: "Made against " + t.ref + "; it may need downloading first." });
    return out;
  }

  M.routes.push(function (mock) {
    var r = mock.router;
    function doc() {
      var d = mock.doc();
      d.added = d.added || {};
      return d;
    }
    function catalog() {
      return doc().catalog || DEFAULT;
    }
    function nameTaken(full) {
      var people = mock.ctx().people;
      return Object.keys(people).some(function (id) {
        return people[id].name === full;
      });
    }
    function idFor(name) {
      var base = String(name).trim().toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "person";
      var id = base;
      for (var n = 2; hasOwn(mock.ctx().people, id); n++) id = base + "-" + n;
      return id;
    }
    function newPerson(full, sex) {
      var id = idFor(full);
      doc().people[id] = { name: full, note: "", added: true, generated: true, sex: sex || null };
      return id;
    }
    function madeUp(sex) {
      var pool = NAMES.filter(function (n) {
        return (sex === "any" || n[2] === sex) && !nameTaken(n[0] + " " + n[1]);
      });
      if (!pool.length) pool = NAMES.filter(function (n) {
        return sex === "any" || n[2] === sex;
      });
      var n = pool[rand(pool.length)];
      return { first: n[0], last: n[1] + (nameTaken(n[0] + " " + n[1]) ? " " + (2 + rand(8)) : ""), sex: n[2] };
    }

    function generated() {
      var c = mock.ctx();
      var people = {};
      var files = {};
      Object.keys(doc().added).forEach(function (k) {
        files[k] = doc().added[k];
        var p = files[k].person;
        if (p && hasOwn(c.people, p)) people[p] = { name: c.people[p].name, note: c.people[p].note || "", generated: !!c.people[p].generated, sex: c.people[p].sex || null };
      });
      Object.keys(doc().people).forEach(function (id) {
        if (doc().people[id].generated) people[id] = { name: c.people[id].name, note: c.people[id].note || "", generated: true, sex: c.people[id].sex || null };
      });
      return { files: files, people: people };
    }

    /* MOCK-ONLY. The catalog the Add files page generates from */
    r.add("GET", "/v1/mock/catalog", "mock", function () {
      return { templates: catalog(), custom: !!doc().catalog, formats: Object.keys(FORMATS), builds: Object.keys(BUILDS), platforms: PLATFORMS, max_count: MAX_COUNT };
    });
    r.add("PUT", "/v1/mock/catalog", "mock", function (req) {
      var v = validate((req.body || {}).templates);
      if (v.errors.length) return { status: 400, body: { error: { code: "usage", message: v.errors[0], details: v.errors } } };
      if (!v.templates.length) M.fail(400, "usage", "Keep at least one template, or restore the default catalog.");
      doc().catalog = v.templates;
      mock.save();
      return { templates: v.templates, custom: true };
    });
    r.add("DELETE", "/v1/mock/catalog", "mock", function () {
      delete doc().catalog;
      mock.save();
      return { templates: DEFAULT, custom: false };
    });

    /* MOCK-ONLY. Generated files and their people, as machine.js FILES
     * entries, for the page's file pickers */
    r.add("GET", "/v1/mock/files", "mock", generated);

    /* MOCK-ONLY. Make files from a template and add them to the library.
     * {template, count, whose: "new" | "unassigned" | <person id>, name}:
     * "new" makes up a person per file, or one person called name */
    r.add("POST", "/v1/mock/files", "mock", function (req) {
      var b = req.body || {};
      var t = catalog().filter(function (x) {
        return x.id === b.template;
      })[0];
      if (!t) M.fail(404, "unknown_template", "No template " + JSON.stringify(b.template) + " in the catalog.");
      var count = b.count === undefined ? 1 : num(b.count);
      if (!(count >= 1 && count <= MAX_COUNT && count % 1 === 0)) M.fail(400, "usage", "count must be a whole number from 1 to " + MAX_COUNT + ".");
      var whose = b.whose || "new";
      var c = mock.ctx();
      if (whose !== "new" && whose !== "unassigned" && !hasOwn(c.people, whose)) M.fail(404, "unknown_person", "No person " + JSON.stringify(whose) + ".");
      var named = whose === "new" && b.name && String(b.name).trim();
      var d = new Date(mock.clock.now() - rand(400) * 86400000);
      var date = String(d.getFullYear()) + ("0" + (d.getMonth() + 1)).slice(-2) + ("0" + d.getDate()).slice(-2);
      var shared = null;
      if (named) {
        var parts = String(b.name).trim().split(/\s+/);
        shared = { first: parts[0], last: parts.slice(1).join(" ") || parts[0], sex: t.sex === "any" ? null : t.sex };
        shared.id = newPerson(String(b.name).trim(), shared.sex);
      }
      var made = [];
      for (var i = 0; i < count; i++) {
        var who;
        if (shared) who = shared;
        else if (whose === "new") {
          who = madeUp(t.sex);
          who.id = newPerson(who.first + " " + who.last, who.sex);
        } else if (whose === "unassigned") who = Object.assign(madeUp(t.sex), { id: null });
        else {
          var parts2 = c.people[whose].name.split(/\s+/);
          who = { id: whose, first: parts2[0], last: parts2.slice(1).join(" ") || parts2[0] };
        }
        who.depth = t.depth;
        var name = fill(t.name, who, date);
        var kind = FORMATS[t.format];
        var big = t.size_gb >= 20;
        var key = "gen-" + Date.now().toString(36).slice(-4) + Math.random().toString(36).slice(2, 6);
        var fastq = t.format === "FASTQ";
        var file = {
          person: who.id, kind: kind, tag: t.format, name: name, stem: name.replace(/(_R1\/R2_001)?\..*$/, ""),
          dir: big ? "@genomes/" + (who.first || "Unassigned") : "@downloads", disk: big ? "genomes" : "system",
          build: t.build || null, ref: t.ref || null, depth: t.depth || null, gb: t.depth ? Math.round(t.depth * 3.1) : null, size: t.size_gb,
          sex: t.sex === "any" ? who.sex || null : t.sex, platform: t.platform || null, source: t.source, template: t.id,
          generated: true, issues: issues(t), added_at: mock.clock.now(),
        };
        if (fastq) {
          file.r1 = name.replace("R1/R2", "R1");
          file.r2 = name.replace("R1/R2", "R2");
        }
        doc().added[key] = file;
        made.push(key);
      }
      mock.save();
      var view = M.resolveFiles({ files: doc().added }, mock.ctx().machine);
      return {
        files: made.map(function (k) {
          return Object.assign({ id: k }, view[k]);
        }),
        people: made.map(function (k) {
          return doc().added[k].person;
        }).filter(function (p, i, all) {
          return p && all.indexOf(p) === i;
        }).map(function (p) {
          return { id: p, name: mock.ctx().people[p].name, generated: !!doc().people[p].generated };
        }),
      };
    });

    /* MOCK-ONLY. Remove every generated file and made-up person */
    r.add("DELETE", "/v1/mock/files", "mock", function () {
      if (mock.requireIdle) Object.keys(doc().added).forEach(function (id) {
        mock.requireIdle(null, mock.ctx().files[id].path);
      });
      var n = Object.keys(doc().added).length;
      doc().added = {};
      Object.keys(doc().people).forEach(function (id) {
        if (doc().people[id].generated) delete doc().people[id];
      });
      mock.save();
      return { removed: n };
    });

    /* PROPOSED — mock only. Take a file out of the library; the file stays
     * on disk. Here only files added since the seed can be removed. */
    r.add("DELETE", "/v1/library/files/{id}", "proposed", function (req) {
      var id = req.params.id;
      if (!hasOwn(mock.ctx().files, id)) M.fail(404, "unknown_file", "No file " + JSON.stringify(id) + ".");
      if (!hasOwn(doc().added, id)) M.fail(409, "seed_file", "The demo’s own files stay; only added files can be removed.");
      if (mock.requireIdle) mock.requireIdle(null, mock.ctx().files[id].path);
      var who = doc().added[id].person;
      delete doc().added[id];
      var left = Object.keys(doc().added).some(function (k) {
        return doc().added[k].person === who;
      });
      if (who && !left && doc().people[who] && doc().people[who].generated) delete doc().people[who];
      mock.save();
      return { id: id, removed: true, file_kept: true };
    });
  });

  M.fileCatalog = { DEFAULT: DEFAULT, FORMATS: FORMATS, BUILDS: BUILDS, PLATFORMS: PLATFORMS, validate: validate, MAX_COUNT: MAX_COUNT };
})(typeof globalThis !== "undefined" ? globalThis : this);
