/* Current product constraints, shared across the deliberately small shells. */
(function () {
  "use strict";
  var W = window.WGSM, h = W.h;
  var EVIDENCE = "A blank variant-only VCF is unknown, not reference. A gVCF distinguishes callable reference, uncovered, filtered and low-depth/low-GQ evidence. Confidence is high, moderate or low; bcftools depth-only blocks lack GQ and cannot justify high confidence. Ploidy-zero sites are not assessed.";
  function remote() {
    return W.v2remote.render();
  }
  function diagnostics(root) {
    var state = W.params.get("demo");
    if (state !== "pixi" && state !== "nocli") return;
    root.prepend(h("p.v2-notice", { "data-tone": "bad", role: "alert" },
      state === "pixi" ? "The installed Pixi version is unsupported. Update Pixi yourself to 0.82.0 or later, then check again. Setup cannot continue with this version." :
        "The CLI is not installed or could not start. Problem reports are unavailable until CLI setup succeeds. No report was copied."));
    root.querySelectorAll("button").forEach(function (b) {
      if (/Copy report|Continue|Finish setup/.test(b.textContent)) { b.disabled = true; b.removeAttribute("data-toast"); }
    });
    if (state === "pixi") root.querySelectorAll('[href="add.html"]').forEach(function (link) {
      link.removeAttribute("href");
      link.setAttribute("aria-disabled", "true");
    });
  }
  function start() {
    var root = W.qs(".v2-page");
    if (!root) return;
    var page = document.body.dataset.pageId;
    if (page === "v2-viewer" || page === "v2-settings") root.appendChild(remote());
    if (page === "v2-settings") root.appendChild(h("details.v2-more", h("summary", "Remote HTTP server listening · Coming in the next release"),
      h("div.v2-more__body.v2-section", h("p", "The desktop’s local server stays on loopback. A separately configured HTTP/JSON server can opt in to another numeric listen interface."),
        h("label.v2-field", h("span.v2-field__label", "Listen interface (server.listen)"), h("input.v2-input", { value: "127.0.0.1", disabled: true })),
        h("p.v2-small", "Non-loopback listening requires an advertised public_url, exact allowed_hosts and allowed_origins, plus validated TLS or a trusted encrypted tunnel. The built-in listener does not configure certificates. Viewer endpoints and allowed roots are default-off."))));
    if (page === "v2-settings" || page === "v2-setup" || page === "v2-install") diagnostics(root);
    if (/variant-table|health|ancestry|learn-variants/.test(page)) root.appendChild(h("section.v2-section", h("h2", "Reference evidence and confidence"), h("p.v2-small.v2-muted", EVIDENCE),
      h("details.v2-more", h("summary", "Illustrative evidence rows"),
        h("div.v2-more__body", h("table.v2-table",
          h("thead", h("tr", ["Evidence", "Confidence", "Reason"].map(function (label) { return h("th", label); }))),
          h("tbody", [
            ["Callable reference · 0/0", "High", "Depth 31, GQ 35; supported gVCF block"],
            ["Callable reference · 0/0", "Moderate", "Depth 18, GQ 24; below high thresholds"],
            ["bcftools reference block", "Moderate", "Depth 31; depth-only evidence, no GQ"],
            ["Uncovered / filtered block", "Low", "No reads or failed call filter"],
            ["Absent from variant-only VCF", "Unknown", "Not assessed; never infer reference"],
          ].map(function (row) { return h("tr", row.map(function (cell) { return h("td", cell); })); })))))));
    if (page === "v2-references") root.appendChild(h("section.v2-section", h("h2", "Offline viewer data"),
      h("p.v2-small", "Gene spans: downloads/genes_hg19.tsv or genes_hg38.tsv, installed explicitly with wgsextract ref gene-map."),
      h("p.v2-small", "Cytobands: downloads/cytobands_hg19.tsv or cytobands_hg38.tsv. Confirm the download: wgsextract ref cytobands --build hg38 --download."),
      h("p.v2-small", "ACMG library data is installed separately. Per-run gnomAD/SpliceAI requests are a separate opt-in that sends sample-derived variant positions to Google Cloud Storage and Ensembl.")));
    if (page === "v2-install") root.appendChild(h("p.v2-notice", { "data-tone": "info" }, "fastVEP / research ACMG is available on Windows x86-64, Linux and macOS. It requires the installed fastvep tool set and local annotation databases. Clair3 on Apple Silicon: streaming MPS is an explicit GPU opt-in with the supported WGSE build."));
    if (page === "v2-settings") {
      var temporary = root.querySelector('[data-annot-id="temporary-work"]');
      if (temporary && W.v2machine.machine.os === "windows") {
        temporary.after(h("details.v2-more", h("summary", "Windows work folders and Microsoft Defender"),
          h("div.v2-more__body.v2-section",
            h("p", "On Windows 11, if your security policy allows it, use a trusted Dev Drive with Defender performance mode for temporary files and outputs. Create the temporary folder first and keep real-time protection on."),
            h("p.v2-small.v2-muted", "Performance mode defers scans; it does not skip them. On NTFS, leave scanning on by default: short tests found little consistent benefit from exclusions. WGS Extract never changes Defender settings."),
            h("a.v2-link", { href: "https://github.com/WGSExtract/wgsextract-cli/blob/main/docs/windows.md#work-folders-and-microsoft-defender" }, "Read the Windows guide"))));
      }
      var about = root.querySelector('[data-annot-id="about"] p');
      if (about) about.textContent = "WGS Extract " + window.WGSMSchema.version + " · command line " + window.WGSMSchema.version + " · server API v1";
    }
    if (page === "v2-tool-region") {
      var link = h("a.v2-btn", { href: "viewer.html" }, "Show demo extraction result in viewer");
      root.appendChild(h("section.v2-section", h("h2", "Verify the extraction"), link,
        h("p.v2-small.v2-muted", "A result must remain inside My Genomes. A single coordinate interval opens at its locus; BED and multiple selections open at the reference’s first chromosome. Check the exact reference, index, both strands, depth and clipping before interpreting a call.")));
      function locus() {
        var input = root.querySelector('[data-v2-key="region"]');
        var value = input && input.value || "";
        link.href = "viewer.html" + (/^(?:chr)?(?:\d+|X|Y|M|MT):\d+-\d+$/i.test(value) ? "?locus=" + encodeURIComponent(value) : "");
      }
      root.addEventListener("input", locus);
      root.addEventListener("change", locus);
      locus();
    }
  }
  W.v2current = { start: start };
})();
