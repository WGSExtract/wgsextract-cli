import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { actionCertification } from "../dist/shared/command-certification.js";
import { localizationLabels, parseTomlStrings } from "../dist/shared/localization.js";
import { visibleActions, visibleControls, visiblePages, visibleSections } from "../dist/shared/ui-mode.js";
import { renderCertificationBadge } from "../dist/web/src/client/view/certification-badge.js";
import { loadManifestFromRoot } from "../dist/web/src/server/bundle-loader.js";

const bundle = fileURLToPath(new URL("../../examples/WGSExtract", import.meta.url));

for (const mode of ["basic", "advanced"]) {
    test(`Every CLI tool in ${mode} has a badge backed by CLI certification`, async () => {
        const manifest = await loadManifestFromRoot(bundle);
        const catalog = await readFile(new URL("../../resources/BuiltinStrings/strings.en.toml", import.meta.url), "utf8");
        const { certificationStrings } = localizationLabels(parseTomlStrings(catalog));
        let checked = 0;
        for (const page of visiblePages(manifest, mode)) {
            for (const section of visibleSections(page, mode)) {
                const actions = [...(section.actions ?? []), ...visibleControls(section, mode)
                    .flatMap(control => [...(control.actions ?? []), ...(control.rowActions ?? [])])];
                for (const action of visibleActions(actions, mode)) {
                    const badge = renderCertificationBadge(action, action.id, certificationStrings);
                    if (!action.command && !action.serverOperation) {
                        assert.equal(badge, "");
                        continue;
                    }
                    const description = actionCertification(action);
                    if (action.command?.arguments?.[0] === "deps") {
                        // Tool rows: the dependency manager's level isn't the tool's.
                        assert.equal(description, undefined);
                        assert.equal(badge, "");
                        continue;
                    }
                    assert.ok(description,`${page.id}/${section.id}/${action.id} has no CLI certification`);
                    const { path, level } = description;
                    assert.ok(certificationStrings[`${level}.tooltip`], `No help text for ${level}`);
                    assert.ok(badge.includes(`certification.html#command-${path.replaceAll(" ", "-")}"`));
                    assert.ok(badge.includes(`>${certificationStrings[`${level}.label`]}</a>`));
                    checked++;
                }
            }
        }
        assert.ok(checked > 0);
    });
}
