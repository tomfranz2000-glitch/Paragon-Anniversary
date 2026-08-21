"""Paragon Consecration Burst (reward-track milestone 150, Paladin).

Generates the server-side spell 1900014: an instant caster-centered holy AoE
whose damage is overridden per cast by paragon_rework_track.lua (Lua passes
the Consecration rank's full 8-second total via CastCustomSpell).

Cloned from Holy Nova's damage sub-spell 48078 (proven instant PBAoE targeting
TA22/TB15) with these overrides:
  - radius index 14 (Consecration's 8yd; donor uses 10yd)
  - EffectBasePoints 0 / DieSides 1 (value comes from the cast override)
  - priest spell family zeroed (SpellClassSet + masks) so priest talents and
    procs can never latch onto a paladin-cast spell
  - mana cost percent zeroed, name/description swapped
Donor's DefenseType 1 (magic) is kept: the burst rolls hit and crit like a
normal damage spell, so it can crit (unlike Consecration's ticks) — on
average slightly MORE than double. Tunable later if that bothers anyone.

Also emits the spell_bonus_data row (direct 0.32 SP + 0.32 AP = 8 ticks of
Consecration's 0.04/0.04) so the burst scales with gear like the DoT total.

Usage: python gen_consecration_burst.py [--apply]
"""
import argparse
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DATA = os.path.join(HERE, "..", "Client", "Data")
CACHE = os.path.join(HERE, "cache")
OUT_SQL = os.path.join(HERE, "generated", "consecration_burst.sql")

DB_CONTAINER = "ac-database"
DB_PASS = os.environ.get("ACORE_DB_PASS", "")
if not DB_PASS:
    raise SystemExit("set ACORE_DB_PASS to your world DB password")

DONOR = 48078
NEW_ID = 1900014
RADIUS_INDEX = 14          # 8 yd, same as Consecration
SPELL_FIELDS = 234


def mysql(sql, db="acore_world"):
    r = subprocess.run(
        ["docker", "exec", "-i", DB_CONTAINER, "mysql", "-uroot", "-p" + DB_PASS, "-N", db],
        input=sql.encode(), capture_output=True)
    if r.returncode != 0:
        sys.exit("mysql failed: " + r.stderr.decode()[:500])
    return [line.split("\t") for line in r.stdout.decode().splitlines()
            if line and not line.startswith("mysql:")]


def extract_spell_dbc():
    path = os.path.join(CACHE, "Spell.dbc")
    if os.path.exists(path):
        return path
    from mpyq import MPQArchive
    os.makedirs(CACHE, exist_ok=True)
    for mpq in ("patch-enUS-3.MPQ", "patch-enUS-2.MPQ", "patch-enUS.MPQ", "locale-enUS.MPQ"):
        p = os.path.join(CLIENT_DATA, "enUS", mpq)
        if not os.path.exists(p):
            continue
        try:
            data = MPQArchive(p).read_file(b"DBFilesClient\\Spell.dbc")
        except Exception:
            data = None
        if data:
            with open(path, "wb") as f:
                f.write(data)
            return path
    sys.exit("Spell.dbc not found in client MPQs")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    cols = mysql("SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS "
                 "WHERE TABLE_SCHEMA='acore_world' AND TABLE_NAME='spell_dbc' "
                 "ORDER BY ORDINAL_POSITION;")
    assert len(cols) == SPELL_FIELDS, len(cols)
    idx = {n: i for i, (n, _) in enumerate(cols)}

    blob = open(extract_spell_dbc(), "rb").read()
    magic, nrec, nfield, rsz, ssz = struct.unpack_from("<4sIIII", blob, 0)
    assert magic == b"WDBC" and nfield == SPELL_FIELDS
    str_base = 20 + nrec * rsz
    strings = blob[str_base:str_base + ssz]

    def sval(off):
        return strings[off:strings.index(b"\0", off)].decode("utf-8", "replace")

    donor_off = None
    for i in range(nrec):
        off = 20 + i * rsz
        if struct.unpack_from("<i", blob, off)[0] == DONOR:
            donor_off = off
            break
    assert donor_off is not None

    rec = bytearray(blob[donor_off:donor_off + rsz])

    def seti(name, v):
        struct.pack_into("<i", rec, idx[name] * 4, v)

    seti("ID", NEW_ID)
    seti("EffectBasePoints_1", 0)
    seti("EffectDieSides_1", 1)
    seti("EffectRadiusIndex_1", RADIUS_INDEX)
    seti("ManaCostPct", 0)
    seti("ManaCost", 0)
    seti("SpellClassSet", 0)
    for n in ("SpellClassMask_1", "SpellClassMask_2", "SpellClassMask_3"):
        seti(n, 0)

    OVERRIDE_TEXT = {"Name_Lang_enUS": "Paragon Consecration Burst"}
    esc = lambda s: s.replace("\\", "\\\\").replace("'", "''")
    vals = []
    for i, (name, dtype) in enumerate(cols):
        raw = rec[i * 4:i * 4 + 4]
        if name in OVERRIDE_TEXT:
            vals.append("'%s'" % esc(OVERRIDE_TEXT[name]))
        elif name.startswith(("Description_Lang_", "AuraDescription_Lang_")) and not name.endswith("_Mask") and dtype in ("varchar", "text"):
            vals.append("''")
        elif dtype in ("varchar", "text"):
            vals.append("'%s'" % esc(sval(struct.unpack("<i", raw)[0])))
        elif dtype == "float":
            vals.append(repr(struct.unpack("<f", raw)[0]))
        else:
            vals.append(str(struct.unpack("<i", raw)[0]))

    sql = [
        "-- GENERATED by Tools/gen_consecration_burst.py - do not hand-edit.",
        "-- See 'Paragon Core Patches.md'. Server-side only; no client patch.",
        f"DELETE FROM spell_dbc WHERE ID = {NEW_ID};",
        "INSERT INTO spell_dbc (%s) VALUES (%s);" % (
            ", ".join("`%s`" % n for n, _ in cols), ", ".join(vals)),
        f"DELETE FROM spell_bonus_data WHERE entry = {NEW_ID};",
        "INSERT INTO spell_bonus_data (entry, direct_bonus, dot_bonus, ap_bonus, ap_dot_bonus, comments)",
        f"VALUES ({NEW_ID}, 0.32, 0, 0.32, 0, 'Paragon - Consecration Burst (8x Consecration tick coefficients)');",
    ]
    os.makedirs(os.path.dirname(OUT_SQL), exist_ok=True)
    with open(OUT_SQL, "w", encoding="utf-8") as f:
        f.write("\n".join(sql) + "\n")
    print("wrote", OUT_SQL)

    for k in ("ID", "Effect_1", "ImplicitTargetA_1", "ImplicitTargetB_1", "EffectRadiusIndex_1",
              "EffectBasePoints_1", "EffectDieSides_1", "SchoolMask", "SpellClassSet",
              "ManaCostPct", "DefenseType", "PreventionType", "SpellLevel"):
        i = idx[k]
        print("  %-20s %s" % (k, struct.unpack_from("<i", rec, i * 4)[0]))

    if args.apply:
        with open(OUT_SQL, encoding="utf-8") as f:
            mysql(f.read())
        print("applied to acore_world (worldserver restart required)")


if __name__ == "__main__":
    main()
