"""Filter a GRCh37 CombinedKit v5 file to the classic CombinedKit v3 subset."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from wgsextract_cli.core.microarray.formats import COMBINED_V3_TEMPLATES
from wgsextract_cli.core.microarray.identity import MicroarrayIdentity, resolve_rsid
from wgsextract_cli.core.microarray.targets import collect_template_markers
from wgsextract_cli.core.microarray.templates import (
    GenotypeTally,
    TemplateSearchInput,
    _chrom_for_liftover,
    _iter_template_search_dirs,
    is_autosome,
    vendor_chrom,
)
from wgsextract_cli.core.utils import WGSExtractError


def convert_to_combined_v3(
    combined_kit_txt: str,
    output_path: str,
    templates_dir: TemplateSearchInput,
    lift_chain: str | None = None,
    *,
    identity: MicroarrayIdentity | None = None,
    rejected_ids: Collection[str] = (),
) -> GenotypeTally:
    """Filter a GRCh37 CombinedKit v5 file to the classic 14-template
    CombinedKit v3 marker set, restoring v3 rsID precedence."""
    search_roots = list(_iter_template_search_dirs(templates_dir))
    from wgsextract_cli.core.microarray.templates import template_resource_paths

    if not lift_chain and template_resource_paths(search_roots, "FTDNA_V1_Affy"):
        raise WGSExtractError(
            "CombinedKit v3 needs hg18ToHg19.over.chain.gz to include "
            "FTDNA_V1_Affy's markers. Install the chain locally or rerun "
            "'wgsextract microarray' with --download."
        )
    chains = {"NCBI36": lift_chain} if lift_chain else None
    v3_markers = collect_template_markers(
        search_roots, chains=chains, formats=COMBINED_V3_TEMPLATES
    )
    if not v3_markers:
        raise WGSExtractError(
            f"CombinedKit v3 templates not found near {templates_dir!r}; "
            "run 'wgsextract ref bootstrap' first."
        )

    check_rsids = bool(identity and identity.resolve_rsids)
    rejected = frozenset(rejected_ids)
    tmp_path = output_path + ".tmp"
    autosomal = GenotypeTally()
    try:
        with (
            open(combined_kit_txt, encoding="utf-8") as f_in,
            open(tmp_path, "w", encoding="utf-8", newline="\n") as f_out,
        ):
            for line in f_in:
                if line.startswith("#"):
                    f_out.write(line)
                    continue
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) < 4 or not parts[2].isdigit():
                    continue
                site = (_chrom_for_liftover(parts[1]), int(parts[2]))
                rsid = v3_markers.get(site)
                if rsid is None:
                    continue
                result = parts[3]
                if rsid in rejected or (
                    check_rsids and resolve_rsid(rsid).current is None
                ):
                    result = "--"
                if is_autosome(vendor_chrom(parts[1])):
                    autosomal.add(result)
                f_out.write(f"{rsid}\t{parts[1]}\t{parts[2]}\t{result}\n")
        Path(tmp_path).replace(output_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return autosomal
