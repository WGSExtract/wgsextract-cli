import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { parseIconMapToml } from "../dist/shared/icon-map.js";
import { parseTomlStrings, parseTomlStringValue } from "../dist/shared/localization.js";
import {
  conditionMatches,
  displayCommand,
  evaluateNumeric,
  hydrateRows,
  isPrecheckReady,
  missingPlaceholders,
  parseFlatToml,
  serializeFlatToml,
} from "../dist/shared/rendering.js";

test("parses flat localization TOML with comments and multiline values", () => {
  const table = parseTomlStrings(`
    "language.name" = "English" # translator hint
    key = """ 
first
second
"""
  `);
  assert.equal(table["language.name"], "English");
  assert.equal(table.key, "first\nsecond");
});

test("reads one localization TOML string without parsing the full table", () => {
  const value = parseTomlStringValue(`
    "language.name" = "English" # translator hint
    invalid trailing line without equals
  `, "language.name");

  assert.equal(value, "English");
});

test("rejects malformed trailing content in single-value TOML parsing", () => {
  const value = parseTomlStringValue(`
    "language.name" = "English" trailing junk
  `, "language.name");

  assert.equal(value, undefined);
});

test("single-value TOML boolean parsing follows TOML casing", () => {
  assert.equal(parseTomlStringValue(`ai_translated = true # metadata`, "ai_translated"), "true");
  assert.equal(parseTomlStringValue(`ai_translated = True`, "ai_translated"), undefined);
});

test("critical Bootstrap Icons stylesheet covers every WebUI icon", () => {
  const iconMap = parseIconMapToml(
    readFileSync(
      new URL("../../resources/BuiltinIconMap/iconmap.toml", import.meta.url),
      "utf8"
    )
  );
  const iconNames = new Set(Object.values(iconMap.bootstrap ?? {}));
  assert.ok(iconNames.size > 0, "No Bootstrap icons found in built-in iconmap.toml");
  iconNames.add("copy");
  const criticalCSS = readFileSync(new URL("../web/vendor/bootstrap-icons/bootstrap-icons-critical.css", import.meta.url), "utf8");

  for (const iconName of iconNames) {
    assert.match(criticalCSS, new RegExp(`\\.bi-${iconName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}::before`));
  }
});

test("renders commands with required and optional placeholders", () => {
  const command = {
    executable: "tool",
    arguments: ["run", "{{input}}"],
    optionalArguments: [["--label", "{{label}}"], ["--missing", "{{missing}}"]],
  };
  const context = { fieldValues: { input: "file name.bam", label: "sample" }, checkedOptions: {}, configValues: {}, rowValues: {} };
  assert.deepEqual(missingPlaceholders(command, context), []);
  assert.equal(displayCommand(command, context), "tool run 'file name.bam' --label sample");
});

test("hydrates list rows from item values and templates", () => {
  const rows = hydrateRows({
    columns: [{ id: "name", title: "Name" }],
    rowTemplate: { id: "{{id}}", title: "{{name}}", values: { build: "{{build}}" }, status: "{{status}}" },
    items: [{ id: "hg38", name: "GRCh38", build: "GRCh38", status: "installed" }],
  });
  assert.deepEqual(rows, [
    { id: "hg38", title: "GRCh38", values: { build: "GRCh38" }, status: "installed", tags: [], tooltip: undefined },
  ]);
});

test("hydrates library row metadata from top-level datasource item fields", () => {
  const rows = hydrateRows({
    columns: [{ id: "name", title: "Name" }],
    rowTemplate: {
      id: "{{id}}",
      title: "{{name}}",
      values: { name: "{{name}}", code: "{{code}}" },
      status: "{{status}}",
      tags: [{ id: "recommended", title: "{{recommended}}", style: "primary" }],
      tooltip: "{{description}}",
    },
    items: [
      {
        id: "hg38",
        title: "GRCh38",
        status: "missing",
        tags: [{ id: "recommended", title: "Recommended", style: "primary" }],
        tooltip: "Reference genome",
        values: { name: "GRCh38", code: "hs38", recommended: "" },
      },
    ],
  });
  assert.equal(rows[0].status, "missing");
  assert.deepEqual(rows[0].tags, [{ id: "recommended", title: "Recommended", style: "primary" }]);
  assert.equal(rows[0].tooltip, "Reference genome");
  assert.equal(rows[0].values.code, "hs38");
});

