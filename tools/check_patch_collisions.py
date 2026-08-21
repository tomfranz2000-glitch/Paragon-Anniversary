# -*- coding: utf-8 -*-
"""Does any third-party client patch shadow a file we own?

Run this after dropping ANY new patch-*.MPQ into Client/Data. It rebuilds the
client's real mount ladder and then, for every file the Paragon patches ship,
reports which archive actually wins.

    python check_patch_collisions.py
    python check_patch_collisions.py --ui-name patch-V.MPQ \
        --general-name patch-Y.MPQ \
        --locale-name patch-enUS-Y.MPQ

WHY THIS EXISTS
---------------
Patch priority is decided by the archive NAME, not by install order, and the
rule is not obvious (see doc 2t):

  * Wow.exe searches Data\\patch-?.MPQ and Data\\<loc>\\patch-<loc>-?.MPQ --
    '?' being a raw Win32 FindFirstFile wildcard, so EXACTLY ONE character.
    "patch-10.MPQ" is never found at all.
  * The matches are sorted DESCENDING by FULL PATH (comparator 0x401200 =
    -SStrCmpI) and mounted BACKWARDS (0x405E90) with SFileOpenArchive's
    priority counting UP, so the alphabetically HIGHEST name mounts LAST and
    WINS.
  * SStrCmpI folds A-Z by +0x20, so the compare is case-insensitive and digits
    rank BELOW letters.
  * Because the sort is on the full path, "Data\\p..." beats "Data\\enUS\\...":
    EVERY general patch outranks EVERY locale patch.

A collision is silent. Nothing errors, nothing logs -- the DBC or texture just
comes from somebody else's archive.

WHY IT PROBES HASHES INSTEAD OF READING (listfile)
--------------------------------------------------
An MPQ stores only name HASHES; (listfile) is an ordinary file inside the
archive that the author may simply omit, and plenty of patch authors do. That
makes contents unenumerable but never unqueryable: we know exactly which names
we care about, so we hash those and look them up. The listfile, when present,
is used only for the informational per-archive summary.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DATA = os.path.abspath(os.environ.get(
    "PARAGON_CLIENT_DATA", os.path.join(HERE, "..", "Client", "Data")))
LOCALE = "enUS"

DEFAULT_UI_NAME = "patch-W.MPQ"
DEFAULT_GENERAL_NAME = "patch-X.MPQ"
DEFAULT_LOCALE_NAME = "patch-enUS-X.MPQ"

sys.path.insert(0, HERE)
from build_mpq import (hash_string, decrypt_block,          # noqa: E402
                       HASH_TABLE_OFFSET, HASH_NAME_A, HASH_NAME_B,
                       HASH_FILE_KEY, OWNER_MARKER_NAME,
                       is_owned_archive, validate_patch_name)


# --------------------------------------------------------------------------
# 1. discovery -- the same two wildcards the client uses, via the same API
# --------------------------------------------------------------------------
class _FindData(ctypes.Structure):
    _fields_ = [("attrs", wt.DWORD), ("ctime", wt.FILETIME),
                ("atime", wt.FILETIME), ("mtime", wt.FILETIME),
                ("size_hi", wt.DWORD), ("size_lo", wt.DWORD),
                ("r0", wt.DWORD), ("r1", wt.DWORD),
                ("name", wt.WCHAR * 260), ("alt_name", wt.WCHAR * 14)]


_k32 = ctypes.windll.kernel32
_k32.FindFirstFileW.restype = ctypes.c_void_p
_k32.FindFirstFileW.argtypes = [wt.LPCWSTR, ctypes.POINTER(_FindData)]
_k32.FindNextFileW.restype = wt.BOOL
_k32.FindNextFileW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_FindData)]
_k32.FindClose.argtypes = [ctypes.c_void_p]
_INVALID = ctypes.c_void_p(-1).value


def win32_glob(pattern):
    """Real FindFirstFile semantics -- fnmatch is NOT equivalent here."""
    data = _FindData()
    handle = _k32.FindFirstFileW(pattern, ctypes.byref(data))
    if handle in (None, _INVALID):
        return []
    out = []
    while True:
        out.append(data.name)
        if not _k32.FindNextFileW(handle, ctypes.byref(data)):
            break
    _k32.FindClose(handle)
    return out


def mount_ladder():
    """Return [(priority, relative_path)], lowest priority first."""
    found = []
    for subdir, pattern in ((".", "patch-?.MPQ"),
                            (LOCALE, "patch-%s-?.MPQ" % LOCALE)):
        base = CLIENT_DATA if subdir == "." else os.path.join(CLIENT_DATA, subdir)
        for name in win32_glob(os.path.join(base, pattern)):
            rel = name if subdir == "." else os.path.join(subdir, name)
            found.append(rel)
    # comparator 0x401200 is -SStrCmpI, i.e. descending and case-insensitive
    found.sort(key=lambda s: s.lower(), reverse=True)
    # pass 2 appends these AFTER the sort, so they land at the very bottom
    found += ["patch.MPQ", os.path.join(LOCALE, "patch-%s.MPQ" % LOCALE)]
    # the client walks the array backwards with the priority counting up
    return list(enumerate(reversed(found)))


# --------------------------------------------------------------------------
# 2. querying -- hash-table probe, no (listfile) required
# --------------------------------------------------------------------------
def _read_header(raw):
    """MPQ headers are aligned to 512 bytes and need not sit at offset 0."""
    for off in range(0, min(len(raw), 0x100000), 512):
        if raw[off:off + 4] == b"MPQ\x1a":
            hdr = struct.unpack_from("<4sIIHHIIII", raw, off)
            return off, hdr
    return None, None


def archive_contains(path, names):
    """Which of `names` exist in this archive? Returns a set, or None if the
    archive could not be parsed (reported rather than silently treated as
    empty -- an unreadable archive is not a safe archive)."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        print("    ! unreadable: %s" % exc)
        return None
    base, hdr = _read_header(raw)
    if hdr is None:
        print("    ! no MPQ header found")
        return None
    _magic, _hsize, _asize, ver, _bshift, hpos, _bpos, hcount, _bcount = hdr
    if ver > 1:
        print("    ! MPQ format version %d not handled" % ver)
        return None
    start = base + hpos
    blob = raw[start:start + hcount * 16]
    if len(blob) != hcount * 16:
        print("    ! truncated hash table")
        return None
    table = decrypt_block(blob, hash_string("(hash table)", HASH_FILE_KEY))
    entries = [struct.unpack_from("<4I", table, i * 16) for i in range(hcount)]

    present = set()
    for name in names:
        slot = hash_string(name, HASH_TABLE_OFFSET) & (hcount - 1)
        want_a = hash_string(name, HASH_NAME_A)
        want_b = hash_string(name, HASH_NAME_B)
        idx = slot
        while True:
            a, b, _loc, block = entries[idx]
            if block == 0xFFFFFFFF:          # empty slot ends the probe chain
                break
            if a == want_a and b == want_b:
                present.add(name)
                break
            idx = (idx + 1) & (hcount - 1)
            if idx == slot:
                break
    return present


