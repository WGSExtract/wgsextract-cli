import logging
import os
from os import fspath
from pathlib import Path

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.qc import FakeDataParams, RelateParams
from wgsextract_cli.cli.registration.qc import register as register
from wgsextract_cli.core.builds import fake_data_library_code
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.reference_assets import reference_genomes_dir
from wgsextract_cli.core.results import current_result

from .fake_data import (
    generate_fake_genomics_data,
)
from .relate import run_relate


def run_fake_data(params: FakeDataParams) -> None:
    verify_dependencies(["samtools", "bcftools", "bgzip", "tabix"])
    log_dependency_info(["samtools", "bcftools"])

    outdir = fspath(params.outdir) if params.outdir else os.getcwd()
    Path(outdir).mkdir(parents=True, exist_ok=True)

    from wgsextract_cli.core.ref_library import get_available_genomes
    from wgsextract_cli.core.variant_files import resolve_reference

    # Try to find reference in library if a known build is specified
    lib_ref = None
    target_md5 = None

    target_code = fake_data_library_code(params.build)
    if target_code:
        all_genomes = get_available_genomes()
        genome_info = next(
            (
                g
                for g in all_genomes
                if g["code"] == target_code or g["final"].startswith(target_code)
            ),
            None,
        )
        if genome_info:
            target_md5 = genome_info.get("md5") if genome_info.get("md5") else None
            if target_md5:
                logging.debug(f"Found target MD5 for {params.build}: {target_md5}")
            # See if it's installed
            reflib_dir = settings.get("reference_library")
            if reflib_dir:
                candidate = str(
                    Path(reference_genomes_dir(reflib_dir), genome_info["final"])
                )
                if Path(candidate).exists():
                    lib_ref = candidate

    ref_path = resolve_reference(fspath(params.ref), None) if params.ref else lib_ref

    # If the resolved path is still a directory, it means it didn't find a fasta file there.
    if ref_path and Path(ref_path).is_dir():
        ref_path = None

    # Parse types
    types = [t.strip().lower() for t in params.types.split(",")]
    if "all" in types:
        types = ["vcf", "cram", "bam", "fastq"]

    result = current_result()
    result.set_manifest_stem(Path(outdir, "fake_data"))
    result.add_metrics(build=params.build, coverage=params.coverage, seed=params.seed)
    generate_fake_genomics_data(
        outdir,
        ref_path,
        coverage=params.coverage,
        seed=params.seed,
        build=params.build,
        full_size=params.full_size,
        types=types,
        target_md5=target_md5,
        legacy_bam=params.legacy_bam,
    )


cmd_fake_data = cli_handler(FakeDataParams, run_fake_data)
cmd_relate = cli_handler(RelateParams, run_relate)
