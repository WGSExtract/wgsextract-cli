import re

HG37_BUILD_ALIASES = {"37", "hg19", "hg37", "grch37", "hs37", "hs37d5"}
HG38_BUILD_ALIASES = {"38", "hg38", "grch38", "hs38", "hs38dh", "hs38d1", "hs38d1v0"}
T2T_BUILD_ALIASES = {
    "t2t",
    "t2tv20",
    "t2tv2",
    "t2tv2.0",
    "chm13",
    "chm13v2",
    "chm13v2.0",
}
_HG37_EXTRA_MODELS = {
    "hg19p13",
    "hg19p13a",
    "hg19p13n",
    "hg37w",
    "grch37-",
    "grch37p13",
    "hs37-",
    "hs37a",
    "hs37n",
}
_HG38_EXTRA_MODELS = {
    "hg38p12",
    "grch38-",
    "grch38p13",
    "hs38a",
    "thgyseqp",
}
_T2T_EXTRA_MODELS = {
    "t2t chm13",
    "t2t-chm13",
    "t2t-chm13v2.0",
    "t2tv20a",
    "t2tv11",
    "t2tv10",
    "t2tv09",
    "hppv11",
    "hppv1",
}
T2T_PR1_ALIASES = {"pr1", "t2t pr1", "thg1243v3", "hg01243v3"}
T2T_HG002_ALIASES = {"hg002", "t2t hg002", "thgv27", "thgv20", "hg002xy"}

# Build-name tokens, including catalog variants, in preference order.
BUILD_PATH_TOKENS = {
    "hg38": (
        "hg38",
        "grch38",
        "hs38",
        "hs38dh",
        "hs38a",
        "hs38d1",
        "hs38d1v0",
        "hs38d1a",
        "hs38d1s",
        "hs38d1t",
        "hs38d1c",
        "hs38d1v",
    ),
    "hg19": ("hg19", "hg37", "grch37", "hs37", "hs37d5"),
    "t2t": (
        "t2t",
        "chm13",
        "t2tv20",
        "t2tv20a",
        "t2tv2",
        "t2tv2.0",
        "t2tv11",
        "t2tv10",
        "t2tv09",
        "chm13v2",
        "chm13v2.0",
        "chm13v11y",
        "chm13v1y",
    ),
}

_BUILD_PATH_PATTERNS = {
    build: re.compile(
        r"(?<![a-z0-9])(?:"
        + "|".join(map(re.escape, tokens))
        + r")(?:p[0-9]+)?(?![a-z0-9])",
        re.IGNORECASE,
    )
    for build, tokens in BUILD_PATH_TOKENS.items()
}

# chr1 lengths are distinct per assembly and survive any contig naming scheme.
CHR1_LENGTH_BUILDS = {
    249250621: "hg19",
    248956422: "hg38",
    248387328: "t2t",
}

BUILD_CHOICES = [
    *sorted(HG38_BUILD_ALIASES),
    "GRCh38",
    "hs38DH",
    *sorted(HG37_BUILD_ALIASES),
    "GRCh37",
    *sorted(T2T_BUILD_ALIASES),
    "T2Tv20",
    "T2Tv2",
    "T2Tv2.0",
    "CHM13",
    "CHM13v2.0",
]


def _build_key(build: str) -> str:
    return build.strip().lower()


def is_hg37_build(build: str) -> bool:
    key = _build_key(build)
    return key in HG37_BUILD_ALIASES or key in _HG37_EXTRA_MODELS


def is_hg38_build(build: str) -> bool:
    key = _build_key(build)
    return (
        key in HG38_BUILD_ALIASES
        or key in _HG38_EXTRA_MODELS
        or key.startswith("hs38d1")
    )


def is_t2t_build(build: str) -> bool:
    key = _build_key(build)
    return key in T2T_BUILD_ALIASES or key in _T2T_EXTRA_MODELS


def canonical_build(build: str | None) -> str | None:
    """Map any recognized build alias to "hg19", "hg38" or "t2t"."""
    if not build:
        return None
    if is_hg37_build(build):
        return "hg19"
    if is_hg38_build(build):
        return "hg38"
    if is_t2t_build(build):
        return "t2t"
    return None


def display_build(build: str | None) -> str | None:
    """User-facing Major Build name: GRCh37, GRCh38, T2T CHM13, T2T HG002, or T2T PR1.

    Each Major Build (37, 38, CHM13, HG002, PR1) corresponds to distinct primary
    chromosome lengths.
    """
    if not build:
        return None
    key = _build_key(build)
    if key in T2T_PR1_ALIASES:
        return "T2T PR1"
    if key in T2T_HG002_ALIASES:
        return "T2T HG002"
    canonical = canonical_build(build)
    if canonical == "hg19":
        return "GRCh37"
    if canonical == "hg38":
        return "GRCh38"
    if canonical == "t2t":
        return "T2T CHM13"
    return build


def compact_build(build: str | None) -> str | None:
    """Short Major Build token for Build columns: 37, 38, CHM13, HG002, or PR1."""
    shown = display_build(build)
    if shown is None:
        return None
    return {
        "GRCh37": "37",
        "GRCh38": "38",
        "T2T CHM13": "CHM13",
        "T2T HG002": "HG002",
        "T2T PR1": "PR1",
    }.get(shown, shown)


def ploidy_preset(build: str | None) -> str | None:
    """bcftools' predefined ploidy for ``build``: "GRCh37" or "GRCh38". None
    for T2T, whose PARs aren't GRCh38's, and for unrecognised builds."""
    if build and is_hg37_build(build):
        return "GRCh37"
    if build and is_hg38_build(build):
        return "GRCh38"
    return None


def build_from_path(path: str) -> str | None:
    """Recognize build tokens in a name or metadata text.

    Filesystem callers must pass the file or reference folder's own name,
    not its ancestors. Metadata callers may pass descriptions or header values.
    """
    for build, pattern in _BUILD_PATH_PATTERNS.items():
        if pattern.search(path):
            return build
    return None


def reference_header_name(value: str) -> str:
    """The part of a VCF ``##reference=`` value that can name a build: a path
    or URL's file name alone, as tools write the reference's full path and a
    folder name (a project called chm13) says nothing about the file."""
    value = value.strip().strip('<>"')
    if "/" in value or "\\" in value:
        value = re.split(r"[/\\]", value.split("?")[0].rstrip("/\\"))[-1]
    return value


def build_from_reference_header(value: str) -> str | None:
    return build_from_path(reference_header_name(value))


def build_from_chr1_length(contig_lengths: dict[str, int]) -> str | None:
    """Identify the human assembly from the chr1 length of a sequence dictionary."""
    for name in ("chr1", "1"):
        if name in contig_lengths:
            return CHR1_LENGTH_BUILDS.get(contig_lengths[name])
    return None


def fake_data_library_code(build: str) -> str | None:
    key = _build_key(build)
    if key == "hg19":
        return "hg19"
    if is_hg37_build(build):
        return "hs37d5"
    if is_hg38_build(build):
        return "hg38"
    if is_t2t_build(build):
        return "T2Tv20"
    return None
