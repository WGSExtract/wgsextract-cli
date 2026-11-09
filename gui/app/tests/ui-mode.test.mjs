import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { commandContextFromState, placeholdersIn } from "../dist/shared/rendering.js";
import { basicModeHiddenInputs, visibleControls, visibleOptions, visiblePages, visibleSections } from "../dist/shared/ui-mode.js";

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const wgsExtract = path.resolve(appRoot, "..", "examples", "WGSExtract");

function wgsExtractManifest() {
  const manifest = JSON.parse(readFileSync(path.join(wgsExtract, "manifest.json"), "utf8").replace(/^\s*\/\/.*$/gm, ""));
  return { ...manifest, pages: manifest.pages.map((name) => JSON.parse(readFileSync(path.join(wgsExtract, "pages", name), "utf8"))) };
}

const sample = {
  pages: [
    {
      id: "common",
      title: "Common",
      sections: [
        {
          id: "inputs",
          controls: [
            { id: "input", kind: "path" },
            { id: "region", kind: "text", advancedOnly: true },
            { id: "library", kind: "path", advancedOnly: true },
            { id: "flags", kind: "checkboxGroup", advancedOnly: true, options: [{ id: "--on", selected: true }, { id: "--extra" }] },
            {
              id: "formats",
              kind: "checkboxGroup",
              options: [{ id: "v3", selected: true }, { id: "v5" }, { id: "long", advancedOnly: true }],
            },
          ],
          actions: [{ id: "run", command: { executable: "tool", arguments: ["{{input}}"] } }],
        },
        { id: "tuning", controls: [{ id: "depth", kind: "text", advancedOnly: true }] },
        { id: "expert", advancedOnly: true, controls: [{ id: "expert_only", kind: "text" }] },
      ],
    },
    { id: "hidden", title: "Hidden", advancedOnly: true, sections: [{ id: "s", controls: [{ id: "x", kind: "text" }] }] },
    { id: "emptied", title: "Emptied", sections: [{ id: "s", actions: [{ id: "a", advancedOnly: true, command: { executable: "tool" } }] }] },
  ],
};

test("Basic hides advancedOnly pages, sections, controls and options, and pages or sections it leaves empty", () => {
  assert.deepEqual(visiblePages(sample, "basic").map((page) => page.id), ["common"]);
  assert.deepEqual(visiblePages(sample, "advanced").map((page) => page.id), ["common", "hidden", "emptied"]);

  const [page] = sample.pages;
  assert.deepEqual(visibleSections(page, "basic").map((section) => section.id), ["inputs"]);
  assert.deepEqual(visibleSections(page, "advanced").map((section) => section.id), ["inputs", "tuning", "expert"]);

  const [inputs] = page.sections;
  assert.deepEqual(visibleControls(inputs, "basic").map((control) => control.id), ["input", "formats"]);
  assert.deepEqual(visibleOptions(inputs.controls[4].options, "basic").map((option) => option.id), ["v3", "v5"]);
  assert.equal(visibleOptions(inputs.controls[4].options, "advanced").length, 3);
});

test("Basic runs commands with hidden settings at their defaults and hidden options unchecked, keeping hidden paths", () => {
  const state = {
    manifest: sample,
    fieldValues: { input: "/data/sample.bam", region: "chrY", library: "/refs", depth: "30" },
    checkedOptions: { flags: new Set(["--extra"]), formats: new Set(["v5", "long"]) },
  };

  const basic = commandContextFromState({ ...state, uiMode: "basic" });
  assert.equal(basic.fieldValues.input, "/data/sample.bam");
  assert.equal(basic.fieldValues.region, "");
  assert.equal(basic.fieldValues.depth, "");
  assert.equal(basic.fieldValues.library, "/refs");
  assert.equal(basic.checkedOptions.flags, "--on");
  assert.equal(basic.checkedOptions.formats, "v5");

  const advanced = commandContextFromState({ ...state, uiMode: "advanced" });
  assert.equal(advanced.fieldValues.region, "chrY");
  assert.equal(advanced.checkedOptions.flags, "--extra");
  assert.equal(advanced.checkedOptions.formats, "long,v5");
  // The saved values themselves are untouched.
  assert.deepEqual([...state.checkedOptions.formats], ["v5", "long"]);
});

test("a control Basic shows anywhere keeps its value in Basic", () => {
  const manifest = {
    pages: [
      { id: "a", sections: [{ id: "s", controls: [{ id: "shared", kind: "text", advancedOnly: true }] }] },
      { id: "b", sections: [{ id: "s", controls: [{ id: "shared", kind: "text" }] }] },
    ],
  };
  assert.equal(basicModeHiddenInputs(manifest).controls.has("shared"), false);
});

