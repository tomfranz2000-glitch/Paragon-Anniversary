#!/usr/bin/env python3
"""One-time collection XP classifier (mounts / companions / transmog).

Computes a flat paragon-XP reward for every collectible, scaled by how hard
it is to acquire (user spec 2026-08-18), and writes:

  acore_ale.paragon_collectible_spell_xp   mount+companion spells (ALL rows)
  acore_ale.paragon_collectible_item_xp    appearance items ABOVE the 1000
                                           baseline only (baseline lives in
                                           the Lua module)
  Tools/generated/collectible_xp_review.csv   human review dump

  --seed additionally creates + seeds the one-time-reward mirrors with the
  CURRENT collections (no retroactive payout):
  acore_ale.paragon_rewarded_collectible_spell (account_id, spell_id)
  acore_ale.paragon_rewarded_appearance        (account_id, item_id)

Difficulty model ("easiest path wins"): every acquisition path of an item
is scored as a multiplier of the group baseline (mount 80k / companion 30k /
appearance 1k) and the CHEAPEST path defines the reward.

  vendor (gold only)          x1   (>=1000g x1.2, >=10000g x1.5)
  vendor (token/emblem cost)  x3
  reputation-gated item       x3 revered / x4 exalted   (combines w/ vendor)
  quest reward                x2
  achievement reward          x5
  drop                        (100/chance)^0.7 x access, where access =
                               rare spawn (rank 2) x3; boss (rank 3) x1.5..3
                               by level tier; heroic-variant creature x2
  no source found             x1  ("unclassified" — never excluded, so a
                               missed path still pays at least baseline)

Loot math handles reference_loot_template indirection and grouped rolls
(Chance = 0 rows share the group's remaining probability equally — this is
what makes world-drop groups like Teebu's effectively <0.01%).

Transmog appearances map to a tier ladder instead of a raw multiplier
(user tuning: the mass stays at 1k, the top is truly mythic):

  2,500   raid-boss epics
  5,000   <=10% drops / heroic-only tables / rare-spawn loot
  12,000  ilvl 277 / <=2% drops
  30,000  ilvl 284 / <=0.5% drops / effective world-drop <=0.1%
  500,000 effective <=0.02% (Teebu's tier)
  750,000 legendaries (quality 5), marquee pieces pinned higher in OVERRIDES
  ...a gold/emblem vendor or quest path caps the tier at 2,500, and
  TEST/DEPRECATED items are dropped.

Rerunnable: value tables are DELETE+repopulated; mirrors are only touched
with --seed (INSERT IGNORE). Worldserver restart loads the new values.
"""
import argparse
import csv
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DATA = os.path.abspath(os.environ.get(
    "PARAGON_CLIENT_DATA", os.path.join(HERE, "..", "Client", "Data")))
CACHE = os.path.abspath(os.environ.get(
    "PARAGON_DBC_CACHE", os.path.join(HERE, "cache")))
OUT_CSV = os.path.join(HERE, "generated", "collectible_xp_review.csv")
DB_CONTAINER = os.environ.get("ACORE_DB_CONTAINER", "ac-database")

BASE_MOUNT, BASE_COMPANION, BASE_ITEM = 80000, 30000, 1000
# Formula ceiling (mounts 960k / companions 360k). Deliberately BELOW the
# override band so the hand-picked marquee list is strictly the top of the
# ladder (first run had five formula mounts tied with Invincible's 2M).
MULT_CAP = 12.0
# Invisible equipment slots never read as "appearances": neck, ring,
# trinket, relic. They still collect (mod-transmog tracks them) but stay
# at the 1000 baseline.
INVISIBLE_INVTYPE = {2, 11, 12, 28}
SKILL_MOUNTS, SKILL_COMPANIONS = 777, 778

