# Data sources

A control, config setting or section can fill itself from a `dataSource`: a bundle script the app runs when it shows the page. The script prints a JSON payload: `options` for a dropdown, `items` or `rows` for a library list, `values` for a section, and `rowActions`/`actions` to replace the declared ones.

```jsonc
"dataSource": {
  "path": "scripts/list-genomes.sh",          // a bundle script, resolved per platform as for actions
  "arguments": ["--list"],
  "optionalArguments": [["--ref", "{{ref_path}}"]], // optional; a group is left out when a placeholder in it is empty
  "environment": { "LIBRARY": "{{ref_path}}" }, // optional
  "workingDirectory": "data",                   // optional, in the bundle; the bundle root by default
  "timeoutSeconds": 120                         // optional; 15 by default
}
```

It must exit 0 within 15 seconds, or the `timeoutSeconds` it declares. It runs with `WGSEXTRACT_GUI_DATA_SOURCE=1`, and each field and config value as `WGSEXTRACT_GUI_FIELD_<ID>` and `WGSEXTRACT_GUI_CONFIG_<ID>`.

## Transforms

When the command prints JSON of its own, such as a CLI's `--json` output, `transform` maps it to the payload instead of a wrapper script:

```jsonc
"dataSource": {
  "path": "scripts/run-tool.sh",
  "arguments": ["--json", "list"],
  "transform": { "module": "transforms/genomes.mjs", "export": "options" }
}
```

```js
// transforms/genomes.mjs
export function options(output) {
  return { options: output.details.genomes.map((genome) => ({ id: genome.path, title: genome.label })) };
}
```

- **`module`** is an ES module in the bundle; **`export`** names one of its functions. The app calls it in its own Node process with the parsed output and shows the object it returns (it may return a promise). It runs with the same trust as the bundle's scripts.
- The command must print valid JSON. A missing export, a throw or anything but an object fails the data source with that message, as a failing script does.
- An edited module is loaded again on the next run.
- Bundle test plans' `dataSource` steps run the transform too, and their output checks (`requiredOutput`, `forbiddenOutput`) see the payload it returned as indented JSON rather than the command's output.

WGSExtract's reference genome dropdowns and Reference Genomes table run `wgsextract --json ref library --list` through `transforms/reference-genomes.mjs`.
