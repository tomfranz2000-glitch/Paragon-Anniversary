"""Extended talent generator for the Paragon reward track.

One config table drives everything a talent-cap raise needs besides the Lua
milestone entry (see EXTENDED_TALENTS in paragon_rework_track.lua):

  1. spell_dbc rows for the new rank spells   (server, needs restart)
  2. talent_dbc override giving the talent its new rank list (server, restart)
  3. Patched client Talent.dbc + Spell.dbc packed into
     Data/patch-5.MPQ and Data/enUS/patch-enUS-5.MPQ (client, needs restart)

Companion docs: "Paragon Core Patches.md". The server-side gate lives in
paragon_rework_track.lua (PLAYER_EVENT_ON_CAN_LEARN_TALENT = 74, an ALE hook
added for this system) — the core itself allows up to 9 ranks unconditionally.

Usage (from this Tools directory):
    python extended_talents.py            # generate SQL + MPQs
    python extended_talents.py --apply    # ... and pipe the SQL into MySQL

Rank spells are cloned field-for-field from a designated live rank of the same
talent, changing only ID, effect value and "Rank N" subtext — so every
attribute (school, icon, passive flags, ...) stays authentic.

HARD LIMIT: Talent.dbc has exactly 9 rank slots per talent. This tool refuses
configs that exceed it.
"""
import argparse
import os
import struct
import subprocess
import sys

# ============================================================================
# CONFIG — add one entry per extended talent
# ============================================================================

EXTENDED = [
    {
        "talent_id": 2185,                 # Divine Strength (Paladin, Protection tab 383)
        "clone_spell": 20266,              # rank 5 — template for the new ranks
        "value_field": "EffectBasePoints_1",
        "new_ranks": [                     # (new spell id, effect value), rank 6 upward
            (1900010, 18),
            (1900011, 21),
            (1900012, 24),
            (1900013, 27),
        ],
    },
]

# ============================================================================

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DATA = os.path.join(HERE, "..", "Client", "Data")
CACHE = os.path.join(HERE, "cache")
OUT_SQL = os.path.join(HERE, "generated", "extended_talents.sql")
STAGE_LOCALE = os.path.join(HERE, "stage-locale", "DBFilesClient")   # Spell + Talent
STAGE_GENERAL = os.path.join(HERE, "stage-general", "DBFilesClient") # Talent only
MPQ_GENERAL = os.path.abspath(os.path.join(CLIENT_DATA, "patch-5.MPQ"))
MPQ_LOCALE = os.path.abspath(os.path.join(CLIENT_DATA, "enUS", "patch-enUS-5.MPQ"))

DB_CONTAINER = "ac-database"
DB_PASS = os.environ.get("ACORE_DB_PASS", "")
if not DB_PASS:
    raise SystemExit("set ACORE_DB_PASS to your world DB password")

MAX_RANK_SLOTS = 9
SPELL_FIELDS = 234
TALENT_FIELDS = 23


def mysql(sql, db="acore_world"):
    r = subprocess.run(
        ["docker", "exec", "-i", DB_CONTAINER, "mysql", "-uroot", "-p" + DB_PASS, "-N", db],
        input=sql.encode(), capture_output=True)
    if r.returncode != 0:
        sys.exit("mysql failed: " + r.stderr.decode()[:500])
    return [line.split("\t") for line in r.stdout.decode().splitlines()
            if line and not line.startswith("mysql:")]


def extract_dbc(name):
    """Pull a DBC out of the client's locale MPQ chain (highest patch wins,
    skipping our own patch-enUS-5)."""
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
            print(f"extracted {name} from {mpq}")
            return path
    sys.exit(f"{name} not found in client MPQs")


def load_dbc(path, expect_fields):
    blob = open(path, "rb").read()
    magic, nrec, nfield, rsz, ssz = struct.unpack_from("<4sIIII", blob, 0)
    assert magic == b"WDBC" and nfield == expect_fields, (magic, nfield)
    return blob, nrec, nfield, rsz, ssz


