import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { readFile } from "node:fs/promises";
import { parseJsonWithComments } from "../dist/shared/json-comments.js";
import { conditionMatches, displayCommand, hydrateRows, isActionVisible, isSectionVisible, missingPlaceholders } from "../dist/shared/rendering.js";
import { readsLastResultDocument } from "../dist/shared/last-result-documents.js";
import { currentSetupPlatform } from "../dist/shared/setup-platforms.js";
import { loadLocalizedBundle } from "../dist/web/src/server/bundle-loader.js";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const conformanceBundleRoot = path.join(repoRoot, "tests/conformance/basic-bundle");
const wgsExtractBundleRoot = path.join(repoRoot, "examples/WGSExtract");

function assertWgsExtractReleaseCompatible(tag, pages) {
  const match = /^v(\d+)\.(\d+)\.(\d+)$/.exec(tag);
  assert.ok(match, `Invalid bundled WGSExtract CLI release tag: ${tag}`);
  const [major, minor, patch] = match.slice(1).map(Number);
  const supportsNewFeatures = major > 0 || minor > 6 || (minor === 6 && patch >= 4);
  const newFormats = new Set([
    "myheritage_gsa_2025", "myheritage_gsa_2025_609424", "ancestry_v2_2021", "ancestry_v2_2024",
    "aadr_1240k", "aadr_ho", "aadr_1240k_ho", "aadr_2m",
  ]);
  const newSettings = new Set(["atomic_outputs", "keep_awake", "temp_directory", "log_dir"]);
  const newFlags = new Set([
    "--poz", "--ybrowse", "--fastq", "--bins", "--tree-links", "--scatter", "--indel-manifest", "--keep-fastq",
    "--min-gq", "--min-dp", "--strand-ambiguous", "--resolve-rsids", "--no-assume-hom-ref",
    "--exclude-filtered", "--call-reasons", "--aadr-sample-id", "--aadr-sex",
  ]);
  const features = [];
  function inspect(value) {
    if (typeof value === "string") {
      if (value.includes(".post-v063.") || newFormats.has(value) || newSettings.has(value) || newFlags.has(value)) {
        features.push(value);
      }
    } else if (value && typeof value === "object") {
      for (const child of Object.values(value)) inspect(child);
    }
  }
  inspect(pages);
  assert.ok(supportsNewFeatures || features.length === 0,
    `Post-v0.6.3 GUI controls/actions/formats require bundled CLI >= v0.6.4; found ${tag}: ${features.slice(0, 5).join(", ")}. Land the real release bump before merging.`);
}

test("WGSExtract post-v0.6.3 controls/actions/formats require a v0.6.4-or-newer bundle pin", async () => {
  const manifest = parseJsonWithComments(await readFile(path.join(wgsExtractBundleRoot, "manifest.json"), "utf8"));
  const pages = await Promise.all(manifest.pages.map(async (file) =>
    parseJsonWithComments(await readFile(path.join(wgsExtractBundleRoot, "pages", file), "utf8"))));
  const tag = (await readFile(path.join(wgsExtractBundleRoot, "scripts/wgsextract-release-tag.txt"), "utf8")).trim();
  assertWgsExtractReleaseCompatible(tag, pages);
});

test("WGSExtract release conformance rejects unsupported features without blocking older pages", () => {
  const pages = [{ sections: [{ controls: [{ label: "controls.fastq.post-v063.multiqc-file.label" }] }] }];
  assert.throws(() => assertWgsExtractReleaseCompatible("v0.6.3", pages), /require bundled CLI >= v0.6.4/);
  assert.throws(() => assertWgsExtractReleaseCompatible("v0.6.3", [{ options: [{ id: "aadr_1240k" }] }]), /require bundled CLI/);
  assert.doesNotThrow(() => assertWgsExtractReleaseCompatible("v0.6.3", [{ id: "original-page" }]));
  assert.doesNotThrow(() => assertWgsExtractReleaseCompatible("v0.6.4", pages));
  assert.doesNotThrow(() => assertWgsExtractReleaseCompatible("v0.7.0", pages));
});

