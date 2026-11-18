"""Extract marker-only templates from vendor exports.

Source comments are never published: headers use generic vendor boilerplate.
Bodies contain only marker ID, chromosome and position, including no-call rows.

Usage: python scripts/microarray_template_from_raw.py RAW_FILE [RAW_FILE ...] NAME

Several exports of one variant (say Ancestry v2a and v2b) are merged into one
template: the first export's rows in its order, each later export's extra rows
after the row preceding them in that export. Use --delta-base auto to store the
body as a delta against the shipped template it differs least from (or BASE),
--output-dir for an alternate template root, or --overwrite to rebuild.
"""

import argparse
import bisect
import csv
import gzip
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

from wgsextract_cli.core.microarray.templates import (
    DELTA_EXTENSIONS,
    DELTA_SUFFIX,
    get_template_format,
    template_rows,
)

TEMPLATES = (
    Path(__file__).resolve().parents[1]
    / "src/wgsextract_cli/assets/microarray/raw_file_templates"
)
# Vendor IDs are rsIDs, internal IDs (i, VG, GSA-, kgp, chr1-..., ...), SNP
# names in Y files, or tellmeGen's ".": all but "." hold a digit, which keeps a
# name or a kit label in the ID column from passing as a marker.
_MARKER_ID = re.compile(r"[^\s\",]*[0-9][^\s\",]*|\.")
_CHROMS = {*map(str, range(27)), "X", "Y", "XY", "M", "MT"}
# Exports whose column labels are a comment line, or that have none.
_NO_COLUMN_HEADER = ("23andMe_", "Nucleus_", "LDNA_", "TellMeGen_", "SelfDecode_")
# A base row this far ahead is a reordering, written as inserts, not a skip.
_MAX_SKIP = 50_000

Row = tuple[str, str, str]


def _header(name: str, eol: str) -> str:
    if name.startswith("MyHeritage_"):
        lines = [
            "##fileformat=MyHeritage",
            "##format=MHv1.0",
            *(["##chip=GSA"] if "_GSA_" in name else []),
            "##reference=build37",
            "# MyHeritage DNA raw data.",
            "RSID,CHROMOSOME,POSITION,RESULT",
        ]
    elif name.startswith("Ancestry_"):
        lines = [
            "#AncestryDNA raw data download",
            "#Data was collected using AncestryDNA array version: V2.0"
            if "_V2" in name
            else "#AncestryDNA array data",
            "#Human reference assembly build 37; forward (+) strand.",
            "rsid\tchromosome\tposition\tallele1\tallele2",
        ]
    elif name.startswith(("FTDNA_", "Genera_", "meuDNA_")):
        lines = ["RSID,CHROMOSOME,POSITION,RESULT"]
    elif name.startswith("23andMe_"):
        lines = [
            "# This is a generic 23andMe-format raw data export.",
            "# Human reference assembly build 37; plus strand.",
            "# rsid\tchromosome\tposition\tgenotype",
        ]
    elif name.startswith("SelfDecode_"):
        lines = [
            "# Source: SelfDecode",
            "# Assembly: GRCh37",
            "# rsid\tchromosome\tposition\tgenotype",
        ]
    elif name.startswith("MTHFR_"):
        lines = ["rsid\tChromosome\tPosition\tgenotype"]
    elif name.startswith(("LDNA_", "TellMeGen_")):
        lines = ["# rsid\tchromosome\tposition\tgenotype"]
    else:
        return ""
    return eol.join(lines) + eol


