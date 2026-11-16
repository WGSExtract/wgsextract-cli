"""Development-time schemas for the server's writer-used transport models."""

from typing import is_typeddict

from wgsextract_cli.core.output_schema import Shape, output_schema

from . import transport


def response_schemas(*, ref_prefix: str = "#/components/schemas/") -> dict[str, object]:
    models = {
        model: Shape(name)
        for name, model in vars(transport).items()
        if is_typeddict(model) and not name.endswith("Required")
    }
    components: dict[str, object] = {}
    for model, shape in models.items():
        document = output_schema(
            model,
            shapes=models,
            schema_id=f"urn:wgsextract:server:{shape.name}",
            title=shape.name,
            description="Server transport document.",
        )
        definitions = document.pop("$defs")
        for key in ("$schema", "$id", "title", "description"):
            document.pop(key)
        components[shape.name] = document
        if isinstance(definitions, dict):
            components.update(definitions)

    def relocate(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: (
                    item.replace("#/$defs/", ref_prefix)
                    if key == "$ref" and isinstance(item, str)
                    else relocate(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [relocate(item) for item in value]
        return value

    return {name: relocate(value) for name, value in components.items()}
