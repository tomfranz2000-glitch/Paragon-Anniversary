#!/usr/bin/env python3
"""Generate paragon_glyph_data.lua: glyph item -> glyph property -> aura spell.

Sources:
  - acore_world.item_template (class 16 = glyph items; subclass = character class)
  - client Spell.dbc          (item use-spell -> SPELL_EFFECT_APPLY_GLYPH misc value)
  - client GlyphProperties.dbc (property -> aura SpellId + TypeFlags 0=major/1=minor)

Column indices for Spell.dbc are taken from the spell_dbc SQL table schema, whose
column order matches the DBC field order positionally (same trick as
paragon_client_patch.py). Output is a server-side Lua data module.
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
CLIENT_DATA = os.path.abspath(os.environ.get(
    "PARAGON_CLIENT_DATA", os.path.join(REPO_ROOT, "Client", "Data")))
CACHE = os.path.abspath(os.environ.get(
    "PARAGON_DBC_CACHE", os.path.join(HERE, "cache")))
OUT_LUA = os.path.join(REPO_ROOT, "serverside", "paragon", "modules",
                       "paragon_glyph_data.lua")
DB_CONTAINER = os.environ.get("ACORE_DB_CONTAINER", "ac-database")


def mysql(sql, db="acore_world"):
    r = subprocess.run(
        ["docker", "exec", "-i", DB_CONTAINER, "sh", "-lc",
         'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" '
         '--default-character-set=utf8mb4 --raw --batch '
         '--skip-column-names "$1"', "paragon-mysql", db],
        input=sql.encode(), capture_output=True)
    if r.returncode != 0:
        sys.exit("mysql failed: " + r.stderr.decode()[:500])
    return [line.split("\t") for line in r.stdout.decode().splitlines()
            if line and not line.startswith("mysql:")]


def extract_dbc(name):
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        return path
    from mpyq import MPQArchive
    os.makedirs(CACHE, exist_ok=True)
    for mpq in ("patch-enUS-3.MPQ", "patch-enUS-2.MPQ", "patch-enUS.MPQ", "locale-enUS.MPQ"):
        p = os.path.join(CLIENT_DATA, "enUS", mpq)
        if not os.path.exists(p):
            continue
        try:
            data = MPQArchive(p).read_file(("DBFilesClient\\" + name).encode())
        except Exception:
            data = None
        if data:
            with open(path, "wb") as f:
                f.write(data)
            return path
    sys.exit(name + " not found in client MPQs")


def read_wdbc_ints(path):
    """Return (records as list of int32-tuples, field_count, string_block)."""
    with open(path, "rb") as f:
        raw = f.read()
    magic, rec_count, field_count, rec_size, str_size = struct.unpack_from("<4sIIII", raw, 0)
    if magic != b"WDBC":
        sys.exit(path + ": not a WDBC file")
    if rec_size != field_count * 4:
        sys.exit(path + ": unexpected record size")
    out = []
    off = 20
    fmt = "<%di" % field_count
    for _ in range(rec_count):
        out.append(struct.unpack_from(fmt, raw, off))
        off += rec_size
    return out, field_count, raw[off:off + str_size]


def string_at(block, offset):
    end = block.index(b"\0", offset)
    return block[offset:end].decode("utf-8", "replace")


def spell_column_indices():
    rows = mysql(
        "SELECT COLUMN_NAME, ORDINAL_POSITION FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA='acore_world' AND TABLE_NAME='spell_dbc' "
        "ORDER BY ORDINAL_POSITION;")
    return {name: int(pos) - 1 for name, pos in rows}


def write_generated(path, content, check=False):
    """Write generated text or fail when the tracked artifact is stale."""
    if check:
        try:
            with open(path, encoding="utf-8") as handle:
                current = handle.read()
        except FileNotFoundError:
            sys.exit("generated artifact is missing: %s" % path)
        if current != content:
            sys.exit("generated artifact is stale: %s" % path)
        return
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="\n", dir=directory,
                prefix=os.path.basename(path) + ".", suffix=".tmp",
                delete=False) as handle:
            handle.write(content)
            temporary = handle.name
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fail instead of rewriting a stale artifact")
    parser.add_argument("--output", default=OUT_LUA,
                        help="generated Lua path (default: %(default)s)")
    args = parser.parse_args(argv)
    cols = spell_column_indices()
    eff_idx = [cols["Effect_%d" % i] for i in (1, 2, 3)]
    misc_idx = [cols["EffectMiscValue_%d" % i] for i in (1, 2, 3)]

    spells, _, _ = read_wdbc_ints(extract_dbc("Spell.dbc"))
    spell_by_id = {rec[0]: rec for rec in spells}

    # SpellIcon.dbc: (ID, texture path) — glyph properties point at the round
    # rune art (Interface\Spellbook\UI-Glyph-Rune*) the stock sockets display.
    icon_recs, _, icon_strings = read_wdbc_ints(extract_dbc("SpellIcon.dbc"))
    icon_paths = {rec[0]: string_at(icon_strings, rec[1]) for rec in icon_recs}

    props = {}
    for rec in read_wdbc_ints(extract_dbc("GlyphProperties.dbc"))[0]:
        rune = icon_paths.get(rec[3], "").replace("\\", "/")
        props[rec[0]] = {"spell": rec[1], "flags": rec[2], "rune": rune}

    items = mysql(
        "SELECT entry, subclass, spellid_1, name FROM item_template "
        "WHERE class=16 AND spellid_1>0 AND name LIKE 'Glyph of%' ORDER BY entry;")

    out, skipped = [], []
    per_class, majors, minors = {}, 0, 0
    for entry, subclass, spellid, name in items:
        entry, subclass, spellid = int(entry), int(subclass), int(spellid)
        rec = spell_by_id.get(spellid)
        if not rec:
            skipped.append((entry, name, "use-spell %d not in Spell.dbc" % spellid))
            continue
        prop_id = 0
        for e, m in zip(eff_idx, misc_idx):
            if rec[e] == 74:  # SPELL_EFFECT_APPLY_GLYPH
                prop_id = rec[m]
                break
        prop = props.get(prop_id)
        if not prop_id or not prop or not prop["spell"]:
            skipped.append((entry, name, "no glyph property (%d)" % prop_id))
            continue
        minor = 1 if (prop["flags"] & 1) else 0
        majors, minors = majors + (0 if minor else 1), minors + minor
        per_class[subclass] = per_class.get(subclass, 0) + 1
        out.append((entry, subclass, prop_id, prop["spell"], minor, prop["rune"], name))

    lines = [
        "-- Generated by Tools/gen_glyph_data.py (do not hand-edit).",
        "-- ParagonGlyphData[itemId] = { class=char class id, property=GlyphProperties id,",
        "--                              aura=glyph passive spell, minor=1 for minor glyphs,",
        "--                              rune=socket art the client shows when filled }",
        "ParagonGlyphData = {",
    ]
    for entry, subclass, prop_id, aura, minor, rune, name in out:
        lines.append('  [%d] = { class=%d, property=%d, aura=%d, minor=%d, rune="%s" }, -- %s'
                     % (entry, subclass, prop_id, aura, minor, rune, name))
    lines.append("}")
    content = "\n".join(lines) + "\n"
    write_generated(args.output, content, args.check)

    verb = "verified" if args.check else "wrote"
    print("%s %s: %d glyphs (%d major / %d minor)" %
          (verb, args.output, len(out), majors, minors))
    print("per class:", dict(sorted(per_class.items())))
    for entry, name, why in skipped[:8]:
        print("skipped %d %s: %s" % (entry, name, why))
    if len(skipped) > 8:
        print("... and %d more skipped" % (len(skipped) - 8))


if __name__ == "__main__":
    main()