test("every WGS Extract action shown in Basic can get its required inputs in Basic", () => {
  const manifest = wgsExtractManifest();
  const controls = new Map();
  for (const page of manifest.pages) {
    for (const section of page.sections ?? []) {
      for (const control of section.controls ?? []) {
        controls.set(control.id, control);
      }
    }
  }
  const problems = [];
  for (const page of visiblePages(manifest, "basic")) {
    const shownOnPage = new Set(visibleSections(page, "basic").flatMap((section) => visibleControls(section, "basic").map((control) => control.id)));
    for (const section of visibleSections(page, "basic")) {
      for (const action of (section.actions ?? []).filter((candidate) => candidate.advancedOnly !== true)) {
        for (const placeholder of placeholdersIn(action.command?.arguments ?? [])) {
          const control = controls.get(placeholder);
          // Paths (the Reference Library) keep their value when hidden; anything else must be on the page to be set.
          if (control && control.kind !== "path" && !shownOnPage.has(placeholder) && !control.value) {
            problems.push(`${page.id}/${action.id} needs ${placeholder}`);
          }
        }
      }
    }
  }
  assert.deepEqual(problems, []);
});

test("WGS Extract starts Basic with the common pages and keeps the expert ones for Advanced", () => {
  const manifest = wgsExtractManifest();
  const basicPages = visiblePages(manifest, "basic").map((page) => page.id);
  assert.ok(!basicPages.includes("annotate"));
  for (const id of ["datasets", "fastq", "info-bam", "vcf", "extract", "microarray", "ancestry", "library", "reference-genomes", "settings"]) {
    assert.ok(basicPages.includes(id), id);
  }
  assert.equal(visiblePages(manifest, "advanced").length, manifest.pages.length);
});

test("the CLI features added for Advanced mode stay out of Basic", () => {
  const manifest = wgsExtractManifest();
  const leaks = [];
  for (const page of visiblePages(manifest, "basic")) {
    for (const section of visibleSections(page, "basic")) {
      for (const control of visibleControls(section, "basic")) {
        if (String(control.label).startsWith("controls.cli.")) leaks.push(`${page.id}/${control.id}`);
      }
      for (const action of section.actions ?? []) {
        if (action.advancedOnly !== true && String(action.title).startsWith("actions.cli.")) leaks.push(`${page.id}/${action.id}`);
      }
    }
  }
  assert.deepEqual(leaks, []);
  const workflows = manifest.pages.find((page) => page.id === "workflows");
  assert.equal(workflows.advancedOnly, true);
  assert.ok(visiblePages(manifest, "advanced").some((page) => page.id === "workflows"));
});

test("every WGS Extract list with a row template fills each of its columns", () => {
  const missing = [];
  for (const page of wgsExtractManifest().pages) {
    for (const section of page.sections ?? []) {
      for (const control of section.controls ?? []) {
        const values = control.rowTemplate?.values;
        if (!values) continue;
        for (const column of control.columns ?? []) {
          if (!["name", "status"].includes(column.id) && !(column.id in values)) missing.push(`${page.id}/${control.id}/${column.id}`);
        }
      }
    }
  }
  assert.deepEqual(missing, []);
});

test("WGS Extract asks before an Example Datasets download over 25 GB", async () => {
  const { isActionVisible } = await import("../dist/shared/rendering-conditions.js");
  const datasets = wgsExtractManifest().pages.find((page) => page.id === "datasets");
  const actions = datasets.sections.find((section) => section.id === "example-datasets").actions
    .filter((action) => action.command?.arguments?.includes("download"));
  const shown = (example_all, example_tag) => actions.filter((action) => isActionVisible(action, {
    fieldValues: { example_tag }, checkedOptions: { example_all },
  }));
  for (const [all, tag, id, confirms] of [
    ["", "", "example-download", false],
    ["", "smallest", "example-download", false],
    ["", "fastq", "example-download", false],
    ["--all", "", "example-download-all", true],
    ["", "pacbio", "example-download-large-tag", true],
    ["", "giab", "example-download-large-tag", true],
  ]) {
    const visible = shown(all, tag);
    assert.deepEqual(visible.map((action) => action.id), [id], `${all} ${tag}`);
    assert.equal(Boolean(visible[0].confirm), confirms, `${all} ${tag}`);
  }
});
