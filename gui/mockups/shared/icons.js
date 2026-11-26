/* Inline SVG icons from icons/icons.js. Markup uses <i data-icon="name"></i>
 * (optionally with class="icon--sm" etc.); WGSM.icons.hydrate() swaps them
 * for SVG. Scripts use WGSM.icon("name"). Icons are decorative
 * (aria-hidden); the label sits next to them or on the button. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var ICONS = window.WGSM_ICONS || {};
  var NS = "http://www.w3.org/2000/svg";
  // Keep archived mockup markup working without keeping its old drawings.
  var draftNames = {
    admixture: "ancestry",
    align: "alignment-import",
    chip: "microarray",
    convert: "refresh-cw",
    "file-bam": "bam",
    "file-cram": "bam",
    "file-fasta": "fasta",
    "file-fastq": "fastq",
    "file-raw": "basecall",
    "file-vcf": "vcf",
    gpu: "cpu",
    helix: "fasta",
    jobs: "terminal",
    "kit-upload": "upload",
    "mt-ring": "mt-dna",
    qc: "coverage",
    realign: "alignment-import",
    reference: "reference-genome",
    relate: "trio",
    remote: "server",
    repeats: "typing",
    variant: "variant-calling",
    "y-lineage": "haplotree",
  };

  WGSM.icon = function (name, extraClass) {
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    svg.setAttribute("class", "icon" + (extraClass ? " " + extraClass : ""));
    svg.dataset.icon = name;
    var icon = ICONS[Object.hasOwn(draftNames, name) ? draftNames[name] : name];
    if (!icon) {
      console.warn("Unknown icon: " + name);
      icon = ICONS["circle-help"];
    }
    svg.innerHTML = icon.body;
    return svg;
  };

  WGSM.icons = {
    names: function () {
      return Object.keys(ICONS);
    },
    meta: function (name) {
      return ICONS[name];
    },
    hydrate: function (scope) {
      WGSM.qsa("i[data-icon]", scope).forEach(function (el) {
        var svg = WGSM.icon(el.dataset.icon, el.className);
        el.replaceWith(svg);
      });
    },
  };
})();
