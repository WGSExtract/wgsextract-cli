import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import test from "node:test";
import { parseIconMapToml } from "../dist/shared/icon-map.js";
import { parseJsonWithComments } from "../dist/shared/json-comments.js";

async function clientView() {
  globalThis.localStorage = { getItem() { return null; }, setItem() {} };
  globalThis.window = { innerHeight: 900 };
  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  Object.assign(state, createInitialState(), { labels: { chooseButtonTitle: "Choose..." } });
  return { state, ...(await import("../dist/web/src/client/view.js")), ...(await import("../dist/web/src/client/terminal.js")) };
}

test("every WGSExtract page and titled section has a Bootstrap icon", () => {
  // UI audit: Workflows and several Advanced sections fell back to emoji while the app showed Bootstrap Icons.
  const bundle = new URL("../../examples/WGSExtract/", import.meta.url);
  const bootstrap = parseIconMapToml(readFileSync(new URL("iconmap.toml", bundle), "utf8")).bootstrap;
  const missing = [];
  for (const file of readdirSync(new URL("pages/", bundle))) {
    const page = parseJsonWithComments(readFileSync(new URL(`pages/${file}`, bundle), "utf8"));
    for (const item of [page, ...(page.sections ?? []).filter((section) => section.title)]) {
      if (!bootstrap[item.iconName]) {
        missing.push(`${page.id}/${item.id}`);
      }
    }
  }
  assert.deepEqual(missing, []);
});

test("a section with only actions has no blank header or controls band", async () => {
  // UI audit: BAM "Info Commands" and Extract "Subset" showed an empty band above their buttons.
  const { state, renderSection } = await clientView();
  const section = { id: "commands", title: "Commands", actions: [{ id: "run", title: "Run", command: { executable: "tool" } }] };
  const untitled = { id: "formats", subsection: true, controls: [{ id: "name", kind: "text", label: "Name" }] };
  const page = { id: "page", title: "Page", sections: [section, untitled] };
  state.manifest = { id: "demo", pages: [page] };

  const commands = renderSection(section, page);
  assert.match(commands, /data-action-id="run"/);
  assert.doesNotMatch(commands, /class="controls"/);
  assert.doesNotMatch(renderSection(untitled, page), /section-header/);
});

test("a rows checkbox group without a group name does not indent its options", async () => {
  // UI audit: Extract's Formats checkboxes sat behind an empty name column, out of line with the other fields.
  const { renderCheckboxGroup } = await clientView();
  const html = renderCheckboxGroup({
    id: "formats", kind: "checkboxGroup", label: "Formats", layout: "rows",
    options: [{ id: "bam", title: "BAM" }, { id: "v5", title: "v5", group: "23andMe" }],
  });

  assert.equal(html.match(/class="option-row untitled"/g)?.length, 1);
  assert.equal(html.match(/class="option-row-title"/g)?.length, 1);
  assert.match(html, /class="option-row-title">23andMe</);
});

test("the command output toggle never floats over the output", async () => {
  // UI audit: the show/hide button sat on top of the last lines of output and the result summary.
  const { state, renderTerminalPane, renderTerminalToggle } = await clientView();
  state.isTerminalVisible = true;
  assert.match(renderTerminalPane(), /<header class="terminal-header">[\s\S]*data-terminal-toggle[\s\S]*<\/header>/);

  state.isTerminalVisible = false;
  assert.match(renderTerminalToggle(), /class="terminal-toggle" data-terminal-toggle/);
});

test("an action without a disk estimate shows no empty precheck banner", async () => {
  // UI audit: Extract's Realign showed a blank blue box when the server had no disk estimate for the chosen BAM.
  const { state, renderActions } = await clientView();
  const { actionPrecheckKey, contextWithFileState } = await import("../dist/web/src/client/operations.js");
  const action = { id: "realign", title: "Realign", precheck: { diskSpaceGB: "10" }, command: { executable: "tool" } };
  const context = { fieldValues: {}, checkedOptions: {}, configValues: {}, rowValues: {} };
  const key = actionPrecheckKey(action, contextWithFileState(context));
  const render = () => renderActions([action], context, { pageID: "extract", sectionID: "realign" });

  state.actionPrechecks.set(key, {});
  assert.doesNotMatch(render(), /precheck-banner/);

  state.actionPrechecks.set(key, { severity: "info", title: "Disk space estimate", message: "Estimated 10 GB needed." });
  assert.match(render(), /class="action-stack has-precheck"[\s\S]*class="precheck-banner info"[\s\S]*Estimated 10 GB needed\./);
});

