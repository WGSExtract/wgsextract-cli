"""The external tools the CLI uses, and where Pixi provides them."""

import platform
import sys
from collections.abc import Callable

MANDATORY_TOOLS = [
    "samtools",
    "bcftools",
    "tabix",
    "bgzip",
    "bwa",
    "python3",
    "tar",
]


OPTIONAL_TOOLS = [
    "bwa-mem2",
    "bwa-mem3",
    "minimap2",
    "pbmm2",
    "pbsv",
    "sniffles",
    "sambamba",
    "samblaster",
    "fastp",
    "fastqc",
    "multiqc",
    "delly",
    "freebayes",
    "vep",
    "vep-rs",
    "vep-cache-builder",
    "fastvep",
    "gatk",
    "run_deepvariant",
    "wgse-deepvariant",
    "run_clair3.sh",
    "modkit",
    "verifybamid2",
    "somalier",
    "plink2",
    "whatshap",
    "longphase",
    "GLIMPSE2_phase",
    "GLIMPSE2_ligate",
    "GLIMPSE2_chunk",
    "GLIMPSE2_split_reference",
    "curl",
    "java",
    "haplogrep3",
    "haplocheck",
    "htsfile",
    "pharmcat_python",
    "cyrius",
    "smn_caller.py",
    "run-t1k",
    "ExpansionHunter",
    "LongTR",
    "strkit",
    "dorado",
    "java17",
    "discvrseq",
    "granite",
]

# Optional tools users install themselves (no Pixi environment): how to.
INSTALL_HINTS = {
    "plink2": "Download PLINK 2 for your system from "
    "https://www.cog-genomics.org/plink/2.0/ and put plink2 on PATH. "
    "It is optional, used only by score pgs --plink2-check.",
    "dorado": "Dorado is ONT's basecaller and isn't installed with WGS Extract: "
    "download the release for your system from "
    "https://github.com/nanoporetech/dorado#installation, unpack it and put its "
    "bin folder on PATH, or set dorado_executable in config.toml (or pass "
    "basecall --dorado-path).",
    "wgse-deepvariant": "wgse-deepvariant (DeepVariant without Docker): on "
    "Linux `pixi install -e deepvariant` (NVIDIA driver) or the CPU-only "
    "linux-deepvariant package on PATH, on a Mac a macos-deepvariant release on "
    "PATH; set deepvariant_weights in config.toml (native/deepvariant/README.md).",
    "java17": "Install Java 17 with 'wgsextract deps install java17' (or "
    "install OpenJDK 17 yourself and put it on PATH or set java17_executable "
    "in config.toml). Required by vcf variantqc (DISCVRSeq).",
    "discvrseq": "Install with 'wgsextract deps install discvrseq' (or download "
    "DISCVRSeq.jar from https://github.com/BimberLab/DISCVRSeq/releases into "
    "jartools, set discvrseq_jar in config.toml, or pass vcf variantqc "
    "--discvrseq-jar). Requires Java 17 (java17); optional, used by vcf variantqc.",
    "granite": "Install with 'wgsextract deps install granite' (or install "
    "granite-suite from https://github.com/dbmi-bgm/granite with "
    "'pip install granite-suite' and put granite on PATH, or set "
    "granite_executable in config.toml / pass vcf granite-qc --granite-path). "
    "Optional, used by vcf granite-qc.",
}
# What a tool still needs after its Pixi environment is installed.
PIXI_SETUP_HINTS = {
    "wgse-deepvariant": "then set deepvariant_weights in config.toml to its "
    "weights folder (native/deepvariant/README.md).",
}


def required_dependency_tools(include_python: bool = True) -> list[str]:
    """Return the centrally-defined required dependency tools."""
    if include_python:
        return list(MANDATORY_TOOLS)
    return [tool for tool in MANDATORY_TOOLS if tool != "python3"]