test("WGSExtract v0.8.6 controls start unset and platform-limited sections hide on Windows", async () => {
  const bundle = await loadLocalizedBundle("en", repoRoot, wgsExtractBundleRoot, wgsExtractBundleRoot);
  const pages = bundle.manifest.pages;
  const sections = pages.flatMap((page) => page.sections ?? []);
  const controls = sections.flatMap((section) => section.controls ?? []);
  for (const id of ["deepvariant_model", "sv_read_type", "methylation_context", "methylation_5hmc", "methylation_combine_strands", "y_str_panel", "yleaf_tree"]) {
    const control = controls.find((candidate) => candidate.id === id);
    assert.ok(control, `${id} exists`);
    assert.equal(control.value, undefined, `${id} has no default value`);
    assert.equal(control.options[0].id, "", `${id} starts on its unset choice`);
    assert.equal(control.options.some((option) => option.selected), false, `${id} selects nothing`);
  }
  assert.deepEqual(
    controls.find((control) => control.id === "sv_read_type").options.map((option) => option.id),
    ["", "--ont", "--long-read"],
  );
  assert.ok(controls.find((control) => control.id === "deepvariant_model").options.some((option) => option.id === "ONT_R104"));

  const context = { fieldValues: { bam_path: "/tmp/sample.bam" }, checkedOptions: {}, configValues: {}, rowValues: {}, bundleRootPath: "/bundle" };
  const calling = sections.find((section) => section.id === "variant-calling");
  const svCommand = displayCommand(calling.actions.find((action) => action.id === "vcf-sv").command, context);
  const deepVariantCommand = displayCommand(calling.actions.find((action) => action.id === "vcf-deepvariant").command, context);
  assert.ok(!/--ont|--long-read|--caller|--tandem-repeats/.test(svCommand), svCommand);
  assert.ok(!/--ont|--model-type/.test(deepVariantCommand), deepVariantCommand);

  const methylation = sections.find((section) => section.id === "methylation");
  const pileup = displayCommand(methylation.actions[0].command, context);
  assert.ok(!/--context|5hmc|combine-strands/.test(pileup), pileup);

  for (const id of ["clair3", "methylation"]) {
    const section = sections.find((candidate) => candidate.id === id);
    assert.deepEqual(section.visibleWhen, [{ placeholder: "hostPlatform", notEquals: "windows" }]);
    assert.equal(isSectionVisible(section, context), currentSetupPlatform() !== "windows");
  }
  assert.equal(isSectionVisible(calling, context), true);
  assert.equal(conditionMatches({ placeholder: "hostPlatform", equals: currentSetupPlatform() }, context), true);
});

test("PR #530 review and #528: WGSExtract's bug report preview and GitHub report target the app's last run, never themselves", async () => {
  const bundle = await loadLocalizedBundle("en", repoRoot, wgsExtractBundleRoot, wgsExtractBundleRoot);
  const section = bundle.manifest.pages.flatMap((page) => page.sections ?? []).find((candidate) => candidate.id === "report-issue");
  const paths = { last: "/w/.last-results/last.json", lastFailed: "/w/.last-results/last-failed.json" };
  const shown = (lastFailedChecked, lastResultDocumentPaths) => {
    const context = { fieldValues: { report_description: "Crashed" }, checkedOptions: { report_last_failed: lastFailedChecked ? "--last-failed" : "" }, configValues: {}, rowValues: {}, lastResultDocumentPaths };
    const visible = section.actions.filter((action) => isActionVisible(action, context));
    assert.deepEqual(visible.map((action) => action.title), ["Preview Bug Report", "Open Bug Report on GitHub"]);
    assert.ok(visible.every(readsLastResultDocument));
    // #528: opening the browser is only offered behind a confirmation saying that GitHub receives the report but no issue is created until the user submits it.
    assert.equal(visible[0].confirm, undefined);
    assert.match(visible[1].confirm.message, /GitHub receives the report text in the link\. No issue is created until/);
    const [preview, open] = visible.map((action) => displayCommand(action.command, context).replace(/^\S+ /, ""));
    // contract: --open is the CLI's only way to open the browser without its terminal prompt.
    assert.equal(open, preview.replace("--print", "--open"));
    return preview;
  };

  assert.equal(shown(false, paths), `report-issue --print --run ${paths.last} --description Crashed`);
  assert.equal(shown(true, paths), `report-issue --print --run ${paths.lastFailed} --description Crashed`);
  // Before the app has run anything, the CLI's own record is the only one.
  assert.equal(shown(false, {}), "report-issue --print --description Crashed");
  assert.equal(shown(true, {}), "report-issue --print --last-failed --description Crashed");
});

