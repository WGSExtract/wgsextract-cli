# Repairing FTDNA VCF files

Older FamilyTreeDNA (FTDNA) Big Y `variants.vcf` files write expressions such
as `DP=1` or `QUAL=12.5` in the FILTER column. VCF allows only IDs there, each
declared by a `##FILTER` header line, so bcftools, VEP and other tools reject or
misread those records. `wgsextract repair ftdna-vcf` rewrites the column into
declared IDs that keep the failure's meaning:

```sh
wgsextract repair ftdna-vcf --input variants.vcf        # writes wgse/variants_repaired.vcf
bcftools view variants.vcf | wgsextract repair ftdna-vcf > fixed.vcf
```

The repair runs offline and needs no reference data. It reads plain, gzipped or
BCF input (BCF needs bcftools) and writes plain VCF.

## FILTER mapping

Each `;`-separated entry of a FILTER value is handled on its own:

| Input entry | Output ID | Header description |
| --- | --- | --- |
| `PASS` or `.` (whole value) | unchanged | unchanged |
| a valid ID declared in the header (`LowQual`, `q10`) | unchanged | the input's own |
| a valid ID the header doesn't declare | unchanged | declared as "meaning unknown" |
| `DP=1` | `DP1` | a single read covers the site |
| `DP=n`, n > 750 | `DPM` | more than 750 reads cover the site |
| `DP=n`, 2 ≤ n ≤ 750 | `DPF` | failed an FTDNA depth filter of undocumented threshold |
| `QUAL=q`, q < 40 | `Q40` | site quality below 40 |
| `QUAL=q`, q ≥ 40 | `QF` | failed an FTDNA quality filter of undocumented threshold |
| `GTL=x` | `GTL` | failed FTDNA's undocumented GTL filter |
| anything else (`Low Coverage`, `DP<5`, empty) | `FTDNA_UNKNOWN` | failed a vendor filter of unknown meaning |

- A failed call never becomes `PASS`: `PASS` next to a failing entry
  (`PASS;DP=1`) is dropped, and the remaining failure is kept. A value of only
  `PASS` entries (`PASS;PASS`) becomes `PASS`, and a `.` entry beside others
  is dropped.
- Several entries stay several IDs (`DP=1;QUAL=19.1` becomes `DP1;Q40`).
- Each replaced expression is kept, percent-encoded, in `INFO/FTDNA_FILTER`
  (`DP%3D1,QUAL%3D19.1`), so the original evidence is still in the file.
- Only IDs the records use are declared, and new declarations go just before
  the `#CHROM` line. An input whose FILTER values are all valid and declared,
  and that has no undeclared `HG`/`ISOGG` keys, is copied unchanged.
- If the input already declares one of these IDs, or `INFO/FTDNA_FILTER`,
  with another description, the repair keeps the input's declaration and uses
  a new name (`DP1_WGSE`) for its own category.
- `INFO/HG` and `INFO/ISOGG` values are copied as they are. If records use them
  without a header declaration, they're declared as `Number=.,Type=String`; the
  repair never adds haplogroup values. Filling them from yBrowse is a separate,
  explicit step (see [Lineage](lineage.md)).

## Warnings and failures

The result manifest (`--json`) counts `records`, `repaired_records` and
`unknown_filter_records`, and carries structured warnings:

| Code | When |
| --- | --- |
| `ftdna_vcf_unknown_filter` | Some entries matched no rule and became `FTDNA_UNKNOWN` |
| `ftdna_vcf_undeclared_filter` | Valid IDs were used without a declaration |
| `ftdna_vcf_id_conflict` | A mapped ID was renamed because the input already used it |

A record before the `#CHROM` line, a missing `#CHROM` line or a record with
fewer than eight columns stops the repair with an error. The repair reads the
whole input before writing, so a failure leaves no partial file and, in pipe
mode, writes nothing to stdout. Empty input gives empty output.

## Differences from WGS Extract v4

WGS Extract v4's standalone `fixFTDNAvcf.py` declared `PASS`, `DP1`, `DPM`,
`Q40` and `GTL` and the `HG`/`ISOGG` INFO keys in every file, then mapped
each entry by substring: `DP=1` to `DP1`, any other `DP=` to `DPM` ("More than
750 reads"), `GTL=` to `GTL` and `QUAL=` to `Q40` ("Quality below 40"). This
command differs in that it:

- checks the value, so `DP=37` and `QUAL=55` don't get a description that is
  untrue for them (`DPF` and `QF` instead);
- keeps already-valid IDs such as `LowQual` and declares undeclared ones,
  where v4 blanked every entry it didn't recognise, which could leave an empty
  FILTER or a bare `;`;
- maps unrecognised expressions to the declared `FTDNA_UNKNOWN` and warns,
  rather than dropping them;
- keeps the original expressions in `INFO/FTDNA_FILTER`;
- declares only the IDs and INFO keys the file uses, and resolves conflicts
  with existing declarations.

The previous version of this command only deleted characters VCF forbids
(`Low Qual;q<10` became `LowQual;q10`), which could leave undeclared IDs or
borrow the meaning of an unrelated declared filter.

A synthetic corpus of these cases (no donor data) is in
`tests/fixtures/ftdna_vcf/legacy_filters.vcf`.
