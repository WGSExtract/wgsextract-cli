"""Build-specific follow-up for well-characterized short-read limitations."""

from collections.abc import Mapping

_SHORT_READ = {
    "SMN1": "typing smn",
    "SMN2": "typing smn",
    "CYP2D6": "typing cyp2d6",
    "RHD": "RHtyper (external)",
}
_PARAPHASE_GRCH37 = {
    "PMS2": "typing paraphase --genes pms2",
    "STRC": "typing paraphase --genes strc",
    "IKBKG": "typing paraphase --genes ikbkg",
    "CYP21A2": "typing paraphase --genes rccx",
}
_PARAPHASE_GRCH38 = {
    "GBA1": "typing paraphase --genes GBA",
    "TNXB": "typing paraphase --genes TNXB",
}
_BY_BUILD = {
    "hg19": {**_SHORT_READ, **_PARAPHASE_GRCH37},
    "hg38": {**_SHORT_READ, **_PARAPHASE_GRCH37, **_PARAPHASE_GRCH38},
    "t2t": {
        "SMN1": "typing paraphase --genes smn1",
        "SMN2": "typing paraphase --genes smn1",
    },
}


def specialist_callers(build: str | None) -> Mapping[str, str]:
    """Return recommended commands by gene, preferring short-read specialists."""
    return _BY_BUILD.get(build, {}) if build else {}
