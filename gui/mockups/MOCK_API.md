# The mockups' mock server

The v2 mockup (`versions/v2/`) talks to WGS Extract the way the app will:
through the HTTP API of `wgsextract serve` ([docs/server.md](../../docs/server.md)).
Until the app is built against the real server, a **mock server** answers
instead. Jobs queue, run, finish, fail and resume on a **simulated clock**;
downloads move bytes at a **simulated network speed**; disks fill up as jobs
write. Where the UI needs something the real server doesn't have, the mock
offers a **proposed** route, marked as such here and in the code.

## Two modes, one implementation

| Mode | Where the mock runs | State | Used when |
| --- | --- | --- | --- |
| **browser** (default) | In the page | This browser's `localStorage` | GitHub Pages, opening files from disk, or `?api=browser` |
| **shared** | `mock-server/server.mjs` (Node), over HTTP | One JSON file on that computer, until someone clears it | `?api=<URL>`, the settings control, or found on its own (below) |

Both run the same core files in `shared/shells/v2/`. They touch no DOM,
`window` or `localStorage`: storage (`get(key)`, `set(key, value)`), the
real-time source and the demo data (the profiles and files from
`machine.js`, the people from `state.js`) are passed in. The browser builds
them from `WGSM.v2machine` and `WGSM.v2state`; the Node server loads those
same two files against a stub and keeps its state in a file.

| File | What it holds |
| --- | --- |
| `api-core.js` | Namespace `WGSMMock`, the simulated clock, the router, formatting |
| `api-ops.js` | The operations (`GET /v1/ops`) and each job's plan: steps, time or bytes, outputs, flags |
| `api-seed.js` | Config from plain data, the pretend computer's disks, reference data, the demo's starting jobs |
| `api-engine.js` | The job engine: queue, progress, cancel, rerun, step and byte resume, disk use |
| `api-views.js` | The job view, its log and its result manifest |
| `api-routes.js` | Routes the real server has |
| `api-proposed.js` | PROPOSED routes |
| `api-admin.js` | MOCK-ONLY routes (clock, network, reset, flags, machine) |
| `api-server.js` | Browser: `WGSM.v2api`, the transport and the mode |
| `api-clock.js` | Browser: `WGSM.v2clock` and its settings control |
| `api-bind.js` | Browser: `WGSM.v2bind`, markup hooks that follow a job |
| `api-jobs-cards.js`, `api-jobs.js` | Browser: the Jobs page, render block `v2-jobs` |

### Run the shared server

```sh
node gui/mockups/mock-server/server.mjs --host 0.0.0.0 --port 8767
just mockups-api --host 0.0.0.0     # the same
```

