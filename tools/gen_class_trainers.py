#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Generate the SPELL_RANKS entries for the nine non-Paladin classes' six
"Beyond Mastery" trainer waves (milestones 175/525/775/900/1175/1425).

THE RULES, all inherited from the Paladin track rather than invented here:

  values  Each new rank continues its OWN chain's measured growth ratio. The
          ratio is taken from the stock ranks themselves (geometric mean of the
          last few steps) and applied to every column that actually scales --
          basepoints and die sides together, so the damage RANGE grows with the
          minimum rather than flattening out.

  cost    Per-wave base band, plus 2,000g for every custom rank already in that
          spell's chain. A spell you have extended three times costs more to
          extend a fourth.

  gate    trainer_spell.ReqAbility1 is the wave's marker spell (taught by the
          milestone) and ReqAbility2 is the PREVIOUS rank, so ranks cannot be
          skipped and the chain stays closed.

  clone   ALWAYS the stock top rank, never a previous custom rank: clone_record
          reads the PRISTINE client extract, where custom rows do not exist.

DEATH KNIGHT BREAKS PARITY, by explicit decision. Every other class has 14-40
trainer-taught rank chains to draw on; the Death Knight has eight, because its
spells all start at level 55. Holding it to 31 new ranks would drive some
chains four or five ranks deep while the Paladin's deepest is three. It gets
four spells per wave instead of five or six -- 24 ranks over eight chains,
exactly three each, which is the Paladin's own maximum depth.

Run:  python gen_class_trainers.py           # review table + warnings
      python gen_class_trainers.py --emit    # + the SPELL_RANKS python block
