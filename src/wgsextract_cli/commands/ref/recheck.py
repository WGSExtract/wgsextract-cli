"""``ref recheck``: compare recorded resource releases with the newest ones."""

import shlex

from wgsextract_cli.api.ref import RefRecheckParams
from wgsextract_cli.core import release_recheck
from wgsextract_cli.core.atomic_output import safe_output
from wgsextract_cli.core.config import config_args
from wgsextract_cli.core.results import current_result, human_output


def run_recheck(params: RefRecheckParams) -> None:
    manifests = [p.expanduser().absolute() for p in params.manifests]
    command = shlex.join(
        ["wgsextract", *config_args(), "ref", "recheck", *map(str, manifests)]
    )
    answers = params.answers.expanduser().absolute() if params.answers else None
    document, fetched = release_recheck.recheck(
        manifests, answers_file=answers, online=params.online, command=command
    )
    result = current_result()
    for path in manifests:
        result.add_input(path, "manifest")
    if answers is not None:
        result.add_input(answers, "release_answers")
    if fetched is not None and params.save_answers is not None:
        target = params.save_answers.expanduser().absolute()
        target.parent.mkdir(parents=True, exist_ok=True)
        with (
            safe_output(target) as staged,
            open(staged, "w", encoding="utf-8", newline="\n") as stream,
        ):
            stream.write(release_recheck.answers_json(fetched))
        result.add_output(target, "release_answers", kind="json")
    for item in document["resources"]:
        if item["status"] in ("newer_available", "different_release"):
            result.warn(
                "newer_release_available",
                f"{item['name']}: the runs used {item['used_release']}; the answers "
                f"name {item['latest_release']}.",
                resource=item["id"],
                build=item["build"],
            )
        elif item["status"] == "lookup_error":
            result.warn(
                "release_lookup_failed",
                f"{item['name']}: {item['reason']}",
                resource=item["id"],
                build=item["build"],
            )
    for unrecorded in document["unrecorded_manifests"]:
        result.warn(
            "resources_not_recorded",
            "The manifest predates recording resource releases; its resources "
            "are unknown.",
            manifest=unrecorded,
        )
    result.add_metrics(
        resources=len(document["resources"]),
        **{f"resources_{status}": n for status, n in document["counts"].items()},
    )
    result.set_details(dict(document))
    for line in release_recheck.human_lines(document):
        human_output(line)