Node 18 or newer, no packages. Options: `--host` (default `127.0.0.1`;
`0.0.0.0` lets other computers in), `--port` (default 8767), `--state FILE`
(default `gui/mockups/out/mock-server/state.json`, ignored by git) and
`--speed N` (simulated seconds per real second). It answers `/v1/*` only,
with CORS open to any origin (including `Authorization`, `Content-Type`
and Chrome's private-network preflight), so pages on GitHub Pages or
`serve.py` can call it. It needs no token: don't expose it beyond people you
trust with the demo.

Point a page at it with `?api=http://HOST:8767`; the choice is remembered
in that browser (`?api=browser` goes back). A page served by `serve.py` on
port 8765 tries `http://<same host>:8767/v1/health` for 0.8 s and uses the
server if it answers `"mock": true`. The settings control does the same.

**Clear the shared state:** "Clear the shared mock state" in the control,
`curl -X POST http://HOST:8767/v1/mock/reset`, or stop the server and
delete the state file. Everything returns to the seed, clock included.

### Swap in the real server

`WGSM.v2api.request(method, path, body)` is the seam: it resolves
`{status, body}` and is all the pages use. In shared mode it is
`fetch(base + path)` with JSON and, when set, `Authorization: Bearer
<token>` (`setMode("shared", url, token)`). Pointed at `wgsextract serve`,
real routes work as they are. Pages must then do without the `"ui"` object,
`?detail=1`, the PROPOSED and MOCK-ONLY routes, and `"_mock"` (the real
server rejects or ignores unknown fields), or the real server grows them.

## Browser API

```js
WGSM.v2api.get("/v1/jobs?detail=1").then(({ status, body }) => …);
WGSM.v2api.post("/v1/ops/realign", { input, ref: "/Users/you/wgs-extract/reference/genomes/hs38.fa.gz", outdir, _mock: {…} });
WGSM.v2api.del("/v1/jobs/" + id);         // cancel
WGSM.v2api.patch("/v1/library/people/helen", { note: "mum" });
WGSM.v2api.on(({ mode, now }) => …);       // about once a second; returns unsubscribe
WGSM.v2api.mode();                         // "browser" | "shared"
WGSM.v2api.setMode("shared", "http://127.0.0.1:8767");  // -> {ok, mock, message}
WGSM.v2api.reset();                        // this browser's, or the shared state
WGSM.v2clock.now(); .speed(); .setSpeed(600); .network(); .setNetwork(1e8);
WGSM.v2clock.format(ms) /* "09:58" */; .when(ms) /* "Today 06:14" */; .dur(ms) /* "1 h 52 min" */
WGSM.v2clock.control();                    // element for the mockup-settings dialog
```

In shared mode `on()` listeners are called after a `GET /v1/clock` each
second. Capability flags the server derives from jobs (below) are copied
into `WGSM.v2state` when they change, so `data-v2-if` hooks follow them.

### Markup hooks (`api-bind.js`)

```html
<a class="v2-progress" href="jobs.html" data-v2-job="realign:gordon">
  <span>Realigning to hs38 · <span data-v2-job-text="eta">1 h 52 min left</span></span>
  <span class="v2-bar" role="progressbar" aria-valuenow="47" aria-valuemin="0"
        aria-valuemax="100" aria-label="Realigning to hs38" data-v2-job-bar><span style="--v: 47%"></span></span>
  <span data-v2-job-when="done">Done: the GRCh38 copy is ready</span>
</a>
```

`data-v2-job` picks the newest job: `op:person` (`realign:gordon`,
`lineage/y-str:reginald`), `id:<job id>`, `label:<label>`, or `running`
(whichever job is running, for the sidebar). Inside it,
`data-v2-job-text` is `full` ("47% · 1 h 52 min left", "Queued · starts in
…", "Done", "Failed after 12 s", "Interrupted at 62%", "Cancelled"),
`eta`, `percent`, `status` or `label`; `data-v2-job-bar` sets `--v` on a
`.v2-bar`'s fill, `aria-valuenow`, and `data-state="stopped"`;
`data-v2-job-when` takes `running queued succeeded failed cancelled
interrupted done stopped active none`. The `data-v2-job` element gets
`data-v2-job-status`. Static text stays as the fallback until the first tick.

The Jobs page is `<div data-render="v2-jobs" data-section="running|queued|needs-you|done" [data-limit="8"]>`.

## The simulated clock and network

- **Clock.** Starts today at 09:56:10, local time. Speed is simulated
  seconds per real second: 0 (paused), 1, **60** (default: a minute per
  second), 600. It persists (per browser, or in the server's state) and
  **only runs while someone is looking**: if nothing asks for the time for
  90 s of real time (every page closed, or no requests to the server), it
  continues from where it stopped instead of jumping ahead.
- **Network.** Downloads move bytes at **1 GB/s** by default (also 10 MB/s,
  100 MB/s, or 0 for unlimited: done at once), measured in **simulated**
  seconds, so the clock's speed and pause apply to downloads too and a
  paused demo stays paused. Analysis jobs run on the clock alone. Changing
  the speed mid-download keeps the bytes already done.
- A download writes `<output>.partial` as it goes, bound to its URL (like
  `download_file`): the partial takes disk space while it grows, is kept when
  the job is cancelled, interrupted or fails, and a rerun with the same URL
  resumes from its last byte (a different URL starts over). On success the
  partial is gone and the file counts instead.

## The `_mock` hint

Any `POST /v1/ops/{op}` (and `POST /v1/estimate`'s `params`) may carry a
mock-only `_mock` object, for the UI's own estimate engine. All optional:

```jsonc
{"input": "/Volumes/Genomes/Gordon/60820188479016.bam", "ref": "/Users/you/wgs-extract/reference/genomes/hs38.fa.gz", "outdir": "/Volumes/Genomes/Gordon/",
 "_mock": {
   "label": "Realign Gordon to hs38", "person": "gordon",
   "hours": 3.55,                      // CPU jobs: simulated duration
   "gb": 17,                           // downloads: size; time follows the network speed
   "steps": ["Extract reads", ["Align", 4, "reads"], {"name": "Sort and index", "w": 1}],
   "outputs": [{"path": "/Volumes/Genomes/Gordon/60820188479016.hs38.cram", "gb": 34, "disk": "Genomes", "kind": "cram", "library": true}],
   "tempGB": 41, "memGB": 18,
   "flags": ["mem2-hs38"],             // set when it succeeds
   "cancelFlags": ["realign-cancelled"],
   "fail": {"at": 0.4, "message": "…"},  // or true: fail 10% in
   "thenRerun": "<job id>",            // rerun that job when this one succeeds
   "metrics": {"haplogroup": "J-ZS241"}
 }}
```

Without it, each operation has defaults (`api-ops.js`): label, steps, time
scaled by the input file's size, outputs beside the input, temporary space.
Download operations: `ref/library`, `ref/index`, `ref/imputation`,
`vep/download`, `ref/clinvar`, `ref/gnomad`, `ref/gnomad-sv`, `ref/revel`,
`ref/cadd`, `ref/alphamissense`, `ref/spliceai`, `ref/test-genome`.

**Flags** the UI uses come from finished jobs: `ref/library {install}` →
`ref-<code>`; `ref/index {ref: <FASTA>, aligner: "bwa-mem2"}` → `mem2-<code>`;
`ref/imputation` → `panel-1kg`; `vep/download` → `vep-cache`;
`bam/index` of a file that needs it → that file's flag (`alex-fixed`);
cancelling a realign → `realign-cancelled`. `POST /v1/mock/flags` overrides.

## Requests are checked as the real server checks them

`POST /v1/ops/{op}` takes the operation's `Params` as JSON, exactly as
`wgsextract serve` does (`src/wgsextract_cli/server/operations.py`), and
`api-validate.js` refuses what the real server refuses, with the same
messages: an unknown operation (404 `unknown_op`), a missing required
parameter, an unknown one, a wrong type, a value outside an enum, or a path
that isn't absolute or is a URL (400 `usage`). The schema it checks against
is `shared/data/api-schema.js`, generated from the server's OpenAPI document,
so the mock knows every real operation (`GET /v1/ops`); `api-ops.js` only
adds how long each runs. `_mock` is the one key the real server wouldn't
accept: the mockups' hint, which the real server never sees.

Downloads and fixes the pages offer are real operations too: `ref/index`
(bwa-mem2's index), `ref/library` (`install` a reference genome),
`ref/imputation`, `vep/download`, `ref/gnomad`, `ref/revel`, `ref/cadd`,
`ref/alphamissense`, `ref/spliceai` and `bam/index`. Polygenic scores and
the personal report fetch their own public files (a scoring file, a gene
list) as part of the run, as the CLI does.

## Routes

Kinds: **real** — in docs/server.md, same shapes; **proposed** — the UI
needs it, the real server lacks it, mock only for now; **mock** — drives the
simulation, never for the real server. `GET /v1` lists them all.

### Real

| Request | Mock behaviour |
| --- | --- |
| `GET /v1/health` | `{"ok": true, "version": "mock", "mock": true}` |
| `GET /v1` | Index, allowed paths, and (mock) every route with its kind |
| `GET /v1/ops`, `GET /v1/ops/{op}` | Operations; one with a loose parameter schema |
| `POST /v1/ops/{op}?wait=S` | 202 with the job (200 if done by the end of `wait`, real seconds); 404 `unknown_op`, 400 `bad_json` |
| `GET /v1/jobs?status=` | `{"jobs": [{id, op, status, created, finished?}]}`, oldest first |
| `GET /v1/jobs/{id}?wait=S&full=1` | The job view; `full` adds `params` and the manifest's `provenance` |
| `GET /v1/jobs/{id}/events` | SSE `status` and `progress` events (Node server streams them; in the browser, use `on()`) |
| `GET /v1/jobs/{id}/log?tail=N` | `text/plain`, simulated timestamps |
| `DELETE /v1/jobs/{id}` | Cancels at once: 200, `cancelled`, exit 130; 409 `finished` if it had ended |
| `POST /v1/jobs/{id}/rerun` | 202, a new job with the same parameters; resumes (below) |
| `GET /v1/manifest?path=` | The manifest of the job that wrote `path` |
| `GET /v1/variants`, `GET /v1/report` | Empty answers |

A job view:

```jsonc
{"id": "20261116-161500-3fa2c1", "op": "realign", "kind": "job", "status": "running",
 "created": "2026-11-16T16:14:40+00:00", "started": "2026-11-16T16:15:00+00:00",
 "progress": {"stage": "Align", "message": "411,632,097 of 876,231,560 reads", "fraction": 0.4746, "eta_s": 6720},
 "ui": {"label": "Realign Gordon to hs38", "person": "gordon", "person_name": "Gordon McDonald", "mode": "cpu",
        "steps": [{"name": "Extract reads", "state": "done", "fraction": 1, "weight": 2262000}, …],
        "outputs": [{"path": "…/60820188479016.hs38.cram", "gb": 34, "disk": "Genomes", "library": true}],
        "temp_gb": 41, "mem_gb": 18, "command": "wgsextract realign --input …", "eta_s": 6720,
        "finishes_at": "…", "rerun_of": null, "then_rerun": null, "resumed_bytes": 0, "note": null}}
```

Finished jobs add `finished`, `exit_code`, `error` (`{kind, message}`) and
`result`, a minimal `wgsextract.result/1` manifest (outputs with
`size_bytes`, `metrics` from the plan). Downloads' `progress` adds
`bytes_done`, `bytes_total`, `rate_bps` and `eta_s`. Queued jobs' `ui`
adds `waiting: {reason: "one_at_a_time", behind, starts_at, starts_in_s}`
and `est_s`; stopped ones add `resume: {from_step}` or
`{from_bytes, partial}`. One job runs at a time (`server_max_jobs` = 1);
queries (`info`, `sample/show`, `ref/status`) start at once. A rerun of a
cancelled, failed or interrupted analysis skips the steps it finished (as
[step resume](../../docs/step-resume.md) does); a download resumes from its
`.partial`.

PROPOSED additions to real routes: `GET /v1/jobs?detail=1` (full views in
one request, for the Jobs page), the job view's `ui` object and the
download fields of `progress`.

### Proposed

| Request | Answer |
| --- | --- |
| `GET /v1/system` | `{os, arch, label, spec, cores, performance_cores, ram_gb, gpu: {kind, memory_gb}, workspace, profile}` |
| `GET /v1/disks` | `{"disks": [{id, name, path, total_gb, free_gb, held_gb, system}]}`, live: outputs, temporary space, growing and kept partials |
| `GET /v1/library` | `{profile, people: [{id, name, note, removed, files: [{id, name, path, tag, build, size_gb, disk, status: ready\|needs_fix\|being_made, job?, fraction?}]}], unassigned: […]}` |
| `PATCH /v1/library/people/{id}` | `{name?, note?, removed?}` → the person |
| `DELETE /v1/library/people/{id}` | Removes them from the library; files stay on disk |
| `POST /v1/library/files/{id}/assign` | `{"person": "gordon"}` or `{"name": "New person"}` → the library |
| `POST /v1/estimate` | `{op, params}` → `{label, mode, duration_s, download_bytes, memory_gb, ram_gb, fits_memory, steps, disk: [{disk, need_gb, temp_gb, free_gb, fits}], starts_at, starts_in_s, finishes_at}` |
| `GET /v1/references` | `{"references": [{id, name, build, kind, gb, installed, flag, indexes?, downloading?, partial?}]}` |

### Mock only

| Request | Does |
| --- | --- |
| `GET /v1/clock` | `{now, now_ms, speed, paused, network_bps}` |
| `POST /v1/clock` | `{speed?, now?, advance_s?}` |
| `GET`/`POST /v1/mock/network` | `{bps}`, bytes per simulated second; 0 unlimited |
| `POST /v1/mock/reset` | Everything back to the seed, clock included |
| `POST /v1/mock/interrupt` | As if the server stopped: queued and running jobs become `interrupted` |
| `GET`/`POST /v1/mock/flags` | `{flags, explicit}`; POST `{set: [], unset: [], clear: []}` |
| `GET`/`POST /v1/mock/machine` | `{profile}`: which `machine.js` computer the shared server pretends to be |
| `GET /v1/mock/state` | The whole stored state |

## The demo's starting jobs

Seeded relative to the clock's start (09:56:10): Gordon's realign to hs38
(started 08:15, 47 %, 1 h 52 min left, 41 GB temporary on Genomes); Y-STR
markers for Reginald, queued behind it; Call SNPs for Alex, failed after
12 s (no index; the fix indexes the BAM and reruns it); the hs37d5 index
download, interrupted at 62 % with its partial kept; and, done, Helen's
DNA-test files (3 days ago) and Reginald's mtDNA, Y haplogroup and ancestry
(06:09, 06:11, 06:14). The disks' free space in `machine.js` is the space at
that moment.
