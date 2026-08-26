import importlib.util
import inspect
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "upgrades" / "instance-xp-v1" / "install.py"


def load_installer():
    name = "paragon_instance_xp_upgrade_installer"
    spec = importlib.util.spec_from_file_location(name, INSTALLER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


upgrade = load_installer()


class NativeFixture:
    def __init__(self, root):
        self.core = pathlib.Path(root) / "core"
        self.ale = self.core / "modules" / "mod-ale"
        self.write(
            "src/LuaEngine/LuaFunctions.cpp",
            """
            { "IsPlayerBot", &LuaPlayer::IsPlayerBot },
            { "GetAtLevelXPReward", &LuaCreature::GetAtLevelXPReward },

            ALERegister<Map> MapMethods[] =
            {
                // Getters
                { "GetName", &LuaMap::GetName },
                { "GetDifficulty", &LuaMap::GetDifficulty },
                { "GetInstanceId", &LuaMap::GetInstanceId },
                { "GetInstanceData", &LuaMap::GetInstanceData },
                { "GetPlayerCount", &LuaMap::GetPlayerCount },
            };
            """,
        )
        self.write(
            "src/LuaEngine/methods/MapMethods.h",
            """
            namespace LuaMap
            {
                int GetDifficulty(lua_State* L, Map* map)
                {
                    ALE::Push(L, map->GetDifficulty());
                    return 1;
                }

                /**
                 * Returns the instance ID of the [Map].
                 *
                 * @return uint32 instanceId
                 */
                int GetInstanceId(lua_State* L, Map* map)
                {
                    return 1;
                }
            }
            """,
        )
        self.write(
            "src/LuaEngine/Hooks.h",
            "\n".join(marker for _path, marker in upgrade.PRIOR_MARKERS),
        )
        subprocess.run(
            ["git", "init", "--quiet", str(self.ale)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def write(self, relative, content):
        destination = self.ale / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            textwrap.dedent(content).lstrip(), encoding="utf-8", newline="\n"
        )

    def config(self):
        return upgrade.Config(
            core_root=self.core,
            lua_root=self.core / "lua",
            lua_source_override=None,
            database_container="database",
            worldserver_container="world",
            compose_service="world",
            compose_project="project",
            compose_files=(),
            compose_env_files=(),
            backup_root=self.core.parent / "backups",
            ready_pattern=upgrade.READY_DEFAULT,
            readiness_timeout=30,
            stop_timeout=10,
            worldserver_binary="/worldserver",
            allow_development_layout=True,
        )


class InstanceXpUpgradeInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = NativeFixture(self.temporary.name)

    def test_incremental_native_transition_is_semantic_and_idempotent(self):
        prior = upgrade.assess_native(self.fixture.ale)
        self.assertEqual("prior", prior.state, prior.explanation)

        layout = upgrade.Layout(
            release_root=ROOT,
            lua_source=ROOT / "serverside" / "paragon",
            cumulative_patches=ROOT / "patches",
            incremental_patch=(
                ROOT
                / "upgrades"
                / "instance-xp-v1"
                / "native"
                / "mod-ale-instance-xp.patch"
            ),
            sql=ROOT / "upgrades" / "instance-xp-v1" / "sql" / "instance-xp.sql",
            packaged=False,
        )
        upgrade.apply_native(
            self.fixture.config(), layout.incremental_patch,
            upgrade.sha256_file(layout.incremental_patch),
        )

        target = upgrade.assess_native(self.fixture.ale)
        self.assertEqual("target", target.state, target.explanation)
        self.assertEqual("target", upgrade.assess_native(self.fixture.ale).state)

    def test_partial_native_transition_is_refused(self):
        functions = self.fixture.ale / "src/LuaEngine/LuaFunctions.cpp"
        source = functions.read_text(encoding="utf-8")
        functions.write_text(
            source.replace(
                '{ "GetDifficulty", &LuaMap::GetDifficulty },',
                '{ "GetExpansion", &LuaMap::GetExpansion },',
            ),
            encoding="utf-8",
            newline="\n",
        )
        assessment = upgrade.assess_native(self.fixture.ale)
        self.assertEqual("partial", assessment.state, assessment.explanation)

    def test_compose_selection_must_resolve_the_inspected_container(self):
        config = self.fixture.config()
        inspect = {
            "Id": "a" * 64,
            "Config": {
                "Image": "example/world:current",
                "Labels": {
                    "com.docker.compose.service": "world",
                    "com.docker.compose.project": "project",
                },
            },
        }
        with mock.patch.object(
            upgrade,
            "output",
            side_effect=("world\n", "b" * 64 + "\n", "example/world:current\n"),
        ), self.assertRaisesRegex(upgrade.UpgradeError, "not inspected container"):
            upgrade.verify_compose(config, inspect)

    def test_rollback_rejects_a_different_docker_selection_before_commands(self):
        run_dir = pathlib.Path(self.temporary.name) / "run"
        run_dir.mkdir()
        journal = {
            "release": upgrade.RELEASE,
            "core_root": str(self.fixture.core),
            "lua_root": str(self.fixture.core / "lua"),
            "database_container": "different-database",
            "worldserver_container": "world",
            "compose_service": "world",
            "compose_project": "project",
            "compose_files": [],
            "steps": [],
        }
        with mock.patch.object(upgrade, "compose") as compose, \
                self.assertRaisesRegex(upgrade.UpgradeError, "selection differs"):
            upgrade.rollback(self.fixture.config(), run_dir, journal)
        compose.assert_not_called()

    def test_mutations_are_intent_journaled_before_execution(self):
        source = inspect.getsource(upgrade.apply_upgrade)
        for intent, mutation in (
            ('record(journal_path, journal, "native-patching", native_changed=True)',
             "apply_native(config, staged_patch"),
            ('record(journal_path, journal, "database-applying",',
             "mysql(config, sql_text)"),
            ('record(journal_path, journal, "lua-deploying")',
             "atomic_replace_verified_file("),
        ):
            with self.subTest(intent=intent):
                self.assertIn(intent, source)
                self.assertIn(mutation, source)
                self.assertLess(source.index(intent), source.index(mutation))

    def test_keyboard_interrupt_rolls_back_before_it_is_reraised(self):
        config = self.fixture.config()
        layout = upgrade.Layout(
            release_root=ROOT,
            lua_source=ROOT / "serverside" / "paragon",
            cumulative_patches=ROOT / "patches",
            incremental_patch=(
                ROOT
                / "upgrades"
                / "instance-xp-v1"
                / "native"
                / "mod-ale-instance-xp.patch"
            ),
            sql=ROOT / "upgrades" / "instance-xp-v1" / "sql" / "instance-xp.sql",
            packaged=False,
        )
        target = upgrade.sha256_file(
            layout.lua_source / upgrade.FOCAL_LUA_RELATIVE
        )
        transition = upgrade.LuaTransition(
            baseline_sha256=upgrade.BASELINE_FOCAL_SHA256,
            target_sha256=target,
            baseline_crlf_sha256=upgrade.BASELINE_FOCAL_CRLF_SHA256,
            target_crlf_sha256=upgrade.crlf_variant_sha256(
                layout.lua_source / upgrade.FOCAL_LUA_RELATIVE
            ),
        )
        assessment = upgrade.NativeAssessment("prior", "fixture")
        artifacts = upgrade.PackageArtifacts(
            incremental_patch_sha256=upgrade.sha256_file(layout.incremental_patch),
            sql_sha256=upgrade.sha256_file(layout.sql),
        )
        compose_contract = upgrade.ComposeContract("c" * 64, "d" * 64, ())
        inspect_data = {
            "State": {"Running": True},
            "Image": "sha256:" + "a" * 64,
            "Config": {"Image": "example/world:test"},
        }
        with mock.patch.object(upgrade, "backup_native", return_value=({}, {})), \
                mock.patch.object(
                    upgrade, "apply_native", side_effect=KeyboardInterrupt
                ), mock.patch.object(upgrade, "rollback") as rollback, \
                self.assertRaises(KeyboardInterrupt):
            upgrade.apply_upgrade(
                config,
                layout,
                assessment,
                inspect_data,
                {},
                upgrade.BASELINE_FOCAL_SHA256,
                compose_contract,
                transition,
                artifacts,
            )
        rollback.assert_called_once()

    def test_lock_identity_does_not_depend_on_caller_backup_root(self):
        first = self.fixture.config()
        second = upgrade.Config(
            core_root=first.core_root,
            lua_root=first.lua_root,
            lua_source_override=first.lua_source_override,
            database_container=first.database_container,
            worldserver_container=first.worldserver_container,
            compose_service=first.compose_service,
            compose_project=first.compose_project,
            compose_files=first.compose_files,
            compose_env_files=first.compose_env_files,
            backup_root=pathlib.Path(self.temporary.name) / "elsewhere",
            ready_pattern=first.ready_pattern,
            readiness_timeout=first.readiness_timeout,
            stop_timeout=first.stop_timeout,
            worldserver_binary=first.worldserver_binary,
            allow_development_layout=first.allow_development_layout,
        )
        self.assertEqual(
            upgrade.canonical_lock_path(first), upgrade.canonical_lock_path(second)
        )

    def test_atomic_focal_replacement_preserves_safe_metadata(self):
        root = pathlib.Path(self.temporary.name)
        source = root / "new.lua"
        destination = root / "installed.lua"
        source.write_bytes(b"target\n")
        destination.write_bytes(b"baseline\n")
        destination.chmod(0o640)
        metadata = upgrade.safe_file_metadata(destination)
        expected = upgrade.sha256_file(source)

        upgrade.atomic_replace_verified_file(
            source, destination, expected, metadata
        )

        self.assertEqual(expected, upgrade.sha256_file(destination))
        self.assertTrue(upgrade.metadata_equivalent(
            upgrade.safe_file_metadata(destination), metadata
        ))

    def test_native_backup_is_private_and_restore_preserves_source_mode(self):
        config = self.fixture.config()
        run_dir = pathlib.Path(self.temporary.name) / "native-backup"
        run_dir.mkdir()
        source = self.fixture.ale / upgrade.NATIVE_FILES[0]
        source.chmod(0o640)
        original_hash = upgrade.sha256_file(source)
        original_metadata = upgrade.safe_file_metadata(source)

        hashes, metadata = upgrade.backup_native(config, run_dir)
        backup = run_dir / "backup/native" / upgrade.NATIVE_FILES[0]
        if upgrade.os.name != "nt":
            self.assertEqual(0o600, upgrade.stat.S_IMODE(backup.stat().st_mode))
        source.write_text("mutated\n", encoding="utf-8")
        upgrade.restore_native(config, run_dir, hashes, metadata)

        self.assertEqual(original_hash, upgrade.sha256_file(source))
        self.assertTrue(upgrade.metadata_equivalent(
            upgrade.safe_file_metadata(source), original_metadata
        ))

    def test_compose_service_hash_must_match_live_container_label(self):
        config = self.fixture.config()
        inspect_data = {
            "Id": "a" * 64,
            "Config": {
                "Image": "example/world:current",
                "Labels": {
                    "com.docker.compose.service": "world",
                    "com.docker.compose.project": "project",
                    "com.docker.compose.config-hash": "d" * 64,
                },
            },
        }
        with mock.patch.object(
            upgrade,
            "output",
            side_effect=("world\n", "a" * 64 + "\n", "example/world:current\n"),
        ), mock.patch.object(
            upgrade,
            "compose_contract",
            return_value=upgrade.ComposeContract("b" * 64, "e" * 64, ()),
        ), self.assertRaisesRegex(upgrade.UpgradeError, "inspected container records"):
            upgrade.verify_compose(config, inspect_data)

    def test_compose_drift_does_not_block_independent_rollback(self):
        config = self.fixture.config()
        run_dir = pathlib.Path(self.temporary.name) / "rollback-run"
        focal_backup = run_dir / "backup/lua" / upgrade.FOCAL_LUA_RELATIVE
        focal_backup.parent.mkdir(parents=True)
        focal_backup.write_bytes(b"baseline\n")
        metadata = upgrade.safe_file_metadata(focal_backup)
        journal = {
            "release": upgrade.RELEASE,
            "core_root": str(config.core_root),
            "lua_root": str(config.lua_root),
            "database_container": config.database_container,
            "worldserver_container": config.worldserver_container,
            "compose_service": config.compose_service,
            "compose_project": config.compose_project,
            "compose_files": [],
            "compose_env_files": [],
            "compose_config_sha256": "a" * 64,
            "compose_service_config_hash": "b" * 64,
            "steps": ["candidate-started", "lua-deploying", "database-applying"],
            "world_was_running": True,
            "old_image_id": "sha256:" + "a" * 64,
            "focal_backup_sha256": upgrade.sha256_file(focal_backup),
            "focal_original_metadata": metadata.to_json(),
            "config_before": {"saved": "value"},
            "native_changed": True,
            "native_backup_sha256": {"saved": "hash"},
            "native_backup_metadata": {"saved": {}},
        }
        with mock.patch.object(
            upgrade, "require_compose_contract",
            side_effect=upgrade.UpgradeError("compose changed"),
        ), mock.patch.object(upgrade, "stop_worldserver_exact") as stop, \
                mock.patch.object(upgrade, "atomic_replace_verified_file") as lua_restore, \
                mock.patch.object(upgrade, "restore_config") as db_restore, \
                mock.patch.object(upgrade, "restore_native") as native_restore, \
                mock.patch.object(
                    upgrade, "assess_native",
                    return_value=upgrade.NativeAssessment("prior", "restored"),
                ), mock.patch.object(
                    upgrade, "docker_inspect_optional",
                    return_value={"Image": "sha256:" + "b" * 64},
                ), self.assertRaisesRegex(
                    upgrade.UpgradeError, "state was restored"
                ):
            upgrade.rollback(config, run_dir, journal)
        stop.assert_called_once()
        lua_restore.assert_called_once()
        db_restore.assert_called_once_with(config, {"saved": "value"})
        native_restore.assert_called_once()

    def test_build_only_rollback_does_not_stop_the_serving_container(self):
        config = self.fixture.config()
        run_dir = pathlib.Path(self.temporary.name) / "build-only-run"
        run_dir.mkdir()
        journal = {
            "release": upgrade.RELEASE,
            "core_root": str(config.core_root),
            "lua_root": str(config.lua_root),
            "database_container": config.database_container,
            "worldserver_container": config.worldserver_container,
            "compose_service": config.compose_service,
            "compose_project": config.compose_project,
            "compose_files": [],
            "compose_env_files": [],
            "compose_config_sha256": "a" * 64,
            "compose_service_config_hash": "b" * 64,
            "steps": ["candidate-built"],
            "world_was_running": True,
            "native_changed": False,
        }
        with mock.patch.object(
            upgrade, "require_compose_contract",
            side_effect=upgrade.UpgradeError("compose changed"),
        ), mock.patch.object(upgrade, "stop_worldserver_exact") as stop:
            upgrade.rollback(config, run_dir, journal)
        stop.assert_not_called()

    def test_unproven_stop_gates_lua_and_database_but_not_native_restore(self):
        config = self.fixture.config()
        run_dir = pathlib.Path(self.temporary.name) / "failed-stop-run"
        run_dir.mkdir()
        journal = {
            "release": upgrade.RELEASE,
            "core_root": str(config.core_root),
            "lua_root": str(config.lua_root),
            "database_container": config.database_container,
            "worldserver_container": config.worldserver_container,
            "compose_service": config.compose_service,
            "compose_project": config.compose_project,
            "compose_files": [],
            "compose_env_files": [],
            "compose_config_sha256": "a" * 64,
            "compose_service_config_hash": "b" * 64,
            "steps": ["candidate-started", "lua-deploying", "database-applying"],
            "world_was_running": True,
            "native_changed": True,
            "native_backup_sha256": {"saved": "hash"},
            "native_backup_metadata": {"saved": {}},
        }
        with mock.patch.object(upgrade, "require_compose_contract"), \
                mock.patch.object(
                    upgrade, "stop_worldserver_exact",
                    side_effect=upgrade.UpgradeError("still running"),
                ), mock.patch.object(
                    upgrade, "atomic_replace_verified_file"
                ) as lua_restore, mock.patch.object(
                    upgrade, "restore_config"
                ) as db_restore, mock.patch.object(
                    upgrade, "restore_native"
                ) as native_restore, mock.patch.object(
                    upgrade, "assess_native",
                    return_value=upgrade.NativeAssessment("prior", "restored"),
                ), self.assertRaisesRegex(
                    upgrade.UpgradeError, "restore skipped because worldserver stop was not proven"
                ):
            upgrade.rollback(config, run_dir, journal)
        lua_restore.assert_not_called()
        db_restore.assert_not_called()
        native_restore.assert_called_once()

    def test_rollback_directly_starts_intact_old_container_despite_compose_drift(self):
        config = self.fixture.config()
        run_dir = pathlib.Path(self.temporary.name) / "direct-start-run"
        run_dir.mkdir()
        old_id = "sha256:" + "a" * 64
        old_container_id = "e" * 64
        journal = {
            "release": upgrade.RELEASE,
            "core_root": str(config.core_root),
            "lua_root": str(config.lua_root),
            "database_container": config.database_container,
            "worldserver_container": config.worldserver_container,
            "compose_service": config.compose_service,
            "compose_project": config.compose_project,
            "compose_files": [],
            "compose_env_files": [],
            "compose_config_sha256": "c" * 64,
            "compose_service_config_hash": "d" * 64,
            "steps": ["worldserver-stopped"],
            "world_was_running": True,
            "native_changed": False,
            "old_image_id": old_id,
            "old_container_id": old_container_id,
        }
        with mock.patch.object(
            upgrade, "require_compose_contract",
            side_effect=upgrade.UpgradeError("compose changed"),
        ), mock.patch.object(upgrade, "stop_worldserver_exact"), \
                mock.patch.object(
                    upgrade, "docker_inspect_optional",
                    return_value={"Id": old_container_id, "Image": old_id}
                ), mock.patch.object(
                    upgrade, "docker_inspect",
                    return_value={"Id": old_container_id, "Image": old_id}
                ), mock.patch.object(upgrade, "wait_ready") as ready, \
                mock.patch.object(upgrade, "run") as run_command:
            upgrade.rollback(config, run_dir, journal)
        run_command.assert_called_once_with(
            ("docker", "container", "start", config.worldserver_container)
        )
        ready.assert_called_once()

    def test_staged_patch_and_sql_are_the_only_mutation_inputs(self):
        source = inspect.getsource(upgrade.apply_upgrade)
        staged = source.index('"package-artifacts-staged"')
        self.assertLess(staged, source.index("backup_native(config, run_dir)"))
        self.assertIn("apply_native(config, staged_patch", source)
        self.assertIn("read_verified_text(staged_sql, artifacts.sql_sha256)", source)
        self.assertNotIn("layout.sql.read_text", source)

    def test_malformed_rollback_metadata_is_rejected(self):
        for value in (
            {"mode": 0o4755, "uid": 0, "gid": 0},
            {"mode": 0o644, "uid": -1, "gid": 0},
            {"mode": True, "uid": 0, "gid": 0},
        ):
            with self.subTest(value=value), self.assertRaises(upgrade.UpgradeError):
                upgrade.FileMetadata.from_json(value)

    def test_exact_crlf_variants_are_accepted_without_broad_normalization(self):
        root = pathlib.Path(self.temporary.name)
        lf = root / "lf.lua"
        crlf = root / "crlf.lua"
        mixed = root / "mixed.lua"
        lf.write_bytes(b"local x = 1\nreturn x\n")
        crlf.write_bytes(lf.read_bytes().replace(b"\n", b"\r\n"))
        mixed.write_bytes(b"local x = 1\r\nreturn x\n")
        transition = upgrade.LuaTransition(
            baseline_sha256="a" * 64,
            target_sha256=upgrade.sha256_file(lf),
            baseline_crlf_sha256="b" * 64,
            target_crlf_sha256=upgrade.crlf_variant_sha256(lf),
        )
        self.assertIn(upgrade.sha256_file(lf), transition.accepted_installed_hashes)
        self.assertIn(upgrade.sha256_file(crlf), transition.accepted_installed_hashes)
        self.assertNotIn(upgrade.sha256_file(mixed), transition.accepted_installed_hashes)
        with self.assertRaisesRegex(upgrade.UpgradeError, "LF only"):
            upgrade.crlf_variant_sha256(mixed)

    def test_private_directory_refuses_an_existing_symlink(self):
        if not hasattr(pathlib.Path, "symlink_to"):
            self.skipTest("symlinks unsupported")
        root = pathlib.Path(self.temporary.name)
        real = root / "real"
        link = root / "link"
        real.mkdir()
        try:
            link.symlink_to(real, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"cannot create symlink: {error}")
        with self.assertRaisesRegex(upgrade.UpgradeError, "not a real directory"):
            upgrade.make_private_directory(link)

    @unittest.skipIf(sys.platform == "win32", "POSIX mode semantics")
    def test_private_json_and_staged_artifact_modes(self):
        root = pathlib.Path(self.temporary.name) / "private"
        journal = root / "journal.json"
        upgrade.atomic_json(journal, {"ok": True})
        self.assertEqual(0o700, upgrade.stat.S_IMODE(root.stat().st_mode))
        self.assertEqual(0o600, upgrade.stat.S_IMODE(journal.stat().st_mode))

        source = pathlib.Path(self.temporary.name) / "source.sql"
        staged = root / "staged" / "migration.sql"
        source.write_bytes(b"SELECT 1;\n")
        digest = upgrade.sha256_file(source)
        upgrade.stage_verified_artifact(source, staged, digest)
        self.assertEqual(0o444, upgrade.stat.S_IMODE(staged.stat().st_mode))


if __name__ == "__main__":
    unittest.main()
