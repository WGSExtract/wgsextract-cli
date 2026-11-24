# Recorded resource releases, `ref recheck` and `report-delta`

A finding rests on the public data it was annotated with. ClinVar
reclassifies variants every week, so the same calls can give a different
report a month later. WGS Extract records which release of each resource a run
used, says when newer releases exist and shows what changed between two runs.

## Recorded releases

Every [result manifest](result-manifest.md) has a `resources` list: the public
resources the run used, each with:

| Field | Meaning |
| --- | --- |
| `id`, `name`, `role` | `clinvar`, `revel`, `gnomad`, `spliceai`, `alphamissense`, `pharmgkb`, `phylop`, `cadd` (and `cadd-indel`, its indel table), `gerp` (annotation) or `gene-list` (the report's gene list). |
| `build` | `hg19` or `hg38` when the resource states it (ClinVar's `##reference`). |
| `release`, `release_basis` | The exact release used. `file_header`: ClinVar's own `##fileDate` (with `##source=ClinVar`), e.g. `2026-11-15`. `pinned_sha256`: a built-in gene list, named by its SHA-256-pinned id, e.g. `acmg-sf-3.3`. `unrecorded`: the file doesn't say, so `release` is `null`. A release is never guessed from a file name, date or size. |
| `path`, `fingerprint` | The file and its [fingerprint](result-manifest.md#fingerprints). |
| `source_url`, `licence`, `licence_url`, `attribution` | The publisher and its terms from the audited [resource catalog](resource-mirrors.md). Recording a resource's terms doesn't make the data redistributable; see the source's own terms. |

The `vcf` annotators (`vcf clinvar`, `vcf revel`, `vcf gnomad`, ...) record
the resource they read. `report` records its gene list and copies the
resources of the runs its sections show, so a report's manifest keeps the
releases its findings rest on. Manifests written before this have no
`resources` field: their releases are unknown.

## `ref recheck`: are newer releases out?

```sh
wgsextract ref recheck HG002/HG002_personal_report.manifest.json --answers answers.json
wgsextract ref recheck HG002/HG002_personal_report.manifest.json --online --save-answers answers.json
```

It reads the manifests' recorded releases and compares each with the newest
release, which comes from one of two explicit sources:

- **`--answers FILE`** (offline): a JSON file you or an earlier run wrote.
- **`--online`**: asks the resource's public release listing. For ClinVar
  that is NCBI's weekly VCF listing,
  `https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/weekly/` (or
  `vcf_GRCh37/weekly/` for hg19). The request is a plain GET of that URL: it
  sends no sample name, path, position, genotype or file. Only resources with
  a known listing are asked; the others stay `unknown`. `--save-answers FILE`
  keeps what it found, so the same check can be repeated offline.

With neither, the command makes no connection and stops (exit 2), naming the
resources and releases it would check and both ways to continue. It never
downloads data or reruns an analysis. Each resource gets a `status`:

| Status | Meaning |
| --- | --- |
| `up_to_date` | The runs used the newest release. |
| `newer_available` | The answers name a later release. |
| `different_release` | The answers name another release that can't be ordered by date. |
| `unknown` | The run didn't record its release, the answers don't cover the resource, or they name an older release than the run used (out-of-date answers). |
| `lookup_error` | The online lookup failed. A failed lookup is never reported as up to date. |

`installed` says whether the file at the recorded path is still the one the
runs used (`same`, `changed` with its release as `installed_release`,
`missing` or `unknown`; a server job checks only files in its allowed
folders). Copies of the same release at different paths are checked separately.
`used_by` lists the runs, and `next_step` says what to do: download
the newer release yourself (for ClinVar from NCBI, then pass it with
`vcf clinvar --clinvar-file`), then rerun those commands, the analyses before
the report. If the installed file already has that release, rerun without
downloading it again. `wgsextract ref <resource>` installs the release this WGS Extract
pins, often an audited mirror snapshot, which may be older than the newest. A
truncated or failed listing is a `lookup_error`. The result has the warnings
`newer_release_available`, `release_differs` (for `different_release`),
`release_lookup_failed` and
`resources_not_recorded`; its `details` follow `wgsextract.recheck/1`
([schema](../src/wgsextract_cli/assets/schemas/wgsextract.recheck.1.schema.json)),
and `details.answers` records where the answers came from: the file's path,
SHA-256, `retrieved` time and `source`, or the URLs contacted. The answers
file is also an input of the result, with its fingerprint.

### Answers file

```json
{
  "schema": "wgsextract.release_answers/1",
  "retrieved": "2026-11-22T08:00:00+00:00",
  "source": "NCBI ClinVar weekly listing",
  "answers": [
    {"id": "clinvar", "build": "hg38", "latest": "2026-11-22"},
    {"id": "clinvar", "build": "hg19", "latest": null, "error": "timed out"}
  ]
}
```

Omit `build` or use `null` for an answer that applies to any build without
a build-specific answer. Omit `latest` or use `null` when the latest release
is unknown. A file of another schema is refused with error code
`unsupported_schema`.

## `report-delta`: what changed between two runs

```sh
wgsextract report-delta --before old/HG002/HG002_personal_report.manifest.json \
                        --after HG002/HG002_personal_report.manifest.json
```

It compares two result manifests of the same command and sample (a report's
sample, else the sample the run recorded selecting, else the input's file
name), by meaning
rather than bytes. Timestamps, ordering, file paths and regenerated documents
are ignored, and no network request is made. Two report manifests are
compared section by section; any other pair of manifests (two `vcf clinvar`
runs, say) is compared by status, warnings, metrics, outputs and provenance.

Report findings are matched by section and variant (`chrom:pos:ref:alt`), or
by section and label for structural variants (their type, span and size) and
non-variant findings. Each changed finding lists its `changes`:

| Change | Meaning |
| --- | --- |
| `added`, `removed` | The finding is in only one report. |
| `reclassified` | Same variant and genotype, another classification (e.g. Pathogenic to Likely pathogenic). |
| `review_changed` | ClinVar's review status, stars or conflicting-submitter evidence changed. |
| `genotype_changed` | The sample's genotype or zygosity at the variant changed, or which calls it is phased with, on the same or the other copy (`phased_with`; phase-set ids themselves may be renumbered). |
| `result_changed` | A non-variant result changed (a diplotype, a haplogroup; PharmCAT's candidate diplotypes compare as a set), or a variant's evidence did (its ClinVar condition, ACMG criteria, transcript or consequence, an SV's population frequency). |
| `confidence_changed` | The call's confidence label or reasons changed: its callability. |
| `ambiguity_changed` | A PharmCAT gene became resolved, ambiguous or no-call. |

Unchanged findings are counted per section. A section that failed or did not
run in either report is not compared (`compared: false`, with a note): its
findings are not reported as removed. Within a section that has several runs
(pharmacogenomics, lineage), a run that failed or did not run in either report
is listed in `not_compared`, with a note, and its own findings are not
reported as added or removed. `changes` lists the status change of each such
run (area `status`, key `section command`) and each change in a gene's
interpretation (area `gene`: a ClinVar gene's status and phase, such as
`biallelic` to `possible_biallelic`). `releases` lists each recorded resource
as `same`, `changed`, `content_changed` (same release name, different file),
`added`, `removed` or `unknown` (release unrecorded and the file differs). A
changed finding names the releases its section rests on that changed
(`release_changes`), and `summary.resource_only` is true when releases changed
but no finding, section, run status, gene interpretation, metric or output
did.

Each changed finding keeps its confidence label and disclaimer as the report
gave them, and the document carries the report's disclaimer and its
`limitations`: release changes are an association, not a proven cause; a
ClinVar finding added after an update may be a reclassified call or a new
call, because a report lists only P/LP calls; sections not compared are not
removals. These are technical results, not diagnosis.

The command refuses runs it can't compare, with error codes
`delta_command_mismatch` (different commands), `delta_sample_mismatch`
(different samples), `delta_build_mismatch` (hg19 against hg38, or a run whose
sections, reference or resources name different builds: positions differ) and `unsupported_schema` (not a `wgsextract.result/1` manifest, or a
report of another schema). Its `details` follow `wgsextract.delta/1`
([schema](../src/wgsextract_cli/assets/schemas/wgsextract.delta.1.schema.json)).

Both commands are also typed API operations (`api.ref.recheck`,
`api.report.delta`) and server operations; a read-only server session can't
set `online`.
