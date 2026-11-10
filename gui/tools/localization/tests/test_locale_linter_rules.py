from pathlib import Path
import tempfile
import unittest

from tools.localization.locale_linter.models import ParsedEntry, ParsedFile
from tools.localization.locale_linter.parser import parse_toml_file
from tools.localization.locale_linter.rules import (
    lint_locale,
    lint_source,
    merge_source_hash,
    short_source_hash,
    update_source_hashes,
)


class LocaleLinterRuleTests(unittest.TestCase):
    def test_lint_locale_reports_missing_empty_language_and_untranslated(self) -> None:
        source = parsed_file(
            [
                entry("language.code", "en", 1),
                entry("language.layoutDirection", "ltr", 2),
                entry("greeting", "Hello", 3),
                entry("missing", "Missing", 4),
            ]
        )
        target = parsed_file(
            [
                entry("language.code", "de", 1),
                entry("language.layoutDirection", "sideways", 2),
                entry("greeting", "Hello", 3),
                entry("empty", "", 4),
            ]
        )

        report = lint_locale(source, target, "Bundle", "fr", requires_builtin=False)
        codes = {finding.code for finding in report.findings}

        self.assertIn("missing-key", codes)
        self.assertIn("extra-key", codes)
        self.assertIn("empty-value", codes)
        self.assertIn("invalid-layout-direction", codes)
        self.assertIn("language-code-mismatch", codes)
        self.assertIn("untranslated", codes)

    def test_lint_source_reports_builtin_missing_and_duplicate_keys(self) -> None:
        parsed = parsed_file([entry("language.code", "en", 1)])
        parsed.duplicate_keys.append(("language.code", 2, 1))

        codes = {finding.code for finding in lint_source(parsed, requires_builtin=True)}

        self.assertIn("duplicate-key", codes)
        self.assertIn("missing-builtin-key", codes)

    def test_source_hash_warning_and_rewrite_helpers(self) -> None:
        source = parsed_file([entry("title", "New source", 1)])
        target = parsed_file([entry("title", "Translated", 1, recorded_source_hash="deadbeef")])

        report = lint_locale(source, target, "Bundle", "fr", requires_builtin=False)

        self.assertIn("source-changed", {finding.code for finding in report.findings})
        self.assertEqual(
            merge_source_hash("# i18n-ignore i18n-source-hash:deadbeef", "12345678"),
            "# i18n-ignore i18n-source-hash:12345678",
        )

    def test_update_source_hashes_rewrites_single_line_values(self) -> None:
        source = parsed_file([entry("title", "Hello", 1)])
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "strings.fr.toml"
            target.write_text('"title" = "Bonjour"  # i18n-source-hash:deadbeef\n', encoding="utf-8")

            updated = update_source_hashes(source, target)

            self.assertEqual(updated, 1)
            self.assertIn(short_source_hash("Hello"), target.read_text(encoding="utf-8"))

    def test_update_source_hashes_skips_multiline_bodies(self) -> None:
        source = parsed_file([entry("description", "Line 1\nLine 2", 1), entry("title", "Hello", 2)])
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "strings.fr.toml"
            target.write_text(
                '"description" = """\n'
                "not_a_key = still body\n"
                '"""\n'
                '"title" = "Bonjour"  # i18n-source-hash:deadbeef\n',
                encoding="utf-8",
            )

            updated = update_source_hashes(source, target)
            rewritten = target.read_text(encoding="utf-8")

            self.assertEqual(updated, 1)
            self.assertIn("not_a_key = still body", rewritten)
            self.assertIn(short_source_hash("Hello"), rewritten)

    def test_update_source_hashes_skips_literal_multiline_bodies(self) -> None:
        source = parsed_file([entry("description", "Line 1\nLine 2", 1), entry("title", "Hello", 2)])
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "strings.fr.toml"
            target.write_text(
                '"description" = \'\'\'\n'
                "not_a_key = still body\n"
                "'''\n"
                '"title" = "Bonjour"  # i18n-source-hash:deadbeef\n',
                encoding="utf-8",
            )

            updated = update_source_hashes(source, target)
            rewritten = target.read_text(encoding="utf-8")

            self.assertEqual(updated, 1)
            self.assertIn("not_a_key = still body", rewritten)
            self.assertIn(short_source_hash("Hello"), rewritten)

    def test_update_source_hashes_only_rewrites_changed_translated_key_issue_641(self) -> None:
        # Regression for #641: one changed English string rewrote hundreds of
        # unrelated lines by adding hashes to i18n-ignore and TODO entries.
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source_path = directory / "strings.en.toml"
            source_path.write_text(
                '"changed" = "New source"\n'
                '"stable" = "Stable"\n'
                '"brand" = "WGS Extract"\n'
                '"todo" = "Not yet translated"\n'
                '"fresh" = "Fresh"\n'
                '"copied" = "Copied"\n',
                encoding="utf-8",
            )
            target = directory / "strings.fr.toml"
            original_lines = [
                '"changed" = "Traduit"  # i18n-source-hash:deadbeef',
                f'"stable" = "Stable FR"  # i18n-source-hash:{short_source_hash("Stable")}',
                '"brand" = "WGS Extract"  # i18n-ignore',
                '"todo" = "Not yet translated"  # i18n-ignore; TODO: translate',
                '"fresh" = "Nouveau"',
                '"copied" = "Copied"',
            ]
            target.write_text("\n".join(original_lines) + "\n", encoding="utf-8")

            updated = update_source_hashes(parse_toml_file(source_path), target)
            rewritten = target.read_text(encoding="utf-8").splitlines()

            self.assertEqual(updated, 2)
            changed = [i for i, (a, b) in enumerate(zip(original_lines, rewritten)) if a != b]
            self.assertEqual(changed, [0, 4])
            self.assertIn(f"i18n-source-hash:{short_source_hash('New source')}", rewritten[0])
            self.assertIn(f"i18n-source-hash:{short_source_hash('Fresh')}", rewritten[4])

    def test_translated_line_without_hash_is_flagged_and_stamped_once(self) -> None:
        # Review of #653: a new translation must get a first hash, or later
        # source changes are never reported.
        source = parsed_file([entry("title", "Hello", 1), entry("brand", "WGS", 2)])
        target = parsed_file(
            [entry("title", "Bonjour", 1), entry("brand", "WGS", 2, ignore_untranslated=True)]
        )

        report = lint_locale(source, target, "Bundle", "fr", requires_builtin=False)

        self.assertEqual(
            [(f.code, f.key) for f in report.findings if f.code == "missing-source-hash"],
            [("missing-source-hash", "title")],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "strings.fr.toml"
            path.write_text('"title" = "Bonjour"\n"brand" = "WGS"  # i18n-ignore\n', encoding="utf-8")

            self.assertEqual(update_source_hashes(source, path), 1)
            self.assertEqual(update_source_hashes(source, path), 0)
            restamped = parse_toml_file(path)
            self.assertEqual(restamped.entries[0].recorded_source_hash, short_source_hash("Hello"))
            self.assertIsNone(restamped.entries[1].recorded_source_hash)

    def test_parser_tracks_duplicate_keys_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "strings.fr.toml"
            path.write_text(
                '"title" = "Bonjour # inside"  # i18n-ignore i18n-source-hash:ABCDEF12\n'
                '"title" = "Salut"\n',
                encoding="utf-8",
            )

            parsed = parse_toml_file(path)

            self.assertEqual(parsed.entries[0].value, "Bonjour # inside")
            self.assertTrue(parsed.entries[0].ignore_untranslated)
            self.assertEqual(parsed.entries[0].recorded_source_hash, "abcdef12")
            self.assertEqual(parsed.duplicate_keys, [("title", 2, 1)])


def entry(
    key: str,
    value: str,
    line: int,
    *,
    ignore_untranslated: bool = False,
    recorded_source_hash: str | None = None,
) -> ParsedEntry:
    return ParsedEntry(key, value, line, ignore_untranslated, recorded_source_hash)


def parsed_file(entries: list[ParsedEntry]) -> ParsedFile:
    parsed = ParsedFile(path=Path("strings.test.toml"), entries=entries)
    parsed.key_index = {entry.key: index for index, entry in enumerate(entries)}
    return parsed


if __name__ == "__main__":
    unittest.main()
