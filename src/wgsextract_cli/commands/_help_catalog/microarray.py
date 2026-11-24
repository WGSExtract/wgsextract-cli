"""Help entries for ``microarray``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "microarray": _h(
        "<input stem>_<format>.txt (or the vendor's extension) per format, "
        "such as sample_23andMe_V5.txt, and <input stem>_CombinedKit.txt; with "
        "--format also <input stem>_genotypes.tsv or .parquet. Each output "
        "gets an <output>.inputs.json of the inputs and options that made it; "
        "a rerun reuses outputs whose inputs are unchanged.",
        "wgsextract microarray --input sample.cram --formats 23andme_v5",
        "wgsextract microarray --input sample.cram --formats 23andme_v5,ancestry_v2",
    ),
}
