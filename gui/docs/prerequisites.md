# Prerequisites

Some actions need data that the user installs separately, such as a reference genome. A bundle can declare a check that the app runs before such an action. If the check reports that something is missing, the action doesn't start. Instead a dialog names what is missing and offers three choices:

- **Install and Continue**: the app installs each missing item, showing each install in its own terminal tab, then runs the action. If an install fails, the action doesn't run.
- **Continue**: the user has installed it themselves. The app checks again, and runs the action only if nothing is missing any more; otherwise the dialog stays and says so.
- **Cancel**: nothing runs.

**Install and Continue** is shown only when every missing item can be installed by the app.

```jsonc
// manifest.json
"prerequisites": {
  "command": {
    "executable": "{{bundleRoot}}/scripts/run-tool.sh",
    "arguments": ["needs", "--json", "--"]
  },
  "executables": {
    "{{bundleRoot}}/scripts/run-tool.sh": [],
    "{{bundleRoot}}/scripts/run-tool-calls.sh": ["calls"]
  },
  "kindLabels": { "genome": "prerequisites.kinds.genome" }
}
```

**`executables`** chooses the actions that are checked: those whose command runs one of these executables. The check is given the action's rendered command line, after the arguments listed for its executable. A wrapper script that adds a subcommand, for example, can be described this way.

**`prerequisiteArguments`** on an action gives the check a command line of its own, interpolated like the command's arguments. Use it for an action whose executable takes arguments the check can't read, such as positional ones.

**`kindLabels`** maps each item `kind` to a string-table key. The dialog lists an item with that text, in which `%{name}` is the item's name, for example "%{name} reference genome". An item of another kind is listed by its name.

## The check

The app runs `command` with the command line appended, in the bundle workspace. Its last line on stdout must be a JSON object:

```json
{"missing": [{"id": "hs37d5", "kind": "genome", "name": "hs37d5", "installArguments": ["ref", "library", "--install", "hs37d5"]}]}
```

- `id` names the item when the app asks for it to be installed.
- `installArguments` are run with `command.executable` to install the item. They are `null` when the app can't install it.

An empty list means the action can run. So does a check that exits non-zero, prints no such report, or takes longer than a minute. The app logs why on its console and runs the action, which then reports the problem itself.

To install an item, the app runs the check again on the server and takes the arguments from that report. The page only names the item, so it can't run arbitrary commands.

The WGSExtract bundle checks its `wgsextract` actions with [`wgsextract ref needs`](../../docs/commands.md). It reports the reference genome that a BAM or CRAM input was aligned to when the command decodes a CRAM's reads or calls variants, and the annotation data that the `vcf` annotation commands need.