test("evaluates numeric action conditions", () => {
  assert.equal(
    conditionMatches(
      { placeholder: "size", greaterThanOrEqual: "2 * 5" },
      { fieldValues: { size: "10" }, checkedOptions: {}, configValues: {}, rowValues: {} },
    ),
    true,
  );
});

test("evaluates disk precheck arithmetic expressions", () => {
  assert.equal(evaluateNumeric("1.5 * 6"), 9);
  assert.equal(evaluateNumeric("(2 + 3) * 4"), 20);
});

test("waits to run disk prechecks until source files are chosen", () => {
  const precheck = { diskSpaceGB: "{{fastq_r1.fileSizeGB}} * 8", diskSpacePath: "{{out_dir}}" };
  assert.equal(isPrecheckReady(precheck, { fieldValues: { out_dir: "/tmp/out" }, configValues: {} }), false);
  assert.equal(isPrecheckReady(precheck, { fieldValues: { fastq_r1: "/tmp/read1.fastq", out_dir: "" }, configValues: {} }), true);
});

test("round trips flat TOML config values", () => {
  const text = serializeFlatToml({ output_dir: "/tmp/out", quoted: 'a "value"' });
  assert.deepEqual({ ...parseFlatToml(text) }, { output_dir: "/tmp/out", quoted: 'a "value"' });
});

test("parses quoted TOML keys with separators safely", () => {
  const parsed = parseFlatToml('"a=b" = "value"\n"__proto__" = "safe"\n');
  assert.equal(Object.getPrototypeOf(parsed), null);
  assert.equal(parsed["a=b"], "value");
  assert.equal(parsed.__proto__, "safe");
});

