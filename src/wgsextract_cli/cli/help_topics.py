"""Render the concept pages when requested by the help command."""

from wgsextract_cli.commands._help_topics import HELP_TOPICS

from .help import HELP_SCHEMA, _heading, _name, dump_json


def topic_output(name: str, *, as_json: bool, color: bool) -> str | None:
    if name != "topics" and name not in HELP_TOPICS:
        return None
    names = list(HELP_TOPICS) if name == "topics" else [name]
    if as_json:
        return dump_json(
            {
                "schema": HELP_SCHEMA,
                "topics": [
                    {
                        "name": item,
                        "summary": HELP_TOPICS[item].summary,
                        "body": HELP_TOPICS[item].body,
                    }
                    for item in names
                ],
            }
        )
    if name != "topics":
        return HELP_TOPICS[name].body
    width = max(len(item) for item in names) + 2
    lines = [_heading("Help topics ('wgsextract help <topic>'):", color)]
    for item in names:
        pad = " " * (width - len(item))
        lines.append(f"  {_name(item, color)}{pad}{HELP_TOPICS[item].summary}")
    return "\n".join(lines) + "\n"
