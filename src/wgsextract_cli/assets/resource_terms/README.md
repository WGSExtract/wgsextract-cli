# Audited source-terms snapshots

[`rights.json`](rights.json) is a separate, record-only inventory. Its
`catalog` and `entries` identify the existing catalog keys, release tags or
asset families. The original catalogs retain exact URLs, versions and hashes
where known. For an unspecified grant or version, `license_url` identifies
the source checked and `reason` explains what it does not establish. These
records are not publishing inputs and do not authorize reuse.

These are retained source bytes for the resource redistribution audit, not a
new grant or a replacement for the source terms. `sources.json` records each
source/fetch URL, UTC retrieval date, local file and SHA-256, including aliases
for the original landing pages. AlphaMissense's later relicensing and stale
data-header conflict remain explained in `resource_mirrors.json`.

The publisher prefers these verified bytes over live terms pages, including
the Creative Commons legalcode texts. This keeps bot blocking or a terms-site
outage from interrupting large downloads. New release records retain the
snapshot provenance. Existing completed resource records are never rewritten.

Refresh snapshots only as part of an explicit rights review. Preserve their
exact bytes: the source-licence assets already uploaded to a release are
immutable, even when only website formatting changes.

The directory is `-text` in `.gitattributes`: Windows `core.autocrlf` must not
change the source bytes. Do not normalize these files or weaken checksum
verification to accommodate a converted checkout.
