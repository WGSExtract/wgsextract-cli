/* Mock server core, part 2: the operations it knows (GET /v1/ops) and the
 * plan of a job: label, person, steps, simulated duration or download size,
 * outputs, temporary space and the capability flags it sets. A request's
 * mock-only "_mock" hint (from the UI's estimate engine) overrides any of
 * it; the real server ignores "_mock". Environment-agnostic. */
(function (root) {
  "use strict";
  var M = root.WGSMMock;

  /* op: [kind, title, minutes (CPU) or GB (download), summary]. "net" ops
   * are downloads: their time comes from the simulated network speed. */
  var OPS = {
    info: ["query", "File info", 0.2, "Read a BAM, CRAM or FASTQ header and summarise it"],
    "info/calculate-coverage": ["job", "Coverage", 25, "Coverage per chromosome"],
    "sample/show": ["query", "Sample", 0.1, "Show a sample's settings"],
    "ref/status": ["query", "Reference status", 0.1, "Which reference data is installed"],
    realign: ["job", "Realign", 216, "Realign a BAM or CRAM to another reference"],
    align: ["job", "Align", 300, "Align FASTQ reads to a reference"],
    "bam/index": ["job", "Index", 6, "Create a BAM or CRAM index"],
    "bam/to-cram": ["job", "Convert to CRAM", 55, "Convert a BAM to CRAM"],
    "vcf/snp": ["job", "Call SNPs", 40, "Call SNPs with bcftools"],
    "vcf/indel": ["job", "Call indels", 45, "Call indels with bcftools"],
    "vcf/deepvariant": ["job", "DeepVariant", 190, "Call variants with DeepVariant"],
    "vcf/gatk": ["job", "GATK HaplotypeCaller", 420, "Call variants with GATK"],
    "vcf/phase": ["job", "Phase", 70, "Phase variants"],
    "vcf/impute": ["job", "Impute", 90, "Impute genotypes from a panel"],
    "vcf/clinvar": ["job", "ClinVar", 4, "Annotate with ClinVar"],
    "vcf/table": ["job", "Variant table", 8, "Build a searchable variant table"],
    "vep/run": ["job", "VEP", 75, "Annotate variants with VEP"],
    microarray: ["job", "DNA-test files", 11, "Make files for DNA-test sites"],
    "lineage/y-haplogroup": ["job", "Y haplogroup", 3.7, "Find the paternal haplogroup"],
    "lineage/mt-haplogroup": ["job", "mtDNA haplogroup", 1, "Find the maternal haplogroup"],
    "lineage/y-str": ["job", "Y-STR markers", 4, "Read Y-STR markers"],
    "qc/ancestry": ["job", "Ancestry", 3, "Estimate ancestry proportions"],
    report: ["job", "Personal report", 6, "Build the personal report"],
    "ref/library": ["net", "Download reference", 0.9, "Install a reference genome"],
    "ref/index": ["net", "Download indexes", 17, "Download (or build) aligner indexes"],
    "ref/imputation": ["net", "Download the 1000 Genomes panel", 30, "Install the imputation panel"],
    "vep/download": ["net", "Download the VEP cache", 26, "Install the VEP cache"],
    "ref/clinvar": ["net", "Download ClinVar", 0.18, "Install ClinVar"],
    "ref/gnomad": ["net", "Download gnomAD", 34, "Install gnomAD frequencies"],
    "ref/gnomad-sv": ["net", "Download gnomAD-SV", 1.7, "Install gnomAD structural variants"],
    "ref/test-genome": ["net", "Download an example genome", 9.6, "Install an example genome"],
    "vcf/freebayes": ["job", "FreeBayes", 300, "Call variants with FreeBayes"],
    "vcf/clair3": ["job", "Clair3", 165, "Call long-read variants with Clair3"],
    "vcf/sv": ["job", "Structural variants", 80, "Find structural variants"],
    "vcf/cnv": ["job", "Copy-number changes", 90, "Find copy-number changes"],
    "vcf/trio": ["job", "Family trio", 10, "Analyse a child with both parents"],
    "vcf/gnomad": ["job", "gnomAD frequencies", 5, "Annotate with gnomAD frequencies"],
    "vcf/revel": ["job", "REVEL", 3, "Annotate with REVEL scores"],
    "vcf/cadd": ["job", "CADD", 8, "Annotate with CADD scores"],
    "vcf/alphamissense": ["job", "AlphaMissense", 3, "Annotate with AlphaMissense"],
    "vcf/spliceai": ["job", "SpliceAI", 4, "Annotate with SpliceAI"],
    "vcf/pharmcat": ["job", "Pharmacogenomics", 6, "Run PharmCAT"],
    "qc/fastqc": ["job", "Read quality", 90, "Check reads with FastQC"],
    "qc/fastp": ["job", "Read quality (fastp)", 15, "Check reads with fastp"],
    "qc/contamination": ["job", "Contamination", 15, "Estimate contamination"],
    "qc/relate": ["job", "Relatedness", 5, "Compare genomes for relatedness"],
    "qc/vcf": ["job", "VCF quality", 2, "Summarise a VCF"],
    "typing/hla": ["job", "HLA typing", 25, "Type the HLA genes"],
    "typing/cyp2d6": ["job", "CYP2D6 typing", 10, "Type CYP2D6"],
    "typing/smn": ["job", "SMN typing", 5, "Count SMN1 and SMN2 copies"],
    "typing/str": ["job", "Repeat expansions", 40, "Size repeat expansions"],
    "extract/custom": ["job", "Extract a region", 2, "Copy the reads of a region or gene"],
    "extract/region": ["job", "Extract a region", 10, "Extract a chromosome set"],
    "extract/mito-fasta": ["job", "mtDNA FASTA", 2, "Extract the mtDNA sequence"],
    "extract/mito-vcf": ["job", "mtDNA VCF", 2, "Extract mtDNA variants"],
    "extract/mt-bam": ["job", "mtDNA BAM", 2, "Extract mtDNA reads"],
    "extract/ydna-bam": ["job", "Y-DNA BAM", 4, "Extract Y reads"],
    "extract/ydna-vcf": ["job", "Y-DNA VCF", 4, "Extract Y SNP calls"],
    "extract/unmapped": ["job", "Unmapped reads", 20, "Extract unmapped reads"],
    "score/pgs": ["job", "Polygenic score", 5, "Compute a polygenic score"],
    "methylation/pileup": ["job", "Methylation", 180, "Read methylation marks"],
    "ref/revel": ["net", "Download REVEL", 0.6, "Install REVEL scores"],
    "ref/cadd": ["net", "Download CADD", 85, "Install CADD scores"],
    "ref/alphamissense": ["net", "Download AlphaMissense", 0.6, "Install AlphaMissense scores"],
    "ref/spliceai": ["net", "Download SpliceAI", 1, "Install SpliceAI scores"],
  };

  function has(table, key) {
    return Object.prototype.hasOwnProperty.call(table, key);
  }

  /* Every operation of the real API (shared/data/api-schema.js); OPS adds
   * how long a run takes. One without an entry is a 10-minute job. */
  /* A reference's catalog code: ref/library's install, or the name of a
   * FASTA path as other operations send it (…/genomes/hs38.fa.gz) */
  function refName(p) {
    var ref = p.install || p.ref || p.code;
    return ref ? String(ref).replace(/^.*[\\/]/, "").replace(/\.(fa|fasta|fna)(\.gz)?$/, "") : null;
  }

  function known(op) {
    var schema = root.WGSMSchema && root.WGSMSchema.ops;
    if (has(OPS, op)) return OPS[op];
    return schema && has(schema, op) ? ["job", op, 10, schema[op].summary] : null;
  }

  /* Steps: [name, weight] (CPU: share of the time; downloads: GB) */
  var STEPS = {
    realign: [["Extract reads", 37.7], ["Align", 135.1, "reads"], ["Sort and index", 40.4]],
    align: [["Align", 240, "reads"], ["Sort", 40], ["Index", 20]],
    "vcf/snp": [["Call by chromosome", 36], ["Merge", 4]],
    "vcf/indel": [["Call by chromosome", 40], ["Merge", 5]],
    "vcf/deepvariant": [["Make examples", 80], ["Call variants", 90], ["Write VCF", 20]],
    microarray: [["Call chip positions", 9], ["Write files", 2]],
    "bam/to-cram": [["Convert", 50], ["Verify", 5]],
    "bam/index": [["Index", 1]],
  };

  var READS_PER_GB = 876231560 / 118;
  var REF_GB = { hs38: 0.9, hs37d5: 0.9, T2Tv20: 0.9, hs38d1: 1.0, hs38DH: 1.0, hg38: 0.9, hg19: 0.9, hs37: 0.9 };

  function describe(op) {
    var o = known(op);
    return o && { op: op, kind: o[0] === "query" ? "query" : "job", destructive: false, summary: o[3] };
  }

  function first(name) {
    return String(name || "").trim().split(/\s+/)[0];
  }

  function base(path) {
    return String(path || "").split(/[\\/]/).pop();
  }

  function dirOf(path) {
    return String(path || "").replace(/[\\/][^\\/]*$/, "");
  }

  /* The library file a path names, matched on its file name */
  function fileFor(config, path) {
    if (!path) return null;
    var name = base(path);
    var files = config.files;
    var key = Object.keys(files).filter(function (k) {
      var f = files[k];
      return f.path === path || f.name === name || f.r1 === name || f.r2 === name;
    })[0];
    return key ? Object.assign({ id: key }, files[key]) : null;
  }

  function refDir(machine, ref) {
    return machine.workspace + "/reference/" + ref;
  }

  /* Default outputs: [{path, gb, kind, role, library}] */
  function outputsFor(op, p, file, machine) {
    var stem = file ? file.stem : base(p.input || "output").replace(/\..*$/, "");
    var dir = p.outdir ? String(p.outdir).replace(/[\\/]$/, "") : file ? dirOf(file.path) : machine.workspace + "/output";
    var size = file ? file.size || 1 : 1;
    var ext = String(p.format || (p.output_profile && p.output_profile !== "default" ? "CRAM" : "BAM")).toLowerCase();
    switch (op) {
      case "realign":
        return [{ path: dir + "/" + stem + "_aligned." + ext, gb: Math.round(size * (ext === "cram" ? 0.37 : 0.9)), kind: ext, role: "output", library: true }];
      case "align":
        return [{ path: dir + "/" + stem.replace(/_R?1(_001)?$/, "") + "_aligned." + ext, gb: Math.round(size * (ext === "cram" ? 0.44 : 1.1)), kind: ext, role: "output", library: true }];
      case "bam/to-cram":
        return [{ path: dir + "/" + stem + ".cram", gb: Math.round(size * 0.38), kind: "cram", role: "output", library: true }];
      case "bam/index":
        return [{ path: (file ? file.path : p.input) + ".bai", gb: 0.009, kind: "bai", role: "index" }];
      case "microarray":
        return [
          { path: dir + "/" + stem + "_CombinedKit_v5.txt", gb: 0.024, kind: "tsv", role: "kit" },
          { path: dir + "/" + stem + "_23andMe_v5.txt.zip", gb: 0.006, kind: "zip", role: "kit" },
        ];
      case "vcf/snp":
      case "vcf/indel":
      case "vcf/deepvariant":
      case "vcf/gatk":
        return [{ path: dir + "/" + stem + "." + op.split("/")[1] + ".vcf.gz", gb: 0.15, kind: "vcf", role: "calls" }];
      default:
        if (has(OPS, op) && OPS[op][0] === "net") return netOutputs(op, p, machine);
        return [{ path: dir + "/" + stem + "_" + op.replace(/\//g, "_") + ".json", gb: 0.001, kind: "json", role: "report" }];
    }
  }

  function netOutputs(op, p, machine) {
    var ref = refName(p) || "hs38";
    var gb = OPS[op][2];
    var path;
    if (op === "ref/library") {
      gb = REF_GB[ref] || gb;
      path = refDir(machine, ref) + "/" + ref + ".fa.gz";
    } else if (op === "ref/index") {
      path = refDir(machine, ref) + "/" + ref + ".fa.gz." + (p.aligner || "bwa-mem2") + ".tar";
    } else if (op === "ref/download") {
      path = p.out || machine.workspace + "/downloads/" + base(p.url || "file");
    } else {
      path = machine.workspace + "/reference/" + op.split("/")[1] + "/" + (p.assembly || "GRCh38") + ".tar";
    }
    return [{ path: path, gb: gb, kind: "data", role: "download" }];
  }

  function flagsFor(op, p, file) {
    var ref = refName(p);
    if (op === "ref/library" && ref) return ["ref-" + ref];
    if (op === "ref/index" && ref && (p.aligner || "bwa-mem2") === "bwa-mem2") return ["mem2-" + ref];
    if (op === "ref/imputation") return ["panel-1kg"];
    if (op === "vep/download") return ["vep-cache"];
    if (op === "ref/clinvar" || op === "ref/gnomad" || op === "ref/gnomad-sv") return [op.split("/")[1]];
    if (op === "bam/index" && file && file.needs) return [file.needs];
    return [];
  }

  function labelFor(op, p, who) {
    var title = known(op)[1];
    var ref = refName(p);
    if (op === "realign") return "Realign " + who + " to " + (ref || "hs38");
    if (op === "align") return "Align " + who + "’s reads to " + (ref || "hs38");
    if (op === "ref/library") return "Download reference " + (ref || "");
    if (op === "ref/index") return "Download " + (p.aligner || "bwa-mem2") + "’s index for " + (ref || "hs38");
    if (op === "bam/index") return "Index " + who + "’s file";
    return who ? title + " for " + who : title;
  }

  /* Normalise "_mock".steps: strings, [name, w] or {name, w|gb, unit} */
  function normSteps(list) {
    return list.map(function (s) {
      if (typeof s === "string") return { name: s, w: 1 };
      if (Array.isArray(s)) return { name: s[0], w: s[1] || 1, unit: s[2] || null };
      return { name: s.name, w: s.w || s.gb || 1, unit: s.unit || null, detail: s.detail || null };
    });
  }

  /* The job's plan, from defaults and the request's "_mock" hint.
   * work: CPU jobs in simulated ms, downloads in bytes. */
  M.plan = function (op, params, config, machine) {
    var o = known(op);
    if (!o) M.fail(404, "unknown_op", "No operation " + JSON.stringify(op) + ".");
    var p = params || {};
    var hint = p._mock || {};
    var file = fileFor(config, p.input || p.vcf_input);
    var personId = hint.person !== undefined ? hint.person : file ? file.person : null;
    var person = personId && config.people[personId];
    var who = person ? first(person.name) : "";
    var net = o[0] === "net" && !(op === "ref/index" && p.download === false);
    var outputs = (hint.outputs || outputsFor(op, p, file, machine)).map(function (out) {
      var o2 = Object.assign({ kind: "data", role: "output" }, out);
      o2.disk = M.diskFor(machine, o2.disk || o2.path).id;
      return o2;
    });
    var steps = normSteps(hint.steps || (has(STEPS, op) && STEPS[op]) || [[net ? "Download" : "Run", 1]]);
    var total;
    if (net) {
      var gb = hint.gb || outputs.reduce(function (a, out) {
        return a + out.gb;
      }, 0);
      total = Math.round(gb * 1e9);
      if (hint.gb && outputs.length === 1) outputs[0].gb = hint.gb;
    } else {
      var minutes = hint.hours ? hint.hours * 60 : o[2] * (op === "realign" || op === "align" ? (file && file.gb ? file.gb / 118 : 1) : 1);
      total = Math.round(minutes * 60000);
    }
    var sum = steps.reduce(function (a, s) {
      return a + s.w;
    }, 0);
    steps.forEach(function (s) {
      s.work = Math.round((s.w / sum) * total);
      if (s.unit === "reads") s.total = Math.round((file && file.gb ? file.gb : 118) * READS_PER_GB);
    });
    return {
      label: hint.label || labelFor(op, p, who),
      person: personId || null,
      mode: net ? "net" : "cpu",
      work: total,
      steps: steps,
      outputs: outputs,
      tempGB: hint.tempGB !== undefined ? hint.tempGB : op === "realign" || op === "align" ? Math.round((file ? file.size : 90) * 0.45) : 0,
      tempDisk: outputs[0] ? outputs[0].disk : "system",
      memGB: hint.memGB || (op === "realign" || op === "align" ? 18 : 4),
      flags: hint.flags || flagsFor(op, p, file),
      cancelFlags: hint.cancelFlags || (op === "realign" ? ["realign-cancelled"] : []),
      fail: hint.fail ? Object.assign({ at: 0.1, message: "The mock was asked to fail this job." }, hint.fail === true ? {} : hint.fail) : null,
      thenRerun: hint.thenRerun || null,
      url: net ? p.url || "https://mock.wgsextract.invalid/" + op + "/" + base(outputs[0].path) : null,
      inputs: file ? [{ path: file.path, kind: (file.tag || "").toLowerCase(), role: "input" }] : [],
      metrics: hint.metrics || {},
    };
  };

  M.ops = {
    names: function () {
      var schema = root.WGSMSchema && root.WGSMSchema.ops;
      return schema ? Object.keys(schema).sort() : Object.keys(OPS);
    },
    describe: describe, fileFor: fileFor, first: first,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
