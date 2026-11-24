"""Clair3's ONT models: choosing one for the reads' basecaller, finding it,
and checking it against its pinned SHA-256 digests (``vcf clair3`` and
``deps ready``)."""

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from wgsextract_cli.core.config import settings
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.paths import expand_path
from wgsextract_cli.core.pixi_tools import pixi_env_prefix
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.utils import UsageError, WGSExtractError

from .clair3_runtime import CLAIR3

MODEL_FILES = ("pileup.pt", "full_alignment.pt")

_FETCH = (
    "Download a model directory (pileup.pt and full_alignment.pt) from "
    "https://www.bio8.cs.hku.hk/clair3/clair3_models_rerio_pytorch/ and pass "
    "it with --model or clair3_model in config.toml; see docs/ont.md."
)


class ModelMissing(WGSExtractError):
    """No Clair3 model can be chosen or found: one must be downloaded or named."""


@dataclass(frozen=True)
class Chemistry:
    """Pore/kit/speed must match the model; basecaller accuracy/version should."""

    pore: str
    kit: str | None = None
    speed: str | None = None
    accuracy: str | None = None
    version: str | None = None

    def key(self) -> tuple[str, str | None, str | None]:
        # R9.4.1 models are trained across kits and speeds.
        if self.pore == "941":
            return (self.pore, None, None)
        return (self.pore, self.kit, self.speed)


# Dorado (dna_r10.4.1_e8.2_400bps_sup@v4.1.0) and Guppy (dna_r9.4.1_450bps_hac)
# basecaller model names, as recorded in @RG DS / ID and @PG.
_BASECALLER = re.compile(
    r"dna_r(?P<pore>\d+(?:\.\d+)+)(?:_e(?P<kit>\d+(?:\.\d+)*))?(?:_\d+khz)?"
    r"(?:_(?P<speed>\d+)bps)?(?:_\d+khz)?_(?P<accuracy>fast|hac|sup)"
    r"(?:@v(?P<version>\d+(?:\.\d+)*))?",
    re.IGNORECASE,
)
_MODEL = re.compile(
    r"^r(?P<pore>\d+)(?:_e(?P<kit>\d+))?(?:_(?P<speed>\d+)bps)?(?:_prom)?"
    r"_(?P<accuracy>fast|hac|sup)(?:_(?P<version>[vg][0-9g+]+))?$"
)


def basecaller_chemistries(header: str) -> set[Chemistry]:
    """Basecaller models named in the @RG and @PG header lines."""
    found = set()
    for line in header.splitlines():
        if not line.startswith(("@RG\t", "@PG\t")):
            continue
        for match in _BASECALLER.finditer(line):
            version = match["version"]
            found.add(
                Chemistry(
                    pore=match["pore"].replace(".", ""),
                    kit=match["kit"].replace(".", "") if match["kit"] else None,
                    speed=match["speed"],
                    accuracy=match["accuracy"].lower(),
                    version=f"v{version.replace('.', '')}" if version else None,
                )
            )
    return found


def model_chemistry(name: str) -> Chemistry | None:
    """What a Clair3 ONT model name says it was trained on."""
    if name in ("ont", "ont_guppy5"):
        return Chemistry(pore="941")
    match = _MODEL.match(name)
    if not match:
        return None
    return Chemistry(
        pore=match["pore"],
        kit=match["kit"],
        speed=match["speed"],
        accuracy=match["accuracy"],
        version=match["version"],
    )


def model_for_chemistry(chemistry: Chemistry) -> str | None:
    """The Clair3 model trained for a basecaller, or None when unknown."""
    if chemistry.pore == "941":
        # HKU: also applicable to HAC and fast R9.4.1 reads.
        return "r941_prom_sup_g5014"
    if chemistry.pore == "104" and chemistry.kit == "81" and chemistry.accuracy:
        return f"r104_e81_{chemistry.accuracy}_g5015"
    if (
        chemistry.pore == "1041"
        and chemistry.kit == "82"
        and chemistry.accuracy
        and chemistry.version
    ):
        speed = chemistry.speed or "400"
        return f"r1041_e82_{speed}bps_{chemistry.accuracy}_{chemistry.version}"
    return None


