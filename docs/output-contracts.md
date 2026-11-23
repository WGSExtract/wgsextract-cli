# Output contracts

WGS Extract's structured outputs are contracts with three tiers. The
classification is [`contracts/output-contracts.json`](../contracts/output-contracts.json),
and every change is compared with a **pinned baseline of the previous
release** in [`contracts/baselines/`](../contracts/baselines/), so a field
cannot disappear just because its writer and its schema changed together.

## Tiers

| Tier | Guarantee | Examples |
| --- | --- | --- |
| **stable** | Within a schema major version (`wgsextract.result/1`), a stable field is never removed, never widened to new types or values, never made optional, and keeps its recorded meaning. Only additions are allowed. | The result manifest's envelope: `status`, `exit_code`, `error.kind`, `inputs`/`outputs` file records, `warnings[].code`, `provenance.version`; the output roles `input`, `mt_bam`, `result_card` |
| **experimental** | May change in any release, but each removal or incompatible change since the last release must be announced in the release notes. | Command-specific `details`, `metrics`, the personal report's sections, detected sample facts, HTTP response bodies |
| **internal** | No guarantee. Only the in-repository modules listed as its consumers may read it. | The step journal (`wgsextract.steps/1`) |

A field takes the tier of its nearest classified ancestor, and otherwise its
document's. A stable field needs stable parents: a stable value inside an
experimental object is not reachable stably. Paths name what a reader sees:
`inputs[].role` is the role of every input record, and `metrics.*` is any
value of the `metrics` map. Output roles, the `role` strings in manifests'
`inputs` and `outputs`, are classified under `roles`; a role that is not listed
is experimental. A stable role names its `producers`: a command that records
it and, with `when`, the option that makes it do so (`vcf trio` records
`proband` when given `--proband`). Adding a role, metric, warning code or
`details` key is always compatible.

### Scientific meaning

A stable JSON shape does not make a scientific result stable. Fields that hold
a scientific call (haplogroups, sex, ancestry, findings and their confidence
labels) are marked `scientific`, and the check refuses to make one stable
unless its entry names `validation` evidence: a document or test in this
repository that validates what the value means. Until then such fields stay
experimental, however settled their shape. Stable fields may record their
`meaning` in the registry; the meaning is pinned with the baseline, so
reusing a field for something else is a breaking change even when its type is
unchanged.

## What the check compares

`python scripts/output_contracts.py check` flattens each registered JSON Schema
(the generated files in `src/wgsextract_cli/assets/schemas/`) into paths and
the values a reader can see at each: JSON types, allowed values, format,
pattern, numeric bounds, minimum string length and whether the field is always
present. Every alternative of an `anyOf` counts, so adding a looser one (an
unrestricted object, say) widens the fields under it. It then
compares them with the newest pinned baseline, from the reader's side:

| Finding | Stable | Experimental |
| --- | --- | --- |
| Field, document or role removed | error | notice |
| Document holding any stable field removed | error | — |
| Type, values, format, pattern or bounds widened | error | notice |
| Always-present field made optional | error | notice |
| Recorded meaning changed | error | notice |
| Stable field or role demoted to another tier | error | — |
| Field added, or a value narrowed | compatible | compatible |

Internal documents are not compared. Announce an experimental change in its
[release-note fragment](release-notes/unreleased/README.md),
`docs/release-notes/unreleased/<number>-<slug>.md`. Name the changed output
there (`` `wgsextract.report/1:guide` ``, or `` `role:aadr` `` for a role).
Notes from a release after the baseline also count; an older release's
mention does not announce a new change. The check also rejects registry
entries for paths that no longer exist, a stable role without producers, an
internal document without declared consumers, and an unvalidated scientific
field marked stable.

`python scripts/output_contracts.py instance FILE...` checks real output
documents (a manifest, `--json` output or an API `Result.to_dict()`) against
the baseline: every stable field must be present with an allowed value (type,
values, pattern, bounds, length and `date-time` format), and each stable role's
producers must still record it. The test suite runs it on real outputs of
every stable role's producers: `extract mt-bam` (with `--card`) through the
CLI, its manifest and the Python API, `vcf trio --proband` and
`vcf filter --vcf-input`.

Every check runs offline. Ordinary checks read only files in the checkout and,
when present, local git objects.

## Approved breaking changes

A stable change that would break readers follows this procedure:

1. The repository owner approves it on an issue, which records why readers
   must change.
2. Preferably, the change ships as a new schema major version
   (`wgsextract.result/2`) with its own schema file and registry entry.
   Retiring the old document later is itself a break (`stable_document_removed`,
   with an empty `path`) approved as in step 3.
3. For that, or an exceptional fix within a major version (for example a
   field that never carried a usable value), the PR adds an entry to
   `approved_breaks` in the registry: `document`, `path`, the finding `code`,
   the `approval` (the owner's issue or PR) and the `reason`. The finding is
   then reported as approved. An entry that matches no finding fails the
   check, so the release that pins the next baseline removes the entries it
   absorbed.
4. The release notes describe the change under a **Breaking** heading.

## Pinned baselines

Each baseline records the release tag, its commit, the git blob of every
schema it read, the registry it classified them with, the flattened surface
and a SHA-256 digest of the whole. Baselines are immutable:

- Pinning one is a maintainer release step (see [Releasing](releasing.md)),
  run once the `vX.Y.Z` tag exists locally:

  ```bash
  python scripts/output_contracts.py pin --release vX.Y.Z
  ```

  It reads the tag's own schema and registry files from git, refuses to
  overwrite an existing baseline and does not use the network: fetch the tag
  first if it is missing (`git fetch origin tag vX.Y.Z`). The first baseline,
  v0.8.9, predates the registry, so it recorded the registry introduced with
  it (`tiers_source: bootstrap`). Only the oldest baseline, of a tag without
  a registry, may do that; every later one takes its tiers from its tag.
- `check` verifies each baseline's digest and, when the tag is in the local
  repository, that it regenerates exactly from the tag's files. A shallow
  checkout without the tag reports that it skipped this.
- On pull requests and in the merge queue, the `changes` job runs
  `check --base <base commit>` with the full history: it compares the change
  with the baselines **as they are on the base**, so editing or deleting a
  baseline in the same change is reported (`baseline_modified`,
  `baseline_removed`) and cannot erase the comparison, and a new baseline
  must regenerate from a real release tag (`baseline_unverified`).