# Marquee collectibles the data cannot rank (the DB does not know Icecrown
# heroic is the pinnacle, or that a 100%-chance rare spawn takes weeks of
# camping). Keyed by SPELL id for mounts, ITEM id for appearances.
MOUNT_OVERRIDES = {
    72286: 2000000,   # Invincible (user-pinned)
    63796: 1500000,   # Mimiron's Head
    40192: 1200000,   # Ashes of Al'ar
    71342: 1000000,   # Big Love Rocket
    60002: 800000,    # Time-Lost Proto-Drake
    24252: 500000,    # Swift Zulian Tiger
    24242: 500000,    # Swift Razzashi Raptor
    59996: 500000,    # Blue Proto-Drake
    17481: 400000,    # Rivendare's Deathcharger
    36702: 400000,    # Fiery Warhorse
    41252: 400000,    # Raven Lord
    46628: 300000,    # Swift White Hawkstrider
    48025: 300000,    # Headless Horseman's Mount
    61294: 300000,    # Green Proto-Drake
}
ITEM_OVERRIDES = {
    49623: 1000000,   # Shadowmourne
    32837: 1000000,   # Warglaive of Azzinoth (main)
    32838: 1000000,   # Warglaive of Azzinoth (off)
    19019: 900000,    # Thunderfury
    17182: 800000,    # Sulfuras
    46017: 800000,    # Val'anyr
    34334: 700000,    # Thori'dal
    1728:  600000,    # Teebu's Blazing Longsword
}
BAD_NAME = ("TEST", "Deprecated", "DEPRECATED", "[PH]", "(old)", "OLD")


def assert_exact_rows(label, expected, actual):
    """Fail with key-level diagnostics unless two generated tables match."""
    expected = [tuple(str(value) for value in row) for row in expected]
    actual = [tuple(str(value) for value in row) for row in actual]
    expected.sort()
    actual.sort()
    if expected == actual:
        return
    expected_by_key = {row[0]: row[1:] for row in expected}
    actual_by_key = {row[0]: row[1:] for row in actual}
    missing = sorted(set(expected_by_key) - set(actual_by_key))
    unexpected = sorted(set(actual_by_key) - set(expected_by_key))
    changed = sorted(key for key in set(expected_by_key) & set(actual_by_key)
                     if expected_by_key[key] != actual_by_key[key])
    raise SystemExit(
        "%s differs: expected %d rows, found %d; missing=%s; "
        "unexpected=%s; changed=%s" %
        (label, len(expected), len(actual), missing[:5], unexpected[:5],
         changed[:5]))


def mysql(sql, db="acore_world"):
    r = subprocess.run(
        ["docker", "exec", "-i", DB_CONTAINER, "sh", "-lc",
         'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" '
         '--default-character-set=utf8mb4 --raw --batch '
         '--skip-column-names "$1"', "paragon-mysql", db],
        input=sql.encode(), capture_output=True)
    if r.returncode != 0:
        sys.exit("mysql failed: " + r.stderr.decode()[:800])
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


def sla_spell_sets():
    """spell-id sets for the mount (777) and companion (778) skill lines."""
    blob = open(extract_dbc("SkillLineAbility.dbc"), "rb").read()
    magic, nrec, nf, rsz, _ = struct.unpack_from("<4sIIII", blob, 0)
    assert magic == b"WDBC" and nf == 14, (magic, nf)
    mounts, companions = set(), set()
    for i in range(nrec):
        row = struct.unpack_from("<14i", blob, 20 + i * rsz)
        if row[1] == SKILL_MOUNTS:
            mounts.add(row[2])
        elif row[1] == SKILL_COMPANIONS:
            companions.add(row[2])
    return mounts, companions


def chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def load_sources(interest):
    """All acquisition paths for the interesting item ids.

    Returns dict item_id -> list of path dicts:
      {kind: vendor|token|quest|achievement|drop, chance, rank, level, heroic}
    """
    paths = {}

    def add(item, **p):
        if item in interest:
            paths.setdefault(item, []).append(p)

    # ---- vendors -----------------------------------------------------------
    for item, ec in mysql("SELECT DISTINCT item, ExtendedCost FROM npc_vendor;"):
        add(int(item), kind="token" if int(ec) else "vendor")

    # ---- quest rewards -----------------------------------------------------
    cols = (["RewardItem%d" % i for i in range(1, 5)]
            + ["RewardChoiceItemID%d" % i for i in range(1, 7)])
    for row in mysql("SELECT %s FROM quest_template;" % ", ".join(cols)):
        for v in row:
            v = int(v)
            if v:
                add(v, kind="quest")

    # ---- achievement rewards ----------------------------------------------
    for (item,) in mysql("SELECT itemId FROM achievement_reward WHERE itemId > 0;"):
        add(int(item), kind="achievement")

    # ---- creature metadata for access factors ------------------------------
    creatures = {}     # lootid -> easiest (rank, maxlevel, heroic) tuple
    heroic = set()
    for r in mysql("SELECT difficulty_entry_1, difficulty_entry_2, difficulty_entry_3 "
                   "FROM creature_template;"):
        for v in r:
            if int(v):
                heroic.add(int(v))
    for entry, lootid, rank, maxlevel in mysql(
            "SELECT entry, lootid, `rank`, maxlevel FROM creature_template WHERE lootid > 0;"):
        entry, lootid, rank, maxlevel = int(entry), int(lootid), int(rank), int(maxlevel)
        cur = creatures.get(lootid)
        cand = (rank, maxlevel, entry in heroic)
        # easiest variant of a shared loot table wins (non-heroic, lowest rank)
        if cur is None or (cand[2], cand[0], cand[1]) < (cur[2], cur[0], cur[1]):
            creatures[lootid] = cand

    # ---- grouped-roll shares (aggregate, both loot tables) -----------------
    def group_stats(table):
        stats = {}
        for entry, group, nzero, sumc in mysql(
                "SELECT Entry, GroupId, SUM(Chance = 0), SUM(Chance) FROM %s "
                "GROUP BY Entry, GroupId;" % table):
            stats[(int(entry), int(group))] = (int(nzero), float(sumc))
        return stats

    ref_stats = group_stats("reference_loot_template")
    cl_stats = group_stats("creature_loot_template")
    go_stats = group_stats("gameobject_loot_template")

    def share(stats, entry, group, chance):
        """Effective in-group probability (%) of one row."""
        if chance > 0:
            return chance
        nzero, sumc = stats.get((entry, group), (1, 0.0))
        return max(0.002, (100.0 - min(sumc, 99.8)) / max(nzero, 1))

    # ---- reference tables: rows per entry (interest items only) ------------
    ref_rows = {}
    for entry, item, chance, group in mysql(
            "SELECT Entry, Item, Chance, GroupId FROM reference_loot_template;"):
        entry, item = int(entry), int(item)
        if item in interest:
            ref_rows.setdefault(entry, []).append(
                (item, share(ref_stats, entry, int(group), float(chance))))

    # ---- direct + referenced loot ------------------------------------------
    def walk_loot(table, stats, meta):
        # Fetch once and filter in Python.  A previous implementation created
        # a persistent ``tmp_interest`` helper table in acore_world, which
        # made even --check mutate production state and could collide with a
        # concurrent run.  These loot tables are small enough for a read-only
        # scan, while ``ref_rows`` and ``add`` discard unrelated rows here.
        rows = mysql(
            "SELECT Entry, Item, Reference, Chance, GroupId FROM %s;" % table)
        for entry, item, ref, chance, group in rows:
            entry, item, ref = int(entry), int(item), int(ref)
            eff = share(stats, entry, int(group), float(chance))
            rank, level, hero = meta(entry)
            if ref:
                for ritem, rshare in ref_rows.get(ref, ()):
                    add(ritem, kind="drop", chance=max(0.002, eff * rshare / 100.0),
                        rank=rank, level=level, heroic=hero)
            else:
                add(item, kind="drop", chance=eff, rank=rank, level=level, heroic=hero)

    walk_loot("creature_loot_template", cl_stats,
              lambda e: creatures.get(e, (0, 0, False)))
    walk_loot("gameobject_loot_template", go_stats, lambda e: (0, 0, False))

    # containers + fishing: plain chance, no access factor
    for table in ("item_loot_template", "fishing_loot_template"):
        st = group_stats(table)
        for entry, item, chance, group in mysql(
                "SELECT Entry, Item, Chance, GroupId FROM %s;" % table):
            add(int(item), kind="drop",
                chance=share(st, int(entry), int(group), float(chance)),
                rank=0, level=0, heroic=False)

    return paths


