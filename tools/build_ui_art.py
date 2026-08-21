#!/usr/bin/env python3
"""Build the Paragon UI-art archive from the tracked 14 BLP files.

The addon itself must remain a normal Interface/AddOns/Paragon directory.
Only clientside/Interface outside AddOns is staged into patch-W.MPQ.
"""
import argparse
import os
import shutil
import sys
import tempfile

from build_mpq import UnsafeArchiveError, build, validate_patch_name


HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
ART_ROOT = os.path.join(REPO_ROOT, "clientside", "Interface")
DEFAULT_CLIENT_DATA = os.path.abspath(os.environ.get(
    "PARAGON_CLIENT_DATA", os.path.join(REPO_ROOT, "Client", "Data")))
EXPECTED_ART_FILES = 14


def source_entries():
    """Return [(archive path, source path)] for art, never addon files."""
    entries = []
    for root, dirs, names in os.walk(ART_ROOT):
        dirs[:] = [name for name in dirs if name.lower() != "addons"]
        for name in names:
            source = os.path.join(root, name)
            relative = os.path.relpath(source, os.path.dirname(ART_ROOT))
            archive_name = relative.replace("/", "\\")
            entries.append((archive_name, source))
    entries.sort()
    if len(entries) != EXPECTED_ART_FILES:
        raise ValueError("expected %d UI art files, found %d"
                         % (EXPECTED_ART_FILES, len(entries)))
    unexpected = [name for name, _source in entries
                  if not name.lower().endswith(".blp")
                  or "\\addons\\" in name.lower()]
    if unexpected:
        raise ValueError("unexpected UI archive sources: %s"
                         % ", ".join(unexpected))
    return entries


def stage_sources(stage_root, entries):
    for archive_name, source in entries:
        destination = os.path.join(stage_root,
                                   archive_name.replace("\\", os.sep))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy2(source, destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--client-data", default=DEFAULT_CLIENT_DATA,
        help="WoW Data directory (default: PARAGON_CLIENT_DATA or %(default)s)")
    parser.add_argument(
        "--output-name", default="patch-W.MPQ", metavar="PATCH-?.MPQ",
        help="general patch archive basename (default: %(default)s)")
    args = parser.parse_args()
    try:
        validate_patch_name(args.output_name)
        entries = source_entries()
        output = os.path.abspath(os.path.join(args.client_data,
                                              args.output_name))
        with tempfile.TemporaryDirectory(prefix="paragon-ui-art-") as stage:
            stage_sources(stage, entries)
            built = build(stage, output)
    except (ValueError, UnsafeArchiveError) as exc:
        sys.exit(str(exc))
    print("OK: %d UI art files -> %s" % (len(entries), output))
    print("archive records including ownership metadata: %d" % len(built))


if __name__ == "__main__":
    main()
