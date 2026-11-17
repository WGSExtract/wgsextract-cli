"""Schema descriptions must not depend on compiler docstring dedentation."""

from dataclasses import dataclass

import pytest

from wgsextract_cli.api import Params, json_schema


@pytest.mark.parametrize(
    "docstring",
    [
        "Summary line.\n    Continued line.\n\n    Extra details.",
        "Summary line.\nContinued line.\n\nExtra details.",
        "\n    Summary line.\n    Continued line.\n    \n    Extra details.\n",
        "Summary line.\n\tContinued line.\n\n\tExtra details.",
    ],
)
def test_1081_schema_description_normalizes_raw_and_compiler_dedented_docstrings(
    docstring: str,
) -> None:
    @dataclass(frozen=True)
    class DocumentedParams(Params):
        pass

    # Assign input explicitly: Python 3.13 cleans literal class docstrings itself.
    DocumentedParams.__doc__ = docstring
    schema = json_schema(DocumentedParams)
    assert schema["description"] == "Summary line.\nContinued line."