def path_mult(p, rep_mult):
    """Difficulty multiplier of one acquisition path."""
    if p["kind"] == "vendor":
        return 1.0 * rep_mult, "vendor"
    if p["kind"] == "token":
        return 3.0 * rep_mult, "token vendor"
    if p["kind"] == "quest":
        return 2.0, "quest"
    if p["kind"] == "achievement":
        return 5.0, "achievement"
    # drop
    chance = max(p["chance"], 0.002)
    m = (100.0 / chance) ** 0.7
    access = 1.0
    tag = "drop %.3f%%" % chance
    if p["rank"] == 2:
        access, tag = 3.0, tag + " rare-spawn"
    elif p["rank"] == 3:
        lvl = p["level"]
        access = 1.5 if lvl <= 63 else 2.0 if lvl <= 73 else 2.5 if lvl <= 80 else 3.0
        tag += " boss L%d" % lvl
    if p["heroic"]:
        access *= 2.0
        tag += " heroic"
    return m * access, tag


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--seed", action="store_true",
                      help="create + seed the one-time-reward mirror tables")
    mode.add_argument(
        "--check", action="store_true",
        help="read-only exact comparison with regenerated collectible XP rows")
    args = ap.parse_args()

    mount_spells, companion_spells = sla_spell_sets()
    print("SLA: %d mount spells, %d companion spells" % (len(mount_spells), len(companion_spells)))

    # ---- mount/pet items -> taught spell (spelltrigger 6 slot) -------------
    teach = {}          # spell -> (item, item_name, kind)
    for row in mysql(
            "SELECT entry, name, subclass, spellid_1, spelltrigger_1, spellid_2, "
            "spelltrigger_2, spellid_3, spelltrigger_3 FROM item_template "
            "WHERE class = 15 AND subclass IN (2, 5);"):
        entry, name, sub = int(row[0]), row[1], int(row[2])
        for sid, trig in ((row[3], row[4]), (row[5], row[6]), (row[7], row[8])):
            sid, trig = int(sid), int(trig)
            if trig == 6 and sid:
                kind = "mount" if sub == 5 else "companion"
                if sid not in teach:
                    teach[sid] = (entry, name, kind)

    # spells the items missed but the skill lines know (achievement/quest
    # spell rewards etc.) — classified by SLA membership, no teaching item
    for sid in sorted(mount_spells | companion_spells):
        if sid not in teach:
            teach[sid] = (0, None, "mount" if sid in mount_spells else "companion")

    # ---- transmog pool -----------------------------------------------------
    items = {}          # id -> (name, quality, ilvl, reqrep_rank, invtype)
    for entry, name, q, il, reprank, invtype in mysql(
            "SELECT entry, name, Quality, ItemLevel, RequiredReputationRank, "
            "InventoryType FROM item_template WHERE class IN (2, 4);"):
        items[int(entry)] = (name, int(q), int(il), int(reprank), int(invtype))

    interest = set(items) | {it for it, _n, _k in teach.values() if it}
    print("interest set: %d items" % len(interest))
    paths = load_sources(interest)
    print("sourced items: %d" % len(paths))

    def best(item_id):
        reprank = items.get(item_id, (None, 0, 0, 0, 0))[3]
        rep_mult = 4.0 if reprank >= 7 else 3.0 if reprank >= 6 else 1.0
        scored = [path_mult(p, rep_mult) for p in paths.get(item_id, ())]
        if not scored:
            return None, "unclassified"
        return min(scored, key=lambda s: s[0])

    # ---- mounts + companions ----------------------------------------------
    spell_rows, review = [], []
    spell_names = {}    # for toast display: prefer item name, else spell id
    for sid, (item, iname, kind) in sorted(teach.items()):
        base = BASE_MOUNT if kind == "mount" else BASE_COMPANION
        mult, reason = (1.0, "unclassified")
        if item:
            m = best(item)
            if m[0] is not None:
                mult, reason = m
        xp = int(round(min(mult, MULT_CAP) * base / 500.0) * 500)
        if sid in MOUNT_OVERRIDES:
            xp, reason = MOUNT_OVERRIDES[sid], reason + " +override"
        name = iname or ("spell %d" % sid)
        spell_names[sid] = name
        spell_rows.append((sid, kind, name, xp))
        review.append((kind, sid, name, xp, reason))

    # ---- transmog tiers ----------------------------------------------------
    # Quality gates keep the huge world-drop reference groups honest: a
    # random green's EFFECTIVE chance is astronomically small (wide grouped
    # roll), but nobody calls "of the Monkey" greens prestige. Chance-based
    # tiers therefore require rare+ quality, mythic/pinnacle wild drops
    # require epic. The heroic flag only counts on raid bosses (rank 3) —
    # heroic-DUNGEON loot is trivial badge fodder on this server.
    item_rows = []
    for iid, (name, quality, ilvl, _rep, invtype) in sorted(items.items()):
        if any(b in (name or "") for b in BAD_NAME) or invtype in INVISIBLE_INVTYPE:
            continue
        plist = paths.get(iid, ())
        easy = any(p["kind"] in ("vendor", "token", "quest") for p in plist)
        drops = [p for p in plist if p["kind"] == "drop"]
        minchance = min((p["chance"] for p in drops), default=None)
        boss = any(p["rank"] == 3 for p in drops)
        rare = any(p["rank"] == 2 for p in drops)
        heroraid = any(p["heroic"] and p["rank"] == 3 for p in drops)
        wild = min((p["chance"] for p in drops if p["rank"] == 0), default=None)
        chance3 = minchance if (minchance is not None and quality >= 3) else None
        wild4 = wild if (wild is not None and quality >= 4) else None

        xp = BASE_ITEM
        why = []
        if quality == 5:
            xp, why = 750000, ["legendary"]
        elif wild4 is not None and wild4 <= 0.02:
            xp, why = 500000, ["world-drop %.4f%%" % wild4]
        elif ilvl >= 284 or (chance3 is not None and chance3 <= 0.5 and quality >= 4) \
                or (wild4 is not None and wild4 <= 0.1):
            xp, why = 30000, ["pinnacle (284 / <=0.5% epic)"]
        elif ilvl == 277 or (chance3 is not None and chance3 <= 2):
            xp, why = 12000, ["prestige (277 / <=2% rare+)"]
        elif (chance3 is not None and chance3 <= 5) or heroraid or (rare and quality >= 3):
            xp, why = 5000, ["scarce (<=5% rare+ / heroic raid / rare-spawn)"]
        elif boss and quality >= 4:
            xp, why = 2500, ["raid epic"]
        if easy and quality != 5:
            xp = min(xp, 2500)
            why.append("easy path cap")
        if iid in ITEM_OVERRIDES:
            xp, why = ITEM_OVERRIDES[iid], why + ["override"]
        if xp > BASE_ITEM:
            item_rows.append((iid, name, xp))
            review.append(("appearance", iid, name, xp, " ".join(why)))

    # ---- write DB / exact read-only check ----------------------------------
    expected_spell_rows = [
        (sid, kind, (name or "")[:120], xp)
        for sid, kind, name, xp in spell_rows
    ]
    expected_item_rows = [
        (iid, (name or "")[:120], xp) for iid, name, xp in item_rows
    ]
    if args.check:
        actual_spell_rows = mysql(
            "SELECT spell_id, kind, name, xp "
            "FROM paragon_collectible_spell_xp ORDER BY spell_id;",
            db="acore_ale")
        actual_item_rows = mysql(
            "SELECT item_id, name, xp "
            "FROM paragon_collectible_item_xp ORDER BY item_id;",
            db="acore_ale")
        assert_exact_rows("paragon_collectible_spell_xp",
                          expected_spell_rows, actual_spell_rows)
        assert_exact_rows("paragon_collectible_item_xp",
                          expected_item_rows, actual_item_rows)
        print("OK: regenerated collectible XP exactly matches the database "
              "(%d spells, %d items)" %
              (len(expected_spell_rows), len(expected_item_rows)))
        return

    esc = lambda s: (s or "").replace("\\", "\\\\").replace("'", "''")
    stmts = [
        "CREATE TABLE IF NOT EXISTS paragon_collectible_spell_xp ("
        "spell_id INT PRIMARY KEY, kind VARCHAR(10) NOT NULL, "
        "name VARCHAR(120) NOT NULL, xp INT NOT NULL);",
        "CREATE TABLE IF NOT EXISTS paragon_collectible_item_xp ("
        "item_id INT PRIMARY KEY, name VARCHAR(120) NOT NULL, xp INT NOT NULL);",
        "START TRANSACTION;",
        "DELETE FROM paragon_collectible_spell_xp;",
        "DELETE FROM paragon_collectible_item_xp;",
    ]
    for ch in chunks(spell_rows, 500):
        stmts.append("INSERT INTO paragon_collectible_spell_xp VALUES %s;" % ",".join(
            "(%d,'%s','%s',%d)" % (s, k, esc((n or "")[:120]), x)
            for s, k, n, x in ch))
    for ch in chunks(item_rows, 500):
        stmts.append("INSERT INTO paragon_collectible_item_xp VALUES %s;" % ",".join(
            "(%d,'%s',%d)" % (i, esc((n or "")[:120]), x)
            for i, n, x in ch))
    stmts.append("COMMIT;")
    mysql("\n".join(stmts), db="acore_ale")
    print("wrote %d spell rows, %d above-baseline item rows"
          % (len(spell_rows), len(item_rows)))

    if args.seed:
        mysql("\n".join([
            "CREATE TABLE IF NOT EXISTS paragon_rewarded_collectible_spell ("
            "account_id INT UNSIGNED NOT NULL, spell_id INT UNSIGNED NOT NULL, "
            "PRIMARY KEY (account_id, spell_id));",
            "CREATE TABLE IF NOT EXISTS paragon_rewarded_appearance ("
            "account_id INT UNSIGNED NOT NULL, item_id INT UNSIGNED NOT NULL, "
            "PRIMARY KEY (account_id, item_id));",
            "START TRANSACTION;",
            "INSERT IGNORE INTO paragon_rewarded_collectible_spell "
            "SELECT acs.account_id, acs.spell_id FROM acore_characters.account_collection_spell acs "
            "JOIN paragon_collectible_spell_xp x ON x.spell_id = acs.spell_id;",
            "INSERT IGNORE INTO paragon_rewarded_appearance "
            "SELECT account_id, item_template_id FROM acore_characters.custom_unlocked_appearances;",
            "COMMIT;",
        ]), db="acore_ale")
        for t in ("paragon_rewarded_collectible_spell", "paragon_rewarded_appearance"):
            n = mysql("SELECT COUNT(*) FROM %s;" % t, db="acore_ale")[0][0]
            print("seeded %s: %s rows" % (t, n))

    # ---- review CSV --------------------------------------------------------
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    review.sort(key=lambda r: -r[3])
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kind", "id", "name", "xp", "reason"])
        w.writerows(review)
    print("review CSV:", OUT_CSV)
    print("\nremember: worldserver restart to load the new values")


if __name__ == "__main__":
    main()