test("renders setup status for settings bundles with and without setup steps", async () => {
  globalThis.localStorage = {
    getItem() {
      return null;
    },
    setItem() {},
  };
  globalThis.window = { innerHeight: 900 };

  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderSetupGlobalStatusBar, renderSetupPromptDialog, renderSetupStatusSection, setupNeedsAttention } = await import("../dist/web/src/client/view.js");
  Object.assign(state, createInitialState(), {
    manifest: {
      displayName: "WGSExtract",
      setup: {
        initialInstallSizeGB: 6,
        steps: [
          { id: "install", kind: "setupScript", label: "Install tool", toolName: "Example CLI", toolVersion: "v1.2.3" },
          { id: "check", kind: "pathTool", label: "Check path", toolVersion: "v9.0.0" },
        ],
      },
    },
    labels: {
      setupTitle: "Setup",
      setupRunButtonTitle: "Run Setup",
      setupRerunButtonTitle: "Rerun Setup",
      setupRunningTitle: "Running setup...",
      setupNoStepsTitle: "No setup needed.",
      setupStatusReadyTitle: "Ready to set up.",
      setupStatusOkTitle: "Setup completed.",
      setupStatusFailedTitle: "Setup failed.",
      setupInitialInstallSizeFormat: "Initial setup will install about %{size} GB.",
      setupDiskSpaceCheckingTitle: "Checking disk space.",
      setupStepPendingTitle: "Pending",
      setupStepRunningTitle: "Running",
      setupStepOkTitle: "OK",
      openBundleWorkspaceTitle: "Open Bundle Workspace",
      terminalCancelButtonTitle: "Cancel",
    },
    iconMap: {
      bootstrap: {
        folder: "folder",
        "play.fill": "play-fill",
      },
    },
    setupPreflight: {
      severity: "info",
      message: "Estimated 6.00 GB needed at WGSExtract (120 GB free).",
    },
    setupPreflightKey: JSON.stringify({
      precheck: { diskSpaceGB: "6", diskSpacePath: "{{bundleRoot}}" },
      bundleRootPath: "",
    }),
  });

  let html = renderSetupStatusSection();
  assert.match(html, /Ready to set up/);
  assert.match(html, /data-run-setup/);
  assert.match(html, /data-open-bundle-workspace/);
  assert.match(html, /Install tool/);
  assert.match(html, /Tool: Example CLI v1\.2\.3/);
  assert.match(html, /Version: v9\.0\.0/);
  assert.match(html, /class="setup-header-tool"/);
  assert.match(html, /Tool: Example CLI v1\.2\.3; Version: v9\.0\.0/);
  assert.doesNotMatch(html, /setup-step-tool/);
  assert.match(html, /Pending/);
  assert.equal(setupNeedsAttention(), true);
  assert.match(renderSetupGlobalStatusBar(), /data-setup-global-start/);
  assert.match(renderSetupGlobalStatusBar(), /Ready to set up/);
  state.setupPromptVisible = true;
  assert.match(renderSetupPromptDialog(), /role="alertdialog"/);
  assert.match(renderSetupPromptDialog(), /WGSExtract will probably not work properly/);
  assert.match(renderSetupPromptDialog(), /Initial setup will install about 6 GB\./);
  assert.match(renderSetupPromptDialog(), /Estimated 6\.00 GB needed at WGSExtract/);
  assert.match(renderSetupPromptDialog(), /Tool: Example CLI v1\.2\.3/);
  assert.match(renderSetupPromptDialog(), /<p class="setup-prompt-tool">Tool: Example CLI v1\.2\.3<\/p>/);
  assert.match(renderSetupPromptDialog(), /data-setup-prompt-run/);
  assert.doesNotMatch(renderSetupPromptDialog(), /data-setup-prompt-run disabled/);

  state.setupPreflight = {
    severity: "warning",
    message: "Need 6.00 GB free at WGSExtract, only 2.00 GB available.",
  };
  assert.match(renderSetupPromptDialog(), /setup-prompt-disk warning/);
  assert.match(renderSetupPromptDialog(), /data-setup-prompt-run disabled/);

  state.setupRun = { status: "running", currentStepID: "install", currentStepElapsedMs: 1250, results: [] };
  html = renderSetupStatusSection();
  assert.match(html, /Running setup/);
  assert.match(html, /setup-step running/);
  assert.match(html, /mini-spinner/);
  assert.match(html, /1s/);
  assert.doesNotMatch(html, /setup-step-status" aria-hidden="true">…/);

  state.setupRun = {
    status: "ok",
    currentStepID: null,
    results: [
      { id: "install", status: "ok", durationMs: 62_000 },
      { id: "check", status: "ok", durationMs: 900 },
    ],
  };
  html = renderSetupStatusSection();
  assert.match(html, /Setup completed/);
  assert.match(html, /Rerun Setup/);
  assert.match(html, /bi-play-fill/);
  assert.match(html, /1m 2s/);
  assert.match(html, /0\.9s/);
  assert.equal((html.match(/OK/g) ?? []).length, 2);
  assert.equal(setupNeedsAttention(), false);
  assert.equal(renderSetupGlobalStatusBar(), "");

  state.setupRun = {
    status: "warning",
    currentStepID: null,
    results: [
      { id: "install", status: "ok" },
      { id: "check", status: "warning" },
    ],
  };
  html = renderSetupStatusSection();
  assert.match(html, /Rerun Setup/);
  assert.equal(setupNeedsAttention(), false);
  assert.equal(renderSetupGlobalStatusBar(), "");

  state.manifest.setup.steps = [];
  state.setupRun = null;
  html = renderSetupStatusSection();
  assert.match(html, /No setup needed/);
  assert.doesNotMatch(html, /data-run-setup/);
});

