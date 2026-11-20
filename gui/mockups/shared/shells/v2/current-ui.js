/* Current product constraints, shared across the deliberately small shells. */
(function () {
  "use strict";
  var W = window.WGSM, h = W.h;
  var EVIDENCE = "A blank variant-only VCF is unknown, not reference. A gVCF distinguishes callable reference, uncovered, filtered and low-depth/low-GQ evidence. Confidence is high, moderate or low; bcftools depth-only blocks lack GQ and cannot justify high confidence. Ploidy-zero sites are not assessed.";
  function remote() {
    return h("details.v2-more", h("summary", "View files on a remote server · Coming"),
      h("div.v2-more__body.v2-section",
        h("p", "Not merged yet. Local analyses and local viewing still use this computer. Remote viewing will open one primary BAM/CRAM/VCF with the exact server-side FASTA and index; local gene spans and cytobands will not be overlaid."),
        h("label.v2-field", h("span.v2-field__label", "Private server config.toml"), h("button.v2-btn", { type: "button", disabled: true }, "Choose remote server config.toml")),
        h("p.v2-small", "Contains URL and bearer token. Kept in Application Support, never in the browser or a URL. Choosing it makes no connection."),
        h("label.v2-field", h("span.v2-field__label", "Destination"), h("input.v2-input", { value: "https://genomes.example/v1", readonly: true })),
        ["Primary file on server", "Matching index on server", "Exact reference FASTA on server", "1-based inclusive interval"].map(function (label) {
          return h("label.v2-field", h("span.v2-field__label", label), h("input.v2-input", { disabled: true, placeholder: label.indexOf("interval") >= 0 ? "chrM:1-16569" : "/server/mygenomes/…" }));
        }),
        h("p.v2-notice", { "data-tone": "warn" }, "On Connect, selected paths and genomic byte-range requests are sent to the displayed server; genomic bytes return to this desktop. Offsets reveal which regions you inspect. Use validated HTTPS or a trusted encrypted tunnel. Unencrypted HTTP exposes tokens, paths and genomic data. Redirects are refused."),
        h("label.v2-choice", h("input.v2-check", { type: "checkbox", disabled: true }), h("span.v2-choice__title", "I consent to this transfer to the displayed destination")),
        h("div.v2-row.v2-row--wrap", h("button.v2-btn", { disabled: true }, "Connect and view remote file"), h("button.v2-btn", { disabled: true }, "Forget server and revoke access"))));
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
    if (page === "v2-settings") root.appendChild(h("details.v2-more", h("summary", "Remote HTTP server settings · Coming"),
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
    if (page === "v2-install") root.appendChild(h("p.v2-notice", { "data-tone": "info" }, "fastVEP / ACMG on Windows x86-64: Coming, pending the platform-support change. Clair3 on Apple Silicon: streaming MPS is an explicit GPU opt-in with the supported WGSE build."));
    if (page === "v2-settings") {
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