def spell_columns():
    rows = mysql("SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS "
                 "WHERE TABLE_SCHEMA='acore_world' AND TABLE_NAME='spell_dbc' "
                 "ORDER BY ORDINAL_POSITION;")
    assert len(rows) == SPELL_FIELDS, len(rows)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="pipe generated SQL into MySQL")
    args = ap.parse_args()

    cols = spell_columns()
    idx = {n: i for i, (n, _) in enumerate(cols)}

    spell_path = extract_dbc("Spell.dbc")
    talent_path = extract_dbc("Talent.dbc")
    sblob, snrec, _, srsz, sssz = load_dbc(spell_path, SPELL_FIELDS)
    sstr_base = 20 + snrec * srsz
    sstrings = bytearray(sblob[sstr_base:sstr_base + sssz])

    def sval(off):
        return bytes(sstrings[off:sstrings.index(b"\0", off)]).decode("utf-8", "replace")

    spell_off = {}
    for i in range(snrec):
        off = 20 + i * srsz
        spell_off[struct.unpack_from("<i", sblob, off)[0]] = off

    tblob = bytearray(open(talent_path, "rb").read())
    _, tnrec, _, trsz, _ = load_dbc(talent_path, TALENT_FIELDS)
    talent_off = {}
    for i in range(tnrec):
        off = 20 + i * trsz
        talent_off[struct.unpack_from("<i", tblob, off)[0]] = off

    sql = [
        "-- GENERATED by Tools/extended_talents.py - do not hand-edit.",
        "-- Server half of the extended-talent data; the client half is the",
        "-- patch-5 MPQs built in the same run. See 'Paragon Core Patches.md'.",
    ]
    new_spell_records = bytearray()
    esc = lambda s: s.replace("\\", "\\\\").replace("'", "''")

    for ext in EXTENDED:
        tid, clone_id = ext["talent_id"], ext["clone_spell"]
        toff = talent_off.get(tid) or sys.exit(f"talent {tid} not in Talent.dbc")
        coff = spell_off.get(clone_id) or sys.exit(f"clone spell {clone_id} not in Spell.dbc")

        ranks = list(struct.unpack_from("<9i", tblob, toff + 4 * 4))
        used = sum(1 for r in ranks if r)
        if used + len(ext["new_ranks"]) > MAX_RANK_SLOTS:
            sys.exit(f"talent {tid}: {used}+{len(ext['new_ranks'])} ranks exceeds the "
                     f"{MAX_RANK_SLOTS}-slot Talent.dbc format limit")

        # --- client Talent.dbc: fill the free slots
        for slot, (sid, _) in enumerate(ext["new_ranks"], start=used):
            struct.pack_into("<i", tblob, toff + (4 + slot) * 4, sid)
        ranks_after = list(struct.unpack_from("<9i", tblob, toff + 4 * 4))
        print(f"talent {tid}: ranks {ranks} -> {ranks_after}")

        # --- new rank spells: client records + server SQL, cloned from clone_spell
        all_ids = ", ".join(str(s) for s, _ in ext["new_ranks"])
        sql.append(f"DELETE FROM spell_dbc WHERE ID IN ({all_ids});")
        for rank_no, (sid, value) in enumerate(ext["new_ranks"], start=used + 1):
            rec = bytearray(sblob[coff:coff + srsz])
            struct.pack_into("<i", rec, 0, sid)
            struct.pack_into("<i", rec, idx[ext["value_field"]] * 4, value - 1)
            rank_off = len(sstrings)
            sstrings.extend(f"Rank {rank_no}".encode() + b"\0")
            for name, _t in cols:
                if name.startswith("NameSubtext_Lang_") and not name.endswith("_Mask"):
                    if struct.unpack_from("<i", rec, idx[name] * 4)[0]:
                        struct.pack_into("<i", rec, idx[name] * 4, rank_off)
            new_spell_records.extend(rec)

            vals = []
            for i, (name, dtype) in enumerate(cols):
                raw = rec[i * 4:i * 4 + 4]
                if dtype in ("varchar", "text"):
                    vals.append("'%s'" % esc(sval(struct.unpack("<i", raw)[0])))
                elif dtype == "float":
                    vals.append(repr(struct.unpack("<f", raw)[0]))
                else:
                    vals.append(str(struct.unpack("<i", raw)[0]))
            sql.append("INSERT INTO spell_dbc (%s) VALUES (%s);" % (
                ", ".join("`%s`" % n for n, _ in cols), ", ".join(vals)))

        # --- server talent_dbc override row
        t = struct.unpack_from(f"<{TALENT_FIELDS}i", tblob, toff)
        sql += [
            f"DELETE FROM talent_dbc WHERE ID = {tid};",
            "INSERT INTO talent_dbc (ID, TabID, TierID, ColumnIndex, "
            "SpellRank_1, SpellRank_2, SpellRank_3, SpellRank_4, SpellRank_5, "
            "SpellRank_6, SpellRank_7, SpellRank_8, SpellRank_9, "
            "PrereqTalent_1, PrereqTalent_2, PrereqTalent_3, PrereqRank_1, PrereqRank_2, PrereqRank_3, "
            "Flags, RequiredSpellID, CategoryMask_1, CategoryMask_2) VALUES (%s);" % (
                ", ".join(str(v) for v in t)),
        ]

    # ---- write outputs
    os.makedirs(os.path.dirname(OUT_SQL), exist_ok=True)
    with open(OUT_SQL, "w", encoding="utf-8") as f:
        f.write("\n".join(sql) + "\n")
    print("wrote", OUT_SQL)

    for stage in (STAGE_LOCALE, STAGE_GENERAL):
        os.makedirs(stage, exist_ok=True)
        open(os.path.join(stage, "Talent.dbc"), "wb").write(bytes(tblob))

    total = len(EXTENDED and new_spell_records) // srsz if new_spell_records else 0
    out = bytearray()
    out.extend(struct.pack("<4sIIII", b"WDBC", snrec + total, SPELL_FIELDS, srsz, len(sstrings)))
    out.extend(sblob[20:sstr_base])
    out.extend(new_spell_records)
    out.extend(sstrings)
    open(os.path.join(STAGE_LOCALE, "Spell.dbc"), "wb").write(bytes(out))
    print(f"staged Spell.dbc ({snrec} -> {snrec + total} records) + Talent.dbc")

    # ---- build both MPQs with the sibling builder
    for stage, mpq in ((os.path.dirname(STAGE_GENERAL), MPQ_GENERAL),
                       (os.path.dirname(STAGE_LOCALE), MPQ_LOCALE)):
        r = subprocess.run([sys.executable, os.path.join(HERE, "build_mpq.py"), stage, mpq],
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode != 0:
            sys.exit("MPQ build failed")
    print("built", MPQ_GENERAL)
    print("built", MPQ_LOCALE)

    if args.apply:
        with open(OUT_SQL, encoding="utf-8") as f:
            mysql(f.read())
        print("SQL applied to acore_world")
    else:
        print("SQL NOT applied (rerun with --apply, or pipe the file into mysql)")

    print("\nremember: worldserver restart (spell_dbc/talent_dbc load at startup)")
    print("          full client restart (MPQs load at startup)")
    print("          gate new talents in EXTENDED_TALENTS in paragon_rework_track.lua")


if __name__ == "__main__":
    main()