test("renders sidebar update button with persistent download progress", async () => {
  globalThis.localStorage = {
    getItem() {
      return null;
    },
    setItem() {},
  };
  globalThis.window = { innerHeight: 900 };

  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderUpdateNavigationItem, renderUpdatePopover } = await import("../dist/web/src/client/view/update.js");
  const nextState = createInitialState();
  Object.assign(state, nextState, {
    applicationName: "WGSExtract",
    applicationVersion: "0.1.9",
    update: {
      ...nextState.update,
      supported: true,
      status: "downloading",
      popoverVisible: true,
      currentVersion: "0.1.9",
      availableVersion: "0.1.10",
      percent: 42,
      message: "Downloading update...",
    },
  });

  const html = renderUpdateNavigationItem();
  assert.match(html, /data-update-toggle/);
  assert.match(html, /aria-controls="update-popover"/);
  assert.match(html, /Downloading update/);
  assert.match(html, /42%/);
  assert.doesNotMatch(html, /data-update-popover/);

  const popoverHTML = renderUpdatePopover();
  assert.match(popoverHTML, /data-update-popover/);
  assert.match(popoverHTML, /data-update-surface/);
  assert.match(popoverHTML, /id="update-popover"/);
  assert.match(popoverHTML, /tabindex="-1"/);
  assert.match(popoverHTML, /0\.1\.9/);
  assert.match(popoverHTML, /0\.1\.10/);
  assert.match(popoverHTML, /width: 42%/);
});

test("keeps update navigation visible while checking", async () => {
  globalThis.localStorage = {
    getItem() {
      return null;
    },
    setItem() {},
  };
  globalThis.window = { innerHeight: 900 };

  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderUpdateNavigationItem } = await import("../dist/web/src/client/view/update.js");
  const nextState = createInitialState();
  Object.assign(state, nextState, {
    update: {
      ...nextState.update,
      supported: true,
      status: "checking",
      message: "Checking for updates...",
    },
  });

  const html = renderUpdateNavigationItem();
  assert.match(html, /data-update-toggle/);
  assert.match(html, /Checking for updates/);
});

test("labels update check failures distinctly from available updates", async () => {
  globalThis.localStorage = {
    getItem() {
      return null;
    },
    setItem() {},
  };
  globalThis.window = { innerHeight: 900 };

  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderUpdateNavigationItem, renderUpdatePopover } = await import("../dist/web/src/client/view/update.js");
  const nextState = createInitialState();
  Object.assign(state, nextState, {
    update: {
      ...nextState.update,
      supported: true,
      status: "error",
      popoverVisible: true,
      currentVersion: "0.3.5",
      message: "Command gfc_update_check not allowed by ACL",
    },
  });

  const html = renderUpdateNavigationItem();
  assert.match(html, /Update check failed/);
  assert.doesNotMatch(html, /Update available/);

  const popoverHTML = renderUpdatePopover();
  assert.match(popoverHTML, /Update check failed/);
  assert.doesNotMatch(popoverHTML, /Update available/);
  // Version list should be suppressed when no available version is known.
  assert.doesNotMatch(popoverHTML, /update-version-list/);
  assert.doesNotMatch(popoverHTML, /Unknown/);
});

