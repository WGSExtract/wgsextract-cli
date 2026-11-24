"""Help entries for ``basecall``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "basecall": _h(
        "<POD5 file or folder stem>.<model>[.<modifications>][.subset].bam: "
        "unaligned reads with Dorado's tags (MM/ML with --modified-bases), "
        "beside the POD5 input or in --outdir.",
        "wgsextract basecall --input pod5/ --modified-bases 5mCG_5hmCG",
        "wgsextract basecall --input pod5/ --model sup --regions-bed panel.bed "
        "--alignment sample_aligned.bam",
        "wgsextract basecall --input reads.pod5 --device cpu --model fast",
    ),
}
