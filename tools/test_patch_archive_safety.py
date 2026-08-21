import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

import build_mpq
import check_patch_collisions
import paragon_client_patch


class PatchArchiveSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = os.path.join(self.temp.name, "stage")
        os.makedirs(os.path.join(self.source, "DBFilesClient"))
        self.source_file = os.path.join(
            self.source, "DBFilesClient", "Spell.dbc")
        with open(self.source_file, "wb") as f:
            f.write(b"first")
        self.output = os.path.join(self.temp.name, "patch-X.MPQ")

    def test_owned_archive_can_be_rebuilt(self):
        names = build_mpq.build(self.source, self.output)
        self.assertIn(build_mpq.OWNER_MARKER_NAME, names)
        self.assertTrue(build_mpq.is_owned_archive(self.output))
        self.assertEqual(
            b"first", build_mpq.read_file(self.output, "DBFilesClient\\Spell.dbc"))
        from mpyq import MPQArchive
        archive = MPQArchive(self.output)
        try:
            self.assertEqual(
                build_mpq.OWNER_MARKER_CONTENT,
                archive.read_file(build_mpq.OWNER_MARKER_NAME.encode("ascii")))
        finally:
            archive.file.close()

        with open(self.source_file, "wb") as f:
            f.write(b"second")
        build_mpq.build(self.source, self.output)
        self.assertEqual(
            b"second", build_mpq.read_file(self.output, "DBFilesClient\\Spell.dbc"))

    def test_unmarked_target_is_refused_without_modification(self):
        sentinel = b"third-party archive"
        with open(self.output, "wb") as f:
            f.write(sentinel)

        with self.assertRaises(build_mpq.UnsafeArchiveError):
            build_mpq.build(self.source, self.output)
        with open(self.output, "rb") as f:
            self.assertEqual(sentinel, f.read())

    def test_failed_verification_preserves_previous_archive(self):
        build_mpq.build(self.source, self.output)
        with open(self.output, "rb") as f:
            original = f.read()
        with open(self.source_file, "wb") as f:
            f.write(b"replacement")

        with mock.patch.object(build_mpq, "verify", side_effect=AssertionError("bad build")):
            with self.assertRaisesRegex(AssertionError, "bad build"):
                build_mpq.build(self.source, self.output)
        with open(self.output, "rb") as f:
            self.assertEqual(original, f.read())

    def test_patch_names_require_a_single_suffix_character(self):
        self.assertEqual(
            "patch-Y.MPQ", build_mpq.validate_patch_name("patch-Y.MPQ"))
        self.assertEqual(
            "patch-enUS-5.MPQ",
            build_mpq.validate_patch_name("patch-enUS-5.MPQ", locale=True))
        for bad in ("patch-10.MPQ", "patch.MPQ", "../patch-Y.MPQ",
                    "patch-enUS-Y.MPQ"):
            with self.subTest(name=bad):
                with self.assertRaises(ValueError):
                    build_mpq.validate_patch_name(bad)

        for bad in ("patch-Y.MPQ", "patch-enUS-10.MPQ", "sub/patch-enUS-Y.MPQ"):
            with self.subTest(locale_name=bad):
                with self.assertRaises(ValueError):
                    build_mpq.validate_patch_name(bad, locale=True)

    def test_generator_refuses_foreign_target_before_touching_stages(self):
        client_data = os.path.join(self.temp.name, "Client", "Data")
        os.makedirs(os.path.join(client_data, "enUS"))
        target = os.path.join(client_data, "patch-Y.MPQ")
        with open(target, "wb") as f:
            f.write(b"third-party archive")
        locale_stage = os.path.join(self.temp.name, "stage-locale", "DBFilesClient")
        general_stage = os.path.join(self.temp.name, "stage-general", "DBFilesClient")

        with mock.patch.multiple(
                paragon_client_patch,
                CLIENT_DATA=client_data,
                STAGE_LOCALE=locale_stage,
                STAGE_GENERAL=general_stage), mock.patch(
                    "sys.argv", ["paragon_client_patch.py",
                                 "--general-name", "patch-Y.MPQ",
                                 "--locale-name", "patch-enUS-Y.MPQ"]):
            with self.assertRaisesRegex(SystemExit, "refusing to overwrite unowned"):
                paragon_client_patch.main()

        self.assertFalse(os.path.exists(locale_stage))
        self.assertFalse(os.path.exists(general_stage))
        with open(target, "rb") as f:
            self.assertEqual(b"third-party archive", f.read())

    def test_collision_checker_does_not_claim_foreign_default_name(self):
        client_data = os.path.join(self.temp.name, "Client", "Data")
        os.makedirs(os.path.join(client_data, "enUS"))
        with open(os.path.join(client_data, "Patch-X.MPQ"), "wb") as f:
            f.write(b"third-party archive")

        output = io.StringIO()
        with mock.patch.object(check_patch_collisions, "CLIENT_DATA", client_data), \
                mock.patch("sys.argv", ["check_patch_collisions.py"]), \
                contextlib.redirect_stdout(output):
            self.assertEqual(1, check_patch_collisions.main())
        self.assertIn("NOT Paragon-owned", output.getvalue())


if __name__ == "__main__":
    unittest.main()
