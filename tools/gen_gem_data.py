#!/usr/bin/env python3
"""Generate paragon_gem_data.lua: GemProperties id -> enchant id + color mask,
plus ItemLimitCategory id -> max equipped count.

Source: client GemProperties.dbc — fixed 5-int layout (id, enchantId,
maxcount_inv, maxcount_item, colorMask; mask 1=meta 2=red 4=yellow 8=blue) —
and ItemLimitCategory.dbc (20 ints; maxCount at index 18). The server-side
double-buckle module (milestone 1100) resolves a gem ITEM to its enchant via
item_template.GemProperties (runtime WorldDBQuery) and these tables (the DBC
half the server exposes nowhere in Lua); the limit table enforces the
"Unique-Equipped: Jeweler's Gems (3)" class of caps on the custom socket.

Layout is validated against known rows before writing: Bold Cardinal Ruby
(item 40111, prop 1287) must be red, Chaotic Skyflare Diamond (item 41285,
prop 1381) must be meta, limit category 2 (Jeweler's Gems) must cap at 3.
"""
import os
import sys

import gen_glyph_data as g

OUT = os.path.join(g.HERE, "..", "Server", "azerothcore-test", "azerothcore-wotlk",
                   "env", "dist", "etc", "lua_scripts", "paragon", "modules",
                   "paragon_gem_data.lua")
KNOWN = {1287: 2, 1381: 1}  # prop id -> expected color mask