test("disabled action tooltips use localized missing input labels and keep action help", async () => {
  globalThis.localStorage = {
    getItem() {
      return null;
    },
    setItem() {},
  };
  globalThis.window = { innerHeight: 900 };

  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderActions } = await import("../dist/web/src/client/view.js");
  Object.assign(state, createInitialState(), {
    manifest: {
      pages: [
        {
          sections: [
            {
              controls: [
                { id: "realign_bam", kind: "path", label: "Realigned BAM" },
                {
                  id: "wgs_settings",
                  kind: "configEditor",
                  label: "Settings",
                  settings: [
                    {
                      id: "ref_path",
                      key: "reference_library",
                      kind: "path",
                      label: "Reference Library",
                    },
                  ],
                },
              ],
            },
          ],
        },
      ],
    },
    labels: {
      actionMissingInputsFormat: "Missing: %{inputs}",
      actionUnavailableTitle: "Unavailable",
    },
  });

  const html = renderActions(
    [
      {
        id: "run",
        title: "Run",
        tooltip: "Run realignment with the selected inputs.",
        command: {
          executable: "tool",
          arguments: ["{{realign_bam}}", "{{reference_library}}"],
        },
      },
    ],
    { fieldValues: {}, checkedOptions: {}, configValues: {}, rowValues: {} },
    { pageID: "realign", sectionID: "inputs" },
  );

  assert.match(html, /Run realignment with the selected inputs\./);
  assert.match(html, /Missing: Realigned BAM, Reference Library/);
  assert.doesNotMatch(html, /Missing: realign_bam/);
});

test("icon-only action buttons omit empty title spans so icons stay centered", async () => {
  globalThis.localStorage = {
    getItem() {
      return null;
    },
    setItem() {},
  };
  globalThis.window = { innerHeight: 900 };

  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderActions } = await import("../dist/web/src/client/view.js");
  Object.assign(state, createInitialState(), {
    manifest: { pages: [] },
    labels: {
      actionMissingInputsFormat: "Missing: %{inputs}",
      actionUnavailableTitle: "Unavailable",
    },
  });

  const html = renderActions(
    [
      {
        id: "delete",
        title: "Delete",
        iconOnly: true,
        textIcon: "X",
        command: {
          executable: "tool",
          arguments: ["delete"],
        },
      },
    ],
    { fieldValues: {}, checkedOptions: {}, configValues: {}, rowValues: {} },
    { pageID: "library", sectionID: "genomes", controlID: "genome_table" },
    true,
  );

  assert.match(html, /action-button primary compact icon-only/);
  assert.match(html, /data-action-ref="\{&quot;pageID&quot;:&quot;library&quot;,&quot;sectionID&quot;:&quot;genomes&quot;,&quot;controlID&quot;:&quot;genome_table&quot;,&quot;actionID&quot;:&quot;delete&quot;\}"/);
  assert.match(html, /<span class="action-icon" aria-hidden="true">/);
  assert.doesNotMatch(html, /<span><\/span>/);
});

test("renders genome build values as colorful library table pills", async () => {
  globalThis.localStorage = {
    getItem() {
      return null;
    },
    setItem() {},
  };
  globalThis.window = { innerHeight: 900 };

  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderRowCell } = await import("../dist/web/src/client/view.js");
  Object.assign(state, createInitialState(), { labels: {} });

  assert.match(
    renderRowCell({ id: "hg19", values: { build: "GRCh37 / hg19" } }, { id: "build", title: "Build" }),
    /<span class="pill build primary">GRCh37 \/ hg19<\/span>/,
  );
  assert.match(
    renderRowCell({ id: "hg38", values: { build: "GRCh38 / hg38" } }, { id: "build", title: "Build" }),
    /<span class="pill build success">GRCh38 \/ hg38<\/span>/,
  );
  assert.match(
    renderRowCell({ id: "t2t", values: { build: "T2T / CHM13" } }, { id: "build", title: "Build" }),
    /<span class="pill build warning">T2T \/ CHM13<\/span>/,
  );
  assert.match(
    renderRowCell({ id: "custom", values: { build: "MyBuild42" } }, { id: "build", title: "Build" }),
    /<span class="pill build secondary">MyBuild42<\/span>/,
  );
  assert.equal(
    renderRowCell({ id: "empty", values: { build: "   " } }, { id: "build", title: "Build" }),
    "<div></div>",
  );
});