# Optional tools the CLI only uses on some platforms (sys.platform prefixes).
PLATFORM_ONLY_TOOLS = {
    "bwa-mem2": ("linux", "darwin", "win32"),
    "bwa-mem3": ("linux", "darwin"),
    "sambamba": ("linux",),
    "cyrius": ("linux", "darwin"),
    "smn_caller.py": ("linux", "darwin"),
    "run-t1k": ("linux", "darwin"),
    "ExpansionHunter": ("linux", "darwin"),
    "LongTR": ("linux", "darwin"),
    "strkit": ("linux", "darwin"),
    "modkit": ("linux", "darwin"),
    "verifybamid2": ("linux", "darwin"),
    "somalier": ("linux", "darwin"),
    "whatshap": ("linux", "darwin"),
    "longphase": ("linux", "darwin"),
    "GLIMPSE2_phase": ("linux", "darwin", "win32"),
    "GLIMPSE2_ligate": ("linux", "darwin", "win32"),
    "GLIMPSE2_chunk": ("linux", "darwin", "win32"),
    "GLIMPSE2_split_reference": ("linux", "darwin", "win32"),
    "granite": ("linux", "darwin"),
}


# Hosts (OS, CPU) with a Bioconda Clair3 2.0.5+ build.
CLAIR3_HOSTS = {("linux", "x86_64"), ("linux", "aarch64"), ("darwin", "arm64")}


def clair3_host_supported() -> bool:
    """Whether this OS and CPU can run Clair3 (`vcf clair3`, `deps check`)."""
    system = "linux" if sys.platform.startswith("linux") else sys.platform
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64"}.get(machine, machine)
    if system == "linux" and machine == "arm64":
        machine = "aarch64"
    return (system, machine) in CLAIR3_HOSTS


def imputation_host_supported() -> bool:
    """Hosts with the opt-in GLIMPSE2/Beagle environment."""
    system = "linux" if sys.platform.startswith("linux") else sys.platform
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return system in {"linux", "darwin", "win32"}
    return system in {"linux", "darwin"} and machine in {"arm64", "aarch64"}


def _x86_deepvariant_host() -> bool:
    """Whether Google's run_deepvariant (x86-64) can be used: ARM64 hosts
    run the native image instead (`vcf deepvariant`, `deps check`)."""
    from wgsextract_cli.core.native_support import is_arm64_docker_host

    return not is_arm64_docker_host()


def _native_deepvariant_host() -> bool:
    """Whether wgse-deepvariant (Apple Silicon, Linux x86-64) can be used."""
    from wgsextract_cli.core.native_support import is_arm64_docker_host

    if sys.platform == "darwin":
        return is_arm64_docker_host()
    return sys.platform.startswith("linux") and platform.machine().lower() in {
        "x86_64",
        "amd64",
    }


# Optional tools that need a particular OS and CPU.
HOST_ONLY_TOOLS: dict[str, Callable[[], bool]] = {
    "run_clair3.sh": clair3_host_supported,
    "run_deepvariant": _x86_deepvariant_host,
    "wgse-deepvariant": _native_deepvariant_host,
    "GLIMPSE2_phase": imputation_host_supported,
    "GLIMPSE2_ligate": imputation_host_supported,
    "GLIMPSE2_chunk": imputation_host_supported,
    "GLIMPSE2_split_reference": imputation_host_supported,
}


def _built_for_host(tool: str) -> bool:
    return (
        sys.platform.startswith(PLATFORM_ONLY_TOOLS.get(tool, ("",)))
        and HOST_ONLY_TOOLS.get(tool, lambda: True)()
    )


def optional_dependency_tools() -> list[str]:
    """Return the optional dependency tools that apply to this platform."""
    return [tool for tool in OPTIONAL_TOOLS if _built_for_host(tool)]


def platform_unavailable_tools() -> list[str]:
    """The optional tools that aren't built for this platform."""
    return [tool for tool in OPTIONAL_TOOLS if not _built_for_host(tool)]


