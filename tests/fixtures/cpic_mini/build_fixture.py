"""Rebuild cpic_mini.sql.gz: the rows of a few drugs from the CPIC database
export (CC0 1.0), for the report's medication review tests.

    python tests/fixtures/cpic_mini/build_fixture.py cpic_db_dump-v1.61.0.sql.gz
"""

import gzip
import sys
from pathlib import Path

DRUGS = {
    "RxNorm:2670",  # codeine
    "RxNorm:32937",  # paroxetine
    "RxNorm:10324",  # tamoxifen
    "RxNorm:32968",  # clopidogrel
    "RxNorm:283821",  # rasburicase
    "RxNorm:190521",  # abacavir
    "RxNorm:11289",  # warfarin
    "RxNorm:6809",  # metformin
}
# Each kept table and the column that ties its rows to DRUGS (or to their
# guidelines).
TABLES = {
    "drug": "drugid",
    "pair": "drugid",
    "recommendation": "drugid",
    "guideline": "id",
    "publication": "guidelineid",
}


def main(source: str) -> None:
    out = Path(__file__).with_name("cpic_mini.sql.gz")
    with gzip.open(source, "rt", encoding="utf-8", newline="\n") as stream:
        lines = stream.read().split("\n")
    blocks: dict[str, tuple[str, list[str], list[dict[str, str]]]] = {}
    for i, line in enumerate(lines):
        name = line[len("COPY cpic.") :].split(" ", 1)[0]
        if not line.startswith("COPY cpic.") or name not in TABLES:
            continue
        columns = line[line.index("(") + 1 : line.index(")")].split(", ")
        rows = []
        for record in lines[i + 1 :]:
            if record == "\\.":
                break
            rows.append(
                dict(zip(columns, record.split("\t"), strict=True)) | {"": record}
            )
        blocks[name] = (line, columns, rows)
    keep = {
        name: [r for r in blocks[name][2] if r[TABLES[name]] in DRUGS]
        for name in ("drug", "pair", "recommendation")
    }
    guidelines = {r["guidelineid"] for name in keep for r in keep[name]} - {"\\N"}
    keep["guideline"] = [r for r in blocks["guideline"][2] if r["id"] in guidelines]
    keep["publication"] = [
        r for r in blocks["publication"][2] if r["guidelineid"] in guidelines
    ]
    text = (
        "-- CPIC database v1.61.0 (CC0 1.0), rows of a few drugs; see provenance.json\n"
    )
    for name in TABLES:
        text += (
            blocks[name][0] + "\n" + "".join(r[""] + "\n" for r in keep[name]) + "\\.\n"
        )
    with gzip.GzipFile(out, "wb", mtime=0) as stream:
        stream.write(text.encode("utf-8"))


if __name__ == "__main__":
    main(sys.argv[1])