def listfile_of(path):
    """Best-effort content summary; None when the author omitted (listfile)."""
    try:
        from mpyq import MPQArchive
        archive = MPQArchive(path)
        try:
            raw = archive.read_file(b"(listfile)")
        finally:
            archive.file.close()
    except Exception:
        return None
    if raw is None:
        return None
    return [x for x in raw.decode("latin-1").replace("\r\n", "\n").split("\n") if x.strip()]


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ui-name", default=DEFAULT_UI_NAME,
                    metavar="PATCH-?.MPQ")
    ap.add_argument("--general-name", default=DEFAULT_GENERAL_NAME,
                    metavar="PATCH-?.MPQ")
    ap.add_argument("--locale-name", default=DEFAULT_LOCALE_NAME,
                    metavar="PATCH-ENUS-?.MPQ")
    args = ap.parse_args()
    try:
        validate_patch_name(args.ui_name)
        validate_patch_name(args.general_name)
        validate_patch_name(args.locale_name, locale=True)
    except ValueError as exc:
        ap.error(str(exc))

    generated = [args.general_name,
                 os.path.join(LOCALE, args.locale_name)]
    foreign_targets = [rel for rel in generated
                       if os.path.exists(os.path.join(CLIENT_DATA, rel))
                       and not is_owned_archive(os.path.join(CLIENT_DATA, rel))]
    ours = [args.ui_name] + [rel for rel in generated
                               if os.path.exists(os.path.join(CLIENT_DATA, rel))
                               and is_owned_archive(os.path.join(CLIENT_DATA, rel))]
    ladder = mount_ladder()
    ours_rel = set(o.lower() for o in ours)
    configured_rel = set(o.lower() for o in generated)

    print("MOUNT LADDER (last wins)")
    print("-" * 68)
    missing_ours = set(o.lower() for o in [args.ui_name] + generated)
    for prio, rel in ladder:
        full = os.path.join(CLIENT_DATA, rel)
        if not os.path.exists(full):
            continue
        if rel.lower() in ours_rel:
            tag = "  <-- ours"
            missing_ours.discard(rel.lower())
        elif rel.lower() in configured_rel:
            tag = "  <-- configured target, NOT Paragon-owned"
        else:
            tag = ""
        print("  prio %-3d %-34s%s" % (prio, rel, tag))
    print()
    if foreign_targets:
        print("!! REFUSING TO CLAIM CONFIGURED TARGET(S) WITHOUT THE PARAGON MARKER:")
        for rel in foreign_targets:
            print("     %s" % rel)
        print("   Choose free archive names when building and pass those same names")
        print("   to this checker with --ui-name, --general-name, and")
        print("   --locale-name.")
        return 1
    if missing_ours:
        print("!! expected archive(s) not on the ladder: %s"
              % ", ".join(sorted(missing_ours)))
        print("   (renamed? deleted? a two-character suffix never loads at all)")
        print()

    # names that can never be mounted -- easy to get wrong, silent when wrong
    stray = []
    for where in (CLIENT_DATA, os.path.join(CLIENT_DATA, LOCALE)):
        for name in os.listdir(where) if os.path.isdir(where) else []:
            low = name.lower()
            if not low.endswith(".mpq") or not low.startswith("patch"):
                continue
            rel = name if where == CLIENT_DATA else os.path.join(LOCALE, name)
            if rel.lower() not in [r.lower() for _p, r in ladder]:
                stray.append(rel)
    if stray:
        print("!! PRESENT BUT NEVER MOUNTED (the '?' wildcard is exactly one char):")
        for s in stray:
            print("     %s" % s)
        print()

    # what do we ship?
    our_files = {}
    for rel in ours:
        full = os.path.join(CLIENT_DATA, rel)
        if not os.path.exists(full):
            continue
        names = listfile_of(full) or []
        for n in names:
            if n.lower() not in ("(listfile)", OWNER_MARKER_NAME.lower()):
                our_files.setdefault(n, []).append(rel)
    if not our_files:
        sys.exit("could not read our own archives -- nothing to check")
    print("checking %d file(s) we ship against %d mounted archive(s)\n"
          % (len(our_files), len([1 for _p, r in ladder
                                  if os.path.exists(os.path.join(CLIENT_DATA, r))])))

    # for each mounted archive, which of our filenames does it also carry?
    owners = {}
    for prio, rel in ladder:
        full = os.path.join(CLIENT_DATA, rel)
        if not os.path.exists(full):
            continue
        hits = archive_contains(full, list(our_files))
        if hits is None:
            print("  ? %-34s could not be parsed -- CHECK BY HAND" % rel)
            continue
        for name in hits:
            owners.setdefault(name, []).append((prio, rel))

    collisions = []
    for name, holders in sorted(owners.items()):
        winner_prio, winner = max(holders)
        if winner.lower() not in ours_rel:
            collisions.append((name, winner, winner_prio, holders))

    if collisions:
        print("!! COLLISION -- these files come from somebody else's archive:")
        for name, winner, prio, holders in collisions:
            print("     %s" % name)
            print("       wins: %s (prio %d)" % (winner, prio))
            print("       ours: %s" % ", ".join(
                "%s (prio %d)" % (r, p) for p, r in sorted(holders)
                if r.lower() in ours_rel))
        print()
        print("   fix: rename our archive above theirs, or move the file into")
        print("   the general patch (general outranks locale at every letter).")
        return 1

    shadowed = [(n, h) for n, h in owners.items() if len(h) > 1]
    print("OK -- every file we ship is served from one of our own archives.")
    if shadowed:
        print("     (%d of them also exist lower down, which is fine: we win)"
              % len(shadowed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