def _chemistry_text(chemistry: Chemistry) -> str:
    pore = {"1041": "R10.4.1", "104": "R10.4", "941": "R9.4.1"}.get(
        chemistry.pore, f"r{chemistry.pore}"
    )
    parts = [pore]
    if chemistry.kit and chemistry.pore != "941":
        parts.append(f"E{chemistry.kit[0]}.{chemistry.kit[1:]}")
    if chemistry.speed and chemistry.pore != "941":
        parts.append(f"{chemistry.speed} bps")
    return " ".join(parts)


@lru_cache(maxsize=1)
def pinned_models() -> dict[str, dict[str, object]]:
    manifest = Path(__file__).resolve().parents[2] / "assets/clair3/models.json"
    models: dict[str, dict[str, object]] = json.loads(manifest.read_text("utf-8"))[
        "models"
    ]
    return models


def bundled_models_dir(launcher: Path | None = None) -> Path | None:
    """Bioconda ships its pre-trained models in the package's bin/models."""
    tool = str(launcher) if launcher is not None else get_tool_path(CLAIR3)
    candidates = []
    if tool and Path(tool).is_file():
        candidates += [
            Path(tool).parent / "models",
            Path(tool).resolve().parent / "models",
        ]
    elif prefix := pixi_env_prefix("clair3"):
        candidates.append(prefix / "bin" / "models")
    return next((path for path in candidates if path.is_dir()), None)


def _fetch_hint(name: str) -> str:
    pin = pinned_models().get(name)
    if not pin:
        return _FETCH
    return (
        f"Download {name} ({', '.join(MODEL_FILES)}) from {pin['url']} into a "
        f"directory named {name} and pass it with --model or clair3_model in "
        "config.toml; see docs/ont.md."
    )


def resolve_model(value: str, bundled: Path | None) -> Path:
    """A model directory, or the name of one Clair3 bundles."""
    # Normalised, so "." and ".." name the real model folder.
    path = Path(os.path.abspath(expand_path(value)))
    if path.is_dir():
        model = path
    elif bundled and "/" not in value and (bundled / value).is_dir():
        model = bundled / value
    else:
        raise ModelMissing(
            f"Clair3 model {value} isn't installed. {_fetch_hint(path.name)}"
        )
    missing = [name for name in MODEL_FILES if not (model / name).is_file()]
    if missing:
        raise ModelMissing(
            f"Clair3 model {model} lacks {', '.join(missing)}; Clair3 2.x needs "
            f"PyTorch models. {_fetch_hint(model.name)}"
        )
    return model


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_evidence(model: Path) -> dict[str, object]:
    """What can be known offline of a model directory: each weight file's
    SHA-256, the pinned digests and download source of a known model, and
    ``verified`` (True: every file matches its pin; False: one doesn't;
    None: no pin). The catalog records no model-specific terms (``terms``
    unknown); the files' presence grants no right to redistribute them."""
    from wgsextract_cli.core.readiness.model import terms

    pin = pinned_models().get(model.name)
    found = {
        name: _sha256(model / name) for name in MODEL_FILES if (model / name).is_file()
    }
    expected = pin["files"] if pin else None
    assert expected is None or isinstance(expected, dict)
    return {
        "name": model.name,
        "path": str(model),
        "sha256": found,
        "pinned_sha256": expected,
        "verified": None
        if expected is None
        else all(found.get(name) == digest for name, digest in expected.items()),
        "source": pin["url"] if pin else None,
        "terms": terms(),
    }


