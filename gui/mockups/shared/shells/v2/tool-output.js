/* Output overrides are a local Advanced choice. Basic retains the path but
 * leaves outdir unset, so the server's shared CLI configuration still wins. */
(function () {
  "use strict";
  var W = window.WGSM;
  function fields(def) {
    if (def.noInput || !def.ops.some(function (op) {
      var schema = W.v2requests.schema(op);
      return schema && schema.fields.outdir;
    })) return;
    def.hasAdvancedOutput = true;
    def.more = (def.more || []).concat([
      { type: "switch", key: "_advanced_output", label: "Advanced output override", def: false, ui: true,
        small: "Off: use the shared CLI setting, otherwise wgse/ beside the lab input. The saved override is retained but ignored." },
      { type: "param", key: "outdir", label: "Output directory", width: 420,
        small: "Run-specific override in Advanced only. Temporary work stays in the separate configured temporary folder.",
        when: function (c) { return c.v._advanced_output; } },
    ]);
  }
  function describe(ctx, out) {
    if (!ctx.def.hasAdvancedOutput) return;
    var destination = ctx.v._advanced_output && ctx.v.outdir ? ctx.v.outdir : ctx.m.outdir(ctx.file);
    out.makes = (out.makes || "").replace(/in [^.]+[’']s folder|Saved in [^.]+[’']s folder/g, "in " + destination);
    if (!(out.makes || "").includes(destination)) out.makes = (out.makes || "") + " Outputs: " + destination;
  }
  W.v2output = { fields: fields, describe: describe };
})();
