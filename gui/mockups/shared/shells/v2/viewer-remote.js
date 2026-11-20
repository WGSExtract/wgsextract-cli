/* Shipping desktop connection flow, represented with public demo data only.
 * Choosing the simulated private config never reads a token into the browser
 * and none of these controls sends requests to an actual remote server. */
(function () {
  "use strict";
  var W = window.WGSM, h = W.h;
  function render() {
    var configured = false;
    var destination = h("input.v2-input", { readonly: true, placeholder: "No server selected", "data-remote-destination": "" });
    var consent = h("input.v2-check", { type: "checkbox", disabled: true, "data-remote-consent": "" });
    var status = h("p.v2-small", { role: "status", "data-remote-status": "" }, "Choose a private server configuration first.");
    var choose = h("button.v2-btn", { type: "button", "data-remote-choose": "" }, "Choose remote server config.toml");
    var go = h("button.v2-btn", { type: "button", disabled: true, "data-remote-connect": "" }, "Connect and view remote file");
    var forget = h("button.v2-btn", { type: "button", disabled: true, "data-remote-forget": "" }, "Forget server and revoke access");
    var fields = [
      ["Primary file on server", "/server/mygenomes/demo.cram"],
      ["Matching index on server", "/server/mygenomes/demo.cram.crai"],
      ["Exact reference FASTA on server", "/server/references/hs38.fa"],
      ["1-based inclusive interval", "chrM:1-16569"],
    ].map(function (item, i) {
      return { label: item[0], value: item[1], input: h("input.v2-input", { disabled: true, placeholder: item[1], "data-remote-path": String(i) }) };
    });
    function refresh() {
      go.disabled = !configured || !consent.checked || !fields.every(function (f, i) {
        if (i !== 3) return /^(\/|[A-Za-z]:[\\/])/.test(f.input.value);
        var interval = /^[^:]+:(\d+)-(\d+)$/.exec(f.input.value);
        return interval && Number.isSafeInteger(Number(interval[1])) && Number.isSafeInteger(Number(interval[2])) &&
          Number(interval[1]) >= 1 && Number(interval[2]) >= Number(interval[1]);
      });
    }
    choose.addEventListener("click", function () {
      configured = true;
      destination.value = W.params.get("demo") === "remote-http" ? "http://genomes.example/v1" : "https://genomes.example/v1";
      consent.disabled = false;
      consent.checked = false;
      forget.disabled = false;
      fields.forEach(function (f) { f.input.disabled = false; f.input.value = f.value; });
      status.textContent = "Demo configuration selected; no server was contacted. The desktop stores real credentials in Application Support and exposes only the URL and an opaque connection ID.";
      refresh();
    });
    consent.addEventListener("change", refresh);
    fields.forEach(function (f) { f.input.addEventListener("input", refresh); });
    go.addEventListener("click", function () {
      if (go.disabled) return;
      status.textContent = "Demo file grant issued. The public local fixture stands in for remote bytes; no server was contacted. In the desktop, only this action sends selected paths and byte-range requests to " + destination.value + ". Local analyses are unchanged.";
    });
    forget.addEventListener("click", function () {
      configured = false;
      destination.value = "";
      consent.checked = false;
      consent.disabled = true;
      forget.disabled = true;
      fields.forEach(function (f) { f.input.disabled = true; f.input.value = ""; });
      status.textContent = "Server forgotten and demo viewer grants revoked. Local analyses continue.";
      refresh();
    });
    return h("details.v2-more", h("summary", "View files on a remote server · Available on desktop"),
      h("div.v2-more__body.v2-section",
        h("p", "Open one primary BAM/CRAM/VCF with its exact server-side FASTA and index. Reference assembly is not inferred remotely; installed local gene spans and cytobands are not overlaid. Supported server hosts are Linux/macOS; a Windows desktop can view them, but a Windows CLI host returns viewer_unavailable."),
        h("label.v2-field", h("span.v2-field__label", "Private server config.toml"), choose),
        h("p.v2-small", "Contains URL and bearer token, selected by the desktop’s native picker. Credentials stay in Application Support, never in the browser or a URL. Choosing/restoring it makes no connection."),
        h("label.v2-field", h("span.v2-field__label", "Destination"), destination),
        fields.map(function (f) { return h("label.v2-field", h("span.v2-field__label", f.label), f.input); }),
        h("p.v2-small", "FASTA needs its adjacent .fai and, for BGZF, .gzi. Every selected path must be inside the server’s allowed roots. The endpoint is default-off."),
        h("p.v2-notice", { "data-tone": "warn" }, "On Connect, selected paths and genomic byte-range requests are sent to the displayed server; genomic bytes return to this desktop. Offsets reveal which regions you inspect. Use validated HTTPS or a trusted encrypted tunnel. Unencrypted HTTP exposes tokens, paths and genomic data. Redirects are refused."),
        h("label.v2-choice", consent, h("span.v2-choice__title", "I consent to this transfer to the displayed destination")),
        h("div.v2-row.v2-row--wrap", go, forget), status,
        h("p.v2-small.v2-dim", "Preview controls use a simulated configuration and public fixture only. They never read credentials or contact a remote server.")));
  }
  W.v2remote = { render: render };
})();