def read_export(
    raw: Path, name: str, expected_build: str, *, check_header_build: bool = True
) -> tuple[list[Row], str]:
    """An export's (ID, chromosome, position) rows and its line ending."""
    opener = gzip.open if raw.suffix == ".gz" else open
    body: list[Row] = []
    eol = "\n"
    expected_columns = 5 if name.startswith("Ancestry_") else 4
    schema_seen = name.startswith(_NO_COLUMN_HEADER)
    with opener(raw, "rt", encoding="utf-8-sig", newline="") as stream:
        for number, line in enumerate(stream, 1):
            if number == 1 and line.endswith("\r\n"):
                eol = "\r\n"
            if not line.strip():
                continue
            if line.startswith("#"):
                builds = re.findall(r"(?:build|GRCh|NCBI)\s*(3[678])\b", line, re.I)
                if check_header_build and any(b != expected_build for b in builds):
                    raise ValueError(f"Unexpected reference build at line {number}")
                continue
            fields = next(
                csv.reader([line], delimiter="\t" if "\t" in line else ",", strict=True)
            )
            fields = [field.strip() for field in fields]
            if [field.lower() for field in fields[:3]] == [
                "rsid",
                "chromosome",
                "position",
            ]:
                if body:
                    raise ValueError(f"Unexpected column header at line {number}")
                if len(fields) != expected_columns:
                    raise ValueError(f"Unexpected column count at line {number}")
                schema_seen = True
                continue
            if not schema_seen and not body and len(fields) == 3:
                # A marker list without genotypes or column labels.
                schema_seen, expected_columns = True, 3
            if (
                not schema_seen
                or len(fields) != expected_columns
                or not _MARKER_ID.fullmatch(fields[0])
                or fields[1] not in _CHROMS
                or not fields[2].isdigit()
            ):
                raise ValueError(f"Invalid marker schema at line {number}")
            body.append((fields[0], fields[1], fields[2]))
    if not body:
        raise ValueError("Export contains no markers")
    return body, eol


def merge_exports(exports: Sequence[list[Row]]) -> list[Row]:
    """The first export's rows in order, each later export's markers the merged
    list lacks inserted after the row preceding them in that export.

    A marker is its ID, so the first export's coordinates win over an older
    export's; an ID a later export repeats (tellmeGen's ".") is matched by
    its whole row."""
    merged = list(exports[0])
    for export in exports[1:]:
        present = set(merged)
        repeated = {i for i, n in Counter(row[0] for row in export).items() if n > 1}
        known = {row[0] for row in merged} - repeated
        after: dict[Row | None, list[Row]] = defaultdict(list)
        anchor: Row | None = None
        for row in export:
            if row in present:
                anchor = row
            elif row[0] in known:
                continue
            else:
                after[anchor].append(row)
                present.add(row)
        if not after:
            continue
        result = after.pop(None, [])
        for row in merged:
            result.append(row)
            result.extend(after.pop(row, ()))
        merged = result
    return merged


def encode_delta(rows: Sequence[Row], base: Sequence[Row]) -> list[str]:
    """Delta lines rebuilding rows from base (see core/microarray/templates.py)."""
    where: dict[Row, list[int]] = defaultdict(list)
    for index, row in enumerate(base):
        where[row].append(index)
    ops: list[str] = []

    def emit(op: str, count: int) -> None:
        if count and ops and ops[-1][0] == op:
            ops[-1] = f"{op}{int(ops[-1][1:]) + count}"
        elif count:
            ops.append(f"{op}{count}")

    j = 0
    for row in rows:
        indices = where.get(row, [])
        k = bisect.bisect_left(indices, j)
        ahead = indices[k] if k < len(indices) else None
        if ahead is not None and ahead - j <= _MAX_SKIP:
            emit("-", ahead - j)
            emit("=", 1)
            j = ahead + 1
        else:
            ops.append("+" + "\t".join(row))
    return ops


def _delta_base(path: Path) -> str | None:
    if not path.name.endswith(DELTA_SUFFIX):
        return None
    with gzip.open(path, "rt") as f:
        return f.readline().rstrip("\n").split("\t")[-1]


def _shipped_bases(root: Path, name: str) -> Iterable[tuple[str, list[Row]]]:
    """Each shipped single-part template not built on name, with its rows."""
    paths = {
        path.name.split(".")[0]: path
        for path in sorted((root / "body").glob("*.gz"))
        if (info := get_template_format(path.name.split(".")[0])) and info["parts"] == 1
    }
    for base, path in paths.items():
        chain: set[str] = set()
        link: str | None = base
        while link and link in paths and link not in chain:
            chain.add(link)
            link = _delta_base(paths[link])
        if name not in chain:
            yield base, list(template_rows(base, str(path)))


