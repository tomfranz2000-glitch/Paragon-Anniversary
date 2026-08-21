#!/usr/bin/env python3
"""Populate acore_ale.paragon_config_experience_quest with every quest's FULL
base experience value: QuestXP.dbc[quest level][RewardXPDifficulty], with NO
level penalties (user spec 2026-08-18: quests grant paragon XP equal to the
regular XP they would grant a level-appropriate character, flat — the
collection-XP multiplier exempts source QUEST, see paragon_collection_xp.lua).

- QuestLevel -1 (player-level-scaling quests: dailies, DK chain, etc.)
  resolves to level 80, the only paragon-XP-earning level
  (MINIMUM_LEVEL_FOR_PARAGON_XP).
- Rate.XP.Quest = 1 on this server, so base == granted.
- Zero-XP quests get an explicit 0 row (grants nothing); quests missing from
  quest_template fall back to UNIVERSAL_QUEST_EXPERIENCE (1) at runtime.

Rerunnable: DELETEs and repopulates the whole table. Worldserver restart
required afterwards (the repository loads the table once at boot).
"""
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DATA = os.path.abspath(os.environ.get(
    "PARAGON_CLIENT_DATA", os.path.join(HERE, "..", "Client", "Data")))
CACHE = os.path.join(HERE, "cache")
DB_CONTAINER = "ac-database"
DB_PASS = os.environ.get("ACORE_DB_PASS", "")
if not DB_PASS:
    raise SystemExit("set ACORE_DB_PASS to your world DB password")
SCALING_LEVEL = 80   # QuestLevel -1 -> the paragon-earning level


def mysql(sql, db="acore_world"):
    r = subprocess.run(
        ["docker", "exec", "-i", DB_CONTAINER, "mysql", "-uroot", "-p" + DB_PASS, "-N", db],
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


def main():
    blob = open(extract_dbc("QuestXP.dbc"), "rb").read()
    magic, nrec, nf, rsz, _ = struct.unpack_from("<4sIIII", blob, 0)
    assert magic == b"WDBC" and nf == 11, (magic, nf)
    xp = {}
    for i in range(nrec):
        row = struct.unpack_from("<11i", blob, 20 + i * rsz)
        xp[row[0]] = row[1:]          # quest level -> 10 difficulty columns
    max_level = max(xp)

    quests = mysql("SELECT ID, QuestLevel, RewardXPDifficulty FROM quest_template;")
    values, zeroes = [], 0
    for qid, qlevel, diff in quests:
        qid, qlevel, diff = int(qid), int(qlevel), int(diff)
        level = SCALING_LEVEL if qlevel < 0 else max(1, min(qlevel, max_level))
        cols = xp.get(level)
        v = cols[diff] if cols and 0 <= diff < 10 else 0
        values.append("(%d,%d)" % (qid, v))
        if v == 0:
            zeroes += 1

    stmts = ["DELETE FROM paragon_config_experience_quest;"]
    for i in range(0, len(values), 1000):
        stmts.append(
            "INSERT INTO paragon_config_experience_quest (id, experience) VALUES %s;"
            % ",".join(values[i:i + 1000]))
    mysql("\n".join(stmts), db="acore_ale")
    print("populated %d quest rows (%d zero-XP); QuestXP levels 1..%d"
          % (len(values), zeroes, max_level))


if __name__ == "__main__":
    main()