test("path text fields stay left-to-right in RTL locales", async () => {
  globalThis.localStorage = {
    getItem() {
      return null;
    },
    setItem() {},
  };
  globalThis.window = { innerHeight: 900 };

  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderConfigEditor, renderTextControl } = await import("../dist/web/src/client/view.js");
  Object.assign(state, createInitialState(), {
    labels: {
      layoutDirection: "rtl",
      chooseButtonTitle: "Choose...",
      loadButtonTitle: "Load",
      saveButtonTitle: "Save",
      settingsFileLabel: "Settings file",
    },
    configFilePaths: {
      settings: "/tmp/config.toml",
    },
    configValues: {
      "settings.output_dir": "/tmp/output",
    },
  });

  const pathControlHTML = renderTextControl({ id: "input_bam", kind: "path", label: "Input BAM", value: "/tmp/input.bam" });
  const textControlHTML = renderTextControl({ id: "sample_name", kind: "text", label: "Sample", value: "NA12878" });
  const configHTML = renderConfigEditor({
    id: "settings",
    kind: "configEditor",
    label: "Settings",
    configFile: { path: "/tmp/config.toml" },
    settings: [{ id: "output_dir", kind: "path", label: "Output directory" }],
  });

  assert.match(pathControlHTML, /class="path-input" dir="ltr"/);
  assert.doesNotMatch(textControlHTML, /dir="ltr"/);
  assert.match(configHTML, /class="mono path-input" dir="ltr"/);
  assert.match(configHTML, /class="path-input" dir="ltr"/);
});

test("config editor names its settings file once, with the control's label", async () => {
  // Regression: the Settings page read "WGS Extract Settings File Settings File".
  globalThis.localStorage = { getItem() { return null; }, setItem() {} };
  globalThis.window = { innerHeight: 900 };

  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderConfigEditor } = await import("../dist/web/src/client/view.js");
  Object.assign(state, createInitialState(), {
    labels: { loadButtonTitle: "Load", saveButtonTitle: "Save", settingsFileLabel: "Settings File" },
  });

  const html = renderConfigEditor({
    id: "wgs_settings",
    kind: "configEditor",
    label: "WGS Extract Settings File",
    configFile: { path: "/tmp/config.toml" },
    settings: [],
  });
  const text = html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

  assert.match(text, /WGS Extract Settings File/);
  assert.doesNotMatch(text, /Settings File\s+Settings File/);
  assert.equal(text.match(/Settings File/g)?.length, 1);
});

test("a lone checkbox named like its group shows its name once", async () => {
  // Regression: optional checkboxes showed the same text as a heading above and beside the box.
  globalThis.localStorage = { getItem() { return null; }, setItem() {} };
  globalThis.window = { innerHeight: 900 };

  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderCheckboxGroup } = await import("../dist/web/src/client/view.js");
  Object.assign(state, createInitialState());
  const visibleText = (html) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

  const lone = renderCheckboxGroup({
    id: "extract_poz",
    kind: "checkboxGroup",
    label: "Restrict Y BAM to Poz regions",
    tooltip: "Only Poz regions.",
    options: [{ id: "--poz", title: "Restrict Y BAM to Poz regions" }],
  });
  assert.equal(visibleText(lone).match(/Restrict Y BAM to Poz regions/g)?.length, 1);
  assert.match(lone, /data-tooltip="Only Poz regions\."/);
  assert.match(lone, /data-check-group="extract_poz" value="--poz"/);

  const named = renderCheckboxGroup({
    id: "formats",
    kind: "checkboxGroup",
    label: "Formats",
    options: [{ id: "23andme", title: "23andMe v5" }],
  });
  assert.match(visibleText(named), /^Formats 23andMe v5$/);
});