def _gzip(data: bytes) -> bytes:
    return gzip.compress(data, mtime=0)


def build_template(
    raws: Sequence[Path],
    name: str,
    root: Path = TEMPLATES,
    *,
    overwrite: bool = False,
    delta_base: str | None = None,
    check_header_build: bool = True,
) -> tuple[int, str | None]:
    """Write NAME's head and body; return its marker count and delta base."""
    fmt = get_template_format(name)
    if fmt is None or fmt["parts"] != 1:
        raise ValueError("Register a single-part template format before extracting it")
    head_path = root / "head" / f"{name}{fmt['suffix']}"
    body_stem = root / "body" / f"{name}{fmt['suffix']}"
    bodies = [body_stem, *(Path(f"{body_stem}{ext}") for ext in DELTA_EXTENSIONS)]
    if not overwrite and any(path.exists() for path in (head_path, *bodies)):
        raise FileExistsError("Template already exists; choose a new ID or --overwrite")

    expected_build = {"NCBI36": "36", "GRCh38": "38"}.get(fmt.get("build", ""), "37")
    exports = [
        read_export(raw, name, expected_build, check_header_build=check_header_build)
        for raw in raws
    ]
    rows = merge_exports([body for body, _eol in exports])
    full = _gzip("".join("\t".join(row) + "\n" for row in rows).encode())

    chosen: tuple[str, bytes] | None = None
    if delta_base:
        candidates = [
            (base_name, base_rows)
            for base_name, base_rows in _shipped_bases(TEMPLATES, name)
            if delta_base in ("auto", base_name)
        ]
        if not candidates:
            raise ValueError(f"{delta_base} is not a shipped full template body")
        for base_name, base_rows in candidates:
            ops = encode_delta(rows, base_rows)
            data = _gzip(f"#base\t{base_name}\n{chr(10).join(ops)}\n".encode())
            if chosen is None or len(data) < len(chosen[1]):
                chosen = (base_name, data)
        # A delta must earn its indirection.
        if chosen and len(chosen[1]) > 0.6 * len(full):
            chosen = None

    head_path.parent.mkdir(parents=True, exist_ok=True)
    body_stem.parent.mkdir(parents=True, exist_ok=True)
    for path in bodies:
        if overwrite:
            path.unlink(missing_ok=True)
    out = Path(f"{body_stem}{DELTA_SUFFIX}") if chosen else Path(f"{body_stem}.gz")
    with out.open("xb") as output:
        output.write(chosen[1] if chosen else full)
    if list(template_rows(name, str(out))) != rows:
        out.unlink()
        raise RuntimeError(f"{out} does not reproduce the merged export")

    head = _header(name, exports[0][1])
    if head:
        with head_path.open("w" if overwrite else "x", newline="") as output:
            output.write(head)
    elif overwrite:
        head_path.unlink(missing_ok=True)
    return len(rows), chosen[0] if chosen else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path, nargs="+")
    parser.add_argument("name")
    parser.add_argument("--output-dir", type=Path, default=TEMPLATES)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--delta-base",
        metavar="BASE",
        help="store the body as a delta against BASE, or 'auto' for the "
        "shipped template giving the smallest one",
    )
    parser.add_argument(
        "--ignore-header-build",
        action="store_true",
        help="skip the header's declared build (SelfDecode's states GRCh38 "
        "for GRCh37 positions); verify the positions yourself",
    )
    args = parser.parse_args()
    count, base = build_template(
        args.raw,
        args.name,
        args.output_dir,
        overwrite=args.overwrite,
        delta_base=args.delta_base,
        check_header_build=not args.ignore_header_build,
    )
    stored = f"delta against {base}" if base else "full body"
    print(
        f"{args.name}: {count} markers ({stored}); generic header, no source metadata"
    )


if __name__ == "__main__":
    main()
