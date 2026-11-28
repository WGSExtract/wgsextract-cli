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

test("every WGSExtract page and titled section has a platform-mode icon", () => {
  // UI audit: Workflows and several Advanced sections unexpectedly fell back to emoji.
  const bundle = new URL("../../examples/WGSExtract/", import.meta.url);
  const { bootstrap, wgse } = parseIconMapToml(readFileSync(new URL("iconmap.toml", bundle), "utf8"));
  const missing = [];
  for (const file of readdirSync(new URL("pages/", bundle))) {
    const page = parseJsonWithComments(readFileSync(new URL(`pages/${file}`, bundle), "utf8"));
    for (const item of [page, ...(page.sections ?? []).filter((section) => section.title)]) {
      if (!wgse[item.iconName] && !bootstrap[item.iconName]) {
        missing.push(`${page.id}/${item.id}`);
      }
    }
  }
  assert.deepEqual(missing, []);
});

test("domain icons use the shipped SVG in platform mode without replacing generic icons or emoji", async () => {
  const { state, renderCheckboxGroup } = await clientView();
  const { renderIcon } = await import("../dist/web/src/client/model.js");
  state.iconMap = parseIconMapToml(`
[wgse]
cnv = "cnv"
external = "<script>not an icon</script>"
[bootstrap]
settings = "gear"
external = "file-text"
[emoji]
cnv = "📊"
`);
  const control = { id: "calls", kind: "checkboxGroup", options: [{ id: "cnv", title: "Copy number", iconName: "cnv" }] };
  const rendered = renderCheckboxGroup(control);
  assert.match(rendered, /<svg[^>]+aria-hidden="true"[^>]*>.*<\/svg><\/span><span>Copy number<\/span>/);
  assert.match(renderIcon("settings", null, ""), /bi-gear/);
  assert.match(renderIcon("external", null, ""), /bi-file-text/);
  state.iconSet = "emoji";
  assert.match(renderCheckboxGroup(control), /emoji-icon[^>]*>📊<\/span>/);
  assert.doesNotMatch(renderCheckboxGroup(control), /<svg/);
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
  const { state, renderTerminalPane } = await clientView();
  const headerToggle = /<header class="terminal-header">[\s\S]*data-terminal-toggle[\s\S]*<\/header>/;
  state.isTerminalVisible = true;
  assert.match(renderTerminalPane(), headerToggle);

  state.isTerminalVisible = false;
  assert.match(renderTerminalPane(), headerToggle);
});

test("collapsing the Jobs pane keeps its row of job tabs and flips the arrow", async () => {
  const { state, renderTerminalPane, appendTerminal } = await clientView();
  appendTerminal("command", "Sort BAM");

  state.isTerminalVisible = true;
  let html = renderTerminalPane();
  assert.match(html, /data-terminal-tab="1"[\s\S]*Sort BAM/);
  assert.match(html, /class="terminal-log"/);
  assert.match(html, /aria-expanded="true"[^>]*>[\s\S]*⌄/);

  state.isTerminalVisible = false;
  html = renderTerminalPane();
  assert.match(html, /class="terminal-panel collapsed"/);
  assert.match(html, /data-terminal-tab="1"[\s\S]*Sort BAM/);
  assert.doesNotMatch(html, /class="terminal-log"/);
  assert.match(html, /aria-expanded="false"[^>]*>[\s\S]*⌃/);
});

test("job tabs keep the newest next to the pane buttons, and a job opened from the list moves there", async () => {
  // User request: as tabs pile up, the oldest drop off the left so Copy and Hide stay in view.
  const { state, renderTerminalPane, appendTerminal, openJob } = await clientView();
  const first = appendTerminal("command", "First");
  appendTerminal("command", "Second");
  appendTerminal("command", "Third");
  const strip = () => renderTerminalPane().match(/<div class="terminal-job-tabs">([\s\S]*?)<\/div>/)[1];
  const titles = () => [...strip().matchAll(/<span>([^<]+)<\/span>/g)].map(([, title]) => title);

  assert.match(renderTerminalPane(), /<div class="terminal-tabs">\s*<span class="terminal-tab-wrap[^"]*main/);
  assert.deepEqual(titles(), ["Third", "Second", "First"]);

  openJob(first);
  assert.deepEqual(titles(), ["First", "Third", "Second"]);
  assert.equal(state.terminalEntries[state.activeTerminalIndex].title, "First");
});

