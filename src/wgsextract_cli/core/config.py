from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterable, Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast, overload

from wgsextract_cli.core.errors import WGSExtractError
from wgsextract_cli.core.execution_context import ConfigState, bound, current
from wgsextract_cli.core.workspace_activity import relocated_config_path, workspace_use

if TYPE_CHECKING:
    from wgsextract_cli.core.settings_schema import Settings

APP_NAME = "wgsextract"
APP_AUTHOR = "theontho"
T = TypeVar("T")
_warned_unknown_keys: set[tuple[str, str]] = set()


def _normalize_path_setting(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value.expanduser())
    if isinstance(value, str):
        from wgsextract_cli.core.paths import expand_path

        return expand_path(value)
    return value


def normalize_config_paths(config: dict[str, Any]) -> dict[str, Any]:
    from wgsextract_cli.core.settings_schema import (
        DEV_PATH_SETTINGS,
        DEV_TABLE,
        PATH_SETTINGS,
    )

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
    "dataset_library": "mygenomes_library",
    "genome_library": "mygenomes_library",
    "genomes": "mygenomes_library",
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
    from wgsextract_cli.core.settings_schema import (
        DEV_SETTINGS,
        DEV_TABLE,
        MOVED_TO_DEV,
        SERVER_SETTINGS,
        SETTINGS,
        InvalidSetting,
        check_values,
    )

    server = config.get("server", {})
    if not isinstance(server, Mapping):
        raise WGSExtractError(f"Invalid [server] table in {config_path}.")
    try:
        list(check_values(server, SERVER_SETTINGS))
    except InvalidSetting as error:
        raise WGSExtractError(
            f"Invalid setting 'server.{error.key}' in {config_path}: {error}"
        ) from None
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
    keys = sorted(keys)
    if not keys:
        return
    import logging
    from difflib import get_close_matches

    candidates = list(known)
    for key in keys:
        identity = (where, key)
        if identity in _warned_unknown_keys:
            continue
        _warned_unknown_keys.add(identity)
        matches = get_close_matches(key, candidates, n=1)
        hint = f" Did you mean {matches[0]!r}?" if matches else ""
        logging.warning(f"Unknown setting {key!r} in {where}.{hint}")


def get_config_dir() -> Path:
    """Get the standard user configuration directory for this app."""
    from wgsextract_cli.core.paths import windows_local_app_dir

    if directory := windows_local_app_dir(APP_NAME, APP_AUTHOR):
        return directory
    from platformdirs import user_config_dir

    return Path(user_config_dir(APP_NAME, APP_AUTHOR))


# The process's configuration: what the CLI's --config selects and library
# calls use unless they run inside ``scoped_config``. Its path is set from
# --config by a caller (such as the GUI) keeping its own settings file.
_PROCESS_CONFIG = ConfigState()
# Held while a configuration's path is chosen or its settings are loaded, so a
# load publishes the settings of the path it read, never another's.
_load_lock = threading.RLock()


def _state() -> ConfigState:
    """The configuration of the running operation."""
    return current().config or _PROCESS_CONFIG


def _resolve(path: str | os.PathLike[str] | None) -> Path | None:
    return relocated_config_path(Path(path).expanduser().resolve()) if path else None


def set_config_path(path: str | os.PathLike[str] | None) -> None:
    """Use this config.toml instead of the per-user one (None restores it)."""
    with _load_lock:
        _state().path = _resolve(path)


def config_args(*, keep_awake: bool | None = None) -> list[str]:
    """What a wgsextract subprocess inherits: the config file, the sleep
    override, the temporary folder and the folders it may read (a server
    job's), without changing default argv."""
    from wgsextract_cli.core import read_scope

    selected = _state().path
    args = ["--config", str(selected)] if selected else []
    if keep_awake or (keep_awake is False and settings.get("keep_awake", False)):
        args.append("--keep-awake" if keep_awake else "--no-keep-awake")
    # An internal step's card would describe the step; its caller writes its own.
    if settings.get("result_card", False):
        args.append("--no-card")
    from wgsextract_cli.core.temp_directory import configured_temp_directory

    temporary = configured_temp_directory()
    if temporary:
        args += ["--tmp-dir", temporary]
    return args + read_scope.child_args()


