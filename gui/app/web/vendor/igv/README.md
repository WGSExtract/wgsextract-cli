# igv.js

Pinned to igv.js **3.8.10**, MIT, © 2014–2025 Broad Institute of MIT and Harvard,
Regents of the University of California. The full notice is in [LICENSE](LICENSE).
This directory is the app's redistributable third-party asset and notice, like
the adjacent `bootstrap-icons` directory.

Source: <https://github.com/igvteam/igv.js>, npm package `igv@3.8.10`.
The generated JavaScript is gitignored rather than exceeding the repository's
500 KB source-file limit. `npm run build` copies it from the pinned npm dependency
into this directory on every build; it is included by the existing desktop vendor
resource mapping. The viewer document and stylesheet are copied into `dist/web`,
also an existing desktop resource. No Tauri shell configuration change is needed.

Regenerate just the library with `npm run vendor-igv` in `gui/app`.
The script copies the upstream build and licence, removes its source-map URL,
and applies the fail-closed `scripts/patch-igv-viewports.mjs` lifecycle repair
for [#1158 (heap retention on repeated locus jumps)](https://github.com/WGSExtract/wgsextract-cli/issues/1158).
Upstream detaches old ruler viewports without unsubscribing their browser and
document listeners. The repair disposes those obsolete ruler sweepers, allowing
their viewport/read/canvas graphs to be collected after pending reads finish.
Review the patch when updating igv; it rejects a changed upstream removal method.
The bundled build includes the CRAM decoder; no CDN, remote worker or WASM
download is needed. It adds approximately 1.5 MB uncompressed; it loads only when
the viewer opens, not on app startup. Builds enforce a 2 MB uncompressed limit.

The host disables default genomes, query-string sessions, online search and
context menus (which include BLAT). The viewer has a same-origin-only connection
policy; WASM compilation is allowed only on its document. Samples and references
are browser `File` objects, never URLs or upload bodies.
