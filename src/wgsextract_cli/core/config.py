import difflib
import logging
import os
import sys
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar, cast, overload

from platformdirs import user_config_dir

from wgsextract_cli.core.atomic_output import atomic_write
from wgsextract_cli.core.paths import expand_path, windows_local_app_dir
from wgsextract_cli.core.settings_schema import (
    DEV_SETTINGS,
    DEV_TABLE,
    MOVED_TO_DEV,
    SETTINGS,
    DevSettings,
    InvalidSetting,
    Settings,
    check_values,
)
from wgsextract_cli.core.utils import WGSExtractError

# For Python < 3.11, use tomli
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

APP_NAME = "wgsextract"
APP_AUTHOR = "theontho"
T = TypeVar("T")
_warned_unknown_keys: set[tuple[str, str]] = set()

# The settings whose leading ~ loading expands (core/settings_schema.py).
PATH_SETTINGS = frozenset(name for name, item in SETTINGS.items() if item.info.path)
DEV_PATH_SETTINGS = frozenset(
    name for name, item in DEV_SETTINGS.items() if item.info.path
)


def _normalize_path_setting(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value.expanduser())
    if isinstance(value, str):
        return expand_path(value)
    return value


def normalize_config_paths(config: dict[str, Any]) -> dict[str, Any]:
    for key in PATH_SETTINGS:
        if key in config:
            config[key] = _normalize_path_setting(config[key])
    dev = config.get(DEV_TABLE)
    if isinstance(dev, dict):
        for key in DEV_PATH_SETTINGS & dev.keys():
            dev[key] = _normalize_path_setting(dev[key])
    return config


CONFIG_ALIASES = {
    "input": "input_path",
    "outdir": "output_directory",
    "ref": "reference_fasta",
    "reflib": "reference_library",
    "genome_library": "dataset_library",
    "genomes": "dataset_library",
    "threads": "cpu_threads",
    "memory": "memory_limit",
    "haplogrep_path": "haplogrep_executable",
    "haplocheck_path": "haplocheck_executable",
    "jar_dir": "jar_directory",
    "vep_cache": "vep_cache_directory",
    "input_vcf": "default_input_vcf",
    "mother_vcf": "mother_vcf_path",
    "father_vcf": "father_vcf_path",
}


# Removed Windows runtimes and external Yleaf settings from older installers.
# Tools now run from Pixi; Y-DNA analysis is native. Drop these on config reads.
LEGACY_SETTINGS = {
    "tool_runtime",
    "runtime",
    "runtime_directory",
    "runtime_dir",
    "pacman_ucrt64_bin",
    "pacman_bin",
    "yleaf_path",
    "yleaf_executable",
}


def _validate_config_types(
    config: Mapping[str, Any],
    config_path: Path,
    *,
    allow_invalid_threads: bool = False,
) -> None:
    for key, new_key in MOVED_TO_DEV.items():
        if key in config:
            raise WGSExtractError(
                f"Setting {key!r} in {config_path} moved to the [{DEV_TABLE}] "
                f"table: write it as {new_key} = ... under a [{DEV_TABLE}] line "
                "at the end of the file."
            )
    try:
        for _ in check_values(
            config,
            SETTINGS,
            _canonical,
            strict_threads=not allow_invalid_threads,
        ):
            pass
    except InvalidSetting as error:
        canonical = _canonical(error.key)
        alias = f" ({canonical})" if canonical != error.key else ""
        raise WGSExtractError(
            f"Invalid setting {error.key!r}{alias} in {config_path}: {error}"
        ) from None
    dev = config.get(DEV_TABLE)
    if dev is None:
        return
    if not isinstance(dev, Mapping):
        raise WGSExtractError(
            f"Invalid setting {DEV_TABLE!r} in {config_path}: expected a "
            f"[{DEV_TABLE}] table; got {type(dev).__name__}."
        )
    try:
        for _ in check_values(dev, DEV_SETTINGS):
            pass
    except InvalidSetting as error:
        raise WGSExtractError(
            f"Invalid setting '{DEV_TABLE}.{error.key}' in {config_path}: {error}"
        ) from None


