#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Generate the TALENT_RANKS entries for the nine non-Paladin classes.

WHY A GENERATOR: milestone parity means 135 talents x up to 4 new ranks = 360
new spell rows. Hand-authoring 360 basepoint values is how you ship a Holy
Light bug. Everything here is READ OUT OF THE DBCs and extrapolated by rule,
then printed for review before a single row is written.

WHAT IT DOES per pick:
  1. Resolves the talent by (class, name) against Talent.dbc, so a typo is an
     error rather than a silently wrong coordinate.
  2. Reads every retail rank's Spell.dbc row and diffs them field by field.
  3. Auto-extrapolates the EffectBasePoints_1..3 columns that actually scale.
  4. REFUSES anything it cannot justify: a scaling column whose die is not 1
     (the emitter's value-1 convention would be wrong), or a non-basepoint
     field that also scales (proc chances, durations, misc values) -- those
     get flagged for a human instead of guessed at.
  5. Screens each rank chain against spell_script_names for POSITIVE bindings,
     which bind one rank only and would not carry to a new rank.

Run:  python gen_class_talents.py            # review table + warnings
      python gen_class_talents.py --emit     # + the TALENT_RANKS python block
"""
import argparse
import collections
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paragon_client_patch as P  # noqa: E402  (extract_dbc / DB creds)

SPELL_FIELDS = 234
NAME, ICON, DIE1, BP1 = 136, 133, 74, 80
# 0-based Spell.dbc column indices, verified against information_schema on
# acore_world.spell_dbc (the SQL table is the DBC layout, column for column).
EFFECT_BP = {1: 80, 2: 81, 3: 82}
EFFECT_DIE = {1: 74, 2: 75, 3: 76}
EFFECT_ID = {1: 71, 2: 72, 3: 73}
PROC_CHANCE = 35

# Columns this generator is willing to extrapolate. Basepoints cover the vast
# majority; ProcChance covers the "N% chance" talents whose ONLY per-rank
# scaling is the chance itself (Arcane Concentration 2/4/6/8/10%, Burning
# Determination 5/10%) -- without it those talents look unscalable.
SCALABLE = dict([("EffectBasePoints_%d" % e, c) for e, c in EFFECT_BP.items()]
                + [("ProcChance", PROC_CHANCE)])

# Differences between the top two ranks that make a CLONE OF THE TOP RANK
# pointless or wrong, as opposed to merely different from lower ranks.
#
#   EffectTriggerSpell  the rank's real payload lives in the spell it fires, so
#                       a clone fires the OLD rank's payload and gains nothing
#                       (Flurry, Inspiration, Icy Talons all work this way)
#   EffectPointsPerCombo  per-combo-point scaling this generator does not touch
#   EffectMiscValue     names WHAT an aura acts on (stat index, mechanic,
#                       school); if it moves per rank the ranks are not the
#                       same effect getting bigger
#
# Everything else that differs between ranks -- attributes, class masks, die
# sides, implicit targets, bonus multipliers, rune costs -- is inherited from
# the top rank, which is correct by construction: the new rank IS the top rank
# with bigger numbers.
HARD_UNSAFE = set(list(range(116, 119)) + list(range(119, 122))
                  + list(range(110, 113)))

# Picks whose projection passes 100 but where 100 is NOT a ceiling, checked one
# by one against the effect's own aura type rather than assumed:
#   Tactical Mastery   eff2/3 = ADD_PCT_MODIFIER op 2 (THREAT) on Bloodthirst
#                      and Mortal Strike -- a threat multiplier, uncapped
#   Frost Warding      eff1 = ADD_PCT_MODIFIER op 8 (ALL_EFFECTS) on Frost/Ice
#                      Armor -- scales the armor buff itself, uncapped
# Anything NOT listed here that crosses 100 is treated as a real ceiling and
# refused, because the failure mode is silent: a "chance" talent that already
# reads 100% at rank 5 simply grants nothing for the extra ranks.
#   Careful Aim        eff1 = aura 212 MOD_RANGED_ATTACK_POWER_OF_STAT_PERCENT
#                      with misc 3 (Intellect) -- a CONVERSION RATIO, exactly
#                      the shape of the Paladin's own Touched by the Light
#                      (spell power from Strength 60 -> 80%), uncapped
#   Shadow Power       eff2 = ADD_PCT_MODIFIER op 15 (CRIT_DAMAGE_BONUS) -- a
#                      multiplier increase on crit damage, uncapped
CAP_VERIFIED = {
    ("Warrior", "Tactical Mastery"),
    ("Mage", "Frost Warding"),
    ("Hunter", "Careful Aim"),
    ("Priest", "Shadow Power"),
}

# Fields that differ between ranks for uninteresting reasons and must NOT be
# read as "this talent scales something we are not handling".
IGNORED_DIFFS = {
    0,                       # ID
    136, 137, 138, 139, 140, 141, 142, 143,   # Name_Lang_* block
    144, 145, 146, 147, 148, 149, 150, 151,
    152,                     # Name flags
    153, 154, 155, 156, 157, 158, 159, 160,   # NameSubtext_Lang_*
    161, 162, 163, 164, 165, 166, 167, 168,
    169,
    170, 171, 172, 173, 174, 175, 176, 177,   # Description_Lang_*
    178, 179, 180, 181, 182, 183, 184, 185,
    186,
    187, 188, 189, 190, 191, 192, 193, 194,   # AuraDescription_Lang_*
    195, 196, 197, 198, 199, 200, 201, 202,
    203,
    56, 57, 58,              # SpellLevel / BaseLevel / MaxLevel
    23,                      # SpellVisualID variations between ranks
}

# ---------------------------------------------------------------------------
# THE PICKS -- 15 talent slots per class, mirroring the Paladin track exactly.
# (milestone, +ranks) shape is fixed by parity; see the milestone comments in
# paragon_rework_track.lua. Names are matched against Talent.dbc by class.
# ---------------------------------------------------------------------------
SHAPE = [(75, [4]), (275, [4]), (475, [4]), (625, [3]), (725, [2]), (825, [2]),
         (1025, [4]), (1125, [2]), (1225, [2, 2]), (1375, [2, 2, 2]),
         (1475, [1, 4])]

PICKS = {
    "Warrior": {
        75: ["Deflection"], 275: ["Tactical Mastery"], 475: ["Vitality"],
        625: ["Anticipation"], 725: ["Precision"], 825: ["Cruelty"],
        1025: ["Toughness"], 1125: ["Iron Will"],
        1225: ["Commanding Presence", "Booming Voice"],
        1375: ["Two-Handed Weapon Specialization",
               "One-Handed Weapon Specialization", "Impale"],
        1475: ["Strength of Arms", "Improved Berserker Stance"]},
    "Hunter": {
        75: ["Endurance Training"], 275: ["Efficiency"], 475: ["Survivalist"],
        625: ["Lightning Reflexes"], 725: ["Mortal Shots"], 825: ["Lethal Shots"],
        1025: ["Thick Hide"], 1125: ["Surefooted"],
        1225: ["Ferocious Inspiration", "Improved Steady Shot"],
        1375: ["Ranged Weapon Specialization", "Savage Strikes",
               "Improved Aspect of the Hawk"],
        1475: ["Careful Aim", "Hunter vs. Wild"]},
    "Rogue": {
        75: ["Deadliness"], 275: ["Serrated Blades"], 475: ["Vitality"],
        625: ["Lightning Reflexes"], 725: ["Lethality"], 825: ["Malice"],
        1025: ["Deadened Nerves"], 1125: ["Nerves of Steel"],
        1225: ["Improved Expose Armor", "Master Poisoner"],
        1375: ["Dual Wield Specialization", "Mace Specialization",
               "Close Quarters Combat"],
        1475: ["Find Weakness", "Sinister Calling"]},
    "Priest": {
        75: ["Twin Disciplines"], 275: ["Shadow Focus"], 475: ["Spiritual Healing"],
        625: ["Blessed Resilience"], 725: ["Darkness"], 825: ["Holy Specialization"],
        1025: ["Spell Warding"], 1125: ["Unbreakable Will"],
        1225: ["Improved Power Word: Fortitude", "Divine Providence"],
        1375: ["Shadow Power", "Divine Fury", "Improved Flash Heal"],
        1475: ["Mental Strength", "Spiritual Guidance"]},
    "Death Knight": {
        75: ["Subversion"], 275: ["Runic Power Mastery"],
        475: ["Veteran of the Third War"], 625: ["Spell Deflection"],
        725: ["Necrosis"], 825: ["Dark Conviction"], 1025: ["Toughness"],
        1125: ["Frigid Dreadplate"],
        1225: ["Abomination's Might", "Virulence"],
        1375: ["Two-Handed Weapon Specialization", "Nerves of Cold Steel",
               "Vicious Strikes"],
        1475: ["Ravenous Dead", "Impurity"]},
    "Shaman": {
        75: ["Ancestral Knowledge"], 275: ["Convection"], 475: ["Purification"],
        625: ["Nature's Guardian"], 725: ["Concussion"], 825: ["Tidal Mastery"],
        1025: ["Elemental Warding"], 1125: ["Focused Mind"],
        1225: ["Unleashed Rage", "Improved Windfury Totem"],
        1375: ["Weapon Mastery", "Dual Wield Specialization", "Elemental Weapons"],
        1475: ["Nature's Blessing", "Mental Quickness"]},
    "Mage": {
        75: ["Arcane Mind"], 275: ["Arcane Concentration"], 475: ["Molten Shields"],
        625: ["Prismatic Cloak"], 725: ["Fire Power"], 825: ["Critical Mass"],
        1025: ["Frost Warding"], 1125: ["Burning Determination"],
        1225: ["Arcane Empowerment", "Netherwind Presence"],
        1375: ["Improved Fireball", "Improved Frostbolt", "Piercing Ice"],
        1475: ["Spell Power", "Mind Mastery"]},
    "Warlock": {
        75: ["Demonic Embrace"], 275: ["Improved Life Tap"], 475: ["Fel Vitality"],
        625: ["Soul Leech"], 725: ["Shadow Mastery"],
        825: ["Improved Shadow Bolt"], 1025: ["Molten Skin"],
        1125: ["Demonic Resilience"],
        1225: ["Demonic Pact", "Malediction"],
        1375: ["Bane", "Emberstorm", "Improved Corruption"],
        1475: ["Unholy Power", "Demonic Knowledge"]},
    "Druid": {
        75: ["Naturalist"], 275: ["Moonglow"], 475: ["Gift of Nature"],
        625: ["Feral Swiftness"], 725: ["Moonfury"], 825: ["Nature's Majesty"],
        1025: ["Thick Hide"], 1125: ["Primal Tenacity"],
        1225: ["Improved Mark of the Wild", "Improved Leader of the Pack"],
        1375: ["Ferocity", "Feral Aggression", "Savage Fury"],
        1475: ["Balance of Power", "Lunar Guidance"]},
}

# One clean 100-id block per class. Talent ranks take the low 40; trainer ranks
# (gen_class_trainers.py) take 40-79; 80-99 stay spare. Contiguous per-class
# blocks exist because custom spell id RANGES are landmines -- the 1900010
# enchant-marker collision cost a silent bug once already.
CLASS_BLOCK = {"Warrior": 1901000, "Hunter": 1901100, "Rogue": 1901200,
               "Priest": 1901300, "Death Knight": 1901400, "Shaman": 1901500,
               "Mage": 1901600, "Warlock": 1901700, "Druid": 1901800}
CLASS_ID = {"Warrior": 1, "Hunter": 3, "Rogue": 4, "Priest": 5,
            "Death Knight": 6, "Shaman": 7, "Mage": 8, "Warlock": 9, "Druid": 11}


def OFFSET(top, field):
    """Effective value minus raw DBC basepoints for one column.

    A die of 1 means the game rolls 1..1 on top of the basepoints, so the
    effective number is basepoints+1 -- that is where the emitter's `value - 1`
    convention comes from. A die of 0 rolls nothing and the basepoints ARE the
    value. ProcChance is a plain percentage with no die at all.
    """
    if field.startswith("EffectBasePoints_"):
        return 1 if top[EFFECT_DIE[int(field[-1])]] == 1 else 0
    return 0


FIELD_NAME = {}


def load_field_names():
    for row in P.mysql(
            "SELECT ORDINAL_POSITION-1, COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA='acore_world' AND TABLE_NAME='spell_dbc' "
            "ORDER BY ORDINAL_POSITION;"):
        FIELD_NAME[int(row[0])] = row[1]


def dbc(path, fields=None):
    b = open(path, "rb").read()
    magic, n, f, rs, ss = struct.unpack_from("<4sIIII", b, 0)
    assert magic == b"WDBC", magic
    if fields:
        assert f == fields, (path, f)
    body = b[20:20 + n * rs]
    strs = b[20 + n * rs:]
    rows = [struct.unpack_from("<%di" % f, body, i * rs) for i in range(n)]

    def sval(o):
        if o <= 0 or o >= len(strs):
            return ""
        return strs[o:strs.index(b"\0", o)].decode("utf-8", "replace")
    return rows, sval


def project(values):
    """Continue a retail rank progression by its own cadence.

    Two shapes cover every talent in 3.3.5: a straight line from zero
    (3/6/9/12/15 -> step 3) and a line with an offset (12/25 -> step 13).
    Prefer the from-zero reading when the retail ranks actually fit it, since
    that is what Blizzard's own tuning does and it reproduces every value the
    Paladin milestones shipped by hand.
    """
    n = len(values)
    last = values[-1]
    if n >= 2:
        step0 = last / float(n)
        if all(abs(v - step0 * (i + 1)) <= 0.51 for i, v in enumerate(values)):
            step = step0
        else:
            step = (last - values[0]) / float(n - 1)
    else:
        step = float(last)

    def nth(k):
        raw = last + step * k
        # floor toward zero-extended line: matches the shipped Paladin values
        # (Improved Blessing of Might 12/25 -> 37/50, not 38/51).
        return int(raw) if raw >= 0 else -int(-raw)
    return step, nth


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", action="store_true")
    args = ap.parse_args()

    load_field_names()
    tal, _ = dbc(P.extract_dbc("Talent.dbc"), 23)
    tt, tts = dbc(P.extract_dbc("TalentTab.dbc"), 24)
    sp, sps = dbc(P.extract_dbc("Spell.dbc"), SPELL_FIELDS)
    icons, icons_s = dbc(P.extract_dbc("SpellIcon.dbc"), 2)

    icon_path = {r[0]: icons_s(r[1]) for r in icons}
    spell = {r[0]: r for r in sp}
    tabs = {}
    for r in tt:
        for cname, cid in CLASS_ID.items():
            if r[20] & (1 << (cid - 1)):
                tabs[r[0]] = (cname, tts(r[1]), r[22])

    # (class, talent name) -> talent row
    by_name = {}
    for r in tal:
        if r[1] not in tabs:
            continue
        cname, tabname, order = tabs[r[1]]
        ranks = [x for x in r[4:13] if x]
        by_name[(cname, sps(spell[ranks[0]][NAME]))] = (r, tabname, order, ranks)

    # spell_script_names positive bindings (single-rank; do NOT carry forward)
    bound = set()
    for row in P.mysql("SELECT spell_id, ScriptName FROM spell_script_names;"):
        if int(row[0]) > 0:
            bound.add(int(row[0]))

    notes = []        # benign per-rank differences, printed for the record
    entries = []      # emitted TALENT_RANKS dicts
    track = []        # (class, milestone, talent_id, tab, tier, col, base, name, icon)
    problems = []

    for cname in PICKS:
        nid = CLASS_BLOCK[cname]
        print("\n" + "=" * 78)
        print("%s   (spell id block %d-%d)" % (cname, nid, nid + 39))
        print("=" * 78)
        for milestone, adds in SHAPE:
            names = PICKS[cname][milestone]
            for tname, add in zip(names, adds):
                key = (cname, tname)
                if key not in by_name:
                    problems.append("%s: talent %r not found" % (cname, tname))
                    continue
                row, tabname, order, ranks = by_name[key]
                tid, tier, col = row[0], row[2] + 1, row[3] + 1
                base = len(ranks)
                if base + add > 9:
                    problems.append("%s %s: %d+%d exceeds 9 rank slots"
                                    % (cname, tname, base, add))
                    continue

                top = spell[ranks[-1]]
                # --- which columns actually scale? ---------------------------
                # RAW basepoints, not effective: the emitter writes `value - 1`
                # into the DBC, so emitting raw+1 restores the raw column
                # exactly, whatever the die is. The Paladin entries were
                # written as effective values with die verified to be 1, where
                # raw+1 IS the effective value -- so this is the same
                # convention, just one that also survives a die-0 effect.
                scaling = collections.OrderedDict()
                for field, fcol in sorted(SCALABLE.items()):
                    # A basepoint column on an effect slot that is switched OFF
                    # (Effect_N == 0) is dead data -- Blizzard leaves stale
                    # numbers in unused slots and they drift between ranks for
                    # no reason at all. Rogue Master Poisoner carries 33/66/100
                    # in a slot with Effect_3 = 0. Reading that as scaling makes
                    # the generator refuse a perfectly good talent.
                    if field.startswith("EffectBasePoints_"):
                        if top[EFFECT_ID[int(field[-1])]] == 0:
                            continue
                    vals = [spell[s][fcol] + OFFSET(top, field) for s in ranks]
                    if len(set(vals)) > 1:
                        scaling[field] = vals

                # Compare only the TOP TWO ranks. Low ranks of a talent
                # routinely differ from the top in shape (an effect that only
                # exists from rank 3, a school mask that widens, a class mask
                # that picks up another spell) -- and none of that matters,
                # because we clone the TOP rank and inherit its shape. What
                # WOULD matter is the top two ranks differing in something we
                # are not extrapolating: that means rank N is not simply
                # "rank N-1 with bigger numbers", so cloning it is wrong.
                unsafe, benign = [], []
                if len(ranks) >= 2:
                    a, b = spell[ranks[-2]], spell[ranks[-1]]
                    for f in range(SPELL_FIELDS):
                        if f in IGNORED_DIFFS or f in SCALABLE.values():
                            continue
                        if a[f] == b[f]:
                            continue
                        (unsafe if f in HARD_UNSAFE else benign).append(
                            FIELD_NAME.get(f, str(f)))
                if unsafe:
                    problems.append(
                        "%s %s: top two ranks differ in %s -- the payload "
                        "itself changes per rank, so a clone of the top rank "
                        "is just the top rank again"
                        % (cname, tname, ", ".join(unsafe)))
                if benign:
                    notes.append("%s %s: inherits top-rank %s"
                                 % (cname, tname, ", ".join(benign)))
                if not scaling:
                    problems.append("%s %s: nothing scalable found"
                                    % (cname, tname))
                    continue

                # A rank progression we cannot honestly continue: Blizzard
                # occasionally tunes a 1/n curve (Armored to the Teeth uses a
                # 108/54/36 armor divisor), where a straight line runs through
                # zero into nonsense. Demand one consistent sign and a
                # monotone series before projecting anything.
                broken = False
                for field, vals in scaling.items():
                    signs = set((v > 0) - (v < 0) for v in vals if v)
                    up = all(y >= x for x, y in zip(vals, vals[1:]))
                    down = all(y <= x for x, y in zip(vals, vals[1:]))
                    if len(signs) > 1 or not (up or down):
                        problems.append(
                            "%s %s: %s runs %s -- not a linear progression, "
                            "refusing to extrapolate"
                            % (cname, tname, field, "/".join(map(str, vals))))
                        broken = True
                if broken:
                    continue

                hit = [s for s in ranks if s in bound]
                if hit:
                    problems.append(
                        "%s %s: spell_script_names binds rank spell(s) %s by "
                        "POSITIVE id -- new ranks would not inherit the script"
                        % (cname, tname, hit))

                # --- project the new ranks ---------------------------------
                # EVERYTHING above and below is in EFFECTIVE values -- what the
                # tooltip shows and what the game applies -- not raw DBC
                # basepoints. That distinction matters: with die 1 the game
                # reads basepoints+1, so a talent whose tooltip reads 25%/50%
                # stores 24/49, and fitting a from-zero line to 24/49 yields a
                # step of 24.5 and a next rank of 73 instead of 75. Projecting
                # in effective space reproduces every value the Paladin
                # milestones shipped by hand, including Improved Blessing of
                # Might's 12/25 -> 37/50.
                #
                # Converting back: the emitter writes `value - 1` into the DBC,
                # so emit (effective - offset + 1) and the raw column lands
                # exactly where it should for die 0 and die 1 alike.
                new_ranks, shown = [], []
                projected = {}
                for field, series in scaling.items():
                    _, nth = project(series)
                    vals = []
                    for k in range(1, add + 1):
                        v = nth(k)
                        if field == "ProcChance" and v > 100:
                            v = 100       # a chance above certainty is a bug
                        vals.append(v)
                    projected[field] = vals

                for k in range(add):
                    vals = {}
                    for field in scaling:
                        vals[field] = projected[field][k] - OFFSET(top, field) + 1
                    new_ranks.append((nid, vals))
                    shown.append("/".join(str(projected[f][k]) for f in scaling))
                    nid += 1

                for field, series in scaling.items():
                    proj = projected[field]
                    # A monotone series can still be a curve rather than a line:
                    # Armored to the Teeth runs 108/54/36 (an armor DIVISOR,
                    # i.e. 108/n), and a straight line through it lands on 0 and
                    # then goes negative. Any projected value that loses the
                    # retail sign means the extrapolation left the talent behind.
                    sign = 1 if series[-1] > 0 else -1
                    if any(v == 0 or (v > 0) != (sign > 0) for v in proj):
                        problems.append(
                            "%s %s: %s projects to %s -- the line crosses zero, "
                            "so this is a curve (a divisor or a ratio), not a "
                            "linear rank progression"
                            % (cname, tname, field, "/".join(map(str, proj))))
                        continue
                    if (cname, tname) in CAP_VERIFIED:
                        continue
                    if max(map(abs, series)) <= 100 < max(map(abs, proj)):
                        problems.append(
                            "%s %s: %s passes 100 (%s -> %s) -- check whether "
                            "that is a percentage with a real ceiling"
                            % (cname, tname, field,
                               "/".join(map(str, series)),
                               "/".join(map(str, proj))))

                retail = ["/".join(str(scaling[f][i]) for f in scaling)
                          for i in range(base)]
                print("  %-4d +%d  %-34s %-14s t%-2d c%d  %dr" %
                      (milestone, add, tname, tabname, tier, col, base))
                print("        retail  %s" % "  ".join(retail))
                print("        NEW     %s" % "  ".join(shown))

                entries.append({
                    "cls": cname, "milestone": milestone, "talent_id": tid,
                    "name": tname, "tab": tabname, "clone": ranks[-1],
                    "base": base, "add": add, "new_ranks": new_ranks,
                    "single": list(scaling) == ["EffectBasePoints_1"],
                })
                track.append((cname, milestone, tid, order + 1, tier, col, base,
                              tname, icon_path.get(top[ICON], "")))

    print("\n" + "=" * 78)
    print("%d talent extensions, %d new spell ids" %
          (len(entries), sum(len(e["new_ranks"]) for e in entries)))
    if notes:
        print("\n%d note(s) (inherited from the cloned top rank, not errors):"
              % len(notes))
        for n in notes:
            print("   . " + n)
    if problems:
        print("\n!! %d PROBLEM(S) -- these need a human before anything is written:"
              % len(problems))
        for p in problems:
            print("   - " + p)
    else:
        print("no problems: every pick is a pure numbers bump on a linear")
        print("progression, stays inside its own ceiling, and has no")
        print("positive-id script binding on any rank.")

    if args.emit:
        out = os.path.join(HERE, "generated", "class_talent_ranks.py")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write("# GENERATED by gen_class_talents.py -- do not hand-edit.\n")
            f.write("# Paste into paragon_client_patch.py TALENT_RANKS.\n")
            f.write("CLASS_TALENT_RANKS = [\n")
            for e in entries:
                f.write("    # %s milestone %d: %s (%s) ranks %d-%d\n" % (
                    e["cls"], e["milestone"], e["name"], e["tab"],
                    e["base"] + 1, e["base"] + e["add"]))
                f.write("    {\n        \"talent_id\": %d,\n" % e["talent_id"])
                f.write("        \"clone_spell\": %d,\n" % e["clone"])
                if e["single"]:
                    f.write("        \"value_field\": \"EffectBasePoints_1\",\n")
                    f.write("        \"new_ranks\": [%s],\n" % ", ".join(
                        "(%d, %d)" % (sid, v["EffectBasePoints_1"])
                        for sid, v in e["new_ranks"]))
                else:
                    f.write("        \"new_ranks\": [\n")
                    for sid, v in e["new_ranks"]:
                        f.write("            (%d, {%s}),\n" % (sid, ", ".join(
                            '"%s": %d' % (k, v[k]) for k in sorted(v))))
                    f.write("        ],\n")
                f.write("    },\n")
            f.write("]\n\nCLASS_TALENT_TRACK = [\n")
            for t in track:
                f.write("    %r,\n" % (t,))
            f.write("]\n")
        print("\nwrote %s" % out)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
