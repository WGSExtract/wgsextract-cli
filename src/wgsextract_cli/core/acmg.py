"""What `vcf acmg` (issue 629) writes, for the commands that read it back.

`vcf acmg` adds fastVEP's automated ACMG/AMP class of each ALT allele to a
VCF as three Number=A INFO fields. `vcf table` turns them into columns and
`report` lists the P/LP calls; both repeat the disclaimer, because the classes
are a research aid, never a diagnosis.
"""

from __future__ import annotations

DISCLAIMER = (
    "Research use only; not a diagnosis. These ACMG/AMP classes are computed "
    "automatically by fastVEP from public databases, without the clinical, "
    "family and functional evidence (PS3/BS3, PS4, PP1, PP4) a classification "
    "needs, and a VUS can mean that nothing was checked. Don't act on them "
    "without a clinical genetics professional."
)
CLASS, CRITERIA, TRANSCRIPT = "ACMG_CLASS", "ACMG_CRITERIA", "ACMG_TRANSCRIPT"
TAGS = (CLASS, CRITERIA, TRANSCRIPT)
# fastVEP's classes, most to least pathogenic.
CLASSES = {
    "P": "Pathogenic",
    "LP": "Likely pathogenic",
    "VUS": "Uncertain significance",
    "LB": "Likely benign",
    "B": "Benign",
}
PATHOGENIC = ("P", "LP")