def _canonical(key: str) -> str:
    return CONFIG_ALIASES.get(key, key)


def _warn_unknown(keys: Iterable[str], known: Iterable[str], where: str) -> None:
    known = list(known)
    for key in sorted(keys):
        identity = (where, key)
        if identity in _warned_unknown_keys:
            continue
        _warned_unknown_keys.add(identity)
        matches = difflib.get_close_matches(key, known, n=1)
        hint = f" Did you mean {matches[0]!r}?" if matches else ""
        logging.warning(f"Unknown setting {key!r} in {where}.{hint}")


def get_config_dir() -> Path:
    """Get the standard user configuration directory for this app."""
    return windows_local_app_dir(APP_NAME, APP_AUTHOR) or Path(
        user_config_dir(APP_NAME, APP_AUTHOR)
    )


# Set from --config: a caller (such as the GUI) keeping its own settings file.
_config_path_override: Path | None = None


def set_config_path(path: str | os.PathLike[str] | None) -> None:
    """Use this config.toml instead of the per-user one (None restores it)."""
    global _config_path_override
    _config_path_override = Path(path).expanduser().resolve() if path else None


def config_args(*, keep_awake: bool | None = None) -> list[str]:
    """Forward the config file and sleep override without changing default argv."""
    args = ["--config", str(_config_path_override)] if _config_path_override else []
    if keep_awake or (keep_awake is False and settings.get("keep_awake", False)):
        args.append("--keep-awake" if keep_awake else "--no-keep-awake")
    from wgsextract_cli.core.temp_directory import configured_temp_directory

    temporary = configured_temp_directory()
    if temporary:
        args += ["--tmp-dir", temporary]
    return args


def get_config_path() -> Path:
    """Get the path to the config.toml file."""
    if _config_path_override is not None:
        return _config_path_override
    # Priority for macOS: Use ~/.config/wgsextract/ if ~/.config exists
    if sys.platform == "darwin":
        dot_config = Path.home() / ".config"
        if dot_config.is_dir():
            return dot_config / APP_NAME / "config.toml"

    return get_config_dir() / "config.toml"


