/* The tool pages speak the server's API: fields are the operations' Params
 * (shared/data/api-schema.js, generated from the OpenAPI document), a run is
 * one or more POST /v1/ops/{op} requests built from them, and the command line
 * shown beside it is derived from those requests with each field's CLI flag.
 *
 * WGSM.v2requests:
 *   resolve(def)       fill each field's control, options and default from
 *                      the schema; report fields no operation of def has
 *   build(ctx)         [{op, params}] for the tool's current options
 *   command(requests)  the equivalent wgsextract command lines
 *   start(ctx, out)    send the requests, with the page's estimate as _mock
 *   download(flag, label)  a download or fix as a job; its flag is set when
 *                      the job succeeds */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var S = window.WGSMSchema;

  function schema(op) {
    return S.ops[op] || null;
  }

  function spec(def, key) {
    for (var i = 0; i < def.ops.length; i++) {
      var s = schema(def.ops[i]);
      if (s && Object.prototype.hasOwnProperty.call(s.fields, key)) return s.fields[key];
    }
    return null;
  }

  /* A "param" field takes its control from the schema; any field must be a
   * parameter of one of the tool's operations unless it says ui: true */
  function resolve(def) {
    def.ops.forEach(function (op) {
      if (!schema(op)) console.error("Tool " + def.slug + ": no operation " + op + " in the API schema");
    });
    (def.fields || []).concat(def.more || []).forEach(function (field) {
      if (field.ui || field.type === "show") return;
      var s = spec(def, field.key);
      if (!s) {
        console.error("Tool " + def.slug + ": " + field.key + " isn’t a parameter of " + def.ops.join(", "));
        return;
      }
      field.spec = s;
      if (field.type !== "param") return;
      var labels = field.labels || {};
      if (s.enum) {
        field.type = field.as === "select" || s.enum.length > 4 ? "select" : "seg";
        field.options = (s.nullable && field.unset ? [["", field.unset]] : []).concat(s.enum.map(function (v) {
          return [v, labels[v] || v];
        }));
        if (field.def === undefined) field.def = s.default === null || s.default === undefined ? (field.unset ? "" : s.enum[0]) : s.default;
      } else if (s.type === "boolean") {
        field.type = "switch";
        if (field.def === undefined) field.def = !!s.default;
      } else if (s.type === "integer" || s.type === "number") {
        field.type = "number";
        if (field.def === undefined) field.def = s.default === null || s.default === undefined ? "" : s.default;
      } else {
        field.type = "text";
        if (s.path) field.mono = true;
        if (field.def === undefined) field.def = s.default || "";
      }
    });
  }

  /* --- requests ------------------------------------------------------ */

  function coerce(s, value) {
    if (value === "" || value === null || value === undefined) return undefined;
    if (s.type === "array") {
      var list = Array.isArray(value) ? value : String(value).split(",").map(function (x) {
        return x.trim();
      }).filter(Boolean);
      return s.items === "number" ? list.map(Number) : list;
    }
    if (s.type === "integer") return parseInt(value, 10);
    if (s.type === "number") return Number(value);
    if (s.type === "boolean") return !!value;
    if (s.enum && s.enum.indexOf(value) < 0) return undefined;
    return String(value);
  }

  /* One request: the tool's fields this operation takes, sent only when
   * they differ from the API's default. Where the API's default is null
   * (config, else detected or auto), the page's default stands for it and is
   * left out too, unless the field says send: true (the page suggests a
   * value the API wouldn't pick). Plus the input file. */
  function params(ctx, op, extra) {
    var s = schema(op);
    var M = ctx.m;
    var out = {};
    var fields = (ctx.def.fields || []).concat(ctx.def.more || []);
    fields.forEach(function (field) {
      if (field.ui || !Object.prototype.hasOwnProperty.call(s.fields, field.key)) return;
      var f = s.fields[field.key];
      var value = coerce(f, ctx.v[field.key]);
      if (value === undefined) return;
      var unset = f.default === null || f.default === undefined;
      var base = unset ? (field.send ? undefined : coerce(f, field.def)) : f.default;
      if (s.required.indexOf(field.key) < 0 && JSON.stringify(value) === JSON.stringify(base)) return;
      if (f.type === "boolean" && !value && base === undefined) return;
      out[field.key] = f.path ? (Array.isArray(value) ? value.map(M.realPath) : M.realPath(value)) : value;
    });
    var file = ctx.file;
    if (!ctx.def.noInput) {
      if (file.kind === "vcf" && s.fields.vcf_input) out.vcf_input = M.realPath(M.path(file));
      else if (s.fields.input && file.kind !== "fastq") out.input = M.realPath(M.path(file));
      if (s.fields.outdir && !out.outdir) out.outdir = M.realPath(M.outdir(file));
    }
    return Object.assign(out, extra || {});
  }

  function build(ctx) {
    var def = ctx.def;
    var base = function (op, extra) {
      return { op: op, params: params(ctx, op, extra) };
    };
    return def.requests ? def.requests(ctx, base) : [base(def.ops[0])];
  }

  function quote(value) {
    var text = String(value);
    return /^[\w.,:/@+=-]+$/.test(text) ? text : '"' + text.replace(/(["\\$`])/g, "\\$1") + '"';
  }

  /* The CLI spelling of a request: flags from the CLI snapshot */
  function commandLine(req) {
    var s = schema(req.op);
    var words = ["wgsextract " + req.op.replace(/\//g, " ")];
    var positional = [];
    Object.keys(req.params).forEach(function (key) {
      var f = s.fields[key];
      var value = req.params[key];
      var flag = f.flag || ["--" + key.replace(/_/g, "-")];
      if (!flag.length) {
        positional = positional.concat(value);
        return;
      }
      if (f.type === "boolean") {
        if (value) words.push(flag[0]);
        else if (flag[1]) words.push(flag[1]);
        return;
      }
      (Array.isArray(value) ? (f.items === "number" ? [value.join(",")] : value) : [value]).forEach(function (one) {
        words.push(flag[0] + " " + quote(one));
      });
    });
    return words.concat(positional.map(quote)).join(" \\\n  ");
  }

  function command(requests) {
    return requests.map(commandLine).join("\n");
  }

  /* --- sending ------------------------------------------------------- */

  function runHours(out) {
    if (typeof out.hours === "number") return out.hours;
    var t = out.timeText || "";
    return /seconds/.test(t) ? 0.01 : /tens of minutes/.test(t) ? 0.5 : /minute/.test(t) ? 0.15 : 3;
  }

  /* extra: params for every request whose op takes them (a dry run sends
   * only the requests that can be dry-run) */
  function start(ctx, out, extra) {
    var requests = build(ctx).filter(function (req) {
      return !extra || Object.keys(extra).every(function (k) {
        return Object.prototype.hasOwnProperty.call(schema(req.op).fields, k);
      });
    });
    var label = out.jobName || ctx.def.label + (ctx.def.noInput ? "" : " for " + ctx.name);
    function mock(req) {
      return {
        label: requests.length > 1 ? label + " · " + req.op : label,
        person: ctx.def.noInput ? null : ctx.file.person,
        hours: runHours(out) / requests.length,
        memGB: out.memGB,
      };
    }
    /* A dry run writes nothing, so it skips the missing-reference and
     * disk checks and says only when the server refuses it */
    if (extra) {
      return Promise.all(requests.map(function (req) {
        return WGSM.v2api.post("/v1/ops/" + req.op, Object.assign({}, req.params, extra, { _mock: mock(req) })).then(function (res) {
          if (res.status >= 300) WGSM.toast("Request rejected: " + ((res.body && res.body.error && res.body.error.message) || res.status), "circle-alert");
          return res;
        });
      }));
    }
    return WGSM.v2edge.beforeRun(ctx, requests).then(function (ok) {
      if (!ok) return [];
      return Promise.all(requests.map(function (req) {
        return WGSM.v2edge.send(req.op, Object.assign({}, req.params, { _mock: mock(req) }));
      }));
    });
  }

  /* A download or a fix as the real operation that does it: [op, params,
   * GB, label] by the flag it sets */
  function downloadJob(flag, label) {
    var M = WGSM.v2machine;
    var gb = Number((/([\d.]+) GB/.exec(label || "") || [])[1]) || 1;
    var rest = flag.replace(/^[^-]+-/, "");
    if (/^mem2-/.test(flag)) return ["ref/index", { ref: M.fasta(rest), aligner: "bwa-mem2" }, gb, "bwa-mem2’s index for " + rest];
    if (/^ref-/.test(flag)) return ["ref/library", { install: rest }, gb, "Install reference " + rest];
    if (flag === "panel-1kg") return ["ref/imputation", {}, gb, "Install the 1000 Genomes panel"];
    if (/^vepcache-/.test(flag)) return ["vep/download", { engine: rest }, gb, "Install the VEP cache (" + rest + ")"];
    if (flag === "alex-fixed") return ["bam/index", { input: M.realPath(M.path(M.files.alex)) }, 0.9, "Fix Alex’s BAM: install hs38d1 and index it"];
    if (/^data-/.test(flag) && schema("ref/" + rest)) return ["ref/" + rest, {}, gb, "Install " + rest];
    console.error("No operation installs " + flag);
    return null;
  }

  function download(flag, label) {
    var job = downloadJob(flag, label);
    if (!job) return Promise.resolve(null);
    return WGSM.v2edge.send(job[0], Object.assign({}, job[1], { _mock: { label: job[3], gb: job[2], flags: flag === "alex-fixed" ? [flag, "ref-hs38d1"] : [flag] } }));
  }

  WGSM.v2requests = { schema: schema, resolve: resolve, build: build, command: command, start: start, download: download };
})();
