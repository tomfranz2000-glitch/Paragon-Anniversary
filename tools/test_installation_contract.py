import collections
import csv
import os
import tempfile
import unittest

import build_ui_art
from build_mpq import is_owned_archive, read_file


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARAGON_REPOSITORY = (
    "https://github.com/tomfranz2000-glitch/Paragon-Anniversary.git")
TRANSMOG_REPOSITORY = (
    "https://github.com/tomfranz2000-glitch/mod-transmog.git")
TRANSMOG_COMMIT = "31633595cad7b12042b6484ffe3ea34f355b9821"


def read_repository_file(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


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

    def test_main_install_guide_names_the_real_pipeline(self):
        guide = read_repository_file("doc", "INSTALL.md")
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
            "SUM(Points) AS solo_points",
            "96 rows and 1,045 total points",
            "patch-W.MPQ",
            "patch-X.MPQ",
            "patch-enUS-X.MPQ",
        ]
        for text in required:
            self.assertIn(text, guide)

        for setting in (
                "Transmogrification.UseCollectionSystem = 1",
                "Transmogrification.TrackUnusableItems = 1",
                "Transmogrification.AllowPoor = 1",
                "Transmogrification.AllowCommon = 1",
                "Transmogrification.AllowTradeable = 1",
                "Transmogrification.AllowMixedArmorTypes = 1"):
            self.assertIn(setting, guide)

    def test_default_branches_and_transmog_revision_are_authoritative(self):
        documents = {
            "README.md": read_repository_file("README.md"),
            "doc/INSTALL.md": read_repository_file("doc", "INSTALL.md"),
            "doc/PROVENANCE.md": read_repository_file("doc", "PROVENANCE.md"),
            "patches/PINS.md": read_repository_file("patches", "PINS.md"),
        }

        for name, text in documents.items():
            self.assertIn(
                "sole authoritative install branch", text,
                "%s does not identify main as the only install branch" % name)
            self.assertNotIn(
                "wintermute", text.lower(),
                "%s still contains obsolete install-branch guidance" % name)
            self.assertIn(
                "tomfranz2000-glitch/mod-transmog", text,
                "%s does not name the required transmog fork" % name)
            self.assertIn(
                TRANSMOG_COMMIT, text,
                "%s does not pin the tested transmog revision" % name)

        for name in ("README.md", "doc/INSTALL.md"):
            text = documents[name]
            self.assertIn(PARAGON_REPOSITORY, text)
            self.assertIn("--branch main --single-branch", text)

        for name in ("README.md", "doc/INSTALL.md", "patches/PINS.md"):
            text = documents[name]
            self.assertIn(TRANSMOG_REPOSITORY, text)
            self.assertIn("--branch master --single-branch", text)
            self.assertIn("checkout --detach", text)

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

    def test_content_sql_has_complete_solo_achievement_points(self):
        generated_sql = read_repository_file(
            "sql", "content", "01_paragon_content.sql")
        points_by_id = {}

        prefix = "INSERT INTO achievement_dbc ("
        separator = ") VALUES ("
        for line in generated_sql.splitlines():
            if not line.startswith(prefix) or not line.endswith(");"):
                continue
            columns_text, values_text = line[len(prefix):-2].split(
                separator, 1)
            columns = [column.strip().strip("`")
                       for column in columns_text.split(",")]
            values = next(csv.reader(
                [values_text], delimiter=",", quotechar="'",
                doublequote=True, skipinitialspace=True))
            self.assertEqual(len(columns), len(values))
            row = dict(zip(columns, values))
            achievement_id = int(row["ID"])
            if 19000 <= achievement_id < 20000:
                self.assertNotIn(
                    achievement_id, points_by_id,
                    "duplicate custom achievement ID %d" % achievement_id)
                points_by_id[achievement_id] = int(row["Points"])

        self.assertEqual(96, len(points_by_id))
        self.assertTrue(all(points > 0 for points in points_by_id.values()))
        self.assertEqual(1045, sum(points_by_id.values()))
        self.assertEqual(
            collections.Counter({10: 92, 25: 3, 50: 1}),
            collections.Counter(points_by_id.values()))


if __name__ == "__main__":
    unittest.main()
