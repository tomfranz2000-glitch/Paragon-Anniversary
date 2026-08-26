import hashlib
import json
import pathlib
import subprocess
import tempfile
import unittest
import zipfile
from unittest import mock

import build_instance_xp_upgrade as builder


def run_git(repository, *arguments):
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


class InstanceXpUpgradeBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()

        run_git(self.repository, "init", "-b", "main")
        run_git(self.repository, "config", "user.name", "Upgrade Builder Test")
        run_git(self.repository, "config", "user.email", "builder@example.invalid")

        self.write("upgrades/instance-xp-v1/install.py", "#!/usr/bin/env python3\n")
        self.write("upgrades/instance-xp-v1/README.md", "upgrade instructions\n")
        self.write("upgrades/instance-xp-v1/RELEASE_NOTES.md", "release notes\n")
        self.write("upgrades/instance-xp-v1/manifest.json", "{}\n")
        self.write(
            "upgrades/instance-xp-v1/native/mod-ale-instance-xp.patch",
            "incremental patch\n",
        )
        self.write("upgrades/instance-xp-v1/sql/instance-xp.sql", "SELECT 1;\n")
        self.write("serverside/paragon/paragon.lua", "return 'committed runtime'\n")
        self.write(
            "serverside/paragon/modules/paragon_rework_sources.lua",
            "return 'baseline focal'\n",
        )
        self.write("serverside/paragon/modules/.gitkeep", "")
        self.write("patches/05-mod-ale.patch", "patch 05\n")
        self.write("patches/07-mod-ale-profession-xp.patch", "patch 07\n")
        self.write("patches/09-mod-ale-pvp-merit.patch", "patch 09\n")
        self.write("sql/04_insert_default_config.sql", "SELECT 'baseline 04';\n")
        self.write("sql/05_apply_anniversary_config.sql", "SELECT 'baseline 05';\n")
        self.write(
            "patches/PINS.md",
            "\n".join(pin["commit"] for pin in builder.PINS.values()) + "\n",
        )
        self.write("LICENSE", "test license\n")

        run_git(self.repository, "add", ".")
        run_git(self.repository, "commit", "-m", "baseline fixture")
        self.baseline_commit = run_git(self.repository, "rev-parse", "HEAD")

        self.write(
            "serverside/paragon/modules/paragon_rework_sources.lua",
            "return 'target focal'\n",
        )
        self.write("patches/05-mod-ale.patch", "target patch 05\n")
        self.write("sql/04_insert_default_config.sql", "SELECT 'target 04';\n")
        self.write("sql/05_apply_anniversary_config.sql", "SELECT 'target 05';\n")
        run_git(self.repository, "add", ".")
        run_git(self.repository, "commit", "-m", "target fixture")
        self.commit = run_git(self.repository, "rev-parse", "HEAD")
        baseline_patch = mock.patch.object(
            builder, "BASELINE_COMMIT", self.baseline_commit
        )
        baseline_patch.start()
        self.addCleanup(baseline_patch.stop)

    def write(self, relative_path, content):
        destination = self.repository / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8", newline="\n")

    def build(self, output_name):
        return builder.build_upgrade(
            self.repository,
            ref=self.commit,
            output_dir=self.root / output_name,
            allow_unpublished=True,
        )

    def test_archive_name_and_bytes_are_deterministic(self):
        first = self.build("first")
        second = self.build("second")

        expected_name = "Paragon-Anniversary-upgrade-%s-to-%s.zip" % (
            self.baseline_commit[:7],
            self.commit[:7],
        )
        self.assertEqual(expected_name, first.name)
        self.assertEqual(expected_name, second.name)
        self.assertEqual(first.read_bytes(), second.read_bytes())

        first_sidecar = pathlib.Path(str(first) + ".sha256")
        second_sidecar = pathlib.Path(str(second) + ".sha256")
        expected_sidecar = "%s  %s\n" % (
            hashlib.sha256(first.read_bytes()).hexdigest(),
            expected_name,
        )
        self.assertEqual(expected_name + ".sha256", first_sidecar.name)
        self.assertEqual(expected_sidecar, first_sidecar.read_text(encoding="ascii"))
        self.assertEqual(first_sidecar.read_bytes(), second_sidecar.read_bytes())

        with zipfile.ZipFile(first) as archive:
            timestamps = {entry.date_time for entry in archive.infolist()}
            compression = {entry.compress_type for entry in archive.infolist()}
        self.assertEqual({(1980, 1, 1, 0, 0, 0)}, timestamps)
        self.assertEqual({zipfile.ZIP_STORED}, compression)

    def test_manifest_checksums_and_runtime_are_from_commit(self):
        # Local-test mode may consume an uncommitted template, but server files
        # must still be immutable blobs from the requested commit.
        self.write(
            "serverside/paragon/modules/paragon_rework_sources.lua",
            "return 'dirty runtime'\n",
        )
        archive_path = self.build("release")
        root_name = archive_path.stem

        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            relative_names = sorted(
                name[len(root_name) + 1 :]
                for name in names
                if name.startswith(root_name + "/")
            )
            manifest = json.loads(archive.read(root_name + "/RELEASE.json"))
            checksums = archive.read(root_name + "/SHA256SUMS").decode("ascii")

            runtime_path = root_name + "/" + builder.SERVER_LUA_FOCAL_PAYLOAD
            self.assertEqual(b"return 'target focal'\n", archive.read(runtime_path))
            self.assertNotIn(root_name + "/server/serverside/paragon/paragon.lua", names)
            self.assertNotIn(
                root_name + "/server/serverside/paragon/modules/.gitkeep", names
            )
            self.assertIn(root_name + "/install.py", names)
            self.assertIn(root_name + "/README.md", names)
            self.assertFalse(any("/upgrades/" in name for name in names))
            self.assertIn(root_name + "/patches/05-mod-ale.patch", names)
            self.assertIn(root_name + "/patches/07-mod-ale-profession-xp.patch", names)
            self.assertIn(root_name + "/patches/09-mod-ale-pvp-merit.patch", names)
            self.assertIn(root_name + "/patches/PINS.md", names)
            self.assertIn(root_name + "/LICENSE", names)

            self.assertEqual(builder.BASELINE_COMMIT, manifest["baselineCommit"])
            self.assertEqual(self.commit, manifest["targetCommit"])
            self.assertIs(False, manifest["clientChanges"])
            self.assertEqual(builder.PINS, manifest["pins"])

            transition = manifest["serverLuaTransition"]
            self.assertEqual(builder.SERVER_LUA_FOCAL_PAYLOAD, transition["path"])
            baseline_blob = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repository),
                    "show",
                    "%s:%s" % (
                        self.baseline_commit,
                        builder.SERVER_LUA_FOCAL_SOURCE,
                    ),
                ],
                check=True,
                stdout=subprocess.PIPE,
            ).stdout
            target_blob = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repository),
                    "show",
                    "%s:%s" % (self.commit, builder.SERVER_LUA_FOCAL_SOURCE),
                ],
                check=True,
                stdout=subprocess.PIPE,
            ).stdout
            self.assertEqual(
                hashlib.sha256(baseline_blob).hexdigest(),
                transition["baselineSha256"],
            )
            self.assertNotIn(b"\r", baseline_blob)
            self.assertEqual(
                hashlib.sha256(target_blob).hexdigest(),
                transition["targetSha256"],
            )
            self.assertNotIn(b"\r", target_blob)
            self.assertEqual(
                hashlib.sha256(baseline_blob.replace(b"\n", b"\r\n")).hexdigest(),
                transition["baselineCrlfSha256"],
            )
            self.assertEqual(
                hashlib.sha256(target_blob.replace(b"\n", b"\r\n")).hexdigest(),
                transition["targetCrlfSha256"],
            )
            self.assertEqual(
                transition["targetSha256"],
                hashlib.sha256(
                    archive.read(root_name + "/" + transition["path"])
                ).hexdigest(),
            )

            parsed = []
            for line in checksums.splitlines():
                digest, relative_path = line.split("  ", 1)
                parsed.append(relative_path)
                actual = hashlib.sha256(
                    archive.read(root_name + "/" + relative_path)
                ).hexdigest()
                self.assertEqual(digest, actual, relative_path)
            self.assertEqual(sorted(parsed), parsed)
            self.assertEqual(
                [name for name in relative_names if name != "SHA256SUMS"],
                parsed,
            )

            lowered = [name.lower() for name in names]
            self.assertFalse(any(name.endswith((".mpq", ".dbc")) for name in lowered))
            self.assertFalse(any("/__pycache__/" in name for name in lowered))
            self.assertFalse(any("/cache/" in name for name in lowered))

    def test_release_build_rejects_dirty_worktree(self):
        self.write("serverside/paragon/paragon.lua", "return 'dirty runtime'\n")
        output_dir = self.root / "strict"
        with self.assertRaisesRegex(builder.BuildError, "clean worktree"):
            builder.build_upgrade(
                self.repository,
                ref=self.commit,
                output_dir=output_dir,
                allow_unpublished=False,
            )
        self.assertFalse(output_dir.exists())

    def test_template_payload_policy_rejects_client_and_secret_probes(self):
        probes = (
            "clientside/probe.lua",
            "Interface/AddOns/Probe/Probe.lua",
            "image.blp",
            "addon.toc",
            "account.wtf",
            ".aws/credentials",
            ".npmrc",
            "service-account.json",
            "token",
            "unexpected.txt",
        )
        template = self.repository / builder.TEMPLATE_PATH
        for probe in probes:
            with self.subTest(probe=probe):
                path = template / probe
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("must not ship\n", encoding="utf-8", newline="\n")
                with self.assertRaises(builder.BuildError):
                    self.build("probe")
                path.unlink()

    def test_scope_gate_rejects_an_unhandled_runtime_change(self):
        self.write("serverside/paragon/paragon.lua", "return 'unsupported target'\n")
        self.write("clientside/Interface/AddOns/Probe/Probe.toc", "client leak\n")
        self.write("patches/07-mod-ale-profession-xp.patch", "unexpected native delta\n")
        run_git(self.repository, "add", ".")
        run_git(self.repository, "commit", "-m", "unsupported runtime delta")
        unsupported_commit = run_git(self.repository, "rev-parse", "HEAD")

        with self.assertRaisesRegex(
            builder.BuildError,
            "clientside/Interface/AddOns/Probe/Probe.toc.*patches/07.*paragon.lua",
        ):
            builder.build_upgrade(
                self.repository,
                ref=unsupported_commit,
                output_dir=self.root / "unsupported",
                allow_unpublished=True,
            )

    def test_transition_rejects_noncanonical_git_blob_line_endings(self):
        focal = self.repository / builder.SERVER_LUA_FOCAL_SOURCE
        crlf_blob = b"return 'target focal'\r\n"
        focal.write_bytes(crlf_blob)
        blob_sha = subprocess.run(
            ["git", "-C", str(self.repository), "hash-object", "-w", "--stdin"],
            input=crlf_blob,
            check=True,
            stdout=subprocess.PIPE,
        ).stdout.decode("ascii").strip()
        run_git(
            self.repository,
            "update-index",
            "--cacheinfo",
            "100644",
            blob_sha,
            builder.SERVER_LUA_FOCAL_SOURCE,
        )
        run_git(self.repository, "commit", "-m", "noncanonical focal")
        noncanonical_commit = run_git(self.repository, "rev-parse", "HEAD")

        with self.assertRaisesRegex(builder.BuildError, "canonical LF"):
            builder.build_upgrade(
                self.repository,
                ref=noncanonical_commit,
                output_dir=self.root / "noncanonical",
                allow_unpublished=True,
            )


if __name__ == "__main__":
    unittest.main()
