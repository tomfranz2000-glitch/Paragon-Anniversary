import os
import tempfile
import unittest

import build_ui_art
from build_mpq import is_owned_archive, read_file


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class InstallationContractTests(unittest.TestCase):
    def test_documentation_has_no_private_scratchpad_paths(self):
        for base, dirs, names in os.walk(ROOT):
            dirs[:] = [name for name in dirs if not name.startswith(".")]
            for name in names:
                if not name.lower().endswith(".md"):
                    continue
                path = os.path.join(base, name)
                with open(path, encoding="utf-8") as handle:
                    text = handle.read().lower().replace("\\", "/")
                self.assertNotIn(
                    "scratchpad/", text,
                    "%s references a private, unshipped scratchpad path"
                    % os.path.relpath(path, ROOT))

    def test_branch_install_guide_names_the_real_pipeline(self):
        with open(os.path.join(ROOT, "doc", "INSTALL.md"), encoding="utf-8") as handle:
            guide = handle.read()
        required = [
            "modules/mod-ale",
            "sql/01_create_database.sql",
            "sql/05_apply_anniversary_config.sql",
            "python tools/gen_class_talents.py --emit",
            "python tools/gen_class_trainers.py --emit",
            "python tools/paragon_client_patch.py --apply",
            "python tools/paragon_collectible_xp.py --seed",
            "python tools/populate_quest_paragon_xp.py",
            "python tools/build_ui_art.py",
            "Interface/AddOns/Paragon",
            "patch-W.MPQ",
            "patch-X.MPQ",
            "patch-enUS-X.MPQ",
        ]
        for text in required:
            self.assertIn(text, guide)

    def test_installable_payload_counts_match_the_guide(self):
        addon_root = os.path.join(
            ROOT, "clientside", "Interface", "AddOns", "Paragon")
        addon_files = [os.path.join(base, name)
                       for base, _dirs, names in os.walk(addon_root)
                       for name in names]
        self.assertEqual(27, len(addon_files))
        self.assertEqual(14, len(build_ui_art.source_entries()))

    def test_ui_builder_packages_art_but_never_the_addon(self):
        entries = build_ui_art.source_entries()
        with tempfile.TemporaryDirectory() as temporary:
            stage = os.path.join(temporary, "stage")
            output = os.path.join(temporary, "patch-W.MPQ")
            build_ui_art.stage_sources(stage, entries)
            from build_mpq import build
            build(stage, output)
            self.assertTrue(is_owned_archive(output))
            for archive_name, source in entries:
                with open(source, "rb") as handle:
                    self.assertEqual(handle.read(),
                                     read_file(output, archive_name))
                self.assertNotIn("\\AddOns\\", archive_name)

    def test_content_sql_is_single_source(self):
        content = sorted(name for name in os.listdir(
            os.path.join(ROOT, "sql", "content")) if name.endswith(".sql"))
        self.assertEqual(["01_paragon_content.sql"], content)
        with open(os.path.join(ROOT, "sql", "content", content[0]),
                  encoding="utf-8") as handle:
            generated_sql = handle.read()
        for title_id in (200, 201):
            self.assertIn("DELETE FROM chartitles_dbc WHERE ID = %d;"
                          % title_id, generated_sql)


if __name__ == "__main__":
    unittest.main()
