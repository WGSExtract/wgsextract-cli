# Result documents

A command can describe its run as a JSON result document: the files it wrote, warnings, key metrics, and why it failed. When a bundle says which of its commands write one, the app reads the document instead of the log:

- outputs are listed under the run's output, each with **Open** and **Show in Folder**;
- an analysis action can show its result when the run ends, even with the terminal collapsed: its result card (`resultCard`) over the app, or a report the tool wrote (`openOutput`) in the system's default app;
- warnings and metrics are shown in the same summary, not left in the log;
- a refused option is shown next to the action's button, and a missing tool offers **Open Setup**;
- bundle test plans can check the document instead of the printed text.

```jsonc
// manifest.json
"resultDocument": {
  "format": "wgsextract.result/1",
  "executables": ["{{bundleRoot}}/scripts/run-tool.sh"],
  "arguments": ["--result-file", "{{resultDocumentPath}}"]
}
```

**`executables`** lists `command.executable` values exactly as the page files write them. Every action, row action and data-source row action whose command runs one of them is covered. Setup steps and data sources are not.

**`arguments`** are added after all of the command's other arguments, to ask for the document. They decide where the app reads it:

- **A file**, when an argument contains `{{resultDocumentPath}}`. The app replaces it with the path of a new file in a private temporary folder, reads the document from that file once the command exits, and then deletes the folder. Stdout and stderr stream to the terminal pane as for any command, so human-readable output such as a report table still reaches the user. Prefer this mode.
- **Stdout**, otherwise (for example `["--json"]`). Stderr streams to the terminal pane; stdout is captured and read as the document once the command exits. If stdout does not hold a document, it is shown as ordinary output. The document must fit in the 1 MiB the app keeps of a command's output.

**`format`** is the only format the app reads today: [`wgsextract.result/1`](../../docs/result-manifest.md), the document every `wgsextract` command writes with `--json` or `--result-file PATH`.

The complete canonical result and report records are generated from their Python
writer dataclasses/TypedDicts by `scripts/gen_server_contracts.py` into
`app/shared/generated/result-records.ts` and `report-records.ts`. HTTP/job
envelopes remain in `server-transport.ts`. The result adapter derives shared field
types from the canonical records, while its normalized UI shape and tolerant
parser remain handwritten: they accept omitted compact fields and unknown future
statuses/error kinds, rather than asserting that loaded JSON is a validated
writer record. Nullable/defaulted dataclass fields are required in the canonical
record; optional historical report fields remain optional.

## Running a covered action

- An action that runs as a job on the bundle's [job server](job-server.md) gets its document from the job's `result`, whatever the mode.
- **Open** and **Show in Folder** work only for the files a document listed, and for the document's own saved copy (`manifest`). A relative path is relative to the bundle workspace the action ran in.
- WGS Extract also offers **Show in viewer** for existing BAM/CRAM and bgzip
  VCF/gVCF outputs. Add `metrics.locus: "chr1:200-400"` to an output to open
  that interval; a document-level `metrics.locus` applies when the output has
  none. Use 1-based inclusive coordinates. The viewer ignores invalid loci,
  gene symbols and rsIDs and opens the first chromosome instead. This link
  does not grant filesystem access: the existing My Genomes and reference
  library guards still apply. See [Genome viewer](genome-viewer.md).
- A result card is shown only when a document listed it and its name ends in `.card.html`. The app reads it as text and shows it in a frame sandboxed without scripts, forms, popups or navigation.

## Showing an analysis result

Most actions just complete; their outputs are listed in the run's output. An action whose result the user reads, such as a report or an identification, can show it when the run ends:

```jsonc
// manifest.json
"resultDocument": { ..., "cardArguments": ["--card"] }
// a page's action
{ "id": "info", ..., "resultCard": true }
{ "id": "multiqc", ..., "openOutput": ["multiqc_report"] }
```

- **`resultCard: true`** adds the manifest's `resultDocument.cardArguments` to the command (`wgsextract --card` writes `<name>.card.html`, an output with role `result_card`), or asks the [job server](job-server.md) for the job's card. When the run ends, the card pops up over the app, naming the file it is shown from; **View Card** in the run's output shows any card again.
- **`openOutput`** lists output roles in order of preference. When the run ends, the first output the run wrote with one of them opens in the system's default app, as **Open** does. Use it for a tool's own report, such as an HTML page that needs scripts the card frame doesn't allow.

## The last run

The app keeps a copy of the document of the last covered action run in the app, and of the last one that failed, in the bundle workspace. Commands and conditions can name them:

- **`{{lastResultDocumentPath}}`** — the last run's document.
- **`{{lastFailedResultDocumentPath}}`** — the last failed run's document.

Each is empty until such a run happens, so put it in `optionalArguments`, or test it with `exists` in `visibleWhen`. Data sources and setup never replace them, and neither does an action whose command or conditions name either placeholder, so a bug-report action describes what the user last ran rather than a background check or itself:

```jsonc
"command": {
  "executable": "{{bundleRoot}}/scripts/run-tool.sh",
  "arguments": ["report-issue", "--print"],
  "optionalArguments": [["--run", "{{lastResultDocumentPath}}"]]
}
```

## Failures

WGS Extract's report actions need the installed CLI. They remain disabled until
setup succeeds, with an explanation to copy setup output into a GitHub issue
when setup itself fails. Setup output is not a CLI run document and cannot be
reported with `--last-failed`.

Conditions can use `setupReady`: `"true"` after setup completes successfully
(including warnings), or when a bundle has no setup steps for the current
platform; `"false"` before, during, or after a failed setup. Headless bundle
tests apply the same readiness gate and update it after each setup run.

The document's `error.kind` decides what the app shows next to the action's button until it runs again:

| `error.kind` | Shown |
| --- | --- |
| `usage` | "Check the selected options" with `error.message`. |
| `missing_dependency` | "A required tool is not installed" with `error.message`, and **Open Setup**. |
| `failure`, or none | Nothing extra; the terminal pane shows the failure as for any command. |

Without a document — the command rejected its arguments before writing one, or a wrapper script refused to run — the exit code decides: 2 is a usage error, and 3 or 127 a missing tool. The message is the last line of stderr.

## Test plans

An action step can check the document with `expectedResult`:

```jsonc
{
  "kind": "action",
  "actionID": "bam-to-cram",
  "expectedResult": { "status": "ok", "outputs": ["alignment"] }
}
```

- `status` must equal the document's `status`.
- Each role in `outputs` must be listed, and the file must exist with the size the document recorded.
- `errorKind` must equal `error.kind`. Use it with `expectedExitCodes`, such as `[2]` for a usage error.

The step report keeps the document under `result`. `requiredOutput` and `forbiddenOutput` still match the terminal output, which in stdout mode no longer includes a covered action's stdout.