def read_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file, accepting the UTF-8 byte-order mark Windows editors add."""
    return tomllib.loads(path.read_bytes().decode("utf-8-sig"))


def load_config() -> dict[str, Any]:
    """
    Load configuration from the standard config file.
    """
    config = {}

    # Load from config.toml
    config_path = get_config_path()
    if config_path.exists():
        try:
            config = read_toml(config_path)
        except (OSError, tomllib.TOMLDecodeError) as e:
            logging.warning(f"Ignoring unreadable config {config_path}: {e}")

    for legacy_key in LEGACY_SETTINGS & config.keys():
        logging.debug(f"Ignoring obsolete setting {legacy_key!r} in {config_path}")
        del config[legacy_key]

    for old_key, new_key in CONFIG_ALIASES.items():
        if old_key in config and new_key not in config:
            config[new_key] = config[old_key]

    _warn_unknown(
        config.keys()
        - SETTINGS.keys()
        - CONFIG_ALIASES.keys()
        - MOVED_TO_DEV.keys()
        - {DEV_TABLE},
        [*SETTINGS, *CONFIG_ALIASES],
        str(config_path),
    )
    dev = config.get(DEV_TABLE)
    if isinstance(dev, dict):
        _warn_unknown(
            dev.keys() - DEV_SETTINGS.keys(),
            DEV_SETTINGS,
            f"the [{DEV_TABLE}] table of {config_path}",
        )

    _validate_config_types(config, config_path, allow_invalid_threads=True)
    return normalize_config_paths(config)


# main selects --config before loading; importers share this stable dictionary.
settings: dict[str, Any] = {}
_settings_loaded = False


def reload_settings() -> None:
    """Re-load configuration from disk and drop results derived from it."""
    global _settings_loaded
    from wgsextract_cli.core.dependencies import clear_tool_caches

    new_settings = load_config()
    settings.clear()
    settings.update(new_settings)
    _settings_loaded = True
    clear_tool_caches()


def ensure_settings_loaded() -> None:
    """Load config.toml for library use, unless the host application already
    loaded settings or filled them itself."""
    if not _settings_loaded and not settings:
        reload_settings()


def typed_settings() -> Settings:
    """The loaded settings, checked, with defaults for those not set: typed
    attribute access (``typed_settings().cpu_threads``)."""
    return settings_from(settings)


def settings_from(values: Mapping[str, object]) -> Settings:
    """``values`` (config.toml's top level) as Settings. An alias counts only
    when its setting isn't given; an invalid cpu_threads reads as auto, which
    the parser warns about."""
    source = get_config_path()
    _validate_config_types(values, source, allow_invalid_threads=True)
    given = {
        name: value
        for key, name, value in check_values(
            values, SETTINGS, _canonical, strict_threads=False
        )
        if key == name or name not in values
    }
    dev = values.get(DEV_TABLE)
    dev_values = (
        {name: value for _, name, value in check_values(dev, DEV_SETTINGS)}
        if isinstance(dev, Mapping)
        else {}
    )
    return Settings(
        **cast(dict[str, Any], given),
        dev=DevSettings(**cast(dict[str, Any], dev_values)),
    )


@contextmanager
def settings_suspended() -> Iterator[None]:
    """Run with no settings, so code sees only its built-in defaults; the
    loaded settings come back afterwards."""
    global _settings_loaded
    saved, was_loaded = dict(settings), _settings_loaded
    settings.clear()
    _settings_loaded = True  # don't let ensure_settings_loaded refill them
    try:
        yield
    finally:
        settings.clear()
        settings.update(saved)
        _settings_loaded = was_loaded


def save_config(updates: dict[str, Any]) -> None:
    """Save configuration updates to the config.toml file."""
    import tomli_w

    config_path = get_config_path()

    current_config = {}
    if config_path.exists():
        # Refuse to overwrite a config we cannot parse; that would drop the
        # user's other settings.
        try:
            current_config = read_toml(config_path)
        except tomllib.TOMLDecodeError as e:
            raise WGSExtractError(
                f"Cannot update {config_path}: it is not valid TOML ({e})."
            ) from e

    for legacy_key in LEGACY_SETTINGS:
        current_config.pop(legacy_key, None)
    for key, value in updates.items():
        if key not in SETTINGS:
            continue
        # Aliases are mapped onto the canonical key on load, so a stale alias
        # would resurrect a cleared value or shadow nothing but confuse users.
        for alias, canonical in CONFIG_ALIASES.items():
            if canonical == key:
                current_config.pop(alias, None)
        if value is None or value == "":
            current_config.pop(key, None)
        else:
            current_config[key] = _normalize_path_setting(value)

    _validate_config_types(current_config, config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(config_path, tomli_w.dumps(current_config).encode("utf-8"))
    reload_settings()


@overload
def get(key: str) -> object | None: ...


@overload
def get(key: str, default: T) -> object | T: ...


def get(key: str, default: object | None = None) -> object | None:
    """Get a configuration value with an optional default."""
    return settings.get(key, default)


def configured_paths() -> list[object]:
    """Every path setting's value, the [dev] table's too (for redaction)."""
    values = [settings.get(key) for key in sorted(PATH_SETTINGS)]
    dev = settings.get(DEV_TABLE)
    if isinstance(dev, Mapping):
        values += [dev.get(key) for key in sorted(DEV_PATH_SETTINGS)]
    return values


def dataset_library() -> str | None:
    """The dataset_library setting (one folder per person or sample), also read
    from the old genome_library key when a host fills settings itself."""
    value = settings.get("dataset_library") or settings.get("genome_library")
    return str(value) if value else None
