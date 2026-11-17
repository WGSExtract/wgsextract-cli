/* Inline, self-contained lesson drawings: no requests, including on file://. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var serial = 0;
  var diagrams = Object.create(null);

  function node(parent, tag, attrs, value) {
    var el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.keys(attrs || {}).forEach(function (key) { el.setAttribute(key, attrs[key]); });
    if (value !== undefined) el.textContent = value;
    parent.appendChild(el);
    return el;
  }
  function text(parent, x, y, value, cls) {
    return node(parent, "text", { x: x, y: y, class: cls || "" }, value);
  }
  function lines(parent, x, y, values, cls) {
    values.forEach(function (value, i) { text(parent, x, y + i * 19, value, cls); });
  }
  function rect(parent, x, y, width, height, cls) {
    return node(parent, "rect", { x: x, y: y, width: width, height: height, rx: 5, class: cls || "dg-panel" });
  }
  function path(parent, d, cls) {
    return node(parent, "path", { d: d, class: cls || "dg-line" });
  }
  function line(parent, x1, y1, x2, y2, cls) {
    return path(parent, "M" + x1 + " " + y1 + " L" + x2 + " " + y2, cls);
  }
  function down(parent, x, y1, y2, label) {
    line(parent, x, y1, x, y2);
    path(parent, "M" + (x - 4) + " " + (y2 - 5) + " L" + x + " " + y2 + " L" + (x + 4) + " " + (y2 - 5));
    if (label) text(parent, x + 12, (y1 + y2) / 2 + 5, label, "dg-muted");
  }
  function define(name, height, title, description, caption, draw) {
    diagrams[name] = { height: height, title: title, description: description, caption: caption, draw: draw };
  }

  define("genome-scale", 570, "From a cell to DNA letters",
    "A typical cell contains a nucleus and mitochondria. A typical XY nucleus has 22 pairs of autosomes plus X and Y, shown as 23 pairs of rods. XX is another common complement; other complements exist. Mitochondria contain their own circular DNA. A chromosome contains a long DNA double helix, whose paired bases are A with T and C with G. One set of nuclear chromosomes contains roughly 3.1 billion base pairs; a typical cell has two sets.",
    "The 3.1 billion figure describes roughly one set of nuclear chromosomes. Mitochondria carry a separate, much smaller circular genome.",
    function (s) {
      text(s, 18, 27, "Inside a typical human cell", "dg-heading");
      node(s, "ellipse", { cx: 93, cy: 100, rx: 74, ry: 53, class: "dg-panel" });
      node(s, "ellipse", { cx: 83, cy: 97, rx: 34, ry: 29, class: "dg-emphasis" });
      text(s, 83, 102, "Nucleus", "dg-center");
      node(s, "ellipse", { cx: 140, cy: 120, rx: 12, ry: 7, class: "dg-organelle" });
      line(s, 151, 120, 185, 120);
      lines(s, 193, 91, ["Mitochondrion", "has circular", "mtDNA"]);
      node(s, "ellipse", { cx: 272, cy: 145, rx: 16, ry: 8, class: "dg-ring" });
      down(s, 83, 155, 181);
      text(s, 18, 207, "23 chromosome pairs · XY example", "dg-heading");
      for (var i = 0; i < 23; i++) {
        var x = 25 + (i % 12) * 28;
        var y = 225 + Math.floor(i / 12) * 64;
        rect(s, x, y, 7, 25, "dg-chromosome");
        rect(s, x + 10, y, 7, i === 22 ? 16 : 25, "dg-chromosome");
        text(s, x + 8, y + 44, i === 22 ? "X/Y" : String(i + 1), "dg-center dg-small");
      }
      lines(s, 18, 353, ["22 pairs of autosomes, plus X and Y.", "XX is also common; other complements exist."], "dg-muted");
      down(s, 83, 385, 411, "Zoom into one DNA molecule");
      function helixY(x) { return 20 * Math.cos((x - 35) * Math.PI / 100); }
      [1, -1].forEach(function (strand) {
        var points = [];
        for (var x = 35; x <= 325; x += 5) points.push(x + " " + (450 + strand * helixY(x)));
        path(s, "M" + points.join(" L"), "dg-helix");
      });
      [45, 65, 85, 105, 125, 145, 165, 185, 205, 225, 245, 265, 285, 305].forEach(function (x) {
        var dy = helixY(x);
        line(s, x, 450 - dy, x, 450 + dy, "dg-rung");
      });
      text(s, 18, 510, "Base pairs:  A–T   and   C–G", "dg-heading");
      lines(s, 18, 539, ["~3.1 billion base pairs per nuclear set.", "Most cells carry two sets."], "dg-muted");
    });

  define("inheritance-lines", 430, "Three ways DNA reaches Reginald",
    "Gordon McDonald, Reginald's father, passes a Y chromosome to Reginald along the direct paternal line, shown in cyan. Helen McDonald, Reginald's mother, passes mitochondrial DNA to Reginald along the direct maternal line, shown in magenta. Mothers usually pass mitochondrial DNA to all children; fathers usually pass their Y to sons. Below, two autosomal chromosome copies show that Reginald inherits one copy from each parent. Each parent's transmitted copy is itself a mixture of that parent's ancestors. The two direct lineages do not represent all ancestry.",
    "Reginald inherits autosomes from both parents, Y from Gordon, and mtDNA from Helen. A Y or mtDNA haplogroup follows just one ancestral line.",
    function (s) {
      text(s, 18, 27, "The McDonald family", "dg-heading");
      rect(s, 18, 48, 150, 69);
      rect(s, 192, 48, 150, 69);
      lines(s, 30, 75, ["Gordon", "Father"]);
      lines(s, 204, 75, ["Helen", "Mother"]);
      line(s, 91, 123, 91, 211, "dg-paternal-line");
      line(s, 267, 123, 267, 211, "dg-maternal-line");
      lines(s, 102, 150, ["Y", "to sons"], "dg-paternal");
      lines(s, 255, 150, ["mtDNA", "to all children"], "dg-maternal dg-end");
      path(s, "M87 204 L91 211 L95 204", "dg-paternal-line");
      path(s, "M263 204 L267 211 L271 204", "dg-maternal-line");
      rect(s, 18, 220, 324, 58);
      text(s, 180, 245, "Reginald · their son", "dg-center dg-heading");
      text(s, 180, 266, "Y from Gordon · mtDNA from Helen", "dg-center");
      text(s, 18, 313, "Autosomes: one copy from each parent", "dg-heading");
      text(s, 18, 342, "From Gordon");
      text(s, 18, 372, "From Helen");
      [0, 1, 2, 3, 4].forEach(function (i) {
        rect(s, 138 + i * 39, 327, 37, 18, i % 2 ? "dg-series-b" : "dg-series-a");
        rect(s, 138 + i * 39, 357, 37, 18, i % 2 ? "dg-series-c" : "dg-series-d");
      });
      lines(s, 18, 402, ["Each copy mixes earlier ancestors.", "The coloured blocks are illustrative."], "dg-muted");
    });

  define("read-pileup", 390, "Depth varies from position to position",
    "Six short reads are aligned beneath a twelve-base reference. At the highlighted sixth position the reference is A: three reads have A and three have G, evidence consistent with an A/G heterozygous genotype, not proof on its own. Only one read reaches the last two positions, illustrating lower coverage nearby. Six reads are shown for clarity, not 30. A reported 30 times mean depth is an average over a region, not a minimum at every base.",
    "Half the reads here support G where the reference has A. Real calls also depend on base quality, mapping quality and other evidence.",
    function (s) {
      text(s, 18, 27, "A small window into aligned reads", "dg-heading");
      text(s, 18, 52, "6 reads shown, not a 30× average", "dg-muted");
      rect(s, 146, 75, 24, 226, "dg-highlight");
      text(s, 18, 74, "Reference", "dg-muted");
      var reference = "ACTGCAATCGTA";
      function bases(y, start, end, alternate) {
        for (var i = start; i < end; i++) {
          text(s, 28 + i * 26, y, alternate && i === 5 ? "G" : reference[i],
            "dg-center dg-base" + (alternate && i === 5 ? " dg-alt" : ""));
        }
      }
      bases(101, 0, 12, false);
      text(s, 18, 131, "Reads", "dg-muted");
      [[0, 10], [1, 12], [0, 8], [2, 9], [1, 10], [3, 8]].forEach(function (range, i) {
        rect(s, 17 + range[0] * 26, 142 + i * 26, (range[1] - range[0]) * 26 - 3, 23, "dg-read");
        bases(159 + i * 26, range[0], range[1], i % 2 === 0);
      });
      line(s, 158, 304, 158, 319);
      line(s, 328, 179, 340, 179);
      line(s, 340, 179, 340, 335);
      text(s, 158, 340, "3 A + 3 G", "dg-center dg-heading");
      text(s, 340, 357, "Right edge: just 1 read", "dg-end dg-muted");
      text(s, 18, 380, "30× mean depth can still contain gaps.", "dg-muted");
    });

  define("short-vs-long-reads", 405, "Long reads can bridge repeats",
    "A paired-end DNA fragment is read from each end, producing a 150-base R1 and 150-base R2 separated by an unsequenced middle. Below, two identical repeat blocks lie between unique DNA regions. A short read wholly inside a repeat matches both blocks and cannot by itself choose a location. A long read spans both repeats and the unique flanks, helping place the repeated sequence. Sizes are not to scale. HiFi reads are often 10 to 25 kilobases; Nanopore reads can be tens of kilobases or longer.",
    "Paired-end reads leave an unsequenced middle. Long reads can reach unique sequence on both sides of a repeat, making placement easier.",
    function (s) {
      text(s, 18, 27, "Paired-end: two ends of one fragment", "dg-heading");
      line(s, 25, 70, 335, 70, "dg-dashed");
      rect(s, 25, 59, 83, 22, "dg-series-a");
      rect(s, 252, 59, 83, 22, "dg-series-a");
      text(s, 67, 105, "R1 · 150 bp", "dg-center");
      text(s, 293, 105, "R2 · 150 bp", "dg-center");
      text(s, 180, 132, "Middle not read", "dg-center dg-muted");
      text(s, 18, 181, "Repeated DNA can hide the location", "dg-heading");
      rect(s, 18, 201, 66, 27);
      rect(s, 87, 201, 91, 27, "dg-repeat");
      rect(s, 181, 201, 91, 27, "dg-repeat");
      rect(s, 275, 201, 67, 27);
      ["Unique", "Repeat", "Repeat", "Unique"].forEach(function (label, i) {
        text(s, [51, 132, 226, 308][i], 220, label, "dg-center");
      });
      rect(s, 147, 255, 47, 13, "dg-series-a");
      path(s, "M158 250 L132 234 M183 250 L226 234", "dg-dashed");
      text(s, 180, 291, "Short read: which copy?", "dg-center");
      rect(s, 26, 313, 308, 14, "dg-series-c");
      text(s, 180, 349, "Long read: spans both unique flanks", "dg-center");
      lines(s, 18, 378, ["HiFi: often 10–25 kb; ONT: tens of kb+", "Schematic; lengths are not to scale."], "dg-muted");
    });

  define("reference-builds", 440, "The same sequence can have a new address",
    "Two reference tracks show the same illustrative gene at position 1000 in GRCh37 and 1200 in GRCh38. Added or corrected sequence can move coordinates; these numbers are invented examples, not real gene coordinates or a universal offset. A second pair of tracks shows a gap in an older assembly and resolved sequence across that region in T2T-CHM13. A variant file and its annotation resource must agree on the reference assembly. CHM13 itself has no Y chromosome.",
    "Coordinates belong to a particular reference build. These example positions are illustrative: converting real variants is not a fixed offset.",
    function (s) {
      text(s, 18, 27, "Same gene, different map address", "dg-heading");
      text(s, 18, 54, "Illustrative coordinates — not a real gene", "dg-muted");
      ["GRCh37", "GRCh38"].forEach(function (build, i) {
        var y = 89 + i * 90;
        text(s, 18, y, build, "dg-heading");
        line(s, 18, y + 25, 119 + i * 49, y + 25);
        line(s, 191 + i * 49, y + 25, 342, y + 25);
        rect(s, 119 + i * 49, y + 12, 72, 26, "dg-gene");
        text(s, 155 + i * 49, y + 31, "Gene", "dg-center");
        text(s, 155 + i * 49, y + 58, i ? "position 1,200" : "position 1,000", "dg-center");
      });
      text(s, 18, 273, "T2T resolves previously missing DNA", "dg-heading");
      text(s, 18, 304, "Older map");
      line(s, 124, 299, 190, 299, "dg-helix");
      line(s, 190, 299, 270, 299, "dg-dashed");
      line(s, 270, 299, 342, 299, "dg-helix");
      text(s, 230, 322, "gap", "dg-center dg-muted");
      text(s, 18, 352, "T2T-CHM13");
      line(s, 124, 347, 342, 347, "dg-helix");
      rect(s, 190, 339, 80, 16, "dg-series-c");
      lines(s, 18, 394, ["Match the build of calls and annotations.", "CHM13 itself has no Y chromosome."], "dg-muted");
    });

  define("file-journey", 620, "From reads to an interpreted report",
    "A vertical workflow begins with compressed FASTQ, roughly 30 to 100 GB for 30 times short-read human data, holding sequences and base qualities without placements. Alignment produces BAM, often 60 to 100 GB, or CRAM, often tens of GB, preserving read records and adding placements; reference-based CRAM needs the exact reference to decode. Calling produces a compressed small-variant VCF, often hundreds of MB, containing inferred genotypes and quality but not the original reads. Annotation adds context to produce a report, which cannot replace the underlying evidence. Sizes are planning examples, not guarantees.",
    "Keep the original read evidence if you can. A smaller VCF or report cannot reconstruct the reads, and storage needs vary.",
    function (s) {
      text(s, 18, 27, "30× short-read human genome", "dg-heading");
      var steps = [
        { y: 48, h: 96, title: "FASTQ · compressed", rows: ["~30–100 GB", "Read sequences + base qualities", "No reference positions"] },
        { y: 195, h: 115, title: "BAM / CRAM", rows: ["BAM ~60–100 GB; CRAM tens of GB", "Reads + qualities + placements", "CRAM: keep the exact reference", "Some pipelines reduce quality detail"] },
        { y: 361, h: 96, title: "VCF · compressed", rows: ["Hundreds of MB", "Called genotypes + quality", "Original reads not retained here"] },
        { y: 508, h: 96, title: "Report", rows: ["Selected findings + interpretation", "Not the full evidence", "Keep inputs, versions and settings"] },
      ];
      steps.forEach(function (step, i) {
        rect(s, 18, step.y, 324, step.h);
        text(s, 30, step.y + 24, step.title, "dg-heading");
        lines(s, 30, step.y + 46, step.rows, "dg-muted");
        if (i < 3) down(s, 47, step.y + step.h + 8, steps[i + 1].y - 9, ["Align: add placements", "Call: infer alleles", "Annotate: add context"][i]);
      });
    });

  define("genotypes", 480, "Two chromosome copies, one position",
    "At a two-copy site with reference A and first alternate G, the two illustrated chromosome copies carry A and A for genotype 0/0, A and G for 0/1, or G and G for 1/1. A slash does not identify which parent supplied an allele. A missing genotype, dot slash dot, is not A/A. Below, sequence examples compare reference ACT with an SNV AGT, an insertion ACGT, and a deletion AT; dashes show alignment gaps for teaching, not literal VCF allele notation.",
    "In a VCF genotype, 0 means REF and 1 means the first ALT, not good and bad. The sequence examples below are alignments, not VCF record syntax.",
    function (s) {
      text(s, 18, 27, "Two copies · REF = A · ALT = G", "dg-heading");
      [["0/0", "A", "A"], ["0/1", "A", "G"], ["1/1", "G", "G"]].forEach(function (gt, i) {
        var x = 68 + i * 112;
        text(s, x, 66, gt[0], "dg-center dg-heading");
        [1, 2].forEach(function (copy) {
          line(s, x - 36, 82 + copy * 30, x - 14, 82 + copy * 30, "dg-chromosome-line");
          line(s, x + 14, 82 + copy * 30, x + 36, 82 + copy * 30, "dg-chromosome-line");
          rect(s, x - 13, 69 + copy * 30, 26, 26, gt[copy] === "G" ? "dg-highlight" : "dg-panel");
          text(s, x, 88 + copy * 30, gt[copy], "dg-center dg-base");
        });
        text(s, x, 180, ["A / A", "A / G", "G / G"][i], "dg-center");
      });
      lines(s, 18, 211, ["0/1: heterozygous — one of each allele.", "./. means no complete call, not A/A."], "dg-muted");
      text(s, 18, 275, "Three small sequence changes", "dg-heading");
      text(s, 152, 306, "Reference", "dg-center dg-muted");
      text(s, 285, 306, "Changed", "dg-center dg-muted");
      [["SNV", "A C T", "A G T"], ["Insertion", "A C – T", "A C G T"], ["Deletion", "A C T", "A – T"]].forEach(function (row, i) {
        var y = 339 + i * 43;
        text(s, 18, y, row[0]);
        text(s, 152, y, row[1], "dg-center dg-base");
        text(s, 220, y, "→", "dg-center dg-muted");
        text(s, 285, y, row[2], "dg-center dg-base");
      });
      text(s, 18, 466, "Dashes show gaps in this alignment.", "dg-muted");
    });

  define("missing-not-reference", 410, "A missing VCF row is not a reference call",
    "A simplified variant-only VCF has rows at positions 99 and 101, but no row at position 100. Three possible explanations follow: no usable reads, a call removed by filtering, or evidence that really supported the reference A/A. The missing row alone cannot choose among these possibilities. Position 100 is therefore unknown from this file, not automatically genotype 0/0; assessed gVCF blocks or suitable reads can provide additional evidence.",
    "A variant-only VCF does not explain why a position is absent. Check suitable read evidence or assessed gVCF blocks before calling it reference.",
    function (s) {
      text(s, 18, 27, "A variant-only VCF", "dg-heading");
      rect(s, 18, 48, 324, 136);
      ["Position", "REF", "ALT", "GT"].forEach(function (label, i) { text(s, [30, 150, 220, 287][i], 74, label, "dg-muted"); });
      [["99", "C", "T", "0/1"], ["101", "G", "A", "1/1"]].forEach(function (row, i) {
        row.forEach(function (value, j) { text(s, [30, 150, 220, 287][j], 104 + i * 59, value, "dg-base"); });
      });
      line(s, 30, 130, 330, 130, "dg-dashed");
      text(s, 180, 212, "Position 100 has no row", "dg-center dg-heading");
      down(s, 180, 222, 243);
      rect(s, 18, 252, 324, 96);
      lines(s, 30, 277, ["No usable reads?", "A call removed by filtering?", "Actually reference (A/A)?"]);
      text(s, 180, 382, "From absence alone: unknown", "dg-center dg-heading");
      text(s, 180, 403, "Do not fill the gap with 0/0.", "dg-center dg-muted");
    });

  define("evidence-ladder", 460, "Five clinical variant classifications",
    "Five equal-size boxes list the ACMG/AMP classification terms: benign, likely benign, variant of uncertain significance, likely pathogenic and pathogenic. Benign and likely benign support a non-disease-causing interpretation; a VUS has insufficient or conflicting evidence; likely pathogenic and pathogenic support a disease-causing interpretation in the relevant condition and inheritance context. These are categories, not a numerical score or a patient's chance of disease. Clinical confirmation and a clinician's assessment are needed before health decisions.",
    "Classification concerns a variant in a clinical context, not a diagnosis of the person. A VUS should not by itself guide care.",
    function (s) {
      text(s, 18, 27, "ACMG / AMP classification", "dg-heading");
      text(s, 18, 51, "Evidence categories — not a risk score", "dg-muted");
      var labels = ["Benign", "Likely benign", "VUS · uncertain significance", "Likely pathogenic", "Pathogenic"];
      labels.forEach(function (label, i) {
        var y = 72 + i * 53;
        rect(s, 18, y, 324, 42, i === 2 ? "dg-uncertain" : "dg-panel");
        text(s, 32, y + 27, label, "dg-heading");
      });
      lines(s, 18, 365, ["Condition and inheritance matter.", "Pathogenic does not mean you are ill."], "dg-muted");
      rect(s, 18, 402, 324, 45, "dg-emphasis");
      text(s, 180, 430, "Confirm clinically before acting", "dg-center dg-heading");
    });

  define("what-leaves", 490, "Local analysis and explicit network choices",
    "A computer contains genome files, local analysis and results. A boundary separates it from a public-data provider. When the user explicitly chooses a download, an outbound request names public reference data and inbound data returns to the computer; the provider sees normal connection details such as an IP address. That request does not need the person's variant positions or genotypes. A separate dashed box explains that sending genome-derived data to an online service requires a distinct explicit opt-in that identifies the data and recipient. A phone controlling another server does not move computation onto the phone.",
    "Downloading public data contacts its provider, but need not reveal your variants. Sending sample-derived information is a separate, explicit choice.",
    function (s) {
      text(s, 18, 27, "Your computer / chosen server", "dg-heading");
      rect(s, 18, 47, 324, 112);
      ["Genome files", "Local processing", "Results"].forEach(function (label, i) {
        rect(s, 32 + i * 102, 65, 92, 48, "dg-emphasis");
        lines(s, 78 + i * 102, i === 2 ? 94 : 84, label.split(" "), "dg-center");
      });
      text(s, 180, 139, "Your genome stays here by default", "dg-center");
      line(s, 18, 191, 342, 191, "dg-dashed");
      text(s, 180, 183, "Network boundary", "dg-center dg-muted");
      text(s, 180, 214, "Only if you choose a download", "dg-center dg-muted");
      down(s, 40, 229, 267, "Request public data");
      line(s, 320, 267, 320, 229);
      path(s, "M316 234 L320 229 L324 234");
      text(s, 299, 262, "Download", "dg-end");
      rect(s, 18, 284, 324, 70);
      text(s, 180, 309, "Public reference provider", "dg-center dg-heading");
      text(s, 180, 332, "Sees connection details, such as IP", "dg-center dg-muted");
      rect(s, 18, 378, 324, 94, "dg-uncertain");
      lines(s, 32, 402, ["Sharing variants or genome files?", "Separate opt-in required.", "Check what is sent and to whom.", "No silent sample-data upload."]);
    });

  WGSM.render.blocks["v2-diagram"] = function (el, opts) {
    var diagram = diagrams[opts.diagram];
    el.replaceChildren();
    if (!diagram) {
      console.warn("Unknown v2 diagram:", opts.diagram);
      return;
    }
    el.classList.add("v2-diagram");
    el.dataset.v2NoTerms = "";
    var id = "v2-diagram-" + (++serial);
    var svg = node(el, "svg", { viewBox: "0 0 360 " + diagram.height, width: "360", height: diagram.height,
      role: "img", "aria-labelledby": id + "-title", "aria-describedby": id + "-desc", focusable: "false" });
    node(svg, "title", { id: id + "-title" }, diagram.title);
    node(svg, "desc", { id: id + "-desc" }, diagram.description);
    diagram.draw(svg);
    var caption = document.createElement("figcaption");
    caption.textContent = diagram.caption;
    el.appendChild(caption);
  };
})();
