import collections
import csv
import os
import re
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
CORE_REPOSITORY = (
    "https://github.com/mod-playerbots/azerothcore-wotlk.git")
CORE_COMMIT = "efe123fab543c5faf3c477674ec17a18fd59f09f"
ALE_REPOSITORY = "https://github.com/azerothcore/mod-eluna.git"
ALE_COMMIT = "9e5b8c66efeb383871ec58b925e47094c92cc8d5"


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
            "sql/install.sql",
            "python tools/install.py --apply",
            "python tools/install.py --check",
            "--dry-run",
            "python -m pip install -r requirements.txt",
            "python tools/gen_class_talents.py --emit",
            "python tools/gen_class_trainers.py --emit",
            "python tools/gen_recipe_rewards.py",
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
            "PLAYERHOOK_ON_REWARD_KILL_REWARDER",
            "events 77–81",
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

    def test_database_installer_is_the_complete_ordered_entrypoint(self):
        installer = read_repository_file("sql", "install.sql")
        sources = re.findall(
            r"^SOURCE\s+([^;]+);\s*$", installer,
            flags=re.IGNORECASE | re.MULTILINE)
        expected = [
            "sql/01_create_database.sql",
            "sql/02_create_tables.sql",
            "sql/03_create_triggers.sql",
            "sql/04_insert_default_config.sql",
            "sql/05_apply_anniversary_config.sql",
            "sql/06_add_recipe_rewards.sql",
            "sql/07_add_achievement_reward_claims.sql",
            "sql/08_add_collection_pending_claims.sql",
            "sql/09_add_reputation_and_account_collection_rewards.sql",
            "sql/10_expand_skill_mastery_rewards.sql",
        ]
        self.assertEqual(expected, sources)
        for source in sources:
            self.assertTrue(
                os.path.isfile(os.path.join(ROOT, *source.split("/"))),
                "database installer references missing %s" % source)
        self.assertNotIn("11-13-2026_Example_Data.sql", installer)

        for document in (
                read_repository_file("README.md"),
                read_repository_file("doc", "INSTALL.md"),
                read_repository_file("sql", "README.md")):
            self.assertIn("sql/install.sql", document)

        repository = read_repository_file(
            "serverside", "paragon", "paragon_repository.lua")
        self.assertIn("run sql/install.sql", repository)
        self.assertNotIn("Execute 01, 02, 03, 04, and 05", repository)

    def test_authoritative_schema_and_runtime_verifier_cover_every_table(self):
        expected = {
            "paragon_config_category",
            "paragon_config_statistic",
            "paragon_config",
            "paragon_config_experience_creature",
            "paragon_config_experience_achievement",
            "paragon_config_experience_skill",
            "paragon_config_experience_quest",
            "character_paragon",
            "account_paragon",
            "character_paragon_stats",
            "paragon_profession_progress",
            "paragon_reputation_progress",
            "paragon_recipe_reward_claim",
            "paragon_recipe_reward_seed",
            "paragon_pvp_reward_claim",
            "paragon_collectible_spell_xp",
            "paragon_collectible_item_xp",
            "paragon_collectible_account_item_xp",
            "paragon_rewarded_collectible_spell",
            "paragon_rewarded_appearance",
            "paragon_rewarded_account_item",
            "paragon_rewarded_achievement",
            "paragon_banked_experience",
            "paragon_codex_alloc",
            "paragon_custom_glyph",
            "paragon_racial_pick",
            "paragon_rare_kills",
            "paragon_solo_clears",
        }

        schema = read_repository_file("sql", "02_create_tables.sql")
        schema_tables = set(re.findall(
            r"CREATE TABLE IF NOT EXISTS\s+`acore_ale`\.`([^`]+)`",
            schema, flags=re.IGNORECASE))
        self.assertEqual(expected, schema_tables)

        repository = read_repository_file(
            "serverside", "paragon", "paragon_repository.lua")
        required_block = re.search(
            r"local required_tables\s*=\s*\{(.*?)\n\s*\}",
            repository, flags=re.DOTALL)
        self.assertIsNotNone(required_block)
        runtime_tables = set(re.findall(
            r'"([a-z][a-z0-9_]*)"', required_block.group(1)))
        self.assertEqual(expected, runtime_tables)

    def test_database_components_are_rerunnable(self):
        database = read_repository_file("sql", "01_create_database.sql")
        schema = read_repository_file("sql", "02_create_tables.sql")
        triggers = read_repository_file("sql", "03_create_triggers.sql")
        defaults = read_repository_file("sql", "04_insert_default_config.sql")
        anniversary = read_repository_file(
            "sql", "05_apply_anniversary_config.sql")

        self.assertRegex(
            database.upper(), r"CREATE DATABASE IF NOT EXISTS\s+`ACORE_ALE`")
        self.assertEqual(
            len(re.findall(r"CREATE TABLE", schema, flags=re.IGNORECASE)),
            len(re.findall(
                r"CREATE TABLE IF NOT EXISTS", schema,
                flags=re.IGNORECASE)))
        self.assertEqual(2, len(re.findall(
            r"CREATE TRIGGER IF NOT EXISTS", triggers,
            flags=re.IGNORECASE)))
        self.assertIn("INSERT IGNORE", defaults.upper())
        self.assertIn("ON DUPLICATE KEY UPDATE", anniversary.upper())

    def test_statistic_schema_migrates_legacy_values_without_deleting_rows(self):
        schema = read_repository_file("sql", "02_create_tables.sql")
        triggers = read_repository_file("sql", "03_create_triggers.sql")
        self.assertRegex(
            schema,
            r"`type_value`\s+VARCHAR\(32\)\s+NOT NULL\s+DEFAULT\s+'LOOT'")
        self.assertNotRegex(
            schema, r"`type_value`\s+INT(?:\(\d+\))?\b")
        self.assertRegex(
            triggers,
            re.compile(
                r"MODIFY COLUMN\s+`type_value`\s+VARCHAR\(32\).*?"
                r"WHERE\s+`type_value`\s+REGEXP\s+'\^\[0-9\]\+\$'",
                flags=re.DOTALL | re.IGNORECASE))
        for conversion in (
                "WHEN 13 THEN 'ARMOR'",
                "WHEN 24 THEN 'ARMOR_PENETRATION'",
                "WHEN 1900000 THEN 'LOOT'",
                "WHEN 1900001 THEN 'REPUTATION'",
                "WHEN 1900002 THEN 'EXPERIENCE'"):
            self.assertIn(conversion, triggers)
        self.assertNotRegex(triggers.upper(), r"\bDELETE\s+FROM\b")
        self.assertNotRegex(triggers.upper(), r"\bDROP\s+TABLE\b")

    def test_supported_statistic_seed_is_complete_and_non_destructive(self):
        anniversary = read_repository_file(
            "sql", "05_apply_anniversary_config.sql")
        category_match = re.search(
            r"INSERT IGNORE INTO\s+`acore_ale`\."
            r"`paragon_config_category`.*?;",
            anniversary, flags=re.DOTALL | re.IGNORECASE)
        statistic_match = re.search(
            r"INSERT IGNORE INTO\s+`acore_ale`\."
            r"`paragon_config_statistic`.*?;",
            anniversary, flags=re.DOTALL | re.IGNORECASE)
        self.assertIsNotNone(category_match)
        self.assertIsNotNone(statistic_match)

        category_ids = set(map(int, re.findall(
            r"^\((\d+),", category_match.group(0), flags=re.MULTILINE)))
        statistic_ids = set(map(int, re.findall(
            r"^\((\d+),", statistic_match.group(0), flags=re.MULTILINE)))
        self.assertEqual({1, 2, 3, 4}, category_ids)
        self.assertEqual(set(range(1, 16)) | {17, 19}, statistic_ids)

        statistic_seed = statistic_match.group(0)
        for aura in ("'LOOT'", "'REPUTATION'", "'EXPERIENCE'"):
            self.assertIn(aura, statistic_seed)
        self.assertNotIn("'GOLD'", statistic_seed)
        self.assertNotIn("'MOVE_SPEED'", statistic_seed)
        self.assertNotIn("ON DUPLICATE KEY UPDATE", statistic_seed.upper())

        hook = read_repository_file(
            "serverside", "paragon", "paragon_hook.lua")
        self.assertIn("if constant_stat_value == nil then", hook)

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

        install = documents["doc/INSTALL.md"]
        pins = documents["patches/PINS.md"]
        for text in (install, pins):
            self.assertIn(CORE_COMMIT, text)
            self.assertIn(ALE_COMMIT, text)
        self.assertIn(CORE_REPOSITORY, install)
        self.assertIn("--branch Playerbot --single-branch", install)
        self.assertIn(ALE_REPOSITORY, install)
        self.assertNotIn("| Latest |", documents["README.md"])

    def test_python_dependencies_and_complete_test_command_are_pinned(self):
        requirements = [
            line.strip() for line in read_repository_file(
                "requirements.txt").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(["mpyq==0.2.5", "lupa==2.8"], requirements)

        command = 'python -m unittest discover -s tools -p "test_*.py"'
        self.assertIn(command, read_repository_file("README.md"))
        self.assertIn("Python 3.10+", read_repository_file(
            "doc", "INSTALL.md"))

    def test_destructive_reward_refreshes_are_transactional(self):
        for path in (
                ("tools", "paragon_collectible_xp.py"),
                ("tools", "populate_quest_paragon_xp.py")):
            source = read_repository_file(*path)
            self.assertIn('"START TRANSACTION;"', source)
            self.assertIn('"COMMIT;"', source)
            self.assertLess(
                source.index('"START TRANSACTION;"'),
                source.index('"DELETE FROM'),
                "%s deletes before opening its transaction" % path[-1])

    def test_database_password_never_enters_host_command_arguments(self):
        for name in (
                "gen_glyph_data.py",
                "paragon_client_patch.py",
                "paragon_collectible_xp.py",
                "populate_quest_paragon_xp.py"):
            source = read_repository_file("tools", name)
            self.assertNotIn("ACORE_DB_PASS", source, name)
            self.assertNotIn('"-p" +', source, name)
            self.assertIn('$MYSQL_ROOT_PASSWORD', source, name)

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