test("while the Jobs list shows, the tab row holds only Jobs; collapsed or on another tab it shows the job tabs", async () => {
  // User request: the Jobs list's rows repeat the job tabs, so showing both looked duplicated.
  const { state, renderTerminalPane, appendTerminal } = await clientView();
  appendTerminal("command", "First");
  appendTerminal("command", "Second");
  const titles = () => [...renderTerminalPane().match(/<div class="terminal-job-tabs">([\s\S]*?)<\/div>/)[1].matchAll(/<span>([^<]+)<\/span>/g)].map(([, title]) => title);

  state.activeTerminalIndex = 0;
  state.isTerminalVisible = true;
  assert.deepEqual(titles(), []);
  assert.match(renderTerminalPane(), /<div class="terminal-tabs">\s*<span class="terminal-tab-wrap active main/);
  assert.match(renderTerminalPane(), /class="jobs-table"[\s\S]*First/);

  state.isTerminalVisible = false;
  assert.deepEqual(titles(), ["Second", "First"]);

  state.isTerminalVisible = true;
  state.activeTerminalIndex = 1;
  assert.deepEqual(titles(), ["Second", "First"]);
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

test("Reference Data Path shows the resolved folder without rewriting its workspace template", async () => {
  const { state, renderTextControl } = await clientView();
  const page = parseJsonWithComments(readFileSync(new URL("../../examples/WGSExtract/pages/library.json", import.meta.url), "utf8"));
  const control = page.sections.flatMap((section) => section.controls ?? []).find(({ id }) => id === "ref_path");
  state.bundleRootPath = "/application/bundle";
  state.usersWorkspacePath = "/volumes/large disk/wgs-extract";
  state.fieldValues = { ref_path: "{{usersWorkspace}}/reference" };
  assert.match(renderTextControl(control), /value="\/volumes\/large disk\/wgs-extract\/reference"/);
  assert.equal(state.fieldValues.ref_path, "{{usersWorkspace}}/reference");

  state.usersWorkspacePath = "D:\\large disk\\wgs-extract";
  assert.match(renderTextControl(control), /value="D:\\large disk\\wgs-extract\\reference"/);
  state.fieldValues.ref_path = "{{bundleWorkspace}}/reference";
  assert.match(renderTextControl(control), /value="\/application\/bundle\/reference"/);
  state.dataSourcePayloads.set("control:reference_data", { displayPaths: { ref_path: "/home/owner/reference" } });
  for (const configured of ["~/reference", "reference"]) {
    state.fieldValues.ref_path = configured;
    assert.match(renderTextControl(control), /value="\/home\/owner\/reference"/);
    assert.equal(state.fieldValues.ref_path, configured);
  }
  state.fieldValues.ref_path = "/custom/reference";
  assert.match(renderTextControl(control), /value="\/custom\/reference"/);
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
    const [appUpdater, ...kinds] = [...renderSetupStatusSection().matchAll(/class="setup-step-kind">([^<]*)</g)].map((match) => match[1]);
    assert.equal(appUpdater, state.labels.setupStepKindAppUpdateLabel, "the app's own update check comes first");
    assert.equal(kinds.length, setupStepKinds.length + 1);
    assert.equal(kinds.at(-1), "", "an unknown kind shows nothing");
    for (const [index, kind] of setupStepKinds.entries()) {
      assert.ok(kinds[index] && kinds[index] !== kind, `${locale}: ${kind} shows "${kinds[index]}"`);
    }
  }
});

test("setup's first step reports the app's own update check", async () => {
  // Owner request: rerunning setup also shows whether the app itself needs an update.
  const { state, renderSetupStatusSection } = await clientView();
  state.labels = {};
  state.manifest = { id: "demo", pages: [], setup: { steps: [{ id: "tool", kind: "pathTool", label: "Tool", value: "x" }] } };
  state.networkSettings = { consent: "accepted", allowUpdateChecks: true, mode: "full", detectedCost: "unknown" };
  const appRow = () => {
    const html = renderSetupStatusSection();
    const row = /<li class="setup-step (\w+)" data-setup-app-update>[\s\S]*?class="setup-step-label">([^<]*)</.exec(html);
    assert.ok(row, "the app update row renders");
    assert.ok(html.indexOf("data-setup-app-update") < html.indexOf(">Tool<"), "it is the first step");
    return [row[1], row[2]];
  };
  Object.assign(state.update, { supported: false, checked: false, status: "idle" });
  assert.deepEqual(appRow(), ["pending", "Unavailable"]);
  Object.assign(state.update, { supported: true, status: "checking" });
  assert.deepEqual(appRow(), ["running", "Running"]);
  Object.assign(state.update, { checked: true, status: "none" });
  assert.deepEqual(appRow(), ["ok", "OK"]);
  Object.assign(state.update, { status: "available" });
  assert.deepEqual(appRow(), ["warning", "Update available"]);
  Object.assign(state.update, { status: "error" });
  assert.deepEqual(appRow(), ["failed", "Failed"]);
  // A failed startup check is quiet in the sidebar, but Setup reports it.
  Object.assign(state.update, { checked: true, status: "idle" });
  assert.deepEqual(appRow(), ["failed", "Failed"]);
  // A found update stays reported after its download fails.
  Object.assign(state.update, { status: "error", availableVersion: "0.9.1" });
  assert.deepEqual(appRow(), ["warning", "Update available"]);
  Object.assign(state.update, { status: "none", availableVersion: "" });
  state.networkSettings.allowUpdateChecks = false;
  assert.deepEqual(appRow(), ["pending", "Off"]);
  Object.assign(state.networkSettings, { allowUpdateChecks: true, mode: "off" });
  assert.deepEqual(appRow(), ["pending", "Off"]);
});

test("Setup ends with Network mode and Allow check for updates, greyed out and unchecked while Off", async () => {
  // Owner request: Network mode sits at the bottom of Setup, with a separate update-check checkbox.
  const { state, renderSetupStatusSection } = await clientView();
  state.labels = { networkMode: "Network mode" };
  state.manifest = { id: "demo", pages: [], setup: { steps: [{ id: "tool", kind: "pathTool", label: "Tool", value: "x" }] } };
  state.setupRun = { status: "ok", results: [] };
  state.setupExpanded = false;
  const checkbox = () => /<input type="checkbox" data-network-setting="allowUpdateChecks"[^>]*>/.exec(renderSetupStatusSection())?.[0] ?? "";
  state.networkSettings = { consent: "accepted", allowUpdateChecks: true, mode: "low", detectedCost: "unknown" };
  const html = renderSetupStatusSection();
  assert.match(html, /id="network-mode-picker"/, "visible even with the steps collapsed");
  assert.ok(html.indexOf("network-mode-picker") < html.indexOf("allowUpdateChecks"), "the checkbox is below Network mode");
  assert.match(checkbox(), / checked/);
  assert.doesNotMatch(checkbox(), /disabled/);
  state.networkSettings.allowUpdateChecks = false;
  assert.doesNotMatch(checkbox(), / checked/);
  Object.assign(state.networkSettings, { allowUpdateChecks: true, mode: "off" });
  assert.doesNotMatch(checkbox(), / checked/);
  assert.match(checkbox(), /disabled/);
});

test("the app version turns red when out of date, and a development run always is", async () => {
  // Owner request: an out-of-date version shows in red; Dev mode is out of date by definition.
  const { state, renderBundleHeader } = await clientView();
  state.labels = { setupVersionLabel: "Version", appVersionDevLabel: "Dev" };
  state.manifest = { id: "demo", displayName: "Demo", pages: [] };
  const version = () => /<p class="app-version([^"]*)"[^>]*>([^<]*)</.exec(renderBundleHeader())?.slice(1, 3);
  Object.assign(state, { appVersion: "0.9.0", appVersionDev: false });
  Object.assign(state.update, { status: "none" });
  assert.deepEqual(version(), ["", "Version 0.9.0"]);
  state.update.status = "available";
  assert.deepEqual(version(), [" out-of-date", "Version 0.9.0"]);
  // A failed download or install, or checking again, keeps the version the check found.
  Object.assign(state.update, { status: "error", availableVersion: "0.9.1" });
  assert.deepEqual(version(), [" out-of-date", "Version 0.9.0"]);
  Object.assign(state.update, { status: "checking" });
  assert.deepEqual(version(), [" out-of-date", "Version 0.9.0"]);
  Object.assign(state.update, { status: "none", availableVersion: "" });
  Object.assign(state, { appVersionDev: true });
  state.update.status = "none";
  assert.deepEqual(version(), [" out-of-date", "Version 0.9.0 Dev"]);
});

test("Standard UI Options starts collapsed and opens with its toggle", async () => {
  // Owner request: Standard UI Options is collapsible, collapsed by default.
  const { state, renderStandardOptionsAccessory } = await clientView();
  state.labels = { standardOptionsSectionTitle: "Standard UI Options", networkMode: "Network mode" };
  const html = () => renderStandardOptionsAccessory();
  assert.match(html(), /data-section-toggle="settings\/standard-ui-options" aria-expanded="false"/);
  assert.doesNotMatch(html(), /data-locale-picker|data-icon-set-picker/);
  state.toggledSections.add("settings/standard-ui-options");
  assert.match(html(), /aria-expanded="true"/);
  assert.match(html(), /data-icon-set-picker/);
  assert.doesNotMatch(html(), /network-mode-picker/, "Network mode lives in Setup");
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