def legacy_config_paths() -> list[Path]:
    """Where config.toml was kept before it moved to the users workspace."""
    paths = [get_config_dir() / "config.toml"]
    if sys.platform == "darwin":
        paths.insert(0, Path.home() / ".config" / APP_NAME / "config.toml")
    return paths


def default_config_path() -> Path:
    """config.toml in the users workspace, unless an older install already
    keeps one where the CLI used to (it stays there)."""
    from wgsextract_cli.core.users_workspace import users_workspace

    workspace_config = users_workspace() / "config.toml"
    for legacy in legacy_config_paths():
        if legacy.is_file():
            return legacy
    return workspace_config


def get_config_path() -> Path:
    """Get the path to the config.toml file."""
    selected = _state().path
    return selected if selected is not None else default_config_path()


def read_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file, accepting the UTF-8 byte-order mark Windows editors add."""
    return _read_toml(path, settings_errors=False)


def _read_toml(path: Path, *, settings_errors: bool) -> dict[str, Any]:
    data = path.read_bytes()
    if not data:
        return {}
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib

    try:
        return tomllib.loads(data.decode("utf-8-sig"))
    except tomllib.TOMLDecodeError as e:
        if not settings_errors:
            raise
        raise WGSExtractError(f"Invalid TOML in {path}: {e}") from e
    except UnicodeDecodeError as e:
        if not settings_errors:
            raise
        raise WGSExtractError(f"Invalid UTF-8 in {path} at byte {e.start + 1}") from e


def load_config() -> dict[str, Any]:
    """
    Load configuration from the standard config file.
    """
    config_path = get_config_path()
    if _state().path is None:
        from wgsextract_cli.core.users_workspace import users_workspace

        workspace_config = users_workspace() / "config.toml"
        if (
            config_path.is_file()
            and workspace_config.is_file()
            and config_path.resolve() != workspace_config.resolve()
        ):
            import logging

            logging.warning(
                "Multiple configuration files exist: using existing CLI config %s; "
                "ignoring workspace config %s. Use --config PATH to choose explicitly.",
                config_path,
                workspace_config,
            )
    config = {}
    if config_path.exists():
        try:
            config = _read_toml(config_path, settings_errors=True)
        except OSError as e:
            import logging

            logging.warning(f"Ignoring unreadable config {config_path}: {e}")
    if not config:
        return config

    from wgsextract_cli.core.settings_schema import (
        DEV_SETTINGS,
        DEV_TABLE,
        MOVED_TO_DEV,
        SERVER_SETTINGS,
        SETTINGS,
    )

    for legacy_key in LEGACY_SETTINGS & config.keys():
        import logging

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
        - {DEV_TABLE, "server"},
        [*SETTINGS, *CONFIG_ALIASES],
        str(config_path),
    )
    dev = config.get(DEV_TABLE)
    server = config.get("server")
    if isinstance(server, dict):
        _warn_unknown(
            server.keys() - SERVER_SETTINGS.keys(),
            SERVER_SETTINGS,
            f"the [server] table of {config_path}",
        )
    if isinstance(dev, dict):
        _warn_unknown(
            dev.keys() - DEV_SETTINGS.keys(),
            DEV_SETTINGS,
            f"the [{DEV_TABLE}] table of {config_path}",
        )

    _validate_config_types(config, config_path, allow_invalid_threads=True)
    return normalize_config_paths(config)


class _Settings(MutableMapping[str, Any]):
    """The running operation's settings (config.toml's top level): those of
    its ``scoped_config``, else the process's."""

    def __getitem__(self, key: str) -> Any:
        return _state().values[key]

    def __setitem__(self, key: str, value: Any) -> None:
        _state().values[key] = value

    def __delitem__(self, key: str) -> None:
        del _state().values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(_state().values)

    def __len__(self) -> int:
        return len(_state().values)

    def copy(self) -> dict[str, Any]:
        return dict(_state().values)

    def __repr__(self) -> str:
        return repr(_state().values)


# main selects --config before loading; importers share this stable view.
settings: MutableMapping[str, Any] = _Settings()


def reload_settings() -> None:
    """Re-load configuration from disk and drop results derived from it."""
    _load()
    # There is nothing to invalidate in a subsystem we haven't used yet.
    if "wgsextract_cli.core.dependencies" in sys.modules:
        from wgsextract_cli.core.dependencies import clear_tool_caches

        clear_tool_caches()
    elif "wgsextract_cli.core.remote_input" in sys.modules:
        from wgsextract_cli.core.remote_input import clear_remote_input_cache

        clear_remote_input_cache()