test("a control's help shows only in its info tooltip, not also as a native title", async () => {
  // Regression: hovering an info circle on Windows showed the app's tooltip and an unwrapped native one.
  globalThis.localStorage = { getItem() { return null; }, setItem() {} };
  globalThis.window = { innerHeight: 900 };

  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderBundleHeader, renderControl } = await import("../dist/web/src/client/view.js");
  Object.assign(state, createInitialState(), {
    labels: { chooseButtonTitle: "Choose..." },
    manifest: { id: "demo", displayName: "Demo", summary: "Demo bundle summary.", pages: [] },
  });

  const control = renderControl(
    { id: "sample", kind: "text", label: "Sample", tooltip: "Sample name help." },
    { fieldValues: {}, checkedOptions: {}, configValues: {}, rowValues: {} },
    { pageID: "page", sectionID: "section" },
  );
  const header = renderBundleHeader();

  assert.equal(control.match(/data-tooltip="Sample name help\."/g)?.length, 1);
  assert.doesNotMatch(control, /\stitle="Sample name help\./);
  assert.equal(header.match(/data-tooltip="Demo bundle summary\."/g)?.length, 1);
  assert.doesNotMatch(header, /\stitle="Demo bundle summary\./);
});

test("path fields show workspace paths as {{bundleWorkspace}}", async () => {
  globalThis.localStorage = { getItem() { return null; }, setItem() {} };
  globalThis.window = { innerHeight: 900 };

  const workspace = "/home/me/gui-for-cli/BundleWorkspaces/wgs-extract";
  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderConfigEditor, renderTextControl } = await import("../dist/web/src/client/view.js");
  Object.assign(state, createInitialState(), {
    bundleRootPath: workspace,
    labels: { chooseButtonTitle: "Choose...", loadButtonTitle: "Load", saveButtonTitle: "Save" },
    fieldValues: { out_dir: `${workspace}/output`, other_dir: "/data/genomes", sample: `${workspace}/output` },
    configFilePaths: { wgs_settings: `${workspace}/settings/config.toml` },
    configValues: { "wgs_settings.ref_path": `${workspace}/reference` },
  });

  const pathField = renderTextControl({ id: "out_dir", kind: "path", label: "Output Directory" });
  const outsideField = renderTextControl({ id: "other_dir", kind: "path", label: "Genomes" });
  const textField = renderTextControl({ id: "sample", kind: "text", label: "Sample" });
  const config = renderConfigEditor({
    id: "wgs_settings",
    kind: "configEditor",
    label: "WGS Extract Settings File",
    configFile: { path: "{{bundleWorkspace}}/settings/config.toml" },
    settings: [{ id: "ref_path", kind: "path", label: "Reference Library" }],
  });

  assert.match(pathField, /value="\{\{bundleWorkspace\}\}\/output"/);
  assert.match(outsideField, /value="\/data\/genomes"/);
  assert.match(textField, new RegExp(`value="${workspace}/output"`));
  assert.match(config, /value="\{\{bundleWorkspace\}\}\/settings\/config.toml"/);
  assert.match(config, /value="\{\{bundleWorkspace\}\}\/reference"/);
  assert.doesNotMatch(config, new RegExp(`value="${workspace}`));
});

test("renders the Basic / Advanced switch as a radio group with only the current mode checked", async () => {
  globalThis.localStorage = { getItem() { return null; }, setItem() {} };
  globalThis.window = { innerHeight: 900 };
  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderUIModeSwitch } = await import("../dist/web/src/client/view.js");
  Object.assign(state, createInitialState(), { labels: { uiModeBasicLabel: "Basic", uiModeAdvancedLabel: "Advanced" } });

  assert.equal(state.uiMode, "basic");
  let html = renderUIModeSwitch();
  assert.match(html, /role="radiogroup"/);
  assert.match(html, /data-ui-mode="basic"[^>]*>Basic</);
  assert.match(html, /aria-checked="true"[^>]*\s+data-ui-mode="basic"/);
  assert.match(html, /aria-checked="false"[^>]*\s+data-ui-mode="advanced"/);

  state.uiMode = "advanced";
  html = renderUIModeSwitch();
  assert.match(html, /aria-checked="false"[^>]*\s+data-ui-mode="basic"/);
  assert.match(html, /aria-checked="true"[^>]*\s+data-ui-mode="advanced"/);
});