def main():
    records, field_count, _ = g.read_wdbc_ints(g.extract_dbc("GemProperties.dbc"))
    if field_count != 5:
        sys.exit(f"GemProperties.dbc has {field_count} fields, expected 5 — layout changed?")
    by_id = {r[0]: r for r in records}
    for prop, color in KNOWN.items():
        if prop not in by_id:
            sys.exit(f"gem property {prop} missing from DBC")
        if by_id[prop][4] != color:
            sys.exit(f"gem property {prop} color {by_id[prop][4]} != expected {color} — column order changed?")

    limit_records, limit_fc, _ = g.read_wdbc_ints(g.extract_dbc("ItemLimitCategory.dbc"))
    if limit_fc != 20:
        sys.exit(f"ItemLimitCategory.dbc has {limit_fc} fields, expected 20 — layout changed?")
    limits = {r[0]: r[18] for r in limit_records}
    if limits.get(2) != 3:
        sys.exit(f"limit category 2 maxCount {limits.get(2)} != expected 3 — column order changed?")

    # ---- gem enchant STAT decode (milestone 1150 gem doubling) ----------
    # SpellItemEnchantment.dbc layout (calibrated below): [0]=id,
    # [2..4]=effectType x3, [5..7]=amountMin x3, [11..13]=effectArg x3
    # (ItemModType for type 5, spellId for type 3, school for type 4).
    # Types across the whole gem pool: 5=STAT, 3=EQUIP_SPELL, 4=RESISTANCE
    # (school 0 = armor). Type-3 specials decoded from Spell.dbc: a single
    # MOD_STAT aura with misc -1 = "+N All Stats", a MOD_RESISTANCE aura
    # with a multi-school mask = "+N Resist All"; every other embedded
    # spell (meta specials, spell penetration) is intentionally OMITTED —
    # the doubler only re-applies flat stats.
    ench_records, ench_fc, _ = g.read_wdbc_ints(g.extract_dbc("SpellItemEnchantment.dbc"))
    if ench_fc != 38:
        sys.exit(f"SpellItemEnchantment.dbc has {ench_fc} fields, expected 38")
    ench_by_id = {r[0]: r for r in ench_records}

    # Activation conditions (meta gems): SpellItemEnchantmentCondition.dbc is
    # PACKED (uint8 fields — the generic 4-byte reader misparses it): 64-byte
    # records: id u32, color u8x5, lt_operand u32x5 (unused), comparator u8x5,
    # compare_color u8x5, value u32x5, logic u8x5 (unused). Core semantics
    # (Player::EnchantmentFitsRequirements): color/compare_color index
    # 1=meta 2=red 3=yellow 4=blue; comparator 2 '<', 3 '>', 5 '>='.
    import struct
    cond_raw = open(g.extract_dbc("SpellItemEnchantmentCondition.dbc"), "rb").read()
    _, cond_count, _, cond_size, _ = struct.unpack("<4sIIII", cond_raw[:20])
    if cond_size != 64:
        sys.exit(f"SpellItemEnchantmentCondition.dbc record size {cond_size}, expected 64")
    conditions = {}
    for i in range(cond_count):
        off = 20 + i * cond_size
        cid = struct.unpack_from("<I", cond_raw, off)[0]
        color = cond_raw[off + 4:off + 9]
        comparator = cond_raw[off + 29:off + 34]
        compare_color = cond_raw[off + 34:off + 39]
        value = struct.unpack_from("<5I", cond_raw, off + 39)
        rows = ["{ c = %d, op = %d, cc = %d, v = %d }"
                % (color[j], comparator[j], compare_color[j], value[j])
                for j in range(5) if color[j]]
        if rows:
            conditions[cid] = rows
    # calibration: Chaotic Skyflare's enchant 3621 -> condition 142 =
    # "at least 2 blue gems"
    if ench_by_id[3621][34] != 142 or conditions.get(142) != ["{ c = 4, op = 5, cc = 0, v = 2 }"]:
        sys.exit("condition calibration failed (ench 3621 / condition 142)")
    cols = g.spell_column_indices()
    spell_records, _, _ = g.read_wdbc_ints(g.extract_dbc("Spell.dbc"))
    spell_by_id = {r[0]: r for r in spell_records}

    def decode_spell_special(spell_id):
        rec = spell_by_id.get(spell_id)
        if not rec:
            return None
        effects = [(rec[cols["Effect_%d" % i]], rec[cols["EffectAura_%d" % i]],
                    rec[cols["EffectBasePoints_%d" % i]] + rec[cols["EffectDieSides_%d" % i]],
                    rec[cols["EffectMiscValue_%d" % i]]) for i in (1, 2, 3)]
        live = [e for e in effects if e[0] != 0]
        if len(live) == 1 and live[0][0] == 6:
            _, aura, amount, misc = live[0]
            if aura == 29 and misc in (-1, 0xFFFFFFFF):  # MOD_STAT all stats
                return ("as", amount)
            if aura == 22 and bin(misc & 0x7E).count("1") >= 6:  # MOD_RESISTANCE all schools
                return ("ra", amount)
        return None

    def decode_enchant(ench_id):
        rec = ench_by_id.get(ench_id)
        if not rec:
            return []
        out = []
        for i in range(3):
            etype, amount, arg = rec[2 + i], rec[5 + i], rec[11 + i]
            if etype == 5 and amount > 0 and arg > 0:
                out.append('{ k = "s", t = %d, a = %d }' % (arg, amount))
            elif etype == 4 and arg == 0 and amount > 0:
                out.append('{ k = "ar", a = %d }' % amount)
            elif etype == 3:
                special = decode_spell_special(arg)
                if special:
                    out.append('{ k = "%s", a = %d }' % special)
        # activation condition rides the entry's hash part (array part stays
        # the effect list); an unmet condition suppresses the whole enchant
        if out and rec[34] and rec[34] in conditions:
            out.insert(0, "cond = %d" % rec[34])
        return out

    # calibration: known enchants must decode to known shapes
    for ench_id, want in ((3518, '{ k = "s", t = 4, a = 20 }'),      # +20 Strength
                          (3532, '{ k = "s", t = 7, a = 30 }'),      # +30 Stamina
                          (3879, '{ k = "as", a = 10 }'),            # Nightmare Tear
                          (3321, '{ k = "ar", a = 150 }')):          # +150 Armor
        got = decode_enchant(ench_id)
        if want not in got:
            sys.exit(f"calibration failed: enchant {ench_id} decoded {got}, expected {want}")

    stat_rows = []
    for r in sorted(records, key=lambda r: r[0]):
        if r[1]:
            fx = decode_enchant(r[1])
            if fx:
                stat_rows.append("    [%d] = { %s }," % (r[1], ", ".join(fx)))

    rows = ["    [%d] = { ench = %d, color = %d }," % (r[0], r[1], r[4])
            for r in sorted(records, key=lambda r: r[0])
            if r[1]]  # a gem property without an enchant sockets nothing
    limit_rows = ["    [%d] = %d," % (cat, cap) for cat, cap in sorted(limits.items())]
    lines = ["-- GENERATED by Tools/gen_gem_data.py - do not hand-edit.",
             "-- GemProperties.dbc id -> { ench = SpellItemEnchantment id, color = mask }",
             "-- (mask: 1=meta 2=red 4=yellow 8=blue). %d entries." % len(rows),
             "ParagonGemProps = {"]
    lines.extend(rows)
    lines.append("}")
    lines.append("-- ItemLimitCategory.dbc id -> max equipped count. %d entries." % len(limit_rows))
    lines.append("ParagonGemLimitMax = {")
    lines.extend(limit_rows)
    lines.append("}")
    lines.append("-- gem enchant id -> flat-stat effects (milestone 1150 doubling).")
    lines.append('-- k: "s" = stat (t = ItemModType), "as" = all stats, "ra" = resist all,')
    lines.append('-- "ar" = armor; cond = activation condition id (meta gems). Meta')
    lines.append("-- specials / spell-pen spells intentionally absent. %d entries." % len(stat_rows))
    lines.append("ParagonGemStats = {")
    lines.extend(stat_rows)
    lines.append("}")
    lines.append("-- SpellItemEnchantmentCondition.dbc: activation rules (core comparator")
    lines.append("-- semantics: colors 1=meta 2=red 3=yellow 4=blue; op 2 '<', 3 '>', 5 '>=';")
    lines.append("-- cc > 0 compares against that color's count instead of v). %d entries." % len(conditions))
    lines.append("ParagonGemConditions = {")
    for cid in sorted(conditions):
        lines.append("    [%d] = { %s }," % (cid, ", ".join(conditions[cid])))
    lines.append("}")
    lines.append('print("[Paragon] Rework: gem data loaded (" .. tostring(%d) .. " properties)")' % len(rows))

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"OK: {len(records)} gem properties -> {os.path.normpath(OUT)}")


if __name__ == "__main__":
    main()