def verify_model(model: Path) -> dict[str, object]:
    """Check a known model against its pinned SHA-256 digests; the run's
    result records the model and whether its digests were verified."""
    evidence = model_evidence(model)
    verified = evidence["verified"]
    if verified is None:
        logging.warning(
            f"Clair3 model {model.name} has no pinned checksum; its integrity and "
            "chemistry are unverified."
        )
    elif not verified:
        pinned, found = evidence["pinned_sha256"], evidence["sha256"]
        assert isinstance(pinned, dict) and isinstance(found, dict)
        name = next(name for name in pinned if found.get(name) != pinned[name])
        raise WGSExtractError(
            f"Clair3 model file {model / name} doesn't match its pinned "
            f"SHA-256; the download is corrupt or not {model.name}. "
            f"{_fetch_hint(model.name)}"
        )
    else:
        logging.info(f"Verified Clair3 model {model.name} (SHA-256).")
    current_result().add_metrics(
        clair3_model=model.name,
        clair3_model_sha256="unpinned" if verified is None else "verified",
    )
    return evidence


def check_model_matches(model_name: str, detected: set[Chemistry]) -> None:
    """Refuse a model for another chemistry; warn on another basecaller."""
    if model_name.startswith(("hifi", "ilmn")):
        raise UsageError(
            f"Clair3 model {model_name} is not an ONT model; `vcf clair3` calls "
            "Nanopore reads."
        )
    if "with_mv" in model_name:
        raise UsageError(
            f"Clair3 model {model_name} needs Dorado move tables (mv tags) and "
            "--enable_dwell_time, which this command does not use; choose the "
            "matching model without _with_mv."
        )
    trained = model_chemistry(model_name)
    if trained is None or not detected:
        return
    for chemistry in detected:
        model_key = trained.key()
        reads_key = chemistry.key()
        if any(
            mine is not None and theirs is not None and mine != theirs
            for mine, theirs in zip(model_key, reads_key, strict=True)
        ):
            raise WGSExtractError(
                f"Clair3 model {model_name} is for {_chemistry_text(trained)} reads, "
                f"but the basecaller metadata says {_chemistry_text(chemistry)}. "
                f"Use {model_for_chemistry(chemistry) or 'a model for that chemistry'}."
            )
        if trained.pore == "941":
            continue
        for field in ("accuracy", "version"):
            mine, theirs = getattr(trained, field), getattr(chemistry, field)
            if mine and theirs and mine != theirs:
                logging.warning(
                    f"Clair3 model {model_name} was trained for a different basecaller "
                    f"{field} ({mine}) than these reads ({theirs}); accuracy may "
                    f"suffer. Expected model: {model_for_chemistry(chemistry)}."
                )
                break


def choose_model(choice: str | None, header: str, launcher: Path | None = None) -> Path:
    """The model a run uses, unverified: ``choice`` (--model), then
    clair3_model in config.toml, then the basecaller metadata; refused for
    reads of another chemistry."""
    detected = basecaller_chemistries(header)
    bundled = bundled_models_dir(launcher)
    value = choice or settings.get("clair3_model")
    if not value:
        names = {model_for_chemistry(chemistry) for chemistry in detected}
        if len(names) != 1 or None in names:
            reason = (
                "the reads come from several basecaller models"
                if len(detected) > 1
                else "the BAM/CRAM header doesn't name a known basecaller model"
            )
            available = ", ".join(
                sorted(
                    p.name
                    for p in (bundled.iterdir() if bundled else ())
                    if p.is_dir() and model_chemistry(p.name)
                )
            )
            raise ModelMissing(
                f"Can't choose a Clair3 model: {reason}. Pass --model NAME|DIR "
                f"(or set clair3_model in config.toml) for the basecaller that made "
                f"these reads. Bundled ONT models: {available or 'none found'}."
            )
        value = str(names.pop())
        logging.info(f"Basecaller metadata selects Clair3 model {value}.")
    model = resolve_model(str(value), bundled)
    check_model_matches(model.name, detected)
    return model


def select_model(choice: str | None, header: str, launcher: Path | None = None) -> Path:
    """``choose_model``'s model, verified against its pinned digests."""
    model = choose_model(choice, header, launcher)
    verify_model(model)
    return model
