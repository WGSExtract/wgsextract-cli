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
    "A typical cell holds a nucleus and mitochondria. An XY nucleus has 22 pairs of autosomes plus X and Y, drawn as 23 pairs of rods; XX is also common, and other chromosome sets exist. Mitochondria carry their own circular DNA. Each chromosome is a long DNA double helix with A paired to T and C paired to G. One nuclear chromosome set has roughly 3.1 billion base pairs, and most cells carry two sets.",
    "The 3.1 billion figure describes roughly one set of nuclear chromosomes. Mitochondria carry a separate, much smaller circular genome.",
    function (s) {
      text(s, 18, 27, "Inside a typical human cell", "dg-heading");
      node(s, "ellipse", { cx: 98, cy: 103, rx: 80, ry: 56, class: "dg-panel" });
      node(s, "ellipse", { cx: 72, cy: 96, rx: 35, ry: 29, class: "dg-emphasis" });
      text(s, 72, 101, "Nucleus", "dg-center");
      node(s, "ellipse", { cx: 139, cy: 118, rx: 25, ry: 15, class: "dg-organelle" });
      node(s, "ellipse", { cx: 139, cy: 118, rx: 9, ry: 6, class: "dg-ring" });
      line(s, 164, 112, 190, 94);
      lines(s, 198, 83, ["Mitochondrion", "with circular", "mtDNA inside"]);
      down(s, 72, 126, 186);
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
    "Gordon McDonald, Reginald’s father, passes a Y chromosome along the direct paternal line (cyan), while Helen McDonald, Reginald’s mother, passes mitochondrial DNA along the direct maternal line (magenta). Mothers usually pass mitochondrial DNA to all children, and fathers usually pass their Y chromosome to sons. Below, two autosomal copies show Reginald receiving one copy from each parent; each copy mixes segments from earlier ancestors, so the two direct lines trace only a small fraction of his ancestry.",
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

  define("read-pileup", 510, "Depth varies from position to position",
    "Eight short reads align beneath a twelve-base reference sequence. At the shaded sixth base, the reference has A while four reads show A and four show G, which supports a heterozygous A/G call alongside base and mapping quality. Shading also marks the final T and A bases, where only a single read covers the sequence. Arrows link both shaded columns to their depth and genotype notes. The diagram shows eight reads for clarity; even a 30× average across a genome still leaves thin spots.",
    "Half the reads here support G where the reference has A. Real calls also depend on base quality, mapping quality and other evidence.",
    function (s, wideLayout) {
      var first = wideLayout ? 64 : 28;
      var step = wideLayout ? 52 : 26;
      var het = first + 5 * step;
      var edge = first + 10.5 * step;
      text(s, 18, 27, "A small window into aligned reads", "dg-heading");
      text(s, 18, 52, "8 reads shown · an illustrative window", "dg-muted");
      rect(s, het - 12, 75, 24, 278, "dg-highlight");
      rect(s, first + 10 * step - 12, 75, step + 24, 278, "dg-highlight");
      text(s, 18, 74, "Reference", "dg-muted");
      var reference = "ACTGCAATCGTA";
      function bases(y, start, end, alternate) {
        for (var i = start; i < end; i++) {
          text(s, first + i * step, y, alternate && i === 5 ? "G" : reference[i],
            "dg-center dg-base" + (alternate && i === 5 ? " dg-alt" : ""));
        }
      }
      bases(101, 0, 12, false);
      text(s, 18, 131, "Reads", "dg-muted");
      [[0, 10], [1, 12], [0, 8], [2, 9], [1, 10], [3, 8], [2, 10], [0, 9]].forEach(function (range, i) {
        rect(s, first - 11 + range[0] * step, 142 + i * 26, (range[1] - range[0]) * step - 3, 23, "dg-read");
        bases(159 + i * 26, range[0], range[1], i % 2 === 0);
      });
      down(s, edge, 358, 374);
      down(s, het, 358, wideLayout ? 374 : 434);
      lines(s, wideLayout ? edge : 340, 395, ["Only 1 read covers here", "→ low depth"], wideLayout ? "dg-center" : "dg-end");
      lines(s, wideLayout ? het : 180, wideLayout ? 395 : 455,
        ["About half the reads say G", "→ evidence for heterozygous A/G"], "dg-center dg-heading");
      text(s, 18, wideLayout ? 447 : 500, "30× mean depth can still contain gaps.", "dg-muted");
    });

  define("short-vs-long-reads", 405, "Long reads can bridge repeats",
    "Paired-end sequencing reads 150 bases from each end of a DNA fragment (R1 and R2) and leaves the middle unread. Below, two identical repeat blocks sit between unique flanking DNA. A short read inside a repeat matches both copies equally, whereas a long read reaches into the unique DNA on both sides so the aligner can place it. Lengths are schematic: PacBio HiFi reads are typically 10–25 kb, and Oxford Nanopore reads can run for tens of kilobases or more.",
    "Paired-end reads leave the middle of each fragment unread. Long reads reach unique DNA on both sides of a repeat so it can be placed unambiguously.",
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
    "Two reference tracks show the same example gene at position 1000 in GRCh37 and 1200 in GRCh38; added or corrected sequence shifts positions, and these numbers are invented examples, not a fixed offset. Below, a second pair of tracks shows a gap in an older assembly that T2T-CHM13 fills with resolved sequence. Variant calls and annotation files must use the same reference build, and CHM13 itself carries no Y chromosome.",
    "Every coordinate belongs to a specific reference build. Moving positions between builds requires mapping the surrounding sequence, because offsets vary along each chromosome.",
    function (s) {
      text(s, 18, 27, "Same gene, different map address", "dg-heading");
      text(s, 18, 54, "Example positions, not a real gene", "dg-muted");
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
      lines(s, 18, 394, ["Match the build of calls and annotations.", "v2.0 adds a Y from another donor."], "dg-muted");
    });

  define("file-journey", 620, "From reads to an interpreted report",
    "Compressed FASTQ files (~30–100 GB for a 30× short-read human genome) store read sequences and base qualities before alignment. Alignment writes a BAM (~60–100 GB) or CRAM (tens of GB) that keeps the reads and adds reference positions; decoding a CRAM later requires the exact reference it was built against. Variant calling writes a compressed VCF (hundreds of MB) of called genotypes and quality scores without the underlying reads, and annotation summarises selected findings in a report. File sizes vary with depth and compression.",
    "Original reads hold the full evidence if you reanalyse later; keep them when storage allows.",
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
    "In a VCF genotype, 0 means REF and 1 means the first ALT. The sequence alignments show how each small change compares with the reference.",
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
      lines(s, 18, 211, ["0/1: heterozygous (one of each allele).", "./. means no complete call, not A/A."], "dg-muted");
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
    "An absent VCF row has several possible explanations. Check suitable read evidence or assessed gVCF blocks before calling it reference.",
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
    "Classification describes a variant in a clinical context. A qualified clinician can assess the evidence alongside your history before decisions about care.",
    function (s) {
      text(s, 18, 27, "ACMG / AMP classification", "dg-heading");
      text(s, 18, 51, "Evidence categories, not a risk score", "dg-muted");
      var labels = ["Benign", "Likely benign", "VUS · uncertain significance", "Likely pathogenic", "Pathogenic"];
      labels.forEach(function (label, i) {
        var y = 72 + i * 53;
        rect(s, 18, y, 324, 42, "dg-ramp dg-ramp-" + i + (i === 2 ? " dg-vus" : ""));
        text(s, 32, y + 27, label, "dg-heading");
      });
      lines(s, 18, 365, ["Condition and inheritance matter.", "Pathogenic does not mean you are ill."], "dg-muted");
      line(s, 18, 403, 342, 403);
      text(s, 18, 432, "Confirm clinically before acting", "dg-heading");
    });

  define("what-leaves", 490, "Local analysis and explicit network choices",
    "Genome files, processing and results stay on your computer or chosen server behind a network boundary. When you start a reference download, the computer requests public files and the provider sees standard connection details such as your IP address, never your genotypes or variant positions. A dashed box notes that sending any sample-derived data to an external service requires a separate opt-in naming what is sent and where. Controlling a server from a phone leaves the files and computation on that server.",
    "Public-data downloads tell a provider which reference you requested. Sharing genome-derived information is a separate, explicit choice.",
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
      down(s, 40, 229, 267);
      line(s, 320, 267, 320, 229);
      path(s, "M316 234 L320 229 L324 234");
      text(s, 52, 253, "Request public data", "dg-muted");
      text(s, 308, 253, "Data back", "dg-end dg-muted");
      rect(s, 18, 284, 324, 70);
      text(s, 180, 309, "Public reference provider", "dg-center dg-heading");
      text(s, 180, 332, "Sees connection details, such as IP", "dg-center dg-muted");
      rect(s, 18, 378, 324, 94, "dg-uncertain");
      lines(s, 32, 402, ["Sharing variants or genome files?", "Separate opt-in required.", "Check what is sent and to whom.", "No silent sample-data upload."]);
    });

  function across(s, x1, x2, y, cls) {
    var tail = x2 + (x1 < x2 ? -6 : 6);
    line(s, x1, y, x2, y, cls);
    path(s, "M" + tail + " " + (y - 4) + " L" + x2 + " " + y + " L" + tail + " " + (y + 4), cls);
  }
  function wide(name, height, draw) {
    diagrams[name].wide = { height: height, draw: draw };
  }

  wide("read-pileup", 460, function (s) { diagrams["read-pileup"].draw(s, true); });

  wide("file-journey", 340, function (s) {
    text(s, 18, 30, "30× short-read human genome", "dg-heading");
    var stages = [
      { title: "FASTQ", size: ["~30–100 GB", "compressed"], detail: ["Read sequences", "+ base qualities", "No placements"] },
      { title: "BAM / CRAM", size: ["BAM ~60–100 GB", "CRAM tens of GB"], detail: ["Reads + qualities", "+ placements", "CRAM needs the", "exact reference"] },
      { title: "VCF", size: ["Hundreds of MB", "compressed"], detail: ["Called genotypes", "+ quality", "Reads stay in", "your input files"] },
      { title: "Report", size: ["Selected findings", "+ interpretation"], detail: ["Context and limits", "Keep inputs and", "tool settings for", "later reanalysis"] },
    ];
    stages.forEach(function (stage, i) {
      var x = 18 + i * 178;
      rect(s, x, 76, 150, 43, "dg-emphasis");
      text(s, x + 75, 103, stage.title, "dg-center dg-heading");
      lines(s, x, 147, stage.size);
      line(s, x, 184, x + 150, 184);
      lines(s, x, 212, stage.detail, "dg-muted");
      if (i < 3) {
        across(s, x + 154, x + 174, 98);
        text(s, x + 164, 64, ["Align", "Call", "Annotate"][i], "dg-center dg-muted");
      }
    });
    text(s, 18, 311, "Keep read evidence: some pipelines reduce quality detail.", "dg-muted");
    text(s, 18, 332, "Use the same reference build throughout.", "dg-muted");
  });

  wide("what-leaves", 370, function (s) {
    text(s, 18, 30, "Your computer / chosen server", "dg-heading");
    text(s, 592, 30, "Public-data provider", "dg-center dg-heading");
    rect(s, 18, 64, 220, 159);
    lines(s, 36, 98, ["Genome files", "Local processing", "Results"]);
    text(s, 36, 191, "Stay here by default", "dg-heading");
    rect(s, 482, 64, 220, 159);
    lines(s, 500, 98, ["Public references", "and databases"]);
    lines(s, 500, 172, ["Sees your IP and", "connection details"], "dg-muted");
    text(s, 360, 58, "Network", "dg-center dg-muted");
    text(s, 360, 104, "Request public data", "dg-center");
    across(s, 252, 468, 122);
    across(s, 468, 252, 178);
    text(s, 360, 207, "Reference data back", "dg-center");
    text(s, 360, 257, "Only when you choose a download", "dg-center dg-muted");
    rect(s, 18, 286, 684, 68, "dg-uncertain");
    lines(s, 36, 312, ["Sharing your variants or files takes a separate opt-in.", "Check what is sent and to whom before you agree."]);
  });

  wide("inheritance-lines", 352, function (s) {
    text(s, 18, 30, "The McDonald family", "dg-heading");
    [["Gordon", "Father"], ["Reginald", "Their son"], ["Helen", "Mother"]].forEach(function (person, i) {
      var x = 18 + i * 257;
      rect(s, x, 57, 170, 66);
      lines(s, x + 85, 84, person, "dg-center dg-heading");
    });
    across(s, 199, 264, 103, "dg-paternal-line");
    across(s, 521, 456, 103, "dg-maternal-line");
    text(s, 232, 82, "Y", "dg-center dg-paternal");
    text(s, 489, 82, "mtDNA", "dg-center dg-maternal");
    text(s, 18, 151, "Y: father to son.  mtDNA: mother to all children.", "dg-muted");
    text(s, 18, 191, "Autosomes from both parents", "dg-heading");
    text(s, 18, 224, "From Gordon");
    text(s, 360, 224, "Reginald’s two copies", "dg-center");
    text(s, 702, 260, "From Helen", "dg-end");
    for (var i = 0; i < 5; i++) {
      var father = i % 2 ? "dg-series-b" : "dg-series-a";
      var mother = i % 2 ? "dg-series-c" : "dg-series-d";
      rect(s, 18 + i * 24, 238, 22, 18, father);
      rect(s, 291 + i * 28, 238, 26, 18, father);
      rect(s, 584 + i * 24, 274, 22, 18, mother);
      rect(s, 291 + i * 28, 274, 26, 18, mother);
    }
    across(s, 148, 278, 247);
    across(s, 573, 444, 283);
    text(s, 18, 332, "Each transmitted copy mixes earlier ancestors; the coloured blocks are illustrative.", "dg-muted");
  });

  wide("reference-builds", 330, function (s) {
    text(s, 18, 30, "One gene, two map addresses", "dg-heading");
    text(s, 386, 30, "T2T fills a gap", "dg-heading");
    ["GRCh37", "GRCh38"].forEach(function (build, i) {
      var y = 73 + i * 101;
      var x = 119 + i * 49;
      text(s, 18, y, build, "dg-heading");
      line(s, 18, y + 32, x, y + 32);
      line(s, x + 72, y + 32, 338, y + 32);
      rect(s, x, y + 19, 72, 26, "dg-gene");
      text(s, x + 36, y + 38, "Gene", "dg-center");
      text(s, x + 36, y + 61, i ? "position 1,200" : "position 1,000", "dg-center");
      text(s, 386, y, i ? "T2T-CHM13" : "Older assembly", "dg-heading");
      line(s, 386, y + 32, 468, y + 32, "dg-helix");
      line(s, 554, y + 32, 702, y + 32, "dg-helix");
      if (i) rect(s, 468, y + 24, 86, 16, "dg-series-c");
      else {
        line(s, 468, y + 32, 554, y + 32, "dg-dashed");
        text(s, 511, y + 61, "gap", "dg-center dg-muted");
      }
    });
    lines(s, 18, 283, ["Illustrative coordinates.", "Real mappings vary with the sequence."], "dg-muted");
    lines(s, 386, 283, ["Match the build of calls and annotations.", "v2.0 adds a Y from another donor."], "dg-muted");
  });

  wide("evidence-ladder", 290, function (s) {
    text(s, 18, 30, "ACMG / AMP classification", "dg-heading");
    var labels = [["Benign"], ["Likely", "benign"], ["VUS", "Uncertain", "significance"], ["Likely", "pathogenic"], ["Pathogenic"]];
    labels.forEach(function (label, i) {
      var x = 18 + i * 139;
      rect(s, x, 65, 128, 110, "dg-ramp dg-ramp-" + i + (i === 2 ? " dg-vus" : ""));
      lines(s, x + 64, label.length === 1 ? 126 : 108, label, "dg-center");
    });
    text(s, 18, 211, "Condition and inheritance matter; these categories describe evidence.", "dg-muted");
    line(s, 18, 237, 702, 237);
    text(s, 18, 268, "Confirm clinically before acting", "dg-heading");
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
    function draw(variant, width, cls) {
      var id = "v2-diagram-" + (++serial);
      var svg = node(el, "svg", { viewBox: "0 0 " + width + " " + variant.height, width: width, height: variant.height,
        class: cls, role: "img", "aria-labelledby": id + "-title", "aria-describedby": id + "-desc", focusable: "false" });
      node(svg, "title", { id: id + "-title" }, diagram.title);
      node(svg, "desc", { id: id + "-desc" }, diagram.description);
      variant.draw(svg);
    }
    draw(diagram, 360, diagram.wide ? "dg-narrow" : "");
    if (diagram.wide) draw(diagram.wide, 720, "dg-wide");
    var caption = document.createElement("figcaption");
    caption.textContent = diagram.caption;
    el.appendChild(caption);
  };
})();