test("conformance bundle preserves shared runtime semantics in TypeScript", async () => {
  const bundle = await loadLocalizedBundle("en", repoRoot, conformanceBundleRoot, conformanceBundleRoot);
  const { manifest } = bundle;

  assert.equal(manifest.id, "conformance-basic");
  assert.equal(manifest.displayName, "Conformance Basic");
  assert.equal(manifest.summary, "Exercises common bundle runtime semantics.");
  assert.equal(manifest.textIcon, "🧪");
  assert.equal(manifest.sidebarIconStyle, "emoji");
  assert.equal(manifest.terminalTextDirection, "ltr");
  assert.deepEqual(manifest.pages.map((page) => page.id), ["main"]);
  assert.equal(manifest.pages[0].title, "Main");
  assert.equal(manifest.pages[0].sidebarGroup, "Main Group");
  assert.equal(manifest.setup.steps[0].label, "Install dependencies");

  assert.deepEqual(
    manifest.exitCodeReference.find((entry) => entry.code === 7),
    { code: 7, title: "Custom warning", summary: "A custom warning exit code.", severity: "warning" },
  );
  assert.equal(manifest.exitCodeReference.find((entry) => entry.code === 127).title, "Command not found");

  const section = manifest.pages[0].sections[0];
  const input = section.controls.find((control) => control.id === "input_path");
  const refs = section.controls.find((control) => control.id === "refs");
  const settings = section.controls.find((control) => control.id === "settings");
  const run = section.actions.find((action) => action.id === "run");

  assert.equal(input.label, "Input BAM");
  assert.equal(input.value, "/tmp/input.bam");
  assert.equal(settings.configFile.path, "{{bundleWorkspace}}/settings/config.toml");
  assert.equal(settings.configFile.bootstrap.mode, "createIfMissing");
  assert.equal(settings.settings[0].key, "output_dir");
  assert.equal(bundle.fieldValues.input_path, "/tmp/input.bam");
  assert.equal(bundle.configValues["settings.out_dir"], "out");

  const rows = hydrateRows(refs);
  assert.deepEqual(rows, [
    {
      id: "hg38",
      title: "GRCh38",
      values: { code: "hs38", status: "installed" },
      status: "installed",
      tags: [{ id: "recommended", title: "Recommended", style: "primary" }],
      tooltip: undefined,
    },
  ]);

  const context = {
    fieldValues: { input_path: "/tmp/input.bam", "library.ready": "true" },
    checkedOptions: {},
    configValues: { out_dir: "out" },
    rowValues: { ...rows[0].values, id: rows[0].id, status: rows[0].status, locked: "false" },
    bundleRootPath: conformanceBundleRoot,
  };

  assert.equal(conditionMatches(run.visibleWhen[0], context), true);
  assert.equal(conditionMatches(run.disabledWhen[0], context), false);
  assert.equal(displayCommand(run.command, context), "tool run /tmp/input.bam out");

  const rowAction = refs.rowActions[0];
  assert.equal(conditionMatches(rowAction.visibleWhen[0], context), true);
  assert.equal(conditionMatches(rowAction.disabledWhen[0], context), false);
  assert.equal(displayCommand(rowAction.command, context), "tool verify hs38 /tmp/input.bam");
});

test("conformance bundle applies requested localization overlays in TypeScript", async () => {
  const bundle = await loadLocalizedBundle("es", repoRoot, conformanceBundleRoot, conformanceBundleRoot);

  assert.equal(bundle.localizationCode, "es");
  assert.equal(bundle.manifest.displayName, "Conformidad básica");
  assert.equal(bundle.manifest.pages[0].title, "Principal");
  assert.equal(bundle.manifest.pages[0].sections[0].actions[0].title, "Ejecutar flujo");
  assert.equal(bundle.manifest.summary, "Ejercita semánticas comunes de ejecución de paquetes.");
});

