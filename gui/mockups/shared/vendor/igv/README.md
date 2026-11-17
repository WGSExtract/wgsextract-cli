# igv.js 3.8.10

`igv.min.js` is `dist/igv.min.js` from the npm package `igv@3.8.10`
(https://github.com/igvteam/igv.js, MIT, see LICENSE), with its trailing
source-map comment removed. Upstream sha256:
`87b991ae2a720978172e269dd5127c495e56df5af31144dff19c83124e2f5744`.

Only the v2 genome viewer page (`versions/v2/viewer.html`) loads it. It is
configured with a local reference and local tracks only: no default genome
list and no BLAT (its menu items are removed), so it makes no network
requests.
