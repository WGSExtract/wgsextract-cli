from wgsextract_cli.core.alignment.metadata import get_bam_header
from wgsextract_cli.core.paths import PathInput
from wgsextract_cli.core.ref_library.models import sequence_lengths
from wgsextract_cli.core.region_selection import is_region_file, literal_region


def extracted_locus(region: str, alignment: PathInput) -> str | None:
    """One extracted interval for the viewer, bounded by the alignment header."""
    if "," in region or is_region_file(region):
        return None
    lengths = sequence_lengths(get_bam_header(alignment))
    try:
        chrom, start, end = literal_region(region, lengths)
    except ValueError:
        return None
    length = lengths.get(chrom, 0)
    if start >= length:
        return None
    if ":" in region and "-" not in region.rsplit(":", 1)[1]:
        end = length
    return f"{chrom}:{start + 1}-{min(end, length)}"