"""
import argparse
import collections
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paragon_client_patch as P  # noqa: E402

SPELL_FIELDS = 234
NAME = 136
EFFECT_ID = {1: 71, 2: 72, 3: 73}
EFFECT_DIE = {1: 74, 2: 75, 3: 76}
EFFECT_BP = {1: 80, 2: 81, 3: 82}

# Milestone -> (gate marker spell, base cost band in gold). The bands are the
# ones already shipped for the Paladin, so a Warrior and a Paladin pay the same
# for the same slot of the same wave.
WAVES = [
    (175,  1900007, [2500, 4000, 5000, 6000, 7500, 10000]),
    (525,  1900076, [3000, 5000, 6000, 9000, 10000]),
    (775,  1900077, [10000, 11000, 13000, 15000, 17000]),
    (900,  1900078, [18000, 19000, 21000, 23000, 25000]),
    (1175, 1900111, [26000, 27000, 29000, 31000, 33000]),
    (1425, 1900146, [34000, 35000, 37000, 39000, 41000]),
]
PREMIUM_PER_PRIOR_RANK = 2000   # gold

CLASS_BLOCK = {"Warrior": 1901040, "Hunter": 1901140, "Rogue": 1901240,
               "Priest": 1901340, "Death Knight": 1901440, "Shaman": 1901540,
               "Mage": 1901640, "Warlock": 1901740, "Druid": 1901840}

# CANDIDATE chains per class, in priority order: the spells that class
# actually presses, spread across its three specs. The generator FILTERS this
# list before assigning anything -- a spell whose payload lives in a triggered
# spell, a summoned totem or a duration has no column to continue, and there is
# no honest "next rank" for it. Traps, weapon imbues and totems fall out that
# way, so the lists carry slack past the number actually needed.
CHAINS = {
    "Warrior": ["Heroic Strike", "Mortal Strike", "Shield Slam", "Execute",
                "Revenge", "Thunder Clap", "Slam", "Cleave", "Rend",
                "Battle Shout", "Demoralizing Shout", "Devastate",
                "Commanding Shout", "Charge"],
    "Hunter": ["Serpent Sting", "Arcane Shot", "Steady Shot", "Multi-Shot",
               "Aspect of the Hawk", "Raptor Strike", "Mongoose Bite",
               "Volley", "Black Arrow", "Mend Pet", "Counterattack",
               "Hunter's Mark", "Aspect of the Dragonhawk",
               "Aspect of the Wild", "Explosive Trap", "Immolation Trap",
               "Wyvern Sting", "Scare Beast", "Freezing Trap"],
    "Rogue": ["Sinister Strike", "Eviscerate", "Backstab", "Rupture",
              "Ambush", "Envenom", "Hemorrhage", "Garrote", "Deadly Throw",
              "Slice and Dice", "Feint", "Mutilate", "Kidney Shot", "Sap",
              "Evasion", "Sprint", "Vanish"],
    "Priest": ["Mind Blast", "Shadow Word: Pain", "Flash Heal", "Greater Heal",
               "Renew", "Power Word: Shield", "Mind Flay", "Smite",
               "Holy Fire", "Devouring Plague", "Prayer of Healing",
               "Circle of Healing", "Vampiric Touch", "Shadow Word: Death",
               "Holy Nova", "Binding Heal", "Heal", "Prayer of Mending",
               "Inner Fire", "Power Word: Fortitude", "Divine Spirit",
               "Shadow Protection", "Mind Sear", "Resurrection"],
    "Death Knight": ["Obliterate", "Death Strike", "Icy Touch", "Plague Strike",
                     "Blood Strike", "Death Coil", "Blood Boil",
                     "Death and Decay"],
    "Shaman": ["Lightning Bolt", "Healing Wave", "Earth Shock",
               "Chain Lightning", "Flame Shock", "Chain Heal",
               "Lesser Healing Wave", "Lightning Shield", "Frost Shock",
               "Earth Shield", "Healing Stream Totem", "Stoneclaw Totem",
               "Frostbrand Weapon", "Rockbiter Weapon", "Ancestral Spirit",
               "Stoneskin Totem", "Mana Spring Totem", "Flametongue Totem",
               "Windfury Weapon", "Flametongue Weapon", "Searing Totem",
               "Magma Totem", "Strength of Earth Totem", "Fire Nova"],
    "Mage": ["Frostbolt", "Fireball", "Arcane Blast", "Pyroblast", "Scorch",
             "Fire Blast", "Frostfire Bolt", "Ice Lance", "Cone of Cold",
             "Blizzard", "Flamestrike", "Arcane Explosion", "Ice Barrier",
             "Blast Wave", "Living Bomb", "Arcane Barrage", "Dragon's Breath",
             "Mana Shield", "Frost Ward", "Fire Ward", "Arcane Missiles"],
    "Warlock": ["Shadow Bolt", "Corruption", "Immolate", "Incinerate",
                "Curse of Agony", "Unstable Affliction", "Haunt", "Chaos Bolt",
                "Searing Pain", "Shadowburn", "Drain Life", "Soul Fire",
                "Rain of Fire", "Life Tap", "Seed of Corruption", "Drain Soul",
                "Death Coil", "Shadowflame", "Hellfire", "Shadow Ward"],
    "Druid": ["Wrath", "Starfire", "Moonfire", "Healing Touch", "Rejuvenation",
              "Regrowth", "Shred", "Mangle (Cat)", "Mangle (Bear)", "Maul",
              "Rip", "Lifebloom", "Wild Growth", "Insect Swarm",
              "Ferocious Bite", "Swipe (Bear)", "Rake", "Claw", "Ravage",
              "Hurricane", "Tranquility", "Thorns", "Lacerate", "Pounce"],
}
DK_PER_WAVE = 4
MAX_CHAINS = 16          # per class, after filtering -- keeps depth near 2


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


def ratio_of(series):
    """Growth ratio of a rank ladder, from the ladder itself.

    Geometric mean of the last three steps rather than just the final one: a
    single step can be an outlier (a rank added in a later patch and tuned to
    a different curve), and three steps average that out while still tracking
    the TOP of the ladder, which is where the new ranks attach.
    """
    steps = []
    for a, b in zip(series, series[1:]):
        if a > 0 and b > 0:
            steps.append(float(b) / a)
    steps = steps[-3:]
    if not steps:
        return None
    prod = 1.0
    for s in steps:
        prod *= s
    return prod ** (1.0 / len(steps))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", action="store_true")
    args = ap.parse_args()

    sla, _ = dbc(P.extract_dbc("SkillLineAbility.dbc"), 14)
    sp, sps = dbc(P.extract_dbc("Spell.dbc"), SPELL_FIELDS)
    spell = {r[0]: r for r in sp}
    sla_spells = set(r[2] for r in sla)

    # chain root -> ordered stock rank spell ids
    chain = collections.defaultdict(dict)
    for row in P.mysql("SELECT first_spell_id, spell_id, `rank` FROM spell_ranks;"):
        chain[int(row[0])][int(row[2])] = int(row[1])
    trained = set(int(r[0]) for r in
                  P.mysql("SELECT DISTINCT SpellId FROM trainer_spell;"))

    # (class, spell name) -> root, resolved from the chains that are TRAINED
    roots = {}
    for root, ranks in chain.items():
        if root not in spell:
            continue
        nm = sps(spell[root][NAME])
        top = ranks[max(ranks)]
        if top in trained or root in trained:
            roots.setdefault(nm, []).append(root)

    entries, problems = [], []

    def analyse(cname, nm):
        """Everything needed to extend one chain, or a reason it cannot be."""
        if nm not in roots:
            return None, "no trained rank chain by that name"
        root = roots[nm][0]
        ranks = chain[root]
        order = [ranks[k] for k in sorted(ranks)]
        top = order[-1]
        if top not in trained:
            return None, "top rank %d has no trainer row" % top
        if top not in sla_spells:
            return None, "top rank %d has no SkillLineAbility row" % top
        scaling = {}
        for eff in (1, 2, 3):
            if spell[top][EFFECT_ID[eff]] == 0:
                continue
            for label, cols in (("EffectBasePoints_%d" % eff, EFFECT_BP),
                                ("EffectDieSides_%d" % eff, EFFECT_DIE)):
                series = [spell[s][cols[eff]] for s in order]
                # Judge the column by its TOP ranks only. The ratio is measured
                # there and the new ranks attach there, so a rank-1 zero
                # (Multi-Shot adds no bonus damage at rank 1) says nothing about
                # whether the ladder continues.
                tail = series[-4:]
                if len(set(tail)) < 2 or any(v == 0 for v in tail):
                    continue
                if len(set((v > 0) for v in tail)) > 1:
                    continue
                # !! BASEPOINTS ARE NOT ALWAYS A MAGNITUDE !!
                # Living Bomb stores the id of the spell to detonate in
                # EffectBasePoints_2: 44460 / 55360 / 55361 across its three
                # ranks. That is a rising, same-sign, non-zero ladder and it
                # sails through every other test here -- and "continuing" it
                # points rank 4 at a spell that does not exist, which would
                # break the spell outright rather than merely mistune it.
                # A ladder whose every rung is itself a high spell id is an id
                # column; real damage and healing numbers in 3.3.5 do not reach
                # 20000 (the largest in these chains is Ice Barrier at ~3300).
                if all(v >= 20000 and v in spell for v in tail):
                    continue
                sign = 1 if tail[-1] > 0 else -1
                r = ratio_of([abs(v) for v in tail])
                if r and r > 1.0:
                    scaling[label] = (series[-1], r, sign)
        if not scaling:
            return None, ("nothing grows across its top ranks -- the payload "
                          "is a triggered spell, a totem or a duration")
        return {"root": root, "clone": top, "rank": max(ranks),
                "vals": scaling, "prev": top, "customs": 0}, None

    for cname in sorted(CHAINS):
        nid = CLASS_BLOCK[cname]
        state, dropped = {}, []
        usable = []
        for nm in CHAINS[cname]:
            st, why = analyse(cname, nm)
            if st is None:
                dropped.append((nm, why))
            elif len(usable) < MAX_CHAINS:
                usable.append(nm)
                state[nm] = st
        need = DK_PER_WAVE * len(WAVES) if cname == "Death Knight" else \
            sum(len(b) for _, _, b in WAVES)
        if not usable:
            problems.append("%s: no usable chain at all" % cname)
            continue
        if len(usable) * 3 < need:
            problems.append("%s: only %d usable chains for %d slots -- some "
                            "chain would go more than 3 ranks deep"
                            % (cname, len(usable), need))

        print("\n" + "=" * 78)
        print("%s   (spell id block %d-%d)" % (cname, nid, nid + 39))
        print("=" * 78)
        if dropped:
            print("  dropped: %s" % ", ".join(
                "%s (%s)" % (n, w.split(" -- ")[0]) for n, w in dropped))

        cursor = 0
        for milestone, gate, band in WAVES:
            n = DK_PER_WAVE if cname == "Death Knight" else len(band)
            print("  -- milestone %d --" % milestone)
            for i in range(n):
                nm = usable[cursor % len(usable)]
                cursor += 1
                cost_g = band[min(i, len(band) - 1)]
                st = state[nm]
                st["rank"] += 1
                newvals = {}
                for label, (cur, r, sign) in st["vals"].items():
                    nxt = sign * int(round(abs(cur) * r))
                    newvals[label] = nxt
                    st["vals"][label] = (nxt, r, sign)
                cost = cost_g + PREMIUM_PER_PRIOR_RANK * st["customs"]
                entries.append({
                    "cls": cname, "milestone": milestone, "name": nm,
                    "first": st["root"], "clone": st["clone"],
                    "new_id": nid, "rank": st["rank"], "values": newvals,
                    "cost": cost * 10000, "req": gate, "req2": st["prev"],
                })
                print("     %-26s R%-3d %6dg  %s" % (
                    nm, st["rank"], cost,
                    "  ".join("%s=%d" % (k.replace("EffectBasePoints_", "bp")
                                          .replace("EffectDieSides_", "die"), v)
                              for k, v in sorted(newvals.items()))))
                st["prev"] = nid
                st["customs"] += 1
                nid += 1

    print("\n" + "=" * 78)
    print("%d new trainer ranks across %d classes" %
          (len(entries), len(CHAINS)))
    by_cls = collections.Counter(e["cls"] for e in entries)
    for c in sorted(by_cls):
        depth = collections.Counter(e["name"] for e in entries if e["cls"] == c)
        print("  %-14s %2d ranks over %2d chains, deepest %d"
              % (c, by_cls[c], len(depth), max(depth.values())))
    if problems:
        print("\n!! %d PROBLEM(S):" % len(problems))
        for p in problems:
            print("   - " + p)
    else:
        print("\nno problems: every chain is trainer-taught, has a "
              "SkillLineAbility row, and grows.")

    if args.emit:
        out = os.path.join(HERE, "generated", "class_trainer_ranks.py")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write("# GENERATED by gen_class_trainers.py -- do not hand-edit.\n")
            f.write("# Paste into paragon_client_patch.py SPELL_RANKS.\n")
            f.write("CLASS_SPELL_RANKS = [\n")
            cur = None
            for e in entries:
                key = (e["cls"], e["milestone"])
                if key != cur:
                    cur = key
                    f.write("    # ---- %s milestone %d ----\n" % key)
                f.write('    {{ "name": "{name}", "first": {first}, "clone": {clone}, '
                        '"new_id": {new_id}, "rank": {rank},\n'
                        '      "values": {values}, "cost": {cost},\n'
                        '      "req": {req}, "req2": {req2},\n'
                        '      "cls": "{cls}", "milestone": {milestone} }},\n'.format(**e))
            f.write("]\n")
        print("\nwrote %s" % out)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
