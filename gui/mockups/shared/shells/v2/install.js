/* The Install tools page: the optional tool sets this computer lacks, how
 * to add them, and what its platform can't run at all.
 * <div data-render="v2-install"> */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;

  var USED_BY = {
    gatk: "GATK HaplotypeCaller", pharmcat: "Pharmacogenomics", cyrius: "CYP2D6 typing", clair3: "Clair3 (long reads)",
    modkit: "Methylation", vep: "VEP with Ensembl’s engine", "vep-rs": "VEP (its default engine)", deepvariant: "DeepVariant",
    t1k: "HLA typing", somalier: "Relatedness", verifybamid2: "Contamination", smn: "SMN1 and SMN2 typing",
    expansionhunter: "Repeat expansions", freebayes: "FreeBayes", phasing: "Phase variants", imputation: "Imputation",
  };

  function set(env, label) {
    var cyrius = env === "cyrius";
    return h("div.v2-sheet__pad.v2-section", { dataset: { annotId: "set-" + env } },
      h("div.v2-row.v2-row--wrap", h("span.v2-pip", { dataset: { state: "attention" } }), h("strong.v2-grow", label),
        h("span.v2-small.v2-muted", "Needed by " + (USED_BY[env] || label))),
      cyrius
        ? h("p.v2-small.v2-muted", "Its licence (PolyForm Strict) doesn’t allow WGS Extract to bundle or modify it, so it comes from its authors. Install it only if you need CYP2D6.")
        : null,
      h("p.v2-small", "In the folder where WGS Extract’s command line is installed, run:"),
      h("pre.v2-code", "pixi install --locked -e " + env),
      h("p.v2-small.v2-muted", "Then choose Check again. Each tool set is its own environment, so adding one never changes the others."));
  }

  WGSM.render.blocks["v2-install"] = function (el) {
    var m = WGSM.v2machine;
    var sets = m.missingSets();
    var limits = m.limits();
    el.appendChild(h("section.v2-section",
      h("h2", sets.length ? "Not installed" : "Every tool set is installed"),
      sets.length ? h("div.v2-sheet", sets.map(function (s) {
        return set(s[0], s[1]);
      })) : h("p.v2-small.v2-muted", "Everything WGS Extract can run on this computer is here.")));
    if (limits.length) {
      el.appendChild(h("section.v2-section", { dataset: { annotId: "platform" } },
        h("h2", "Not available on " + { windows: "Windows", "linux-arm64": "Linux ARM64", macos: "macOS" }[m.machine.platform]),
        h("div.v2-sheet", h("ul.v2-sheet__pad.v2-small", { style: { paddingLeft: "34px", margin: 0 } }, limits.map(function (text) {
          return h("li", text);
        }))),
        h("p.v2-section__note", "Run these on another computer with WGS Extract; this app and its phone access can connect to it.")));
    }
  };
})();
