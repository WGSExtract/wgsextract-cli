"""Cache Pixi's activation at setup and launch the installed CLI without Pixi."""

import json
import os
import subprocess
import sys
from pathlib import Path


def capture(cache: Path) -> None:
    cache.unlink(missing_ok=True)
    # Windows PowerShell can prepend a BOM when piping native stdin.
    variables = json.loads(sys.stdin.buffer.read())["environment_variables"]
    prefix = Path(sys.prefix).resolve()
    if Path(variables["CONDA_PREFIX"]).resolve() != prefix:
        raise ValueError("Pixi did not activate the installed Python environment")
    path_key = next((key for key in variables if key.upper() == "PATH"), "PATH")
    path = variables.pop(path_key, "")
    # Keep only installed tool directories; inherit the host portion of PATH
    # at launch, in case the app was restarted with different settings.
    path_entries = [
        item
        for item in path.split(os.pathsep)
        if item
        and Path(item).is_absolute()
        and Path(item).resolve().is_relative_to(prefix)
    ]
    if not path_entries:
        raise ValueError("Pixi did not activate the installed tool directories")
    document = {"prefix": str(prefix), "path": path_entries, "variables": variables}
    pending = cache.with_suffix(".pending")
    pending.write_text(json.dumps(document), encoding="utf-8")
    pending.replace(cache)


def launch(cache: Path, pixi: str, args: list[str]) -> None:
    try:
        document = json.loads(cache.read_text(encoding="utf-8"))
        if Path(document["prefix"]).resolve() != Path(sys.prefix).resolve():
            raise ValueError("the installed Python environment changed")
        path = [*document["path"], os.environ.get("PATH", os.defpath)]
        variables = {**document["variables"], "PATH": os.pathsep.join(path)}
        if not all(
            key
            and "=" not in key
            and "\0" not in key
            and isinstance(value, str)
            and "\0" not in value
            for key, value in variables.items()
        ):
            raise ValueError("invalid activation variable")
    except (OSError, KeyError, TypeError, ValueError) as error:
        if pixi != "-":
            # Only retry when activation failed, never after running a command.
            command = [pixi, "run", "--no-install", "--frozen", "wgsextract", *args]
            if os.name != "nt":
                os.execv(pixi, command)
            raise SystemExit(subprocess.call(command)) from None
        raise SystemExit(
            f"Cannot activate WGS Extract's runtime: {error}. Run Setup again."
        ) from None

    os.environ.update(variables)

    from wgsextract_cli.main import main

    sys.argv = ["wgsextract", *args]
    main()


if __name__ == "__main__":
    mode, filename, *argv = sys.argv[1:]
    if mode == "capture":
        capture(Path(filename))
    else:
        pixi, *cli_args = argv
        launch(Path(filename), pixi, cli_args)
