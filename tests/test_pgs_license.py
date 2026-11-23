import gzip
import io
import json
from pathlib import Path
from urllib.response import addinfourl

import pytest

from wgsextract_cli import api
from wgsextract_cli.core.pgs import CURATED

FIXTURES = Path(__file__).parent / "fixtures" / "pgs"


@pytest.mark.parametrize(
    ("license", "restricted"),
    [
        ("Freely available to the academic community for research use.", True),
        ("Creative Commons Attribution-NonCommercial 4.0 (CC BY-NC 4.0).", True),
        ("Creative Commons Attribution-ShareAlike 4.0 (CC BY-SA 4.0)", True),
        ("CC BY 4.0. Commercial use needs the authors' permission.", True),
        ("Creative Commons Attribution 4.0 International (CC BY 4.0).", False),
        ("Creative Commons Attribution 4.0 (CC BY 4.0)", False),
        ("CC-BY-4.0", False),
        (
            "PGS obtained from the Catalog should be cited appropriately, and used "
            "in accordance with any licensing restrictions set by the authors.",
            False,
        ),
    ],
)
def test_864_1165_trait_warns_only_of_restrictive_author_licences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, license: str, restricted: bool
) -> None:
    """#864: --trait bmi's PGS000027 is academic-only, but only its licence
    field said so. #1165: its replacement's CC BY 4.0, however the Catalog
    spells it, only asks for the citation the result carries, so it isn't
    reported as restricted; any further terms still are."""
    import urllib.request

    pgs_id = CURATED["bmi"]
    url = f"https://ftp.ebi.ac.uk/pub/databases/spot/pgs/scores/{pgs_id}/h.txt.gz"
    payloads = {
        f"https://www.pgscatalog.org/rest/score/{pgs_id}/": json.dumps(
            {
                "ftp_harmonized_scoring_files": {"GRCh38": {"positions": url}},
                "license": license,
            }
        ).encode(),
        url: gzip.compress(
            (f"#pgs_id={pgs_id}\n" + (FIXTURES / "additive.txt").read_text()).encode()
        ),
    }

    def response(address: str, **kwargs: object) -> addinfourl:
        payload = payloads[address]
        return addinfourl(
            io.BytesIO(payload), {"Content-Length": str(len(payload))}, address
        )

    monkeypatch.setattr(urllib.request, "urlopen", response)
    result = api.score.pgs(
        api.ScorePgsParams(
            input=FIXTURES / "sample.vcf",
            build="GRCh38",
            trait="bmi",
            outdir=tmp_path,
            download=True,
        )
    )
    warned = [w for w in result.warnings if w.code == "pgs_license_restricted"]
    assert [w.details["license"] for w in warned] == ([license] if restricted else [])
