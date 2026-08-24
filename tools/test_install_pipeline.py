import contextlib
import io
import os
import pathlib
import re
import tempfile
import unittest
from unittest import mock

import install
import paragon_collectible_xp
import populate_quest_paragon_xp


ROOT = pathlib.Path(__file__).resolve().parents[1]


def sample_config(root):
    core = root / "core"
    client = root / "client"
    return install.Config(
        mode="apply",
        core_root=core,
        client_root=client,
        lua_root=core / "env" / "dist" / "etc" / "lua_scripts",
        database_container="db-test",
        worldserver_container="world-test",
        dbc_dir=None,
        python="python-test",
        general_name="patch-X.MPQ",
        locale_name="patch-enUS-X.MPQ",
        ui_name="patch-W.MPQ",
    )


class InstallPipelineTests(unittest.TestCase):
    def test_apply_phase_order_is_an_explicit_contract(self):
        self.assertEqual(
            (
                "preflight",
                "repository-tests",
                "database-bootstrap",
                "static-data",
                "profession-data",
                "server-payload",
                "class-data",
                "content-and-client-dbc",
                "collection-xp",
                "quest-xp",
                "ui-art",
                "client-addon",
                "verification",
            ),
            install.APPLY_PHASES,
        )

    def test_canonical_sql_manifest_has_exact_order(self):
        self.assertEqual(
            install.EXPECTED_SQL_COMPONENTS,
            tuple(path.relative_to(ROOT).as_posix()
                  for path in install.sql_components()),
        )

    def test_sql_manifest_rejects_non_source_statements_and_escapes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            sql = root / "sql"
            sql.mkdir()
            entrypoint = sql / "install.sql"
            entrypoint.write_text("DROP DATABASE anything;\n", encoding="utf-8")
            with self.assertRaisesRegex(install.InstallError,
                                        "must be a SOURCE directive"):
                install.sql_components(entrypoint, root)

            entrypoint.write_text("SOURCE ../outside.sql;\n", encoding="utf-8")
            (root.parent / "outside.sql").write_text("SELECT 1;\n",
                                                     encoding="utf-8")
            try:
                with self.assertRaisesRegex(install.InstallError,
                                            "escapes the repository"):
                    install.sql_components(entrypoint, root)
            finally:
                (root.parent / "outside.sql").unlink()

    def test_apply_commands_cover_every_required_generator_in_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = sample_config(pathlib.Path(temporary))
            commands = install.apply_commands(config)
        scripts = [pathlib.Path(command[1]).name
                   for command in commands if len(command) > 1 and
                   command[1].endswith(".py")]
        self.assertEqual(
            [
                "gen_glyph_data.py",
                "gen_gem_data.py",
                "gen_mount_data.py",
                "gen_companion_data.py",
                "gen_enchant_text.py",
                "gen_profession_xp.py",
                "gen_profession_xp.py",
                "gen_class_talents.py",
                "gen_class_trainers.py",
                "paragon_client_patch.py",
                "paragon_collectible_xp.py",
                "paragon_collectible_xp.py",
                "populate_quest_paragon_xp.py",
                "populate_quest_paragon_xp.py",
                "build_ui_art.py",
                "check_patch_collisions.py",
            ],
            scripts,
        )
        self.assertIn("--check", commands[7])
        self.assertIn("--apply", commands[10])
        self.assertIn("--seed", commands[11])
        self.assertIn("--check", commands[12])
        self.assertIn("--check", commands[14])

    def test_check_commands_are_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = sample_config(pathlib.Path(temporary))
            commands = install.check_commands(config)
        flattened = [argument for command in commands for argument in command]
        self.assertNotIn("--apply", flattened)
        self.assertNotIn("--seed", flattened)
        for command in commands[1:9]:
            self.assertIn("--check", command)

    def test_dry_run_never_executes_a_subprocess_or_creates_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            core = root / "not-created-core"
            client = root / "not-created-client"
            stdout = io.StringIO()
            with mock.patch("install.subprocess.run") as run:
                with contextlib.redirect_stdout(stdout):
                    result = install.main((
                        "--dry-run",
                        "--core-root", str(core),
                        "--client-root", str(client),
                    ))
            self.assertEqual(0, result)
            run.assert_not_called()
            self.assertFalse(core.exists())
            self.assertFalse(client.exists())
            output = stdout.getvalue()
            self.assertIn("sql/install.sql", output)
            self.assertIn("paragon_client_patch.py", output)
            self.assertIn("no changes made", output)

    def test_child_environment_preserves_caller_values_and_sets_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = sample_config(pathlib.Path(temporary))
            environment = install.child_environment(
                config, {"KEEP_ME": "yes", "PATH": "test-path"})
        self.assertEqual("yes", environment["KEEP_ME"])
        self.assertEqual("test-path", environment["PATH"])
        self.assertEqual("db-test", environment["ACORE_DB_CONTAINER"])
        self.assertEqual("1", environment["PYTHONDONTWRITEBYTECODE"])
        self.assertEqual(str(config.client_data),
                         environment["PARAGON_CLIENT_DATA"])

    def test_generated_outputs_are_repository_native(self):
        files = {
            name: (ROOT / "tools" / name).read_text(encoding="utf-8")
            for name in (
                "gen_glyph_data.py",
                "gen_gem_data.py",
                "gen_mount_data.py",
                "gen_companion_data.py",
                "gen_enchant_text.py",
            )
        }
        for name, source in files.items():
            self.assertIn("--check", source, name)
            self.assertNotIn('"Server", "azerothcore-test"', source, name)
        client_patch = (ROOT / "tools" / "paragon_client_patch.py").read_text(
            encoding="utf-8")
        self.assertIn('"sql", "content",', client_patch)
        self.assertIn('"01_paragon_content.sql"', client_patch)

    def test_each_pipeline_uses_a_fresh_ephemeral_dbc_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = sample_config(pathlib.Path(temporary))
            pipeline = install.Pipeline(config)
            try:
                cache = pathlib.Path(pipeline.environment["PARAGON_DBC_CACHE"])
                self.assertTrue(cache.is_dir())
                self.assertFalse(str(cache).startswith(str(ROOT / "tools")))
            finally:
                pipeline.close()
            self.assertFalse(cache.exists())

        for name in (
                "gen_glyph_data.py",
                "paragon_client_patch.py",
                "paragon_collectible_xp.py",
                "populate_quest_paragon_xp.py"):
            source = (ROOT / "tools" / name).read_text(encoding="utf-8")
            self.assertIn("PARAGON_DBC_CACHE", source, name)

    def test_preflight_requires_the_lua52_runtime_not_only_lupa_package(self):
        source = (ROOT / "tools" / "install.py").read_text(encoding="utf-8")
        self.assertIn("import lupa.lua52", source)

    def test_database_verification_requires_exact_tables_and_triggers(self):
        pipeline = object.__new__(install.Pipeline)
        pipeline.verify_canonical_world_content = mock.Mock()
        content_ok = "\t".join(["1"] * 13) + "\n"
        pipeline._mysql = mock.Mock(side_effect=(
            "\n".join(reversed(install.REQUIRED_ALE_TABLES)) + "\n",
            "\n".join(reversed(install.REQUIRED_ALE_TRIGGERS)) + "\n",
            content_ok,
        ))
        pipeline.verify_database()
        pipeline.verify_canonical_world_content.assert_called_once_with()

        pipeline._mysql = mock.Mock(side_effect=(
            "\n".join(install.REQUIRED_ALE_TABLES[:-1]) + "\n",
        ))
        with self.assertRaisesRegex(install.InstallError,
                                    "tables differ.*missing"):
            pipeline.verify_database()

        pipeline._mysql = mock.Mock(side_effect=(
            "\n".join(install.REQUIRED_ALE_TABLES) + "\nunexpected_table\n",
        ))
        with self.assertRaisesRegex(install.InstallError,
                                    "tables differ.*unexpected"):
            pipeline.verify_database()

        pipeline._mysql = mock.Mock(side_effect=(
            "\n".join(install.REQUIRED_ALE_TABLES) + "\n",
            install.REQUIRED_ALE_TRIGGERS[0] + "\n",
        ))
        with self.assertRaisesRegex(install.InstallError,
                                    "triggers differ.*missing"):
            pipeline.verify_database()

    def test_identical_file_verification_detects_stale_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            expected = root / "expected"
            generated = root / "generated"
            expected.write_bytes(b"current")
            generated.write_bytes(b"current")
            install.Pipeline._verify_identical_file(
                expected, generated, "test artifact")
            generated.write_bytes(b"stale")
            with self.assertRaisesRegex(install.InstallError, "stale"):
                install.Pipeline._verify_identical_file(
                    expected, generated, "test artifact")

    def test_canonical_world_plan_rewrites_all_owned_dml_to_temporary_tables(self):
        tables, statements, scopes = install.canonical_world_plan()
        self.assertEqual(install.CANONICAL_WORLD_TABLES, tables)
        self.assertEqual(set(tables), set(scopes))
        self.assertGreater(len(statements), 2000)
        for statement in statements:
            match = install.WORLD_DML_PATTERN.match(statement)
            self.assertIsNotNone(match)
            self.assertTrue(match.group("table").startswith("_paragon_verify_"))
        self.assertTrue(all(scopes[table] for table in tables))
        for table, reserved in install.CANONICAL_WORLD_RESERVED_SCOPES.items():
            self.assertEqual(reserved, scopes[table])
        self.assertIn(
            "ID >= 1900000 AND ID < 2000000", scopes["spell_dbc"])
        self.assertIn(
            "ABS(SpellId) >= 1900000 AND ABS(SpellId) < 2000000",
            scopes["spell_proc"])

    def test_world_digest_comparison_rejects_missing_extra_and_changed_rows(self):
        verify = install.Pipeline._verify_digest_rows
        verify("sample", [("1", "aaa")], [("1", "aaa")])
        for actual, detail in (
                ([], "missing"),
                ([("1", "aaa"), ("2", "bbb")], "unexpected"),
                ([("1", "bbb")], "changed")):
            with self.subTest(detail=detail):
                with self.assertRaisesRegex(install.InstallError, detail):
                    verify("sample", [("1", "aaa")], actual)

    def test_world_verifier_executes_owned_dml_only_against_temp_tables(self):
        pipeline = object.__new__(install.Pipeline)
        pipeline._world_table_layouts = mock.Mock(return_value={
            "spell_dbc": (("ID", "Name"), ("ID",)),
        })
        pipeline._mysql = mock.Mock(return_value="\n".join((
            "__PARAGON_EXPECTED__:spell_dbc",
            "1\tdigest",
            "__PARAGON_ACTUAL__:spell_dbc",
            "1\tdigest",
        )) + "\n")
        with mock.patch.object(install, "canonical_world_plan", return_value=(
                ("spell_dbc",),
                ("INSERT INTO _paragon_verify_spell_dbc (ID,Name) "
                 "VALUES (1,'test');",),
                {"spell_dbc": ("ID = 1",)},
        )):
            pipeline.verify_canonical_world_content()
        sql = pipeline._mysql.call_args.args[0]
        self.assertIn("CREATE TEMPORARY TABLE", sql)
        self.assertIn("START TRANSACTION READ ONLY", sql)
        self.assertIn("INSERT INTO _paragon_verify_spell_dbc", sql)
        self.assertNotIn("INSERT INTO spell_dbc", sql)

    def test_regenerated_value_checks_are_exact(self):
        for checker in (
                paragon_collectible_xp.assert_exact_rows,
                populate_quest_paragon_xp.assert_exact_rows):
            checker("sample", [(1, "value")], [("1", "value")])
            for actual, detail in (
                    ([], "missing"),
                    ([(1, "value"), (2, "extra")], "unexpected"),
                    ([(1, "changed")], "changed")):
                with self.subTest(checker=checker.__module__, detail=detail):
                    with self.assertRaisesRegex(SystemExit, detail):
                        checker("sample", [(1, "value")], actual)

    def test_collectible_check_executes_only_read_only_sql(self):
        statements = []

        def record_mysql(sql, db="acore_world"):
            statements.append((db, sql))
            return []

        with mock.patch.object(
                paragon_collectible_xp, "sla_spell_sets",
                return_value=(set(), set())), \
                mock.patch.object(
                    paragon_collectible_xp, "mysql",
                    side_effect=record_mysql), \
                mock.patch("sys.argv", ["paragon_collectible_xp.py", "--check"]):
            paragon_collectible_xp.main()

        self.assertGreater(len(statements), 10)
        mutating = re.compile(
            r"\b(?:CREATE|DROP|INSERT|UPDATE|DELETE|REPLACE|ALTER|TRUNCATE)\b",
            re.IGNORECASE)
        for database, sql in statements:
            with self.subTest(database=database, sql=sql):
                self.assertIsNone(mutating.search(sql))

    def test_check_runs_full_temporary_client_reproduction(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = sample_config(pathlib.Path(temporary))
            pipeline = install.Pipeline(config)
            pipeline.preflight = mock.Mock()
            pipeline.command = mock.Mock()
            pipeline.verify_tree = mock.Mock()
            pipeline.verify_database = mock.Mock()
            pipeline.verify_generated_client_payload = mock.Mock()
            try:
                pipeline.check()
            finally:
                pipeline.close()
        pipeline.verify_generated_client_payload.assert_called_once_with()

    def test_client_reproduction_routes_every_generated_file_to_temp(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            repository = root / "repository"
            repo_tools = repository / "tools"
            repo_generated = repo_tools / "generated"
            repo_generated.mkdir(parents=True)
            for name in install.REPRODUCTION_TOOLS:
                (repo_tools / name).write_text("# source\n", encoding="utf-8")
            art = repository / "clientside" / "Interface" / "Paragon"
            art.mkdir(parents=True)
            for number in range(14):
                (art / ("art-%02d.blp" % number)).write_bytes(b"blp")
            expected_class = {
                "class_talent_ranks.py": b"talents\n",
                "class_trainer_ranks.py": b"trainers\n",
            }
            for name, content in expected_class.items():
                (repo_generated / name).write_bytes(content)
            canonical_sql = repository / "sql" / "content" / \
                "01_paragon_content.sql"
            canonical_sql.parent.mkdir(parents=True)
            canonical_sql.write_bytes(b"canonical sql\n")

            config = sample_config(root / "installation")
            config.client_data.mkdir(parents=True)
            (config.client_data / "enUS").mkdir()
            (config.client_data / config.general_name).write_bytes(b"general mpq")
            (config.client_data / config.ui_name).write_bytes(b"ui mpq")
            (config.client_data / "enUS" / config.locale_name).write_bytes(
                b"locale mpq")
            protected = {
                path: path.read_bytes()
                for path in (
                    *(repo_generated / name for name in expected_class),
                    canonical_sql,
                    config.client_data / config.general_name,
                    config.client_data / config.ui_name,
                    config.client_data / "enUS" / config.locale_name,
                )
            }

            pipeline = install.Pipeline(config)
            calls = []
            temporary_workspace = None

            def fake_command(command, environment=None):
                nonlocal temporary_workspace
                calls.append((tuple(command), dict(environment or {})))
                name = pathlib.Path(command[1]).name
                if name == "gen_class_talents.py":
                    output = pathlib.Path(command[1]).parent / "generated" / \
                        "class_talent_ranks.py"
                    output.write_bytes(expected_class[output.name])
                elif name == "gen_class_trainers.py":
                    output = pathlib.Path(command[1]).parent / "generated" / \
                        "class_trainer_ranks.py"
                    output.write_bytes(expected_class[output.name])
                elif name == "paragon_client_patch.py":
                    temporary_workspace = pathlib.Path(command[1]).parents[1]
                    generated_sql = temporary_workspace / "sql" / "content" / \
                        "01_paragon_content.sql"
                    generated_sql.parent.mkdir(parents=True)
                    generated_sql.write_bytes(canonical_sql.read_bytes())
                    data = pathlib.Path(environment["PARAGON_CLIENT_DATA"])
                    (data / config.general_name).write_bytes(b"general mpq")
                    (data / "enUS" / config.locale_name).write_bytes(b"locale mpq")
                elif name == "build_ui_art.py":
                    data = pathlib.Path(environment["PARAGON_CLIENT_DATA"])
                    (data / config.ui_name).write_bytes(b"ui mpq")

            pipeline.command = fake_command
            try:
                with mock.patch.object(install, "ROOT", repository):
                    pipeline.verify_generated_client_payload()
            finally:
                pipeline.close()

            for path, content in protected.items():
                self.assertEqual(content, path.read_bytes(), str(path))
            client_build_calls = [
                call for call in calls
                if pathlib.Path(call[0][1]).name in
                ("paragon_client_patch.py", "build_ui_art.py")
            ]
            self.assertEqual(2, len(client_build_calls))
            for _command, environment in client_build_calls:
                self.assertNotEqual(str(config.client_data),
                                    environment["PARAGON_CLIENT_DATA"])
            self.assertIsNotNone(temporary_workspace)
            self.assertFalse(temporary_workspace.exists())


if __name__ == "__main__":
    unittest.main()