test("renders a rows-layout checkbox group under category headings, with advanced options only in Advanced", async () => {
  globalThis.localStorage = { getItem() { return null; }, setItem() {} };
  globalThis.window = { innerHeight: 900 };
  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderCheckboxGroup, renderDropdownControl } = await import("../dist/web/src/client/view.js");
  Object.assign(state, createInitialState(), { labels: {} });
  const control = {
    id: "formats",
    kind: "checkboxGroup",
    label: "Formats",
    layout: "rows",
    options: [
      { id: "all", title: "v3", group: "Combined", category: "WGSE", selected: true },
      { id: "23andme_v3", title: "v3", group: "23andMe", category: "Consumer Tests" },
      { id: "23andme_api", title: "API", group: "23andMe", category: "Consumer Tests" },
      { id: "myheritage_gsa", title: "GSA 2025 (marker-only)", group: "MyHeritage", category: "Consumer Tests", advancedOnly: true },
    ],
  };

  let html = renderCheckboxGroup(control);
  assert.match(html, /<h4>WGSE<\/h4>[\s\S]*option-row-title">Combined<[\s\S]*<h4>Consumer Tests<\/h4>[\s\S]*option-row-title">23andMe</);
  assert.match(html, /value="all" checked/);
  assert.match(html, /value="23andme_api"/);
  assert.doesNotMatch(html, /myheritage_gsa|MyHeritage/);

  state.uiMode = "advanced";
  html = renderCheckboxGroup(control);
  assert.match(html, /option-row-title">MyHeritage<[\s\S]*option-columns wide[\s\S]*value="myheritage_gsa"/);

  const dropdown = renderDropdownControl({ id: "ref_fasta", kind: "dropdown", label: "Reference Genome", options: [], emptyText: "None loaded. See Library page to add." });
  assert.match(dropdown, /<option value="" selected disabled>None loaded\. See Library page to add\.<\/option>/);
});

test("rows-layout category and group headings show the tooltips their options give", async () => {
  globalThis.localStorage = { getItem() { return null; }, setItem() {} };
  globalThis.window = { innerHeight: 900 };
  const { createInitialState, state } = await import("../dist/web/src/client/state.js");
  const { renderCheckboxGroup } = await import("../dist/web/src/client/view.js");
  Object.assign(state, createInitialState(), { labels: {} });
  const options = [
    { id: "23andme_v3", title: "v3", group: "23andMe", category: "Consumer Tests", categoryTooltip: "Generate RAW files." },
    { id: "aadr_ho", title: "HOv1", group: "AADR", category: "Reich Lab" },
    { id: "aadr_1240k", title: "1240K", group: "AADR", category: "Reich Lab", groupTooltip: "Each is a superset.", categoryTooltip: "PLINK + EIGENSTRAT." },
  ];

  let html = renderCheckboxGroup({ id: "formats", kind: "checkboxGroup", label: "Formats", layout: "rows", options });
  assert.match(html, /<h4>Consumer Tests<span class="tooltip"[^>]*data-tooltip="Generate RAW files\.">i<\/span><\/h4>/);
  assert.match(html, /<h4>Reich Lab<span class="tooltip"[^>]*data-tooltip="PLINK \+ EIGENSTRAT\.">i<\/span><\/h4>/);
  assert.match(html, /option-row-title">AADR<span class="tooltip"[^>]*data-tooltip="Each is a superset\.">i<\/span><\/span>/);
  assert.match(html, /option-row-title">23andMe<\/span>/);

  html = renderCheckboxGroup({ id: "formats", kind: "checkboxGroup", label: "Formats", options });
  assert.match(html, /<h4>AADR<span class="tooltip"[^>]*data-tooltip="Each is a superset\.">i<\/span><\/h4>/);
  assert.match(html, /<h4>23andMe<\/h4>/);
});