def _load() -> None:
    """Load the running operation's config.toml into its settings."""
    with _load_lock:
        state = _state()
        state.values = load_config()
        state.loaded = True


def ensure_settings_loaded() -> None:
    """Load config.toml for library use, unless the host application already
    loaded settings or filled them itself."""
    with _load_lock:
        state = _state()
        if not state.loaded and not state.values:
            reload_settings()


def snapshot_config() -> ConfigState:
    """A copy of the running operation's configuration, loaded first if
    nothing was, that later changes to it (``use_config``,
    ``reload_settings``) leave alone."""
    with _load_lock:
        ensure_settings_loaded()
        state = _state()
        return ConfigState(path=state.path, values=dict(state.values), loaded=True)


def select_process_config(path: str | os.PathLike[str] | None) -> None:
    """Select and load the process's config.toml (the user's when None), even
    from inside a ``scoped_config`` or a call running on its own snapshot."""
    with _load_lock, bound(config=None):
        set_config_path(path)
        reload_settings()


@contextmanager
def scoped_config(path: str | os.PathLike[str] | None = None) -> Iterator[None]:
    """Run the block, and the worker threads it starts, with settings read
    from ``path`` (the user's config.toml when None), leaving the process's
    and other threads' settings alone. Raises ``WGSExtractError`` for an
    invalid file."""
    # Unlike reload_settings, this leaves the tool caches: they are shared
    # with the calls running beside this one and don't depend on settings.
    with bound(config=ConfigState(path=_resolve(path))):
        _load()
        yield


def typed_settings() -> Settings:
    """The loaded settings, checked, with defaults for those not set: typed
    attribute access (``typed_settings().cpu_threads``)."""
    return settings_from(settings)


def settings_from(values: Mapping[str, object]) -> Settings:
    """``values`` (config.toml's top level) as Settings. An alias counts only
    when its setting isn't given; an invalid cpu_threads reads as auto, which
    the parser warns about."""
    from wgsextract_cli.core.settings_schema import (
        DEV_SETTINGS,
        DEV_TABLE,
        SERVER_SETTINGS,
        SETTINGS,
        DevSettings,
        ServerSettings,
        Settings,
        check_values,
    )

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
        server=ServerSettings(
            **cast(
                dict[str, Any],
                {
                    name: value
                    for _, name, value in check_values(
                        cast(Mapping[str, object], values.get("server", {})),
                        SERVER_SETTINGS,
                    )
                },
            )
        ),
    )


@contextmanager
def settings_suspended() -> Iterator[None]:
    """Run with no settings, so code sees only its built-in defaults; the
    loaded settings come back afterwards."""
    # Loaded, so ensure_settings_loaded doesn't refill them.
    with bound(config=ConfigState(path=_state().path, loaded=True)):
        yield


def _update_table(
    config: dict[str, Any], updates: object, table_name: str, paths: frozenset[str]
) -> None:
    """Merge ``updates`` into a settings table the way save_config merges
    top-level settings: None or "" clears a key, and None clears the table."""
    if updates is None:
        config.pop(table_name, None)
        return
    if not isinstance(updates, Mapping):
        config[table_name] = updates  # rejected by _validate_config_types
        return
    current = config.get(table_name)
    dev = dict(current) if isinstance(current, Mapping) else {}
    for key, value in updates.items():
        if value is None or value == "":
            dev.pop(key, None)
        elif key in paths:
            dev[key] = _normalize_path_setting(value)
        else:
            dev[key] = value
    if dev:
        config[table_name] = dev
    else:
        config.pop(table_name, None)


def _apply_config_updates(config: dict[str, Any], updates: dict[str, Any]) -> None:
    from wgsextract_cli.core.settings_schema import (
        DEV_PATH_SETTINGS,
        DEV_TABLE,
        MOVED_TO_DEV,
        SETTINGS,
    )

    """Merge save_config's ``updates`` into the settings read from config.toml."""
    for legacy_key in LEGACY_SETTINGS:
        config.pop(legacy_key, None)
    for key, value in updates.items():
        if key in (DEV_TABLE, "server"):
            _update_table(
                config,
                value,
                key,
                DEV_PATH_SETTINGS if key == DEV_TABLE else frozenset(),
            )
            continue
        if key in MOVED_TO_DEV:
            if value is None or value == "":
                config.pop(key, None)
            else:
                config[key] = value  # rejected below, naming its [dev] key
            continue
        if key not in SETTINGS:
            continue
        # Aliases are mapped onto the canonical key on load, so a stale alias
        # would resurrect a cleared value or shadow nothing but confuse users.
        for alias, canonical in CONFIG_ALIASES.items():
            if canonical == key:
                config.pop(alias, None)
        if value is None or value == "":
            config.pop(key, None)
        else:
            config[key] = _normalize_path_setting(value)


