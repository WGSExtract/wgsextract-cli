import gzip

import pytest

from scripts.microarray_template_from_raw import build_template


def test_extraction_never_copies_source_identity_or_genotypes(tmp_path):
    raw = tmp_path / "private-export.csv"
    raw.write_bytes(
        b"##fileformat=MyHeritage\r\n"
        b"#Name: Private Donor\r\n"
        b"#Kit: PRIVATE-KIT-XYZ\r\n"
        b"#Email: donor@example.invalid\r\n"
        b"##timestamp=2099-12-31T23:59:59Z\r\n"
        b"RSID,CHROMOSOME,POSITION,RESULT\r\n"
        b'"rs1","1","100","AG"\r\n'
        b"#Sample: PRIVATE-SAMPLE\r\n"
        b'"rs2","X","200","--"\r\n'
    )
    root = tmp_path / "templates"
    assert build_template([raw], "MyHeritage_GSA_2025", root) == (2, None)
    head = (root / "head/MyHeritage_GSA_2025.csv").read_bytes()
    body = root / "body/MyHeritage_GSA_2025.csv.gz"
    assert gzip.decompress(body.read_bytes()) == b"rs1\t1\t100\nrs2\tX\t200\n"
    assert b"##chip=GSA\r\n" in head
    assert head.endswith(b"RSID,CHROMOSOME,POSITION,RESULT\r\n")
    assert head.count(b"\n") == head.count(b"\r\n")
    for secret in (
        b"Private Donor",
        b"PRIVATE-KIT",
        b"donor@",
        b"2099",
        b"PRIVATE-SAMPLE",
    ):
        assert secret not in head + gzip.decompress(body.read_bytes())
    assert body.read_bytes()[3] & 8 == 0  # No gzip filename.
    assert body.read_bytes()[4:8] == b"\0\0\0\0"


def test_template_bytes_do_not_depend_on_calls_or_missingness(tmp_path):
    bodies = []
    for index, calls in enumerate((("AA", "--"), ("TT", "AC"))):
        raw = tmp_path / f"source-{index}.csv"
        raw.write_text(
            "RSID,CHROMOSOME,POSITION,RESULT\n"
            f"rs1,1,100,{calls[0]}\nrs2,2,200,{calls[1]}\n"
        )
        root = tmp_path / f"templates-{index}"
        build_template([raw], "MyHeritage_GSA_2025", root)
        bodies.append((root / "body/MyHeritage_GSA_2025.csv.gz").read_bytes())
    assert bodies[0] == bodies[1]


@pytest.mark.parametrize(
    "content",
    [
        "Name,CHROMOSOME,POSITION,RESULT\nPrivateDonor,1,100,AA\n",
        "RSID,CHROMOSOME,RESULT,POSITION\nrs1,1,00,100\n",
        "RSID,CHROMOSOME,POSITION,RESULT\nrs1,1,AA,100\n",
        "RSID,CHROMOSOME,POSITION,RESULT\nrs1,AG,100,AA\n",
        "RSID,CHROMOSOME,POSITION,RESULT\nPRIVATE-KIT,1,100,AA\n",
        "RSID,CHROMOSOME,POSITION,RESULT\nrs1,1,100,AA,unexpected\n",
        "rs1,1,100,AA\n",
        "##reference=build38\nRSID,CHROMOSOME,POSITION,RESULT\nrs1,1,100,AA\n",
        "#An export without markers\n",
    ],
)
def test_invalid_or_reordered_schema_publishes_nothing(tmp_path, content):
    raw = tmp_path / "source.csv"
    raw.write_text(content)
    root = tmp_path / "templates"
    with pytest.raises(ValueError):
        build_template([raw], "MyHeritage_GSA_2025", root)
    assert not root.exists()


def test_existing_template_is_not_overwritten_without_explicit_permission(tmp_path):
    raw = tmp_path / "source.csv"
    raw.write_text("RSID,CHROMOSOME,POSITION,RESULT\nrs1,1,100,AA\n")
    root = tmp_path / "templates"
    build_template([raw], "MyHeritage_GSA_2025", root)
    original = (root / "body/MyHeritage_GSA_2025.csv.gz").read_bytes()
    raw.write_text("RSID,CHROMOSOME,POSITION,RESULT\nrs2,2,200,TT\n")
    with pytest.raises(FileExistsError):
        build_template([raw], "MyHeritage_GSA_2025", root)
    assert (root / "body/MyHeritage_GSA_2025.csv.gz").read_bytes() == original
    build_template([raw], "MyHeritage_GSA_2025", root, overwrite=True)
    assert (
        gzip.decompress((root / "body/MyHeritage_GSA_2025.csv.gz").read_bytes())
        == b"rs2\t2\t200\n"
    )