test("WGSExtract exposes genome library controls in TypeScript", async () => {
  const bundle = await loadLocalizedBundle("en", repoRoot, wgsExtractBundleRoot, wgsExtractBundleRoot);
  const library = bundle.manifest.pages.find((page) => page.id === "library");
  const settingsPage = bundle.manifest.pages.find((page) => page.id === "settings");

  assert.ok(library, "library page exists");
  assert.ok(settingsPage, "settings page exists");
  assert.equal(library.sidebarPlacement, "bottom");
  assert.equal(settingsPage.sidebarPlacement, "bottom");
  const libraryPaths = library.sections.find((section) => section.id === "library-paths");
  assert.ok(libraryPaths, "library paths section exists");
  assert.ok(libraryPaths.controls.find((control) => control.id === "ref_path"), "reference data path control exists");
  const genomesPage = bundle.manifest.pages.find((page) => page.id === "reference-genomes");
  assert.ok(genomesPage, "reference genomes page exists");
  assert.equal(genomesPage.sidebarPlacement, "bottom");
  assert.ok(genomesPage.sections.find((section) => section.id === "genome-management"), "genome management moved to Reference Genomes");
  assert.ok(!library.sections.some((section) => section.id === "genome-management"));
  const datasets = bundle.manifest.pages.find((page) => page.id === "datasets");
  assert.equal(datasets.title, "Datasets");
  assert.equal(datasets.sidebarPlacement, "bottom");
  assert.equal(datasets.advancedOnly, undefined);
  const pageOrder = bundle.manifest.pages.map((page) => page.id);
  assert.equal(pageOrder.indexOf("datasets") + 1, pageOrder.indexOf("library"), "Datasets sits just above Reference Data");
  assert.ok(pageOrder.indexOf("annotate") < pageOrder.indexOf("workflows"), "Annotate sits above Workflows");
  const datasetPaths = datasets.sections.find((section) => section.id === "dataset-paths");
  assert.equal(datasetPaths.title, "Library Paths");
  const genomeLibraryControl = datasetPaths.controls.find((control) => control.id === "genome_library");
  assert.equal(genomeLibraryControl.label, "Dataset Library Path");
  assert.ok(genomeLibraryControl, "genome library path control exists");
  assert.equal(genomeLibraryControl.kind, "path");

  const databaseTools = library.sections.find((section) => section.id === "databases-tools");
  assert.ok(databaseTools, "databases and tools section exists");
  assert.equal(databaseTools.dataSource.path, "scripts/library-state.sh");
  assert.deepEqual(databaseTools.dataSource.arguments, ["{{ref_path}}", "{{genome_library}}"]);
  assert.ok(databaseTools.actions.find((action) => action.id === "annotation-vcf-download"));
  assert.ok(databaseTools.actions.find((action) => action.id === "spliceai-download"));
  assert.ok(databaseTools.actions.find((action) => action.id === "alphamissense-download"));
  assert.ok(databaseTools.actions.find((action) => action.id === "pharmgkb-download"));
  assert.equal(
    library.sections.some((section) => section.id === "test-dataset"),
    false,
  );
  const testDataset = datasets.sections.find((section) => section.id === "test-dataset");
  assert.deepEqual(testDataset.dataSource, databaseTools.dataSource);
  assert.ok(!databaseTools.actions.some((action) => action.id.startsWith("test-")));
  assert.ok(datasets.sections.find((section) => section.id === "example-datasets"), "Example Datasets are on Datasets");
  const downloadAction = testDataset.actions.find((action) => action.id === "test-dataset-download");
  assert.ok(downloadAction, "test genome download action exists");
  assert.deepEqual(
    downloadAction.command.arguments,
    ["download", "{{genome_library}}"],
  );
  const deleteAction = testDataset.actions.find((action) => action.id === "test-dataset-delete");
  assert.ok(deleteAction, "test genome delete action exists");
  assert.deepEqual(
    deleteAction.command.arguments,
    ["delete", "{{genome_library}}"],
  );
  assert.ok(deleteAction.confirm);

  const installStep = bundle.manifest.setup.steps.find((step) => step.id === "install-wgsextract");
  assert.equal(installStep.toolName, "WGS Extract CLI");
  assert.equal(installStep.toolVersion, "v0.8.6");
  assert.equal(installStep.toolVersionFile, "scripts/wgsextract-release-tag.txt");
  assert.equal(bundle.manifest.setup.initialInstallSizeGB, 6);

  const settings = settingsPage.sections[0].controls[0];
  const genomeLibrarySetting = settings.settings.find((setting) => setting.id === "genome_library");
  assert.ok(genomeLibrarySetting, "genome library setting exists");
  assert.equal(genomeLibrarySetting.key, "genome_library");
  const bamPath = bundle.manifest.pages
    .find((page) => page.id === "info-bam")
    .sections[0].controls.find((control) => control.id === "bam_path");
  assert.equal(bamPath.defaultDirectory, "{{genome_library}}");
  const fastqAlign = bundle.manifest.pages
    .find((page) => page.id === "extract")
    .sections.find((section) => section.id === "fastq-align")
    .actions.find((action) => action.id === "align");
  assert.equal(fastqAlign.estimatedDurationMinutes, 540);
  assert.deepEqual(fastqAlign.precheck, {
    diskSpaceGB: "{{fastq_r1.fileSizeGB}} * 8",
    diskSpacePath: "{{out_dir}}",
  });
  const calculateCoverage = bundle.manifest.pages
    .find((page) => page.id === "info-bam")
    .sections.find((section) => section.id === "info-commands")
    .actions.find((action) => action.id === "calculate-coverage");
  assert.equal(calculateCoverage.estimatedDurationMinutes, 120);
  const variantCalling = bundle.manifest.pages
    .find((page) => page.id === "extract")
    .sections.find((section) => section.id === "variant-calling");
  const snpAction = variantCalling.actions.find((action) => action.id === "vcf-snp");
  assert.equal(snpAction.command.executable, "{{bundleRoot}}/scripts/run-wgsextract-vcf.sh");
  assert.deepEqual(snpAction.command.arguments.slice(0, 2), ["snp", "--input"]);
  assert.equal(snpAction.estimatedDurationMinutes, 34);
  const indelAction = variantCalling.actions.find((action) => action.id === "vcf-indel");
  assert.equal(indelAction.command.executable, "{{bundleRoot}}/scripts/run-wgsextract-vcf.sh");
  assert.deepEqual(indelAction.command.arguments.slice(0, 2), ["indel", "--input"]);
  assert.equal(indelAction.estimatedDurationMinutes, 34);
  const defaultVcfPath = settings.settings.find((setting) => setting.id === "vcf_path");
  assert.equal(defaultVcfPath.defaultDirectory, "{{genome_library}}");
  const microarrayAction = bundle.manifest.pages
    .find((page) => page.id === "microarray")
    .sections.find((section) => section.id === "microarray-formats")
    .actions.find((action) => action.id === "microarray-generate");
  assert.equal(microarrayAction.command.executable, "{{bundleRoot}}/scripts/run-wgsextract.sh");
  assert.deepEqual(microarrayAction.command.arguments.slice(0, 3), ["microarray", "--input", "{{bam_path}}"]);
  assert.deepEqual(microarrayAction.command.arguments.slice(3, 5), ["--ref", "{{ref_fasta}}"]);
  const microarrayRefControl = bundle.manifest.pages
    .find((page) => page.id === "microarray")
    .sections.find((section) => section.id === "microarray-inputs")
    .controls.find((control) => control.id === "ref_fasta");
  assert.equal(microarrayRefControl.kind, "dropdown");
  assert.equal(microarrayRefControl.emptyText, "None loaded. See Reference Genomes page to add.");
  // The library folder, like the other reference dropdowns: the selected FASTA
  // is not a library, so with it the list emptied once a genome was chosen.
  assert.deepEqual(microarrayRefControl.dataSource.arguments, ["options", "{{ref_path}}"]);
  assert.equal(
    conditionMatches(microarrayAction.disabledWhen[0], {
      fieldValues: { bam_path: "sample.cram.crai" },
      checkedOptions: {},
      configValues: {},
      rowValues: {},
      bundleRootPath: wgsExtractBundleRoot,
    }),
    true,
  );
  assert.equal(
    conditionMatches(microarrayAction.disabledWhen[0], {
      fieldValues: { bam_path: "sample.cram" },
      checkedOptions: {},
      configValues: {},
      rowValues: {},
      bundleRootPath: wgsExtractBundleRoot,
    }),
    false,
  );
  const repairBamAction = bundle.manifest.pages
    .find((page) => page.id === "info-bam")
    .sections.find((section) => section.id === "bam-commands")
    .actions.find((action) => action.id === "repair-ftdna-bam");
  assert.equal(repairBamAction.command.executable, "{{bundleRoot}}/scripts/run-wgsextract.sh");
  assert.deepEqual(repairBamAction.command.arguments, ["repair", "ftdna-bam", "--input", "{{bam_path}}"]);
  assert.deepEqual(repairBamAction.command.optionalArguments[0], ["--outdir", "{{out_dir}}"]);

  const annotate = bundle.manifest.pages
    .find((page) => page.id === "vcf")
    .sections.find((section) => section.id === "vcf-annotation");
  assert.equal(annotate.dataSource.path, "scripts/library-state.sh");
  assert.deepEqual(annotate.dataSource.arguments, ["{{ref_path}}", "{{genome_library}}", "{{vcf_ann_vcf}}", "{{vcf_path}}"]);
  const annotateAction = annotate.actions.find((action) => action.id === "vcf-annotate");
  const spliceaiAction = annotate.actions.find((action) => action.id === "vcf-spliceai");
  const alphamissenseAction = annotate.actions.find((action) => action.id === "vcf-alphamissense");
  const pharmgkbAction = annotate.actions.find((action) => action.id === "vcf-pharmgkb");
  assert.ok(annotateAction, "VCF annotate action exists");
  assert.equal(annotateAction.command.arguments.includes("--ann-vcf"), false);
  assert.deepEqual(annotateAction.command.optionalArguments[0], ["--ann-vcf", "{{library.annotationVcfArgument}}"]);
  assert.deepEqual(
    missingPlaceholders(annotateAction.command, {
      fieldValues: { vcf_path: "input.vcf.gz" },
      checkedOptions: {},
      configValues: {},
      rowValues: {},
      bundleRootPath: wgsExtractBundleRoot,
    }),
    [],
  );
  assert.equal(
    conditionMatches(annotateAction.disabledWhen[0], {
      fieldValues: { "library.annotationVcfReady": "false" },
      checkedOptions: {},
      configValues: {},
      rowValues: {},
      bundleRootPath: wgsExtractBundleRoot,
    }),
    true,
  );
  assert.equal(
    conditionMatches(annotateAction.disabledWhen[0], {
      fieldValues: { "library.annotationVcfReady": "true" },
      checkedOptions: {},
      configValues: {},
      rowValues: {},
      bundleRootPath: wgsExtractBundleRoot,
    }),
    false,
  );
  assert.match(
    displayCommand(annotateAction.command, {
      fieldValues: { vcf_path: "input.vcf.gz", "library.annotationVcfArgument": "annotation.vcf.gz" },
      checkedOptions: {},
      configValues: {},
      rowValues: {},
      bundleRootPath: wgsExtractBundleRoot,
    }),
    /--ann-vcf annotation\.vcf\.gz/,
  );
  assert.deepEqual(spliceaiAction.command.optionalArguments[0], ["--spliceai-file", "{{library.spliceaiFile}}"]);
  assert.deepEqual(alphamissenseAction.command.optionalArguments[0], ["--am-file", "{{library.alphamissenseFile}}"]);
  assert.deepEqual(pharmgkbAction.command.optionalArguments[0], ["--pharmgkb-file", "{{library.pharmgkbFile}}"]);
  const repairVcfAction = annotate.actions.find((action) => action.id === "vcf-repair-ftdna");
  assert.equal(repairVcfAction.command.executable, "{{bundleRoot}}/scripts/run-wgsextract.sh");
  assert.deepEqual(repairVcfAction.command.arguments, ["repair", "ftdna-vcf", "--input", "{{vcf_path}}"]);
  assert.deepEqual(repairVcfAction.command.optionalArguments[0], ["--outdir", "{{out_dir}}"]);
});