test("descriptive text widths follow the window, not a fixed pixel cap", () => {
  // Owner report: explanatory text stayed a fixed width when the window was widened.
  const css = readFileSync(new URL("../web/styles.css", import.meta.url), "utf8");
  const rules = css.replace(/@media[^{]*\{/g, "");
  assert.deepEqual(rules.match(/max-(?:width|inline-size):\s*\d+px/g) ?? [], []);
});

test("a path field shows its full path on hover", async () => {
  // Polish pass: a long BAM path was cut off in its field with no way to read it.
  const { state, renderTextControl } = await clientView();
  const path = "/data/samples/run-2026/very/long/folder/HG00096.hg19-mini_aligned.bam";
  state.fieldValues = { bam_path: path };
  const html = renderTextControl({ id: "bam_path", kind: "path", label: "BAM/CRAM" });
  assert.match(html, new RegExp(`value="${path}" title="${path}"`));

  state.fieldValues = { bam_path: "" };
  assert.doesNotMatch(renderTextControl({ id: "bam_path", kind: "path", label: "BAM/CRAM" }), /title=/);
});

test("setup steps name their kind in words, never the manifest id", async () => {
  // Owner request: Settings showed internal step kinds such as "setupScript" and "pathTool".
  const { setupStepKinds } = await import("../dist/shared/setup-step-kinds.js");
  const { localizationLabels, parseTomlStrings } = await import("../dist/shared/localization.js");
  const { state, renderSetupStatusSection } = await clientView();
  for (const locale of ["en", "de"]) {
    const table = parseTomlStrings(readFileSync(new URL(`../../resources/BuiltinStrings/strings.${locale}.toml`, import.meta.url), "utf8"));
    state.labels = localizationLabels(table);
    state.manifest = {
      id: "demo", pages: [],
      setup: { steps: [...setupStepKinds, "futureKind"].map((kind) => ({ id: kind, kind, label: kind, value: "x" })) },
    };
    const kinds = [...renderSetupStatusSection().matchAll(/class="setup-step-kind">([^<]*)</g)].map((match) => match[1]);
    assert.equal(kinds.length, setupStepKinds.length + 1);
    assert.equal(kinds.at(-1), "", "an unknown kind shows nothing");
    for (const [index, kind] of setupStepKinds.entries()) {
      assert.ok(kinds[index] && kinds[index] !== kind, `${locale}: ${kind} shows "${kinds[index]}"`);
    }
  }
});

test("a long library table scrolls under a header row that stays in view", async () => {
  // Owner request: Reference Genomes' column names scrolled away with its many rows.
  const { state, renderLibraryList } = await clientView();
  state.labels = { actionsColumnTitle: "Actions" };
  const html = renderLibraryList(
    { id: "genomes", kind: "libraryList", label: "Genomes", columns: [{ id: "name", title: "Name" }], rowTemplate: { id: "{{id}}" }, items: [{ id: "hs38" }] },
    { fieldValues: {}, checkedOptions: {}, configValues: {}, rowValues: {} },
    { pageID: "genomes", sectionID: "list", controlID: "genomes" },
  );
  // The table is its own scroll area: keyboard-scrollable, named, and keyed so a re-render keeps its place.
  assert.match(html, /class="table-wrap" data-scroll-key="control:genomes" tabindex="0" role="region" aria-label="Genomes"><table>\s*<thead><tr>\s*<th>Name<\/th>/);

  const css = readFileSync(new URL("../web/styles.css", import.meta.url), "utf8");
  assert.match(css, /\.table-wrap \{[^}]*max-height:[^}]*overflow: auto;/);
  assert.match(css, /\.table-wrap thead th \{[^}]*position: sticky;[^}]*top: 0;/);
});

test("re-rendering keeps a scrolled table where it was", async () => {
  // Review of #704: every render rebuilt the Reference Genomes table and scrolled it back to the top.
  const { captureKeyedScrollStates, restoreKeyedScrollStates } = await import("../dist/web/src/client/scroll-state.js");
  const table = (key, top = 0) => ({ dataset: { scrollKey: key }, scrollLeft: 0, scrollTop: top });
  const root = (elements) => ({ querySelectorAll: (selector) => (selector === "[data-scroll-key]" ? elements : []) });

  const before = root([table("control:genomes", 640), table("control:datasets", 80)]);
  const snapshots = captureKeyedScrollStates(before);
  const genomes = table("control:genomes");
  const other = table("control:other");
  restoreKeyedScrollStates(root([other, genomes]), snapshots);

  assert.equal(genomes.scrollTop, 640);
  assert.equal(other.scrollTop, 0, "a table that wasn't there before starts at the top");
});