@workspace_use()
def save_config(updates: dict[str, Any]) -> None:
    """Save configuration updates to the config.toml file."""
    import tomli_w

    from wgsextract_cli.core.atomic_output import atomic_write
    from wgsextract_cli.core.file_lock import shared_file_lock
    from wgsextract_cli.core.users_workspace import (
        claim_users_workspace,
        users_workspace,
    )

    with _load_lock:
        state = _state()
        selected = state.path
        config_path = get_config_path()
    config_path = relocated_config_path(config_path)
    if config_path == users_workspace() / "config.toml":
        claim_users_workspace(config_path.parent)
    # The app and other commands save this file too: merge into what is there
    # now. Take the workspace lease before the file lock so the target cannot
    # move between resolution and publication.
    with shared_file_lock(config_path) as target:
        current_config = {}
        if target.exists():
            # Refuse to overwrite a config we cannot parse; that would drop the
            # user's other settings.
            current_config = _read_toml(target, settings_errors=True)

        _apply_config_updates(current_config, updates)
        _validate_config_types(current_config, config_path)
        atomic_write(target, tomli_w.dumps(current_config).encode("utf-8"))
    with _load_lock:
        if selected is not None and state.path == selected:
            state.path = config_path
        reload_settings()


@overload
def get(key: str) -> object | None: ...


@overload
def get(key: str, default: T) -> object | T: ...


def get(key: str, default: object | None = None) -> object | None:
    """Get a configuration value with an optional default."""
    value: object | None = settings.get(key, default)
    return value


def configured_paths() -> list[object]:
    """Every path setting's value, the [dev] table's too (for redaction)."""
    from wgsextract_cli.core.settings_schema import (
        DEV_PATH_SETTINGS,
        DEV_TABLE,
        PATH_SETTINGS,
    )

    values = [settings.get(key) for key in sorted(PATH_SETTINGS)]
    dev = settings.get(DEV_TABLE)
    if isinstance(dev, Mapping):
        values += [dev.get(key) for key in sorted(DEV_PATH_SETTINGS)]
    return values


def configured_mygenomes_library() -> str | None:
    """The mygenomes_library setting (one folder per person or sample), also
    read from its older names when a host fills settings itself."""
    for key in ("mygenomes_library", *_aliases_of("mygenomes_library")):
        if key in settings:
            value = settings[key]
            return str(value) if value else None
    return None


def mygenomes_library() -> str:
    """The My Genomes library: config mygenomes_library, else the one an older
    install kept beside its default reference library, else mygenomes/ in the
    users workspace. Lookup never moves a folder containing selected inputs."""
    configured = configured_mygenomes_library()
    if configured:
        return configured
    from wgsextract_cli.core.runtime import repo_root
    from wgsextract_cli.core.users_workspace import (
        default_mygenomes_library,
        existing_mygenomes_library,
    )

    existing = existing_mygenomes_library(repo_root())
    return str(existing or default_mygenomes_library())


def output_directory(inputs: Iterable[object] = ()) -> str | None:
    """config output_directory, else the WGSEv6/ folder beside the primary
    input among ``inputs`` (core/output_location.py); None when neither."""
    configured = settings.get("output_directory")
    if configured:
        return str(configured)
    from wgsextract_cli.core.output_location import default_output_directory

    default = default_output_directory(inputs)
    return str(default) if default else None


def setting_or_default(key: str, inputs: Iterable[object] = ()) -> object | None:
    """``key``'s setting, with the defaults that depend on where things are
    when unset: the output folder (beside the primary input among ``inputs``)
    and the My Genomes library."""
    if key == "output_directory":
        return output_directory(inputs)
    if key == "mygenomes_library":
        return mygenomes_library()
    return settings.get(key)


def _aliases_of(key: str) -> list[str]:
    return [alias for alias, canonical in CONFIG_ALIASES.items() if canonical == key]