def test_ancestry_preserves_numeric_sex_chromosomes_without_alleles(tmp_path):
    raw = tmp_path / "source.txt.gz"
    with gzip.open(raw, "wt") as stream:
        stream.write(
            "#AncestryDNA raw data download\n"
            "rsid\tchromosome\tposition\tallele1\tallele2\n"
            "rs1\t23\t100\tA\tG\n"
            "rs2\t26\t200\t0\t0\n"
            "rs3\t20\t0\t0\t0\n"
        )
    root = tmp_path / "templates"
    build_template([raw], "Ancestry_V2_2021", root)
    assert (
        gzip.decompress((root / "body/Ancestry_V2_2021.txt.gz").read_bytes())
        == b"rs1\t23\t100\nrs2\t26\t200\nrs3\t20\t0\n"
    )


@pytest.mark.parametrize("suffix", [".txt", ".txt.gz"])
def test_existing_headless_template_is_not_shadowed(tmp_path, suffix):
    root = tmp_path / "templates"
    (root / "body").mkdir(parents=True)
    existing = root / "body" / f"Nucleus_V1{suffix}"
    content = b"rs1\t1\t100\n"
    existing.write_bytes(gzip.compress(content) if suffix.endswith(".gz") else content)
    original = existing.read_bytes()
    raw = tmp_path / "source.txt"
    raw.write_text("rs2\t1\t200\tAA\n")
    with pytest.raises(FileExistsError):
        build_template([raw], "Nucleus_V1", root)
    assert existing.read_bytes() == original
    assert list((root / "body").iterdir()) == [existing]


def test_variant_exports_merge_in_vendor_order(tmp_path):
    first = tmp_path / "v2a.csv"
    first.write_text("RSID,CHROMOSOME,POSITION,RESULT\nrs1,1,100,AA\nrs3,1,300,GG\n")
    second = tmp_path / "v2b.csv"
    second.write_text(
        "RSID,CHROMOSOME,POSITION,RESULT\nrs0,1,50,AA\nrs1,1,100,AA\n"
        "rs2,1,200,CC\nrs3,1,300,GG\n"
    )
    root = tmp_path / "templates"
    assert build_template([first, second], "MyHeritage_GSA_2025", root) == (4, None)
    assert gzip.decompress((root / "body/MyHeritage_GSA_2025.csv.gz").read_bytes()) == (
        b"rs0\t1\t50\nrs1\t1\t100\nrs2\t1\t200\nrs3\t1\t300\n"
    )


def test_y_snp_names_and_marker_lists_without_genotypes_are_read(tmp_path):
    raw = tmp_path / "y.txt"
    raw.write_text("Z57\tY\t2782660\nFGC7837_SK1172\tY\t2793195\n")
    root = tmp_path / "templates"
    assert build_template([raw], "FTDNA_V3Y", root) == (2, None)
    assert (root / "head/FTDNA_V3Y.csv").read_text() == (
        "RSID,CHROMOSOME,POSITION,RESULT\n"
    )


def test_delta_against_a_shipped_template_reproduces_the_rows(tmp_path):
    from scripts.microarray_template_from_raw import TEMPLATES
    from wgsextract_cli.core.microarray.templates import template_rows

    base = list(
        template_rows(
            "MyHeritage_GSA_2025", str(TEMPLATES / "body/MyHeritage_GSA_2025.csv.gz")
        )
    )
    rows = [base[0], ("rs_new1", "1", "5"), *base[2:1000], ("rs_new2", "X", "9")]
    raw = tmp_path / "export.csv"
    raw.write_text(
        "RSID,CHROMOSOME,POSITION,RESULT\n"
        + "".join(f"{i},{c},{p},--\n" for i, c, p in rows).replace("rs_new", "rs9")
    )
    expected = [(i.replace("rs_new", "rs9"), c, p) for i, c, p in rows]
    root = tmp_path / "templates"
    count, base_name = build_template(
        [raw], "MyHeritage_V3", root, delta_base="MyHeritage_GSA_2025"
    )
    assert (count, base_name) == (len(expected), "MyHeritage_GSA_2025")
    body = root / "body/MyHeritage_V3.csv.delta.gz"
    # The base's remaining rows are dropped without an explicit skip.
    assert gzip.decompress(body.read_bytes()) == (
        b"#base\tMyHeritage_GSA_2025\n=1\n+rs91\t1\t5\n-1\n=998\n+rs92\tX\t9\n"
    )
    assert list(template_rows("MyHeritage_V3", str(body))) == expected