PIXI_TOOL_ENVS = {
    "tar": "default",
    "curl": "default",
    "fastp": "default",
    "fastqc": "default",
    "multiqc": "multiqc",
    "vep": "vep",
    "vep-rs": "vep-rs",
    "vep-cache-builder": "vep-rs",
    "fastvep": "fastvep",
    "haplogrep3": "default",
    "haplocheck": "default",
    "gatk": "gatk",
    "delly": "default",
    "freebayes": "freebayes",
    "samtools": "default",
    "bcftools": "default",
    "bgzip": "default",
    "tabix": "default",
    "java": "default",
    "java17": "java17",
    "sambamba": "default",
    "samblaster": "default",
    "bwa": "default",
    "bwa-mem2": "default",
    "bwa-mem3": "bwa-mem3",
    "minimap2": "default",
    "pbmm2": "pacbio",
    "pbsv": "pacbio",
    "sniffles": "pacbio",
    "htsfile": "default",
    "cyrius": "cyrius",
    "smn_caller.py": "smn",
    "run-t1k": "t1k",
    "t1k-build.pl": "t1k",
    "ExpansionHunter": "expansionhunter",
    "LongTR": "longtr",
    "strkit": "strkit",
    "run_clair3.sh": "clair3",
    "wgse-clair3": "clair3",
    "wgse-deepvariant": "deepvariant",
    "modkit": "modkit",
    "verifybamid2": "verifybamid2",
    "somalier": "somalier",
    "whatshap": "phasing",
    "longphase": "phasing",
    "GLIMPSE2_phase": "imputation",
    "GLIMPSE2_ligate": "imputation",
    "GLIMPSE2_chunk": "imputation",
    "GLIMPSE2_split_reference": "imputation",
    "granite": "granite",
    # Only through their own environments (never a `python` on PATH): pixi_env_tool.
    "pharmcat_python": "pharmcat",
    "cyrius_python": "cyrius",
}
# Environments without a Python of their own: their tools are probed from here.
PIXI_ENVS_WITHOUT_PYTHON = frozenset({"deepvariant", "java17"})


# Interpreters only their own Pixi environment provides (its packages matter).
ENV_ONLY_TOOLS = frozenset({"pharmcat_python", "cyrius_python", "java17"})

TOOL_EXECUTABLE_ALIASES = {
    "pharmcat_python": ("python",),
    "cyrius_python": ("python",),
    "java17": ("java",),
}


# How to run a jar. Java writes in the ANSI code page on Windows (Haplogrep's
# banner has an "ö"); the CLI's output is UTF-8.
JAVA_JAR = ("java", "-Dstdout.encoding=UTF-8", "-Dstderr.encoding=UTF-8", "-jar")

WINDOWS_PIXI_SCRIPT_LAUNCHERS = {
    # FastQC 0.13's launcher is a Python script (0.12's was Perl).
    "fastqc": ("python",),
    "gatk": ("python",),
}

# Conda package that provides a tool in its Pixi environment, when the names differ.
PIXI_TOOL_PACKAGES = {
    "gatk": "gatk4",
    "java17": "openjdk",
    "vep": "ensembl-vep",
    "vep-cache-builder": "vep-rs",
    "pharmcat_python": "pandas",
    "cyrius_python": "cyrius",
    "smn_caller.py": "smncopynumbercaller",
    "run-t1k": "t1k",
    "t1k-build.pl": "t1k",
    "ExpansionHunter": "expansionhunter",
    "LongTR": "longtr",
    "run_clair3.sh": "clair3",
    "modkit": "ont-modkit",
    "GLIMPSE2_phase": "glimpse-bio",
    "GLIMPSE2_ligate": "glimpse-bio",
    "GLIMPSE2_chunk": "glimpse-bio",
    "GLIMPSE2_split_reference": "glimpse-bio",
    "granite": "granite_suite",
}


def _tool_executable_names(tool: str) -> tuple[str, ...]:
    return TOOL_EXECUTABLE_ALIASES.get(tool, (tool,))
