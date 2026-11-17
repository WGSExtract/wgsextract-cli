/* File housekeeping. Destructive actions are separate, never a side effect
 * of conversion. Paths name files on the connected computer, not uploads. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var T = WGSM.v2tools;
  var f = T.f;
  var FILES = ["reads", "vcf", "kit"];
  var LESSON = { href: "learn-files.html#bam", label: "Read files & their indexes" };

  function action(key, options, def) {
    return f.select(key, "What to do", "", options, def, { ui: true });
  }

  function selected(key) {
    return function (c, base) {
      return [base(c.v[key])];
    };
  }

  function scale(c, minutes) {
    /* Illustrative I/O model at 70 GB and eight cores, not a benchmark. */
    return minutes / 60 * (c.file.size / 70) * 8 / (c.v.threads || c.M.cores);
  }

  T.define({
    slug: "convert-format", label: "Convert format", ops: ["bam/to-cram", "bam/to-bam", "bam/to-ubam", "bam/unalign"], group: "Files",
    keywords: "conversion cram bam ubam unaligned fastq", lesson: LESSON,
    desc: "Keep the same reads in another format: smaller CRAM, widely read BAM, or unaligned reads for a fresh alignment.",
    learn: "Use CRAM to save disk space; decoding it needs the exact reference. BAM is larger but more widely accepted. Neither conversion makes the sequence more accurate.\n\nA uBAM drops alignments but keeps read tags for pipelines such as GATK. Paired FASTQs keep sequences and quality scores, but not all BAM tags. Every choice keeps the original file.",
    accepts: ["reads"], file: "gordon", button: "Convert",
    fields: [
      action("conversion", [["bam/to-cram", "CRAM · smaller"], ["bam/to-bam", "BAM"], ["bam/to-ubam", "Unaligned BAM (uBAM)"], ["bam/unalign", "Paired FASTQ"]], "bam/to-cram"),
      f.param("cram_version", "CRAM version", "3.0 works with more readers; 3.1 may compress better", { when: function (c) { return c.v.conversion === "bam/to-cram"; } }),
    ],
    more: [f.param("ref", "Reference FASTA", "Empty: detect the matching installed reference"), f.threads(), f.sortMemory()],
    extra: function () {
      return '<p class="v2-small">Prefer a guided example? <a class="v2-link" href="convert.html">Convert Gordon’s BAM</a>.</p>';
    },
    requests: selected("conversion"),
    run: function (c) {
      var ratio = c.v.conversion === "bam/to-cram" ? (c.file.tag === "CRAM" ? 1 : 0.38) : c.file.tag === "CRAM" ? 3 : 1.1;
      return { hours: scale(c, 55), memGB: 4, disk: [[c.m.diskName(c.file), c.file.size * ratio]],
        blocked: (c.v.conversion === "bam/to-cram" && c.file.tag === "CRAM") || (c.v.conversion === "bam/to-bam" && c.file.tag === "BAM")
          ? "It is already that format. Choose another, or use Sort & index for a new sorted copy." : null,
        makes: "Writes a new file beside the input. The original stays." };
    },
  });

  T.define({
    slug: "sort-index", label: "Sort & index", ops: ["bam/sort", "bam/unsort", "bam/index", "bam/unindex"], group: "Files", lesson: LESSON,
    desc: "Put reads in chromosome order, build an index for quick region lookups, or undo those housekeeping steps.",
    learn: "Use coordinate sorting before tools that jump to a gene or chromosome. Sorting writes a new sorted file and its index. Index alone adds a lookup file beside an already sorted BAM or CRAM.\n\nMark unsorted writes a copy whose header says unsorted; it does not shuffle the reads. Remove index deletes only the lookup files, not the reads. They can be rebuilt.",
    accepts: ["reads"], file: "gordon", allowUnready: true,
    fields: [
      action("housekeeping", [["bam/sort", "Coordinate-sort & index"], ["bam/index", "Build index in place"], ["bam/unsort", "Copy marked unsorted"], ["bam/unindex", "Remove index files"]], "bam/sort"),
      f.toggle("confirm_remove", "Remove the index", "I understand this deletes the input’s .bai, .crai or .csi files", false, {
        ui: true, when: function (c) { return c.v.housekeeping === "bam/unindex"; },
      }),
    ],
    more: [f.threads(), f.sortMemory(), f.param("ref", "Reference FASTA", "For decoding a CRAM")],
    requests: selected("housekeeping"),
    run: function (c) {
      var op = c.v.housekeeping;
      var copying = op === "bam/sort" || op === "bam/unsort";
      return { hours: scale(c, copying ? 25 : 6), memGB: copying ? 8 : 2,
        disk: copying ? [[c.m.diskName(c.file), c.file.size * 1.5]] : [],
        blocked: op === "bam/unindex" && !c.v.confirm_remove ? "Confirm removing the index first." : null,
        makes: op === "bam/unindex" ? "Deletes the index files in place; reads stay." : copying ? "Writes a new copy beside the input." : "Adds an index beside the input." };
    },
  });

  T.define({
    slug: "merge-reads", label: "Merge read files", ops: ["bam/merge"], group: "Files", lesson: LESSON,
    desc: "Join lanes or sequencing runs from one person into one indexed BAM or CRAM.",
    learn: "Use it when a lab split one sample across lanes or flow cells. All inputs must be coordinate-sorted and aligned to the same reference. Combining unrelated people is not a way to make a deeper genome.\n\nRead groups can preserve which run each read came from. This writes a new merged file and leaves the inputs alone.",
    accepts: ["reads"], file: "gordon", button: "Merge", noInput: true,
    fields: [
      f.param("inputs", "Input paths", "Replace these example lanes: comma-separated paths, at least two files of the same sample", {
        def: "{{bundleWorkspace}}/sample/lane1.bam,{{bundleWorkspace}}/sample/lane2.bam", send: true, width: 420,
      }),
      f.param("cram", "Write CRAM", "Otherwise write BAM; CRAM needs the matching reference"),
      f.param("attach_rg", "Add read groups", "Attach a read group per input, preserving where the reads came from"),
      f.param("sample", "Sample name", "For the read groups when adding them"),
    ],
    more: [f.param("output", "Output file", "Empty: choose a name beside the inputs"), f.param("ref", "Reference FASTA", "Must match every input"), f.threads()],
    requests: function (c, base) {
      return [base("bam/merge", { inputs: String(c.v.inputs || "").split(",").filter(function (p) { return p.trim(); }).map(function (p) { return c.m.realPath(p.trim()); }) })];
    },
    run: function (c) {
      var n = String(c.v.inputs || "").split(",").filter(function (p) { return p.trim(); }).length;
      return { timeText: "depends on input sizes", memGB: 4,
        blocked: n < 2 ? "Choose at least two files from the same sample." : null,
        makes: "Makes one indexed merged file; the input files stay." };
    },
  });

  T.define({
    slug: "repair-ftdna", label: "Repair FamilyTreeDNA files", ops: ["repair/ftdna-bam", "repair/ftdna-vcf"], group: "Files", keywords: "ftdna gatk filter",
    lesson: LESSON,
    desc: "Fix formatting quirks in FamilyTreeDNA files so standard tools can read them.",
    learn: "Use it when GATK rejects spaces in read names, or a VCF reader rejects invalid characters in FILTER labels. This changes those names or labels, not the DNA sequence or genotypes.\n\nA BAM or CRAM becomes a repaired BAM; a VCF becomes a plain repaired VCF. The original file stays.",
    accepts: ["reads", "vcf"], file: "gordon", button: "Repair a copy", allowUnready: true,
    fields: [f.param("output", "Output file", "Empty: use the repaired name beside the input")],
    requests: function (c, base) { return [base(c.file.kind === "vcf" ? "repair/ftdna-vcf" : "repair/ftdna-bam")]; },
    run: function (c) {
      return { hours: scale(c, 20), memGB: 2, disk: [[c.m.diskName(c.file), c.file.size * (c.file.tag === "CRAM" ? 3 : 1.1)]],
        makes: "Writes a repaired copy. Only names and labels change." };
    },
  });

  T.define({
    slug: "sample-facts", label: "Sample facts", ops: ["sample/show", "sample/set", "sample/unset"], group: "Files", keywords: "overrides sex platform workspace",
    lesson: { href: "learn-sequencing.html#quality", label: "What sequencing files can tell you" },
    desc: "View or correct the sex and sequencing platform recorded for a sample. This does not rename the person or change their reads.",
    learn: "Use overrides only when you know a detected fact is wrong. Commands use these values instead of detection, which can affect ploidy and caller choices. Chromosomal sex here is a computational input, not gender.\n\nShow lists the workspace’s overrides, file facts and indexed results. Remove clears selected overrides so detection takes over again.",
    accepts: FILES, file: "reginald", allowUnready: true,
    fields: [
      action("facts_action", [["sample/show", "Show facts"], ["sample/set", "Set overrides"], ["sample/unset", "Remove overrides"]], "sample/show"),
      f.param("sex", "Sex override", "", { unset: "Leave unchanged", when: function (c) { return c.v.facts_action === "sample/set"; } }),
      f.param("platform", "Platform override", "", { unset: "Leave unchanged", when: function (c) { return c.v.facts_action === "sample/set"; } }),
      f.toggle("clear_sex", "Clear sex override", "Return to detection", false, { ui: true, when: function (c) { return c.v.facts_action === "sample/unset"; } }),
      f.toggle("clear_platform", "Clear platform override", "Return to detection", false, { ui: true, when: function (c) { return c.v.facts_action === "sample/unset"; } }),
    ],
    requests: function (c, base) {
      var p = { input: c.m.realPath(c.m.path(c.file)), workspace: c.m.realPath(c.m.outdir(c.file)) };
      if (c.v.facts_action === "sample/set") {
        if (c.v.sex) p.sex = c.v.sex;
        if (c.v.platform) p.platform = c.v.platform;
      } else if (c.v.facts_action === "sample/unset") {
        p.sex = c.v.clear_sex;
        p.platform = c.v.clear_platform;
      }
      return [{ op: c.v.facts_action, params: p }];
    },
    run: function (c) {
      return { timeText: "seconds",
        blocked: c.v.facts_action === "sample/set" && !c.v.sex && !c.v.platform ? "Choose a fact to override." :
          c.v.facts_action === "sample/unset" && !c.v.clear_sex && !c.v.clear_platform ? "Choose an override to remove." : null,
        makes: c.v.facts_action === "sample/show" ? "Shows recorded facts; nothing changes." : "Updates wgsextract.sample.toml. The reads stay unchanged." };
    },
  });

  T.define({
    slug: "sign-verify", label: "Checksums & signatures", ops: ["sign/create", "sign/verify"], group: "Files", keywords: "sha256 sign verify integrity",
    lesson: { href: "learn-privacy.html#sharing", label: "Sharing files safely" },
    desc: "Make a checksum manifest before sharing files, or verify that received files have not changed.",
    learn: "Checksums detect damaged or changed files. An OpenSSH signature can also link the manifest to a trusted signing key. Without a trusted public key, an intact signature is not proof of who sent it.\n\nCreating uses the connected server’s configured signing key, if any; private keys are never sent through this form. Without a configured key it makes checksums only. Verifying with no signature checks only the file hashes.",
    accepts: FILES, file: "reginald", allowUnready: true,
    fields: [
      action("sign_action", [["sign/create", "Create checksums"], ["sign/verify", "Verify manifest"]], "sign/create"),
      f.param("manifest", "Manifest path", "SHA256SUMS on this computer", { def: "{{bundleWorkspace}}/SHA256SUMS", send: true,
        when: function (c) { return c.v.sign_action === "sign/verify"; } }),
      f.param("public_key", "Trusted public key", "Optional: verify who signed it, not just that the signature is intact", {
        when: function (c) { return c.v.sign_action === "sign/verify"; } }),
    ],
    more: [f.threads()],
    requests: function (c, base) {
      return [c.v.sign_action === "sign/create" ? base("sign/create", { paths: [c.m.realPath(c.m.path(c.file))] }) : base("sign/verify")];
    },
    run: function (c) {
      return { hours: scale(c, 5), memGB: 1,
        blocked: c.v.sign_action === "sign/verify" && !c.v.manifest ? "Choose the checksum manifest." : null,
        makes: c.v.sign_action === "sign/create" ? "Makes SHA256SUMS and, if configured, its signature." : "Reports missing or changed files and signature trust." };
    },
  });

  WGSM.v2ops = { action: action, selected: selected, scale: scale };
})();
