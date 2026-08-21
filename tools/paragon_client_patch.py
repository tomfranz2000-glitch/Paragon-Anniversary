"""Unified generator for ALL Paragon custom spell/talent data.

Single source of truth. Supersedes extended_talents.py and patch_client_dbc.py
(kept for history; do not run them — they rebuild the MPQs with only their own
slice of the content).

Emits, from the three config tables below:
  - generated/paragon_content.sql : spell_dbc rows (talent ranks, spell ranks,
    markers), talent_dbc override, spell_ranks chain rows, npc_trainer rows
  - Client/Data/patch-5.MPQ + Client/Data/enUS/patch-enUS-5.MPQ with the
    patched Talent.dbc + Spell.dbc (built from pristine extracts every run;
    includes the client-only mount move-cast pass — milestone 750, see the
    comment block in main())

After applying: worldserver restart + full client restart.
Related Lua gates live in paragon_rework_track.lua (EXTENDED_TALENTS,
LEARNED_SPELL_SPECIALS, CONSECRATION_BURST.totals).

Usage: python paragon_client_patch.py [--apply]
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys

# ============================================================================
# CONFIG
# ============================================================================

# Talents raised past the retail 5-rank cap (client Talent.dbc + talent_dbc).
# Per-rank value: either a single number applied to "value_field", or a dict
# of {column: value} for talents whose ranks scale several effects at once
# (Divinity/Seals of the Pure/Conviction carry twin identical basepoints).
# All values are EFFECTIVE values; the emitter subtracts 1 for the die-1
# convention (verified die=1 on every scaled effect of every clone below).
# Gates live in paragon_rework_track.lua EXTENDED_TALENTS (one line each).
TALENT_RANKS = [
    {
        "talent_id": 2185,               # Divine Strength (Paladin Protection)
        "clone_spell": 20266,            # rank 5
        "value_field": "EffectBasePoints_1",
        "new_ranks": [(1900010, 18), (1900011, 21), (1900012, 24), (1900013, 27)],
    },
    # Milestone 550: Benediction ranks 6-9 (-12/14/16/18% instant cost) —
    # weakest talent of the five-talent package, biggest raise
    {
        "talent_id": 1407,               # Benediction (Paladin Retribution t1)
        "clone_spell": 20105,            # rank 5
        "value_field": "EffectBasePoints_1",
        "new_ranks": [(1900041, -12), (1900042, -14), (1900043, -16), (1900044, -18)],
    },
    # Milestone 575: Divinity ranks 6-9 (+6/7/8/9% healing done AND received)
    {
        "talent_id": 1442,               # Divinity (Paladin Protection t1)
        "clone_spell": 63650,            # rank 5
        "new_ranks": [
            (1900045, {"EffectBasePoints_1": 6, "EffectBasePoints_2": 6}),
            (1900046, {"EffectBasePoints_1": 7, "EffectBasePoints_2": 7}),
            (1900047, {"EffectBasePoints_1": 8, "EffectBasePoints_2": 8}),
            (1900048, {"EffectBasePoints_1": 9, "EffectBasePoints_2": 9}),
        ],
    },
    # Milestone 625: Anticipation ranks 6-8 (+6/7/8% dodge)
    {
        "talent_id": 1629,               # Anticipation (Paladin Protection t2)
        "clone_spell": 20100,            # rank 5
        "value_field": "EffectBasePoints_1",
        "new_ranks": [(1900049, 6), (1900050, 7), (1900051, 8)],
    },
    # Milestone 650: Seals of the Pure ranks 6-7 (+18/21% seal + judgement dmg)
    {
        "talent_id": 1463,               # Seals of the Pure (Paladin Holy t1)
        "clone_spell": 20332,            # rank 5
        "new_ranks": [
            (1900052, {"EffectBasePoints_1": 18, "EffectBasePoints_2": 18}),
            (1900053, {"EffectBasePoints_1": 21, "EffectBasePoints_2": 21}),
        ],
    },
    # Milestone 675: Conviction ranks 6-7 (+6/7% crit)
    {
        "talent_id": 1411,               # Conviction (Paladin Retribution t3)
        "clone_spell": 20121,            # rank 5
        "new_ranks": [
            (1900054, {"EffectBasePoints_1": 6, "EffectBasePoints_2": 6}),
            (1900055, {"EffectBasePoints_1": 7, "EffectBasePoints_2": 7}),
        ],
    },
    # Milestone 1025: Toughness ranks 6-9 (+12/14/16/18% armor from items;
    # the paired slow-duration effect keeps its retail per-rank cadence:
    # -36/42/48/54%)
    {
        "talent_id": 1423,               # Toughness (Paladin Protection t3)
        "clone_spell": 20147,            # rank 5
        "new_ranks": [
            (1900100, {"EffectBasePoints_1": 12, "EffectBasePoints_2": -36}),
            (1900101, {"EffectBasePoints_1": 14, "EffectBasePoints_2": -42}),
            (1900102, {"EffectBasePoints_1": 16, "EffectBasePoints_2": -48}),
            (1900103, {"EffectBasePoints_1": 18, "EffectBasePoints_2": -54}),
        ],
    },
    # Milestone 1125: Stoicism ranks 4-5 (stun duration -40/-50%, dispel
    # resist +40/+50% — both effects continue the retail 10-per-rank
    # cadence). First THREE-rank talent extended: its EXTENDED_TALENTS gate
    # entry carries base = 3 (extended ranks start at 0-based index 3).
    {
        "talent_id": 1748,               # Stoicism (Paladin Protection t2 c1)
        "clone_spell": 53519,            # rank 3 (the talent's top rank)
        "new_ranks": [
            (1900108, {"EffectBasePoints_1": -40, "EffectBasePoints_2": 40}),
            (1900109, {"EffectBasePoints_1": -50, "EffectBasePoints_2": 50}),
        ],
    },
    # Milestone 1225a: Swift Retribution ranks 4-5 (auras also grant 4%/5%
    # casting/ranged/melee haste — retail 1/2/3, so the +1-per-rank cadence
    # continues). CORE-SCRIPTED talent (spell_pal_swift_retribution, bound in
    # spell_script_names as -53379 = "all ranks"), which is safe here because
    # SpellMgr::LoadSpellTalentRanks builds the rank chain straight from the
    # Talent.dbc RankID slots and ObjectMgr::LoadSpellScriptNames then walks
    # GetNextRankSpell() over that whole chain — new ranks inherit the script
    # with no core change. MAX_TALENT_RANK is 9, so 3+2 fits.
    {
        "talent_id": 2148,               # Swift Retribution (Paladin Ret t9 c1)
        "clone_spell": 53648,            # rank 3 (the talent's top rank)
        "value_field": "EffectBasePoints_1",
        "new_ranks": [(1900119, 4), (1900120, 5)],
    },
    # Milestone 1225b: Improved Blessing of Might ranks 3-4 (+37%/+50% to the
    # attack power the blessing grants). Retail is 12/25 — an underlying
    # 12.5-per-rank rule floored, so the continuation is 37 (37.5) and 50.
    # Pure SpellMod aura (108 ADD_PCT_MODIFIER, misc 8) with no core script:
    # the cloned effect keeps the family mask, so the new ranks modify
    # Blessing of Might exactly like the stock ones. Second TWO-rank talent
    # shape: EXTENDED_TALENTS gate entry carries base = 2.
    {
        "talent_id": 1401,               # Improved Blessing of Might (Ret t2 c3)
        "clone_spell": 20045,            # rank 2 (the talent's top rank)
        "value_field": "EffectBasePoints_1",
        "new_ranks": [(1900121, 37), (1900122, 50)],
    },
    # Milestone 1375: THREE talents raised by 2 ranks each, all of them
    # 3-rank talents (so every EXTENDED_TALENTS gate below carries base = 3).
    # Values continue each talent's own arithmetic step exactly.
    # THE NUMBERS BELOW ARE THE DISPLAYED VALUES, not raw base points: the
    # generator stores bp = value - 1 because every clone keeps die = 1
    # (verified against Divine Strength, whose config 18 stored bp 17).
    # Writing raw bp here ships every rank one short and silently breaks
    # the talent's arithmetic progression.
    #
    # One-Handed Weapon Specialization: 4/7/10% -> 13/16% (step +3).
    # Aura 79 MOD_DAMAGE_PERCENT_DONE, single effect.
    {
        "talent_id": 1429,               # One-Handed Weapon Spec (Prot t6 c3)
        "clone_spell": 20198,            # rank 3 (the talent's top rank)
        "value_field": "EffectBasePoints_1",
        "new_ranks": [(1900132, 13), (1900133, 16)],
    },
    # Two-Handed Weapon Specialization: 2/4/6% -> 8/10% (step +2).
    {
        "talent_id": 1410,               # Two-Handed Weapon Spec (Ret t5 c1)
        "clone_spell": 20113,            # rank 3
        "value_field": "EffectBasePoints_1",
        "new_ranks": [(1900134, 8), (1900135, 10)],
    },
    # Combat Expertise: 2/4/6 -> 8/10 (step +2). THREE effects that must move
    # together - aura 240 MOD_EXPERTISE, 137 MOD_TOTAL_STAT_PERCENTAGE and
    # 290 - so this uses the per-field dict form like Divinity/Seals of the
    # Pure rather than value_field. Bumping only EffectBasePoints_1 would
    # raise the expertise and silently leave the stamina behind.
    # Milestone 1475: Touched by the Light +1 rank, Holy Guidance +4 ranks.
    # THE NUMBERS BELOW ARE DISPLAYED VALUES (generator stores bp = value-1;
    # every one of these effects carries die 1).
    #
    # Touched by the Light (talent 2195, Prot tier 9 col 1), 3 ranks ->
    # 4. THREE effects on different steps -- aura 174 spell power from
    # strength 20/40/60 (+20), aura 50 crit 10/20/30 (+10), aura 175 healing
    # power 20/40/60 (+20) -- so the per-field dict form is mandatory;
    # value_field would move one and silently leave the other two behind.
    {
        "talent_id": 2195,               # Touched by the Light (Prot t9 c1)
        "clone_spell": 53592,            # rank 3
        "new_ranks": [
            (1900151, {"EffectBasePoints_1": 80, "EffectBasePoints_2": 40,
                       "EffectBasePoints_3": 80}),
        ],
    },
    # Holy Guidance (talent 1746, Holy tier 8 col 3), 5 ranks -> 9, which
    # EXACTLY fills Talent.dbc's 9 rank slots (MAX_RANK_SLOTS) -- this talent
    # cannot be extended again. Two effects (aura 174 spell power from
    # intellect, aura 175 healing power) that always share a value, on a flat
    # +4 step: 4/8/12/16/20 -> 24/28/32/36.
    {
        "talent_id": 1746,               # Holy Guidance (Holy t8 c3)
        "clone_spell": 31841,            # rank 5
        "new_ranks": [
            (1900147, {"EffectBasePoints_1": 24, "EffectBasePoints_2": 24}),
            (1900148, {"EffectBasePoints_1": 28, "EffectBasePoints_2": 28}),
            (1900149, {"EffectBasePoints_1": 32, "EffectBasePoints_2": 32}),
            (1900150, {"EffectBasePoints_1": 36, "EffectBasePoints_2": 36}),
        ],
    },
    {
        "talent_id": 1753,               # Combat Expertise (Prot t8 c3)
        "clone_spell": 31860,            # rank 3
        "new_ranks": [
            (1900136, {"EffectBasePoints_1": 8, "EffectBasePoints_2": 8,
                       "EffectBasePoints_3": 8}),
            (1900137, {"EffectBasePoints_1": 10, "EffectBasePoints_2": 10,
                       "EffectBasePoints_3": 10}),
        ],
    },
]

# BRAND-NEW talents added to an existing tree: a new client Talent.dbc row +
# talent_dbc row + N passive proc-trigger rank spells + one triggered buff +
# one spell_proc row. Everything is data — no core patch.
#
# PLACEMENT IS CONSTRAINED BY mod-playerbots. PlayerbotAIConfig::
# ParseTempTalentsOrder sorts each tab's talents by (Row, Col) and maps the
# Nth CHARACTER of the premade spec-link string to the Nth sorted talent.
# playerbots.conf's paladin Retribution segments are exactly 26 chars for
# exactly 26 Retribution talents, so a new talent MUST SORT LAST or every
# paladin bot's build silently shifts by one. Divine Storm sits at (10,1), so
# only (10,2) and (10,3) are safe.
#
# The TALENT ID must stay near the existing max (2285): sTalentStore
# .GetNumRows() returns highestId+1, and Player::GetSpec loops it three times
# per call — a 1900xxx talent id would allocate a ~15 MB index table and
# inflate those loops ~830x on a 2500-bot realm. Spell ids stay in 1900xxx.
#
# NEVER DELETE a talent row once anyone has learned it: Player::_LoadTalents
# asserts on the talent position and ActivateSpec/resetTalents deref it
# unguarded, so removal means crashes and failed logins. Strip with
# player:ResetTalents() instead.
NEW_TALENTS = [
    {
        "talent_id": 2286,
        "tab": 381,                  # paladin Retribution
        "tier": 10,                  # 0-based DBC row; sorts after Divine Storm (10,1)
        "column": 2,
        "name": "Sudden Light",
        "icon": 2176,                # Spell_Holy_SurgeOfLight
        # Rank spells clone Surge of Light rank 1 (33150): a single effect,
        # aura 42 SPELL_AURA_PROC_TRIGGER_SPELL, Attributes 464 =
        # IS_ABILITY|PASSIVE|DO_NOT_DISPLAY|DO_NOT_LOG. Art of War's own rank
        # spells carry a second damage-bonus effect we do not want.
        "rank_clone": 33150,
        "ranks": [(1900124, 2), (1900125, 4), (1900126, 6), (1900127, 8), (1900128, 10)],
        # $h renders each row's OWN ProcChance, so one string covers all five
        # ranks and reads 2/4/6/8/10% automatically (352 stock spells do this).
        "description": "Your critical strikes have a $h% chance to make your "
                       "next Holy Light instant. Lasts $1900129d.",
        # The instant-cast buff clones Art of War rank 2's buff (59578): the
        # structural twin — aura 108 ADD_PCT_MODIFIER, misc 10
        # SPELLMOD_CASTING_TIME, bp -101 (= -100%), 1 charge, 15s, and two
        # client-only bits we want: AttributesEx6 0x40 (green floating combat
        # text on proc — WITHOUT it the player sees nothing, because the
        # aura-text cvar defaults OFF) and SpellVisualID 11955 (holy chest
        # flash + HolyProtection.wav).
        "buff_id": 1900129,
        "buff_clone": 59578,
        "buff_description": "Your next Holy Light is instant cast.",
        "buff_aura_description": "Your next Holy Light is instant cast.",
        # Any damaging crit, keyed on the CASTING spell's DBC damage class:
        #   0x4     melee auto attacks
        #   0x10    dmg class MELEE — Crusader Strike, Divine Storm, and
        #           Judgement's damage spell 54158 (Judgement is MELEE here,
        #           not NONE as an earlier comment claimed)
        #   0x100   dmg class RANGED — Hammer of Wrath and Avenger's Shield,
        #           including this track's own custom ranks (1900024/79/116,
        #           1900087). Omitting this bit silently excluded a core
        #           Retribution execute while the tooltip promised "your
        #           critical strikes"; paladins have no ranged AUTO attack
        #           (separate bit 0x40), so nothing unwanted comes in.
        #   0x1000  dmg class NONE negatives (near-vestigial for paladins)
        #   0x10000 dmg class MAGIC — Exorcism, Holy Wrath, Consecration,
        #           Holy Shock damage
        # Art of War uses 0x1014 and excludes both ranged and magic.
        "proc_type_mask": 0x11114,
        # Internal cooldown. Art of War has none, but its mask is melee-only:
        # ours includes AoE magic (Consecration ticks, Divine Storm), and procs
        # are evaluated PER TARGET, so without an ICD the effective rate is far
        # above the advertised 10% and the buff would sit up permanently.
        # 6000 matches Surge of Light, the closest per-rank-chance analogue.
        "proc_cooldown": 6000,
    },
]

# New trainable spell ranks (client+server Spell.dbc, spell_ranks, and the
# MODERN trainer tables: this core loads trainer/trainer_spell via
# ObjectMgr::LoadTrainers — the legacy npc_trainer table is DEAD DATA here.
# Trainer ids are resolved at runtime from the clone spell's existing rows.
# The milestone gate rides trainer_spell.ReqAbility1 (unmet = row shown
# grey with "Requires <marker name>"; only class/race-unfit rows are hidden).
# values: overrides on the cloned record (RAW column values). cost in copper.
# Optional per-entry fields (milestones 900/925/950 wave):
#   req    — gate marker for ReqAbility1 (default TRAINER_REQ_MARKER = 175)
#   req2   — ReqAbility2, set to the PREVIOUS rank id: no rank-skipping, and
#            talent-taught chains (Holy Shock/Shield, Avenger's) stay closed
#            to untalented paladins (stock rows gate on prev rank the same way)
#   hidden — True: spell_dbc + spell_ranks only; no trainer row, no
#            SkillLineAbility row (Holy Shock heal/damage sub-spells — the
#            core's spell_pal_holy_shock script resolves them per-rank via
#            GetSpellWithRank once the chain rows exist)
TRAINER_REQ_MARKER = 1900007
# Track reorder 2026-08-18: the Beyond Mastery waves moved 900/925/950 ->
# 525/775/900. The VARIABLE names and spell ids are historical and stay
# (renaming would churn every SPELL_RANKS entry); only the gate spells'
# display names below carry the new milestone levels.
GATE_900, GATE_925, GATE_950 = 1900076, 1900077, 1900078
# Milestone 1175 "Beyond Mastery V" (the FOURTH gated wave — the track
# titles shifted one numeral in the 2026-08-18 reorder)
GATE_1175 = 1900111
# Milestone 1425 "Beyond Mastery VI" (the FIFTH gated wave)
GATE_1425 = 1900146
SPELL_RANKS = [
    { "name": "Holy Light",              "first": 635,   "clone": 48782, "new_id": 1900020, "rank": 14,
      "values": {"EffectBasePoints_1": 5689, "EffectDieSides_1": 648},  "cost": 100000000 },
    { "name": "Flash of Light",          "first": 19750, "clone": 48785, "new_id": 1900021, "rank": 10,
      "values": {"EffectBasePoints_1": 902,  "EffectDieSides_1": 109},  "cost": 40000000 },
    { "name": "Consecration",            "first": 26573, "clone": 48819, "new_id": 1900022, "rank": 9,
      "values": {"EffectBasePoints_1": 146,  "EffectDieSides_1": 1},    "cost": 75000000 },
    { "name": "Shield of Righteousness", "first": 53600, "clone": 61411, "new_id": 1900023, "rank": 3,
      "values": {"EffectBasePoints_1": 691,  "EffectDieSides_1": 1},    "cost": 25000000 },
    { "name": "Hammer of Wrath",         "first": 24275, "clone": 48806, "new_id": 1900024, "rank": 7,
      "values": {"EffectBasePoints_1": 1476, "EffectDieSides_1": 152},  "cost": 50000000 },
    { "name": "Exorcism",                "first": 879,   "clone": 48801, "new_id": 1900025, "rank": 10,
      "values": {"EffectBasePoints_1": 1342, "EffectDieSides_1": 155},  "cost": 60000000 },
    # ---- milestone 900 "Beyond Mastery" (combat spells continue their own
    # stock growth ratio; buffs +15%; costs 3-10k gold) -------------------
    { "name": "Hammer of Wrath",         "first": 24275, "clone": 48806, "new_id": 1900079, "rank": 8,
      "values": {"EffectBasePoints_1": 1838, "EffectDieSides_1": 189},  "cost": 100000000,
      "req": GATE_900, "req2": 1900024 },
    { "name": "Holy Shock",              "first": 20473, "clone": 48825, "new_id": 1900080, "rank": 8,
      "values": {},                                                     "cost": 90000000,
      "req": GATE_900, "req2": 48825 },
    { "name": "Holy Shield",             "first": 20925, "clone": 48952, "new_id": 1900081, "rank": 7,
      "values": {"EffectBasePoints_2": 319},                            "cost": 60000000,
      "req": GATE_900, "req2": 48952 },
    { "name": "Lay on Hands",            "first": 633,   "clone": 48788, "new_id": 1900082, "rank": 6,
      "values": {"EffectBasePoints_2": 2499},                           "cost": 50000000,
      "req": GATE_900, "req2": 48788 },
    { "name": "Blessing of Might",       "first": 19740, "clone": 48932, "new_id": 1900083, "rank": 11,
      "values": {"EffectBasePoints_1": 631, "EffectBasePoints_2": 631}, "cost": 30000000,
      "req": GATE_900, "req2": 48932 },
    # Holy Shock rank 8 sub-spells (hidden: damage + heal halves)
    { "name": "Holy Shock",              "first": 25912, "clone": 48823, "new_id": 1900084, "rank": 8,
      "values": {"EffectBasePoints_1": 1609, "EffectDieSides_1": 133},  "cost": 0, "hidden": True },
    { "name": "Holy Shock",              "first": 25914, "clone": 48821, "new_id": 1900085, "rank": 8,
      "values": {"EffectBasePoints_1": 2791, "EffectDieSides_1": 231},  "cost": 0, "hidden": True },
    # ---- milestone 925 "Beyond Mastery II" (10-17k gold) ----------------
    { "name": "Flash of Light",          "first": 19750, "clone": 48785, "new_id": 1900086, "rank": 11,
      "values": {"EffectBasePoints_1": 1038, "EffectDieSides_1": 125},  "cost": 170000000,
      "req": GATE_925, "req2": 1900021 },
    { "name": "Avenger's Shield",        "first": 31935, "clone": 48827, "new_id": 1900087, "rank": 6,
      "values": {"EffectBasePoints_1": 1324, "EffectDieSides_1": 295},  "cost": 150000000,
      "req": GATE_925, "req2": 48827 },
    { "name": "Holy Wrath",              "first": 2812,  "clone": 48817, "new_id": 1900088, "rank": 6,
      "values": {"EffectBasePoints_1": 1286, "EffectDieSides_1": 227},  "cost": 130000000,
      "req": GATE_925, "req2": 48817 },
    { "name": "Blessing of Wisdom",      "first": 19742, "clone": 48936, "new_id": 1900089, "rank": 10,
      "values": {"EffectBasePoints_1": 105},                            "cost": 110000000,
      "req": GATE_925, "req2": 48936 },
    { "name": "Retribution Aura",        "first": 7294,  "clone": 54043, "new_id": 1900090, "rank": 8,
      "values": {"EffectBasePoints_1": 128},                            "cost": 100000000,
      "req": GATE_925, "req2": 54043 },
    # ---- milestone 950 "Beyond Mastery III" (18-25k gold) ---------------
    { "name": "Holy Light",              "first": 635,   "clone": 48782, "new_id": 1900091, "rank": 15,
      "values": {"EffectBasePoints_1": 6622, "EffectDieSides_1": 754},  "cost": 250000000,
      "req": GATE_950, "req2": 1900020 },
    { "name": "Exorcism",                "first": 879,   "clone": 48801, "new_id": 1900092, "rank": 11,
      "values": {"EffectBasePoints_1": 1754, "EffectDieSides_1": 203},  "cost": 230000000,
      "req": GATE_950, "req2": 1900025 },
    { "name": "Consecration",            "first": 26573, "clone": 48819, "new_id": 1900093, "rank": 10,
      "values": {"EffectBasePoints_1": 190,  "EffectDieSides_1": 1},    "cost": 210000000,
      "req": GATE_950, "req2": 1900022 },
    { "name": "Devotion Aura",           "first": 465,   "clone": 48942, "new_id": 1900094, "rank": 11,
      "values": {"EffectBasePoints_1": 1385},                           "cost": 190000000,
      "req": GATE_950, "req2": 48942 },
    { "name": "Shield of Righteousness", "first": 53600, "clone": 61411, "new_id": 1900095, "rank": 4,
      "values": {"EffectBasePoints_1": 790,  "EffectDieSides_1": 1},    "cost": 180000000,
      "req": GATE_950, "req2": 1900023 },
    # ---- milestone 1075 "Leap of Devotion" ------------------------------
    # Faithful Leap Rank 2: identical to rank 1 (1900030, CUSTOM_SPELLS)
    # except the 10s cooldown — the ranks pattern keeps both tooltips
    # honest with zero conditional logic anywhere. Hidden: taught by the
    # milestone reconcile (LEARNED_SPELL_SPECIALS.LEAP_COOLDOWN), no
    # trainer row; the spell_ranks chain makes the server send
    # SMSG_SUPERCEDED_SPELL on learn, so the client swaps the spellbook
    # entry and action-bar buttons like any trained rank.
    { "name": "Faithful Leap", "first": 1900030, "clone": 6544, "new_id": 1900106, "rank": 2,
      "hidden": True, "cost": 0,
      "description": "Leap through the air and slam down at the target "
                     "location, dealing $1900031s1 Holy damage to all "
                     "enemies in the area.",
      "values": {
          "SchoolMask": 2, "PowerType": 0, "ManaCost": 700, "DurationIndex": 0,
          "Attributes": 0x10000, "AttributesEx": 0, "AttributesEx2": 0,
          "SpellMissileID": 0, "AttributesEx6": 0,
          "Effect_1": 3, "ImplicitTargetA_1": 28, "EffectRadiusIndex_1": 14,
          "EffectMiscValue_1": 0, "EffectMiscValueB_1": 0,
          "EffectMultipleValue_1": 0,
          "Effect_2": 0, "ImplicitTargetA_2": 0, "EffectTriggerSpell_2": 0,
          "Effect_3": 0, "EffectAura_3": 0, "ImplicitTargetA_3": 0,
          "EffectMiscValue_3": 0,
          "RangeIndex": 4,
          # family 10 + bit 0: Benediction cost scaling, same audit as rank 1
          "SpellClassSet": 10, "SpellClassMask_1": 0x00000001,
          "SpellClassMask_2": 0, "SpellClassMask_3": 0,
          "Category": 0, "CategoryRecoveryTime": 0, "RecoveryTime": 10000,
      } },
    # ---- milestone 1175 "Beyond Mastery V" (26-37k gold: band 26/27/29/
    # 31/33k on the established shape, PLUS +2k per prior custom rank in
    # the spell's chain — Holy Shield carries one, Hammer of Wrath two).
    # Clones are always STOCK rows (customs are absent from the pristine
    # client extract); second-extension entries repeat the previous custom
    # rank's overrides with the new scaling. ----------------------------
    { "name": "Greater Blessing of Wisdom", "first": 25894, "clone": 48938, "new_id": 1900112, "rank": 6,
      "values": {"EffectBasePoints_1": 105},                            "cost": 260000000,
      "req": GATE_1175, "req2": 48938 },
    { "name": "Greater Blessing of Might",  "first": 25782, "clone": 48934, "new_id": 1900113, "rank": 6,
      "values": {"EffectBasePoints_1": 631, "EffectBasePoints_2": 631}, "cost": 270000000,
      "req": GATE_1175, "req2": 48934 },
    # Redemption's stock rows have SpellClassSet 0, fixed by a hardcoded id
    # list in SpellInfoCorrections.cpp — the custom rank sets the column
    # directly instead of touching that list
    { "name": "Redemption",                 "first": 7328,  "clone": 48950, "new_id": 1900114, "rank": 8,
      "values": {"EffectBasePoints_1": 2119, "EffectMiscValue_1": 1580,
                 "SpellClassSet": 10},                                  "cost": 290000000,
      "req": GATE_1175, "req2": 48950 },
    { "name": "Holy Shield",                "first": 20925, "clone": 48952, "new_id": 1900115, "rank": 8,
      "values": {"EffectBasePoints_2": 373},                            "cost": 330000000,
      "req": GATE_1175, "req2": 1900081 },
    { "name": "Hammer of Wrath",            "first": 24275, "clone": 48806, "new_id": 1900116, "rank": 9,
      "values": {"EffectBasePoints_1": 2298, "EffectDieSides_1": 236},  "cost": 370000000,
      "req": GATE_1175, "req2": 1900079 },
    # ---- milestone 1425 "Beyond Mastery VI" -----------------------------
    # COST FORMULA, continuing the established shape exactly: each wave's
    # BASE band starts 8k above the previous wave's and steps +1/+2/+2/+2 --
    # 10/11/13/15/17k (BM III), 18/19/21/23/25k (BM IV), 26/27/29/31/33k
    # (BM V) -> 34/35/37/39/41k here. On top of that, +2k per PRIOR custom
    # rank already in that spell's chain (the BM V premium rule).
    #   Avenger's Shield  34 + 2 (1 prior)  = 36k
    #   Holy Wrath        35 + 2 (1 prior)  = 37k
    #   Exorcism          37 + 4 (2 priors) = 41k
    #   Consecration      39 + 4 (2 priors) = 43k
    #   Holy Light        41 + 4 (2 priors) = 45k
    #
    # VALUES ARE RAW COLUMN VALUES here (unlike TALENT_RANKS, which takes
    # DISPLAYED numbers) -- the displayed amount is bp + die. Each is the
    # previous rank scaled by that chain's OWN measured growth ratio, which
    # every one of these holds to within 0.1% across its whole ladder:
    # Holy Light 1.1640, Exorcism 1.3070, Consecration 1.2993, Avenger's
    # Shield 1.2046, Holy Wrath 1.2259.
    #
    # Shield of Righteousness was deliberately NOT picked: its R3->R4 step
    # (1.1431) broke its own 1.333 stock ratio in the milestone-900 wave, so
    # projecting R5 from it would have been a guess either way.
    #
    # Clones are always STOCK rows (customs are absent from the pristine
    # client extract); req2 chains to the PREVIOUS CUSTOM rank so no rank
    # can be skipped.
    { "name": "Avenger's Shield",           "first": 31935, "clone": 48827, "new_id": 1900141, "rank": 7,
      "values": {"EffectBasePoints_1": 1595, "EffectDieSides_1": 355},  "cost": 360000000,
      "req": GATE_1425, "req2": 1900087 },
    { "name": "Holy Wrath",                 "first": 2812,  "clone": 48817, "new_id": 1900142, "rank": 7,
      "values": {"EffectBasePoints_1": 1577, "EffectDieSides_1": 278},  "cost": 370000000,
      "req": GATE_1425, "req2": 1900088 },
    { "name": "Exorcism",                   "first": 879,   "clone": 48801, "new_id": 1900143, "rank": 12,
      "values": {"EffectBasePoints_1": 2293, "EffectDieSides_1": 265},  "cost": 410000000,
      "req": GATE_1425, "req2": 1900092 },
    # Consecration rank 11 ALSO needs a CONSECRATION_BURST.totals entry in
    # paragon_rework_track.lua (milestone 125 detonates the rank's full
    # 8-second damage on cast): 247 + die 1 = 248/tick x 8 = 1984.
    { "name": "Consecration",               "first": 26573, "clone": 48819, "new_id": 1900144, "rank": 11,
      "values": {"EffectBasePoints_1": 247,  "EffectDieSides_1": 1},    "cost": 430000000,
      "req": GATE_1425, "req2": 1900093 },
    { "name": "Holy Light",                 "first": 635,   "clone": 48782, "new_id": 1900145, "rank": 16,
      "values": {"EffectBasePoints_1": 7708, "EffectDieSides_1": 878},  "cost": 450000000,
      "req": GATE_1425, "req2": 1900091 },
]

# Marker spells: learned server-side by the reward track. Referenced by
# trainer_spell.ReqAbility1 (1900007) or checked via Player::HasSpell in core
# patches (1900008, dual aura in Aura::CanStackWith). Hidden passives
# (Attributes 0xC0) — the client entry exists so the trainer window can
# render "Requires <name>"; harmless for markers no client UI ever names.
MARKERS = [
    { "id": 1900007, "name": "Paragon Level 175", "clone": 20266 },
    { "id": 1900008, "name": "Paragon Dual Aura", "clone": 20266 },
    # weapon-masked: the equipped-item columns define which items the marker's
    # extra permanent enchant slot applies to (read via IsFitToSpellRequirements
    # by the SpellEffects.cpp §1e core patch); further markers 1900010+ would
    # each add one more slot
    { "id": 1900009, "name": "Paragon Dual Enchant", "clone": 20266,
      "overrides": { "EquippedItemClass": 2, "EquippedItemSubclass": 0, "EquippedItemInvTypes": 0 } },
    # read via Player::HasSpell by the §1h dual-blessing patch in
    # Aura::CanStackWith (two same-caster blessings per target)
    { "id": 1900056, "name": "Paragon Dual Blessing", "clone": 20266 },
    # Trainer gates for the Beyond Mastery rank waves (milestones
    # 900/925/950) — SPELL_RANKS entries reference them via "req"
    { "id": 1900076, "name": "Paragon Level 525", "clone": 20266 },
    { "id": 1900077, "name": "Paragon Level 775", "clone": 20266 },
    { "id": 1900078, "name": "Paragon Level 900", "clone": 20266 },
    { "id": 1900111, "name": "Paragon Level 1175", "clone": 20266 },
    { "id": 1900146, "name": "Paragon Level 1425", "clone": 20266 },
    # Milestone 725 enchant-slot ladder (§1e capacity markers, taught by
    # paragon_enchant_slots.lua as average item level crosses thresholds).
    # Fit masks: EquippedItemClass 4 = armor with an inventory-type bit mask
    # (chest covers 5 chest + 20 robe); weapon tier reuses reserved 1900033
    # (class 2, any weapon — mirror of 1900009). Core array §1e lists all.
    { "id": 1900033, "name": "Paragon Weapon Enchant III", "clone": 20266,
      "overrides": { "EquippedItemClass": 2, "EquippedItemSubclass": 0, "EquippedItemInvTypes": 0 } },
    { "id": 1900057, "name": "Paragon Chest Enchant", "clone": 20266,
      "overrides": { "EquippedItemClass": 4, "EquippedItemSubclass": 0, "EquippedItemInvTypes": (1 << 5) | (1 << 20) } },
    { "id": 1900058, "name": "Paragon Legs Enchant", "clone": 20266,
      "overrides": { "EquippedItemClass": 4, "EquippedItemSubclass": 0, "EquippedItemInvTypes": 1 << 7 } },
    { "id": 1900059, "name": "Paragon Hands Enchant", "clone": 20266,
      "overrides": { "EquippedItemClass": 4, "EquippedItemSubclass": 0, "EquippedItemInvTypes": 1 << 10 } },
    { "id": 1900060, "name": "Paragon Feet Enchant", "clone": 20266,
      "overrides": { "EquippedItemClass": 4, "EquippedItemSubclass": 0, "EquippedItemInvTypes": 1 << 8 } },
    { "id": 1900061, "name": "Paragon Wrist Enchant", "clone": 20266,
      "overrides": { "EquippedItemClass": 4, "EquippedItemSubclass": 0, "EquippedItemInvTypes": 1 << 9 } },
    { "id": 1900062, "name": "Paragon Back Enchant", "clone": 20266,
      "overrides": { "EquippedItemClass": 4, "EquippedItemSubclass": 0, "EquippedItemInvTypes": 1 << 16 } },
    { "id": 1900063, "name": "Paragon Head Enchant", "clone": 20266,
      "overrides": { "EquippedItemClass": 4, "EquippedItemSubclass": 0, "EquippedItemInvTypes": 1 << 1 } },
    { "id": 1900064, "name": "Paragon Shoulder Enchant", "clone": 20266,
      "overrides": { "EquippedItemClass": 4, "EquippedItemSubclass": 0, "EquippedItemInvTypes": 1 << 3 } },
    { "id": 1900065, "name": "Paragon Ring Enchant", "clone": 20266,
      "overrides": { "EquippedItemClass": 4, "EquippedItemSubclass": 0, "EquippedItemInvTypes": 1 << 11 } },
    { "id": 1900066, "name": "Paragon Shield Enchant", "clone": 20266,
      "overrides": { "EquippedItemClass": 4, "EquippedItemSubclass": 0, "EquippedItemInvTypes": 1 << 14 } },
]

# Server-only spells: spell_dbc rows the CLIENT never sees (the invisible
# aura family of 1900003-05). Emitted to SQL only — never staged into the
# MPQs, so adding or editing one is a worldserver restart, no client touch.
# Baseline mirrors MARKERS (hidden passive dummy aura, self-target,
# infinite); per-entry overrides tune it. bp convention: value = bp + die.
SERVER_SPELLS = [
    # Milestone 425: slow attenuation. EFFECT_0's basepoints ARE the
    # "incoming slows weakened by N%" knob read by the Unit::UpdateSpeed
    # core patch (Paragon Core Patches.md §1f): bp 24 + die 1 = 25.
    # Retune or tier via this row + restart, no rebuild.
    { "id": 1900037, "name": "Paragon Slow Attenuation", "clone": 20266,
      "overrides": { "EffectBasePoints_1": 24 } },
    # Milestone 500: stat-scaling level. EFFECT_0's basepoints ARE the
    # "attributes scale as if N levels lower" knob read by
    # Player::GetStatScalingLevel (Paragon Core Patches.md §1g): bp 1 +
    # die 1 = 2. Affects every gt-table conversion (all combat ratings
    # uniformly x1.1577 at 80->78, agi/int crit ~x1.16, spirit mana regen
    # x1.109 — multiplicative with milestone 375). Retune via this row +
    # restart, no rebuild; keep the addon's Paragon_ScalingLevel.lua
    # RATING_FACTOR in sync with the bp.
    { "id": 1900039, "name": "Paragon Stat Scaling", "clone": 20266,
      "overrides": { "EffectBasePoints_1": 1 } },
    # Milestone 750: swifter mounting. Pure HasAura marker (bp unused) —
    # the same Unit::ModSpellCastTime patch as 1900005 (§1) takes another
    # 500ms off mount casts, floored at 0: with milestone 100's -1000ms the
    # standard 1500ms mounts become instant.
    { "id": 1900071, "name": "Paragon Swift Mount", "clone": 20266 },
    # Milestone 800: second stat-scaling marker, clone of 1900039 (bp 1 +
    # die 1 = 2 levels). Player::GetStatScalingLevel (§1g) SUMS both
    # markers' amounts -> -4 total at 800. Keep the addon factor tables
    # (Paragon_ScalingLevel.lua / Paragon_SpiritTooltip.lua) keyed for the
    # summed reduction: -4 = rating x1.340208, agi-crit +0.0066/pt,
    # int-crit +0.0021/pt, spirit-mana x1.2287 (spirit-hp stays flat).
    { "id": 1900072, "name": "Paragon Stat Scaling II", "clone": 20266,
      "overrides": { "EffectBasePoints_1": 1 } },
    # Milestone 825: ghost sprint. bp (59 + die 1 = 60) IS the "+N% run
    # speed while dead" knob read by the §1i Unit::UpdateSpeed patch (ADDS
    # to ghost form's own +50%); the same marker's presence waives
    # spirit-healer resurrection sickness in Player::ResurrectPlayer.
    # Passive => survives death (Unit.cpp RemoveAllAurasOnDeath spares
    # passives), which is what makes both reads work while dead.
    { "id": 1900073, "name": "Paragon Ghost Sprint", "clone": 20266,
      "overrides": { "EffectBasePoints_1": 59 } },
    # Milestone 850: durability guard. bp (74 + die 1 = 75) IS the
    # "durability loss reduced by N%" knob read by the §1j patch in
    # Player::DurabilityPointsLoss — the single funnel for ALL loss
    # (combat point ticks, death 10%, spirit-healer 25%), scaled with
    # probabilistic rounding.
    { "id": 1900074, "name": "Paragon Durability Guard", "clone": 20266,
      "overrides": { "EffectBasePoints_1": 74 } },
    # Milestone 875: soft landing. bp (49 + die 1 = 50) IS the "fall damage
    # reduced by N%" knob read by the §1k patch in Player::HandleFall
    # (applied after Safe Fall yards and the Divine Protection halving,
    # before the environmental-damage call).
    { "id": 1900075, "name": "Paragon Soft Landing", "clone": 20266,
      "overrides": { "EffectBasePoints_1": 49 } },
    # Milestone 1100: double belt buckle marker — read via Player:HasSpell
    # by paragon_double_buckle.lua (gates attaching a second Eternal Belt
    # Buckle and socketing its gem). Plain hidden passive, no knobs.
    { "id": 1900107, "name": "Paragon Double Buckle", "clone": 20266 },
    # Paragon codex "Timeless Body" ranks I-III (bp 0 + die 1 = 1 effective
    # level each): the §1g patch in Player::GetStatScalingLevel SUMS these
    # with the milestone markers 1900039/1900072, so three ranks push the
    # total reduction to -7. Applied as auras per owned codex rank by
    # paragon_codex.lua. Deployed 2026-08-18 via surgical SQL (clone of
    # 1900039); listed here so full regenerations reproduce them.
    { "id": 1900096, "name": "Timeless Body I", "clone": 20266,
      "overrides": { "EffectBasePoints_1": 0 } },
    { "id": 1900097, "name": "Timeless Body II", "clone": 20266,
      "overrides": { "EffectBasePoints_1": 0 } },
    { "id": 1900098, "name": "Timeless Body III", "clone": 20266,
      "overrides": { "EffectBasePoints_1": 0 } },
    # Paragon codex "Petting Zoo" mp5 carrier: MOD_POWER_REGEN (85) on mana,
    # NON-passive (0x80 = DO_NOT_DISPLAY only, so CastCustomSpell can cast
    # it) but death-persistent via AttributesEx3 ALLOW_AURA_WHILE_DEAD and
    # arena-safe via AttributesEx4 ALLOW_ENETRING_ARENA (RemoveArenaAuras
    # strips positive non-passive auras otherwise); die 0 so the custom bp
    # passes through exactly. Amount = 5 x mount count, cast by
    # paragon_codex.lua ReconcileRegen. Deployed 2026-08-19 via surgical
    # SQL (clone of 1900096); listed for regenerations.
    { "id": 1900099, "name": "Petting Zoo", "clone": 20266,
      "overrides": { "Attributes": 0x80, "AttributesEx3": 0x00100000,
                     "AttributesEx4": 0x00200000,
                     "EffectAura_1": 85, "EffectMiscValue_1": 0,
                     "EffectBasePoints_1": 0, "EffectDieSides_1": 0 } },
    # Milestone 1150: gem-doubling mp5 carrier — same profile as 1900099
    # (each reconcile-owning module needs its OWN aura id; sharing one
    # would clobber the other's summed amount), bp via CastCustomSpell in
    # paragon_gem_double.lua. Deployed via surgical SQL clone of 1900099.
    { "id": 1900110, "name": "Paragon Gem Doubling Regen", "clone": 20266,
      "overrides": { "Attributes": 0x80, "AttributesEx3": 0x00100000,
                     "AttributesEx4": 0x00200000,
                     "EffectAura_1": 85, "EffectMiscValue_1": 0,
                     "EffectBasePoints_1": 0, "EffectDieSides_1": 0 } },
    # Milestone 1200: solo-dungeon marker (mirrors milestone ownership via
    # LEARNED_SPELL_SPECIALS) — plain hidden passive, no knobs. Deployed
    # via surgical SQL clone of 1900107.
    { "id": 1900117, "name": "Paragon Solo Conqueror", "clone": 20266 },
    # Milestone 1200: crit-damage carrier — aura 163
    # (SPELL_AURA_MOD_CRIT_DAMAGE_BONUS, misc 127 = all schools: melee
    # whites, melee/ranged specials + Auto Shot, all spell crits; healing
    # crits untouched, mirroring the stock Chaotic meta). bp = whole
    # percent, passed through exactly (die 0) by CastCustomSpell in
    # paragon_solo_dungeon.lua. Deployed via surgical SQL clone of 1900110.
    { "id": 1900118, "name": "Paragon Solo Conqueror Crit", "clone": 20266,
      "overrides": { "Attributes": 0x80, "AttributesEx3": 0x00100000,
                     "AttributesEx4": 0x00200000,
                     "EffectAura_1": 163, "EffectMiscValue_1": 127,
                     "EffectBasePoints_1": 0, "EffectDieSides_1": 0 } },
    # Milestone 1275: total durability immunity — the last 25% on top of
    # milestone 875's 75%. NOT another percentage marker: the §1j patch
    # reads only 1900074's amount and does not sum markers, and "100%
    # reduced" is exactly the STOCK aura SPELL_AURA_PREVENT_DURABILITY_LOSS
    # (289), which Player::DurabilityPointsLoss honours on its very first
    # line via HasPreventDurabilityLossAura() — before the §1j scaling runs.
    # So this needs no core change and is exact (no probabilistic rounding
    # left to leak the occasional point). Handler is HandleNoImmediateEffect,
    # i.e. the aura does nothing except be present. Same hidden-passive
    # profile as 1900074, so it is equally present at death and at the
    # spirit healer (the 25%-durability resurrect hit is prevented too).
    { "id": 1900123, "name": "Paragon Durability Immunity", "clone": 20266,
      "overrides": { "EffectAura_1": 289, "EffectMiscValue_1": 0,
                     "EffectBasePoints_1": 0, "EffectDieSides_1": 0 } },
    # Milestone 1350: THIRD stat-scaling marker. bp 0 + die 1 = 1, i.e. one
    # further effective level, and the 1g patch in Player::GetStatScalingLevel
    # SUMS it with 1900039 (2) and 1900072 (2) for -5 total from milestones
    # alone (plus up to 3 more from the codex Timeless Body ranks).
    # NOTE: the marker id must ALSO be added to the hardcoded list in
    # Player::GetStatScalingLevel - that loop names its markers explicitly, so
    # a spell row on its own does nothing. Stats are refreshed on grant by
    # paragon_scaling_level.lua, and the client tooltip factor tables in
    # Paragon_ScalingLevel.lua / Paragon_SpiritTooltip.lua must gain their
    # [5] entries or the tooltips silently keep quoting the -4 numbers.
    { "id": 1900131, "name": "Paragon Stat Scaling III", "clone": 20266,
      "overrides": { "EffectBasePoints_1": 0 } },
    # Codex node 60 "Ward of Ages": DYNAMIC-amount damage reduction carrier.
    # SPELL_AURA_MOD_DAMAGE_PERCENT_TAKEN (87) with EffectMiscValue = 127
    # (all seven schools). The core reads this aura in exactly two places --
    # Unit::SpellDamageBonusTaken (spell hits AND periodic ticks) and
    # Unit::MeleeDamageBonusTaken -- so it covers all incoming combat damage
    # (environmental damage bypasses both).
    #
    # Same profile as 1900099/1900110: NON-passive (Attributes 0x80 =
    # DO_NOT_DISPLAY only) so CastCustomSpell can cast it, death-persistent
    # via AttributesEx3 ALLOW_AURA_WHILE_DEAD, arena-safe via AttributesEx4.
    # !! EffectDieSides_1 MUST stay 0 !! The aura amount is
    # basePoints + dieSides, so a die of 1 would make every rank one percent
    # too strong -- and unlike a talent this CANNOT be read back from Lua
    # (aura amounts are not exposed), so the DBC row is the only place the
    # value can be verified. Amount is set per rank by paragon_codex.lua
    # ReconcileMitigation as CastCustomSpell(-rank).
    #
    # The negative amount makes SpellInfo classify the effect as a DEBUFF
    # (there is no aura-87 special case in _isPositiveEffect). Harmless here:
    # clone base 20266 has DispelType 0, so it cannot be dispelled, and
    # RemoveArenaAuras only strips POSITIVE non-passive auras.
    { "id": 1900138, "name": "Ward of Ages", "clone": 20266,
      "overrides": { "Attributes": 0x80, "AttributesEx3": 0x00100000,
                     "AttributesEx4": 0x00200000,
                     "EffectAura_1": 87, "EffectMiscValue_1": 127,
                     "EffectBasePoints_1": 0, "EffectDieSides_1": 0 } },
    # Milestone 1450 "Bulwark of Ages": FIXED -5% damage taken. Same aura and
    # school mask as 1900138, but granted through the track's SPECIAL_AURAS
    # (player:AddAura), which cannot pass custom basepoints -- so the value is
    # baked into the row: bp -5 + die 0 = -5. Keeps 20266's default
    # Attributes (0x1D0 = PASSIVE | DO_NOT_DISPLAY | ...), matching every
    # other SPECIAL_AURAS marker; passive auras survive death on their own.
    #
    # Deliberately a SEPARATE aura from 1900138 rather than folded into its
    # amount: two aura-87 effects stack MULTIPLICATIVELY
    # (Unit::GetTotalAuraMultiplier AddPct's each one), so 0.95 x 0.90 =
    # 0.855 -> 14.5% combined at codex rank 10, not 15%. That is the accepted
    # behaviour (user: "i dont care if its additive or multiplicative"), and
    # keeping them separate means neither module has to know the other exists.
    { "id": 1900139, "name": "Bulwark of Ages", "clone": 20266,
      "overrides": { "EffectAura_1": 87, "EffectMiscValue_1": 127,
                     "EffectBasePoints_1": -5, "EffectDieSides_1": 0 } },
    # Codex node 61 "Fleet of Ages": DYNAMIC-amount movement speed, +1% per
    # rank, cap 25. THREE effects, one per movement mode, because
    # Unit::UpdateSpeed reads a DIFFERENT aura for each and they do not
    # cross over:
    #   129 MOD_SPEED_ALWAYS                -> running, UNMOUNTED
    #   130 MOD_MOUNTED_SPEED_ALWAYS        -> running, mounted
    #   209 MOD_MOUNTED_FLIGHT_SPEED_ALWAYS -> flying, mounted
    #
    # !! THE *_ALWAYS FAMILY IS THE WHOLE POINT !! UpdateSpeed reads the
    # plain MOD_INCREASE_SPEED (31) family through GetMaxPositiveAuraModifier
    # -- only the single HIGHEST applies, so a 1% aura there would be
    # permanently masked by Sprint/Aspect of the Cheetah/milestone 50 and
    # would do NOTHING. The *_ALWAYS auras go through
    # GetTotalAuraMultiplier instead, and the final line is
    #   speed = max(non_stack_bonus, stack_bonus); AddPct(speed, main_speed_mod)
    # so this multiplies ON TOP of whatever max-picked sprint is running.
    #
    # Swim is deliberately absent: MOVE_SWIM has no *_ALWAYS variant at all
    # (it max-picks MOD_INCREASE_SWIM_SPEED), so a 1% swim effect could never
    # beat milestone 150's +100% and would be dead weight.
    #
    # Effects 2 and 3 need Effect_N/ImplicitTargetA_N set explicitly: the
    # SERVER_SPELLS default override blanks them (Effect_2/3 = 0) and clone
    # base 20266 only carries one effect. ImplicitTargetA = 1 = caster.
    # Die sides 0 on all three so CastCustomSpell's bp passes through exactly
    # (paragon_codex.lua ReconcileMovement casts bp0=bp1=bp2=rank).
    { "id": 1900140, "name": "Fleet of Ages", "clone": 20266,
      "overrides": { "Attributes": 0x80, "AttributesEx3": 0x00100000,
                     "AttributesEx4": 0x00200000,
                     "Effect_1": 6, "EffectAura_1": 129,
                     "EffectBasePoints_1": 0, "EffectDieSides_1": 0,
                     "ImplicitTargetA_1": 1,
                     "Effect_2": 6, "EffectAura_2": 130,
                     "EffectBasePoints_2": 0, "EffectDieSides_2": 0,
                     "ImplicitTargetA_2": 1,
                     "Effect_3": 6, "EffectAura_3": 209,
                     "EffectBasePoints_3": 0, "EffectDieSides_3": 0,
                     "ImplicitTargetA_3": 1 } },
    # Codex node 59: flight over the vanilla continents — read via
    # Player::HasSpell by the §1r patch in SpellInfo::CheckLocation, which
    # waives the AREA_FLAG_OUTLAND term of AreaTableEntry::IsFlyable() on
    # maps 0/1 for holders only. Plain hidden passive, no knobs; the marker
    # is LEARNED (not an aura) so it is present before auras reload at
    # login. Deployed via surgical SQL clone of 1900107. SERVER-ONLY: never
    # staged into the MPQs, exactly like 1900107/1900117.
    { "id": 1900130, "name": "Paragon Skies of Azeroth", "clone": 20266 },
]

# Full custom PLAYER abilities: cloned base rows with arbitrary integer
# overrides plus name/subtext/description. Emitted to BOTH spell_dbc and the
# client MPQs (cast UI, tooltips and dynamic-object ground visuals all need
# the client entry). Optional "bonus" adds a spell_bonus_data row (SP/AP
# coefficients).
# ---------------------------------------------------------------------------
# THE NINE NON-PALADIN CLASSES (2026-08-20)
# ---------------------------------------------------------------------------
# 135 talent extensions and 272 trainer ranks, generated rather than written:
#   Tools/gen_class_talents.py   -> generated/class_talent_ranks.py
#   Tools/gen_class_trainers.py  -> generated/class_trainer_ranks.py
# Both refuse to emit anything they cannot justify from the DBCs (a rank whose
# payload is a triggered spell, a ladder that is really a 1/n curve, a
# "percentage" already sitting at its ceiling), so this append is data only.
#
# One clean 100-id block per class from 1901000, because custom spell id RANGES
# are landmines: the enchant markers once overlapped Divine Strength's extended
# ranks and gave paladins a silent extra enchant slot. 1900152-1900999 stays
# free for future universal work.
_GEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated")
if _GEN not in sys.path:
    sys.path.insert(0, _GEN)
_CLASS_DATA_LOADED = False


def load_generated_class_data():
    """Load generator output only when building the complete client patch.

    The class generators import this module for ``extract_dbc`` and ``mysql``.
    Requiring their own output during that import creates a bootstrap cycle on
    a fresh clone, so the completeness guard belongs on the patch entry point.
    """
    global _CLASS_DATA_LOADED
    if _CLASS_DATA_LOADED:
        return

    try:
        from class_talent_ranks import CLASS_TALENT_RANKS
        from class_trainer_ranks import CLASS_SPELL_RANKS
    except ImportError as exc:  # regenerate; never silently ship a short list
        sys.exit("missing generated class data (%s) -- rerun "
                 "gen_class_talents.py --emit and "
                 "gen_class_trainers.py --emit" % exc)

    TALENT_RANKS.extend(CLASS_TALENT_RANKS)
    SPELL_RANKS.extend(CLASS_SPELL_RANKS)
    _CLASS_DATA_LOADED = True


CUSTOM_SPELLS = [
    # Milestone 350 (Paladin): Faithful Leap. TWO-SPELL ARCHITECTURE — the
    # client half is a plain AoE-click dummy, the jump is server-side:
    # packet-probe-verified that the client casts the Heroic Leap prototype
    # (6544) in its TRAJECTORY mode (castFlags 0x2, src+dest 0x60, dest =
    # facing x range — the reticle is cosmetic) no matter which implicit
    # targets/range the row carries. So 1900030 is stripped to the exact
    # profile of proven click spells (Blizzard: Attributes 0x10000 only,
    # dummy effect, target 28, 0-40yd) and only DELIVERS the clicked point;
    # paragon_faithful_leap.lua relays it via CastSpellAoF into 1900032,
    # which does the actual jump + impact trigger entirely server-side.
    { "id": 1900030, "name": "Faithful Leap", "clone": 6544,
      # $1900031s1: the client substitutes the impact spell's effect-1 value
      # (bp 799 + die 1 = 800) from its own DBC — live number, default-skill
      # style. The referenced spell must be in the client MPQ (it is).
      # Nerf 2026-08-19 (both ranks): 700 mana, range 40 -> 30 yd
      # (RangeIndex 4 = stock Medium Range).
      "description": "Leap through the air and slam down at the target "
                     "location, dealing $1900031s1 Holy damage to all "
                     "enemies in the area.",
      "overrides": {
          "SchoolMask": 2, "PowerType": 0, "ManaCost": 700, "DurationIndex": 0,
          "Attributes": 0x10000, "AttributesEx": 0, "AttributesEx2": 0,
          # THE trajectory trigger: the prototype defines a MISSILE
          # (SpellMissile.dbc 621) — spells with a missile id are cast by
          # the client in its trajectory mode (castFlags 0x2, src+dest,
          # computed aim-forward dest; the vehicle-catapult mechanic) no
          # matter what the effects/targets/range say. Full-column diff vs
          # Blizzard/Flare found it; zeroing it restores normal click casts.
          "SpellMissileID": 0, "AttributesEx6": 0,
          "Effect_1": 3, "ImplicitTargetA_1": 28, "EffectRadiusIndex_1": 14,
          "EffectMiscValue_1": 0, "EffectMiscValueB_1": 0,
          "EffectMultipleValue_1": 0,
          "Effect_2": 0, "ImplicitTargetA_2": 0, "EffectTriggerSpell_2": 0,
          "Effect_3": 0, "EffectAura_3": 0, "ImplicitTargetA_3": 0,
          "EffectMiscValue_3": 0,
          "RangeIndex": 4,
          # family 10 + bit 0: puts the leap inside Benediction's
          # SPELLMOD_COST mask (0x1BF97FB7) so its %-cost reduction applies
          # — bit 0 audited unique: no other paladin modifier, DB proc
          # mask, or hardcoded core branch touches it (2026-08-19)
          "SpellClassSet": 10, "SpellClassMask_1": 0x00000001,
          "SpellClassMask_2": 0, "SpellClassMask_3": 0,
          # 15000 = the honest rank-1 value; milestone 1075 teaches the
          # 10s Rank 2 (1900106, SPELL_RANKS) instead of bending this row
          "Category": 0, "CategoryRecoveryTime": 0, "RecoveryTime": 15000,
      } },
    # The server-side leap: keeps the prototype's jump effect and arc
    # parameters (min/max height 1.0/7.5, 4x run speed), target 87 = use the
    # dest supplied by CastSpellAoF; eff2 triggers the impact at that dest.
    # Never learned or cast by the client.
    { "id": 1900032, "name": "Faithful Leap Jump", "clone": 6544,
      "overrides": {
          "SchoolMask": 2, "PowerType": 0, "ManaCost": 0, "DurationIndex": 0,
          "ImplicitTargetA_1": 87,
          "RangeIndex": 13,
          "Category": 0, "CategoryRecoveryTime": 0, "RecoveryTime": 0,
          "EffectTriggerSpell_2": 1900031,
          "Effect_3": 0, "EffectAura_3": 0, "ImplicitTargetA_3": 0,
          "EffectMiscValue_3": 0,
      } },
    # The impact: clone of Consecration R10 (48819 — the ground visual 5600
    # and 8 yd radius are what we're after). Eff1 becomes the ONE-TIME Holy
    # burst on enemies around the destination (target 16, the same pattern
    # the prototype's own landing spell 52174 uses — dest inherited from the
    # trigger). Eff2 keeps a persistent-area DYNOBJ purely as the visual
    # carrier (dummy aura, no ticks, 1.5s duration index 65 — "the
    # consecrate animation upon impact, staying for like a second"),
    # re-targeted from dest-caster (18: would anchor at the TAKEOFF point,
    # the trigger fires while the paladin is mid-flight) to the dynobj-dest
    # pattern Blizzard/Flare use (28).
    { "id": 1900031, "name": "Faithful Leap Impact", "clone": 48819,
      "overrides": {
          "Effect_1": 2, "EffectAura_1": 0, "EffectAuraPeriod_1": 0,
          "ImplicitTargetA_1": 16, "ImplicitTargetB_1": 0,
          "EffectBasePoints_1": 799, "EffectDieSides_1": 1, "EffectRadiusIndex_1": 14,
          "Effect_2": 27, "EffectAura_2": 4, "EffectAuraPeriod_2": 0,
          "ImplicitTargetA_2": 28, "ImplicitTargetB_2": 0,
          "EffectBasePoints_2": 0, "EffectDieSides_2": 0, "EffectRadiusIndex_2": 14,
          "DurationIndex": 65,
          "RangeIndex": 13, "ManaCost": 0, "ManaCostPct": 0,
          "SpellClassSet": 0, "SpellClassMask_1": 0, "SpellClassMask_2": 0,
          "SpellClassMask_3": 0,
      },
      "bonus": { "direct": 0.15, "dot": 0, "ap": 0.15, "ap_dot": 0,
                 "comment": "Paragon - Faithful Leap Impact (one-time holy burst)" } },
    # Milestone 375 (universal): Empowered Spirit — 3x regen from the same
    # Spirit. NOT a formula edit: the core multiplies exactly the
    # spirit-derived regen terms (and nothing else — MP5/food/drinks are
    # added after) by these two percent auras: aura 110 misc 0 in
    # Player::UpdateManaRegen (StatSystem.cpp), aura 88 in
    # Player::RegenerateHealth (Player.cpp). +200% = x3, per player, gated
    # by the track teaching this visible spellbook passive. The client-side
    # spirit tooltip correction lives in Paragon_SpiritTooltip.lua.
    # Milestone 450 (Paladin): Avenger's Shield +2 chain targets (3 -> 5).
    # EXACT inverse of Blizzard's Glyph of Avenger's Shield (54930: aura 107
    # ADD_FLAT_MODIFIER, misc 17 SPELLMOD_JUMP_TARGETS, bp -3 + die = -2,
    # same effect masks) — the server applies the mod in
    # Spell::SelectImplicitChainTargets for BOTH chaining effects (damage +
    # daze), the client applies it to the "$x1 total targets" tooltip line.
    # Flat mods sum: glyph (-2) + milestone (+2) = stock 3. NOTE:
    # SpellClassSet stays 10 (paladin) — a spellmod only affects spells of
    # ITS OWN family, so the usual zero-the-family rule for customs is
    # deliberately inverted; the spell's own SpellClassMask stays 0 so no
    # other modifier matches the passive itself.
    # Milestone 525 (Paladin): Living Symbol — Greater Blessings need no
    # Symbol of Kings. Aura 256 (SPELL_AURA_NO_REAGENT_USE): the handler
    # unions this effect's SpellClassMask into the client-visible
    # PLAYER_NO_REAGENT_COST fields, and Player::CanNoReagentCast matches
    # the cast spell's family flags against them (pure mask overlap, no
    # family-name check — but only paladins learn this). Mask 0x11010002 =
    # union of all 12 Greater Blessing ranks (GBoM 0x2, GBoW 0x10000,
    # GBoK 0x1000000, GBoS 0x10000000), every one verified to carry
    # Symbol of Kings (21177). Client also greys the reagent line from
    # the same fields — no addon work.
    { "id": 1900040, "name": "Living Symbol", "clone": 20266,
      "description": "Your faith is symbol enough - your Greater Blessings "
                     "no longer require Symbol of Kings.",
      "overrides": {
          "Attributes": 0x40,          # passive, visible in the spellbook
          "SpellIconID": 1800,         # Greater Blessing of Kings
          "SpellClassSet": 10,
          "SpellClassMask_1": 0, "SpellClassMask_2": 0, "SpellClassMask_3": 0,
          "Effect_1": 6, "EffectAura_1": 256, "EffectMiscValue_1": 0,
          "EffectBasePoints_1": 0, "EffectDieSides_1": 1,
          "ImplicitTargetA_1": 1, "EffectAuraPeriod_1": 0,
          "EffectSpellClassMaskA_1": 0x11010002, "EffectSpellClassMaskB_1": 0,
          "EffectSpellClassMaskC_1": 0,
          "Effect_2": 0, "EffectAura_2": 0, "Effect_3": 0, "EffectAura_3": 0,
          "DurationIndex": 21, "RangeIndex": 1, "CastingTimeIndex": 1,
          "ManaCost": 0, "ManaCostPct": 0, "ProcChance": 101,
          "EquippedItemClass": -1,
      } },
    { "id": 1900038, "name": "Avenger's Reach", "clone": 20266,
      "description": "Your Avenger's Shield affects $s1 additional targets.",
      "overrides": {
          "Attributes": 0x40,          # passive, visible in the spellbook
          "SpellIconID": 2172,         # Avenger's Shield
          "SpellClassSet": 10,
          "SpellClassMask_1": 0, "SpellClassMask_2": 0, "SpellClassMask_3": 0,
          "Effect_1": 6, "EffectAura_1": 107, "EffectMiscValue_1": 17,
          "EffectBasePoints_1": 1, "EffectDieSides_1": 1,
          "ImplicitTargetA_1": 1, "EffectAuraPeriod_1": 0,
          "EffectSpellClassMaskA_1": 16384, "EffectSpellClassMaskB_1": 16384,
          "EffectSpellClassMaskC_1": 0,
          "Effect_2": 0, "EffectAura_2": 0, "Effect_3": 0, "EffectAura_3": 0,
          "DurationIndex": 21, "RangeIndex": 1, "CastingTimeIndex": 1,
          "ManaCost": 0, "ManaCostPct": 0, "ProcChance": 101,
          "EquippedItemClass": -1,
      } },
    { "id": 1900036, "name": "Empowered Spirit", "clone": 20266,
      "description": "Your Spirit provides $s1% additional health and mana "
                     "regeneration.",
      "overrides": {
          "Attributes": 0x40,          # passive, visible in the spellbook
          "SpellIconID": 1879,         # Divine Spirit
          "Effect_1": 6, "EffectAura_1": 110, "EffectMiscValue_1": 0,
          "EffectBasePoints_1": 199, "EffectDieSides_1": 1,
          "ImplicitTargetA_1": 1, "EffectAuraPeriod_1": 0,
          "Effect_2": 6, "EffectAura_2": 88, "EffectMiscValue_2": 0,
          "EffectBasePoints_2": 199, "EffectDieSides_2": 1,
          "ImplicitTargetA_2": 1, "EffectAuraPeriod_2": 0,
          "Effect_3": 0, "EffectAura_3": 0, "ImplicitTargetA_3": 0,
          "DurationIndex": 21, "RangeIndex": 1, "CastingTimeIndex": 1,
          "ManaCost": 0, "ManaCostPct": 0, "ProcChance": 101,
          "EquippedItemClass": -1,
          "SpellClassSet": 0, "SpellClassMask_1": 0, "SpellClassMask_2": 0,
          "SpellClassMask_3": 0,
      } },
    # Milestone 1050: Provocation — activatable aggro magnet, TWO-PART:
    # (a) the §1m core patch (Creature::GetAttackDistance) checks this
    # marker and stops creatures shrinking their aggro radius for a level
    # advantage (the flat -1 yd/level term would otherwise swallow any
    # detect bonus against deeply gray mobs — a level-10 mob sits at
    # 20-70 yd before the floor); (b) aura 152 (MOD_DETECTED_RANGE) adds
    # +5 yd on top -> a uniform ~25 yd radius from everything at or below
    # your level, i.e. "as if you were ~5 levels below them". The stock
    # creatureLevel+5 <= MAX_PLAYER_LEVEL gate limits (b) to creatures
    # <= 75; 76+ already use their near-full radius. Infinite cancelable
    # self-buff (clone 588 Inner Fire: instant self APPLY_AURA); taught
    # by LEARNED_SPELL_SPECIALS.PROVOCATION at milestone 1050.
    { "id": 1900104, "name": "Provocation", "clone": 588,
      "description": "Your presence provokes hostility: enemies treat you "
                     "as if you were below their level, applying their "
                     "full aggro radius against you, widened by $s1 yards. "
                     "Lasts until cancelled.",
      "overrides": {
          "EffectAura_1": 152, "EffectMiscValue_1": 0,
          "EffectBasePoints_1": 4, "EffectDieSides_1": 1,
          "DurationIndex": 21, "ManaCost": 0, "ManaCostPct": 0,
          "ProcCharges": 0, "ProcTypeMask": 0,
          # INV_Misc_MonsterHorn_04 — used by ZERO other spells (verified
          # against Spell.dbc icon usage), so the spellbook entry is unique
          "SpellIconID": 3394,
      } },
]

# ============================================================================

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DATA = os.path.join(HERE, "..", "Client", "Data")
CACHE = os.path.join(HERE, "cache")
OUT_SQL = os.path.join(HERE, "generated", "paragon_content.sql")
STAGE_LOCALE = os.path.join(HERE, "stage-locale", "DBFilesClient")
STAGE_GENERAL = os.path.join(HERE, "stage-general", "DBFilesClient")
# !! THE PATCH LETTER IS THE LOAD ORDER, AND EVERY LETTER BEATS EVERY DIGIT !!
# Wow.exe builds its patch list from four search patterns (table at .text
# 0x405AB0): Data\patch-?.MPQ, Data\<loc>\patch-<loc>-?.MPQ, Data\patch.MPQ,
# Data\<loc>\patch-<loc>.MPQ. That '?' is a raw Win32 FindFirstFile wildcard --
# EXACTLY ONE CHARACTER -- so "patch-10.MPQ" is never found at all. The matches
# are sorted DESCENDING (qsort at 0x405D2F, comparator 0x401200 = -SStrCmpI)
# and then mounted BACKWARDS (loop at 0x405E90) with SFileOpenArchive's
# priority argument counting UP, so the alphabetically HIGHEST name is mounted
# LAST and wins a filename collision. SStrCmpI (0x41B9E0) folds 'A'-'Z' by
# +0x20, so the order is case-insensitive and digits (0x30-0x39) rank BELOW
# letters (0x61-0x7A).
#
# Hence letters. The digits we used to sit on lose to any third-party
# patch-A..Z, and every HD model pack in the wild ships as a letter (the
# Warmane set is patch-C / patch-D / patch-F). W and X clear those while
# leaving Y and Z free on purpose: Y/Z are exactly what a mod author reaches
# for when told "use a late letter", and a same-name drop is an OVERWRITE,
# which is worse than losing a priority contest.
#
# patch-W.MPQ is the Paragon UI art. It has NO generator here -- its source
# BLPs are not in the tree -- so it is only ever renamed by hand, and the
# only copy that exists besides the live one is Tools/mpq-backup/.
MPQ_GENERAL = os.path.abspath(os.path.join(CLIENT_DATA, "patch-X.MPQ"))
MPQ_LOCALE = os.path.abspath(os.path.join(CLIENT_DATA, "enUS", "patch-enUS-X.MPQ"))
# The names this tool wrote before the rename. Deleted on every run so a stale
# copy can never linger in the mount list.
LEGACY_MPQS = [os.path.abspath(os.path.join(CLIENT_DATA, "patch-5.MPQ")),
               os.path.abspath(os.path.join(CLIENT_DATA, "enUS", "patch-enUS-5.MPQ"))]
DB_CONTAINER = "ac-database"
DB_PASS = os.environ.get("ACORE_DB_PASS", "")
if not DB_PASS:
    raise SystemExit("set ACORE_DB_PASS to your world DB password")
SPELL_FIELDS, TALENT_FIELDS, MAX_RANK_SLOTS = 234, 23, 9

# Codex node 59 "Skies of Azeroth": every CLIENT record carrying
# SPELL_ATTR4_ONLY_FLYING_AREAS gets that bit stripped, so the client stops
# refusing flying-mount casts on maps 0/1 before it sends anything. Selected
# BY THE ATTRIBUTE rather than by an id list, so the set stays correct without
# maintenance -- it is exactly "the spells the client pre-judges on flying
# areas" (118 records in stock 3.3.5a: every flying mount, the flying vehicles
# and toys, and druid Flight Form 33943 / Swift Flight Form 40120).
# Confirmed in-game with 32235 alone before widening. See the azeroth flight
# pass in main() for why this is safe.
CLIENT_FLIGHT_UNGATE_ALL = True
# Sanity floor: if a future client extract yields far fewer than this, the
# layout or the attribute assumption changed -- fail loudly rather than
# silently shipping a pass that did nothing.
CLIENT_FLIGHT_UNGATE_MIN = 100
HIDDEN_PASSIVE_ATTR = 0xC0   # SPELL_ATTR0_PASSIVE | SPELL_ATTR0_DO_NOT_DISPLAY

# Milestone 1000's exclusive title (custom CharTitles.dbc row, BOTH sides:
# client displays it, server validates it — on the ALE SetKnownTitle grant
# AND on the dropdown selection opcode). MaskID is a bit index into
# PLAYER__FIELD_KNOWN_TITLES (KNOWN_TITLES_SIZE 3 x uint64 = 192 bits).
# THE MASK MUST BE CONTIGUOUS (stock max + 1, asserted below): stock masks
# are dense 1..142 while row IDs run sparse to 177, and the client's title
# dropdown iterates mask indexes 1..GetNumTitles() (the ROW COUNT) — a
# gapped mask lands past the loop's end and never renders (field-proven:
# mask 180 granted server-side fine but was invisible in the client UI).
# The row ID has no such constraint. The track's TITLE reward
# (paragon_rework_track.lua) carries the same id — keep them in sync.
CHAR_TITLES_FIELDS = 37
PARAGON_TITLE_ID, PARAGON_TITLE_MASK, PARAGON_TITLE_NAME = 200, 143, "Paragon %s"
LOC_MASK = 16712190          # standard populated-locale mask on *_Lang_Mask

# ============================================================================
# Milestone 1200 companion: solo-clear achievements (96 total).
# Per-dungeon achievement id = SOLO_ACH_BASE + LFG dungeon id (the id
# paragon_solo_dungeon.lua records in acore_ale.paragon_solo_clears). The
# three era metas complete NATIVELY through type-8 (COMPLETE_ACHIEVEMENT)
# criteria — CompletedAchievement fires the type-8 update
# (AchievementMgr.cpp:2341) and login runs CheckAllAchievementCriteria — and
# the capstone chains off the metas, granting the Pinnacle title through
# achievement_reward. Client rows ride the locale MPQ (Achievement /
# Achievement_Criteria / Achievement_Category.dbc, all rebuilt from pristine
# extracts); the server loads the SAME rows from the acore_world *_dbc
# override tables (DBCStores.cpp LOAD_DBC), so the server side is SQL only.
# Granting is Lua (Player:SetAchievement) from the solo module's reconcile;
# the core refuses grants in GM mode, so the reconcile self-heals on the
# next GM-off login.
SOLO_ACH_BASE = 19000            # + dungeonId -> per-dungeon achievement id
SOLO_ACH_CRIT_BASE = 20000       # + dungeonId -> era-meta criteria id
SOLO_ACH_META = {"classic": 19301, "tbc": 19302, "wotlk": 19303}
SOLO_ACH_META_TEXT = {
    "classic": ("Solo Conqueror: Classic",
                "Solo-clear all 28 classic dungeon wings."),
    "tbc":     ("Solo Conqueror: Outland",
                "Solo-clear all 16 Outland dungeons on Normal and Heroic difficulty."),
    "wotlk":   ("Solo Conqueror: Northrend",
                "Solo-clear all 16 Northrend dungeons on Normal and Heroic difficulty."),
}
SOLO_ACH_CAPSTONE = 19304        # meta-meta: all three era metas
SOLO_ACH_CAPSTONE_CRIT = 20500   # +0/1/2: one criteria per era meta
SOLO_ACH_CATEGORY = 15200        # new subtab under Dungeons & Raids
SOLO_ACH_POINTS = (10, 25, 50)   # per dungeon / era meta / capstone
PINNACLE_TITLE_ID, PINNACLE_TITLE_MASK = 201, 144
PINNACLE_TITLE_NAME = "%s the Pinnacle"

# (dungeonId, era, display name) — mirror of paragon_solo_dungeon.lua
# DUNGEONS (the Lua module owns detection; this list owns the achievements).
# Declaration order = Ui_Order within the subtab.
SOLO_DUNGEONS = [
    (1,   "classic", "Wailing Caverns"),
    (2,   "classic", "Scholomance"),
    (4,   "classic", "Ragefire Chasm"),
    (6,   "classic", "The Deadmines"),
    (8,   "classic", "Shadowfang Keep"),
    (10,  "classic", "Blackfathom Deeps"),
    (12,  "classic", "The Stockade"),
    (14,  "classic", "Gnomeregan"),
    (16,  "classic", "Razorfen Kraul"),
    (18,  "classic", "Scarlet Monastery: Graveyard"),
    (20,  "classic", "Razorfen Downs"),
    (22,  "classic", "Uldaman"),
    (24,  "classic", "Zul'Farrak"),
    (26,  "classic", "Maraudon: Foulspore Cavern"),
    (28,  "classic", "The Temple of Atal'Hakkar"),
    (30,  "classic", "Blackrock Depths: Detention Block"),
    (32,  "classic", "Lower Blackrock Spire"),
    (34,  "classic", "Dire Maul: Warpwood Quarter"),
    (36,  "classic", "Dire Maul: Capital Gardens"),
    (38,  "classic", "Dire Maul: Gordok Commons"),
    (40,  "classic", "Stratholme: Crusaders' Square"),
    (163, "classic", "Scarlet Monastery: Armory"),
    (164, "classic", "Scarlet Monastery: Cathedral"),
    (165, "classic", "Scarlet Monastery: Library"),
    (272, "classic", "Maraudon: The Wicked Grotto"),
    (273, "classic", "Maraudon: Earth Song Falls"),
    (274, "classic", "Stratholme: The Gauntlet"),
    (276, "classic", "Blackrock Depths: Upper City"),
    (136, "tbc", "Hellfire Ramparts"),
    (137, "tbc", "The Blood Furnace"),
    (138, "tbc", "The Shattered Halls"),
    (140, "tbc", "The Slave Pens"),
    (146, "tbc", "The Underbog"),
    (147, "tbc", "The Steamvault"),
    (148, "tbc", "Mana-Tombs"),
    (149, "tbc", "Auchenai Crypts"),
    (150, "tbc", "Sethekk Halls"),
    (151, "tbc", "Shadow Labyrinth"),
    (170, "tbc", "Old Hillsbrad Foothills"),
    (171, "tbc", "The Black Morass"),
    (172, "tbc", "The Mechanar"),
    (173, "tbc", "The Botanica"),
    (174, "tbc", "The Arcatraz"),
    (198, "tbc", "Magisters' Terrace"),
    (188, "tbc", "Hellfire Ramparts (Heroic)"),
    (187, "tbc", "The Blood Furnace (Heroic)"),
    (189, "tbc", "The Shattered Halls (Heroic)"),
    (184, "tbc", "The Slave Pens (Heroic)"),
    (186, "tbc", "The Underbog (Heroic)"),
    (185, "tbc", "The Steamvault (Heroic)"),
    (179, "tbc", "Mana-Tombs (Heroic)"),
    (178, "tbc", "Auchenai Crypts (Heroic)"),
    (180, "tbc", "Sethekk Halls (Heroic)"),
    (181, "tbc", "Shadow Labyrinth (Heroic)"),
    (183, "tbc", "Old Hillsbrad Foothills (Heroic)"),
    (182, "tbc", "The Black Morass (Heroic)"),
    (192, "tbc", "The Mechanar (Heroic)"),
    (191, "tbc", "The Botanica (Heroic)"),
    (190, "tbc", "The Arcatraz (Heroic)"),
    (201, "tbc", "Magisters' Terrace (Heroic)"),
    (202, "wotlk", "Utgarde Keep"),
    (203, "wotlk", "Utgarde Pinnacle"),
    (204, "wotlk", "Azjol-Nerub"),
    (218, "wotlk", "Ahn'kahet: The Old Kingdom"),
    (214, "wotlk", "Drak'Tharon Keep"),
    (220, "wotlk", "The Violet Hold"),
    (216, "wotlk", "Gundrak"),
    (208, "wotlk", "Halls of Stone"),
    (207, "wotlk", "Halls of Lightning"),
    (206, "wotlk", "The Oculus"),
    (209, "wotlk", "The Culling of Stratholme"),
    (225, "wotlk", "The Nexus"),
    (245, "wotlk", "Trial of the Champion"),
    (251, "wotlk", "The Forge of Souls"),
    (253, "wotlk", "Pit of Saron"),
    (255, "wotlk", "Halls of Reflection"),
    (242, "wotlk", "Utgarde Keep (Heroic)"),
    (205, "wotlk", "Utgarde Pinnacle (Heroic)"),
    (241, "wotlk", "Azjol-Nerub (Heroic)"),
    (219, "wotlk", "Ahn'kahet: The Old Kingdom (Heroic)"),
    (215, "wotlk", "Drak'Tharon Keep (Heroic)"),
    (221, "wotlk", "The Violet Hold (Heroic)"),
    (217, "wotlk", "Gundrak (Heroic)"),
    (213, "wotlk", "Halls of Stone (Heroic)"),
    (212, "wotlk", "Halls of Lightning (Heroic)"),
    (211, "wotlk", "The Oculus (Heroic)"),
    (210, "wotlk", "The Culling of Stratholme (Heroic)"),
    (226, "wotlk", "The Nexus (Heroic)"),
    (249, "wotlk", "Trial of the Champion (Heroic)"),
    (252, "wotlk", "The Forge of Souls (Heroic)"),
    (254, "wotlk", "Pit of Saron (Heroic)"),
    (256, "wotlk", "Halls of Reflection (Heroic)"),
]

# Icon harvest: stock per-dungeon achievement names differ from ours in a
# few places — lookup-name overrides applied before the search (classic
# wing names additionally strip their ":" suffix in code).
SOLO_ICON_NAME = {
    "The Deadmines": "Deadmines",
    "The Temple of Atal'Hakkar": "Sunken Temple",
    "The Stockade": "Stormwind Stockade",
    "Dire Maul": "King of Dire Maul",        # applied after the ":" strip
    "The Underbog": "Underbog",
    "Old Hillsbrad Foothills": "The Escape From Durnholde",
    "The Black Morass": "Opening of the Dark Portal",
    "Magisters' Terrace": "Magister's Terrace",
    "Pit of Saron": "The Pit of Saron",
    "Halls of Reflection": "The Halls of Reflection",
}
SOLO_META_ICON_NAMES = {
    "classic": ["Classic Dungeonmaster"],
    "tbc": ["Outland Dungeonmaster", "Outland Dungeon Hero"],
    "wotlk": ["Northrend Dungeonmaster", "Northrend Dungeon Hero"],
}
SOLO_CAPSTONE_ICON_NAMES = ["Glory of the Hero", "Northrend Dungeonmaster"]


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    load_generated_class_data()

    cols = [(n, t, "unsigned" in ct) for n, t, ct in mysql(
        "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA='acore_world' AND TABLE_NAME='spell_dbc' "
        "ORDER BY ORDINAL_POSITION;")]
    assert len(cols) == SPELL_FIELDS, len(cols)
    idx = {n: i for i, (n, _, _) in enumerate(cols)}

    sblob = open(extract_dbc("Spell.dbc"), "rb").read()
    magic, snrec, snf, srsz, sssz = struct.unpack_from("<4sIIII", sblob, 0)
    assert magic == b"WDBC" and snf == SPELL_FIELDS
    sstr_base = 20 + snrec * srsz
    sstrings = bytearray(sblob[sstr_base:sstr_base + sssz])

    def sval(off):
        return bytes(sstrings[off:sstrings.index(b"\0", off)]).decode("utf-8", "replace")

    def addstr(text):
        off = len(sstrings)
        sstrings.extend(text.encode("utf-8") + b"\0")
        return off

    spell_off = {}
    for i in range(snrec):
        off = 20 + i * srsz
        spell_off[struct.unpack_from("<i", sblob, off)[0]] = off

    tblob = bytearray(open(extract_dbc("Talent.dbc"), "rb").read())
    tmagic, tnrec, tnf, trsz, tssz = struct.unpack_from("<4sIIII", tblob, 0)
    assert tmagic == b"WDBC" and tnf == TALENT_FIELDS
    pending_talent_rows = []
    talent_off = {}
    for i in range(tnrec):
        off = 20 + i * trsz
        talent_off[struct.unpack_from("<i", tblob, off)[0]] = off

    esc = lambda s: s.replace("\\", "\\\\").replace("'", "''")
    sql = ["-- GENERATED by Tools/paragon_client_patch.py - do not hand-edit.",
           "-- Single source for all Paragon custom spell/talent data.",
           "-- See 'Paragon Core Patches.md'."]
    new_records = bytearray()

    def clone_record(clone_id, overrides):
        """Clone a full Spell.dbc record with integer-field overrides applied."""
        coff = spell_off.get(clone_id) or sys.exit("clone spell %d missing" % clone_id)
        rec = bytearray(sblob[coff:coff + srsz])
        for field, v in overrides.items():
            struct.pack_into("<i", rec, idx[field] * 4, v)
        return rec

    def set_subtext(rec, text):
        off = addstr(text) if text else 0
        for n, _t, _u in cols:
            if n.startswith("NameSubtext_Lang_") and not n.endswith("_Mask"):
                # enUS is force-written for non-empty text — a clone with
                # no subtext at all (6544 -> Faithful Leap Rank 2) would
                # otherwise silently drop its "Rank N"; other locales keep
                # the populated-only rule
                if n == "NameSubtext_Lang_enUS" and text:
                    struct.pack_into("<i", rec, idx[n] * 4, off)
                elif struct.unpack_from("<i", rec, idx[n] * 4)[0] or text == "":
                    struct.pack_into("<i", rec, idx[n] * 4, off if text else 0)
        if text and not struct.unpack_from("<i", rec, idx["NameSubtext_Lang_Mask"] * 4)[0]:
            struct.pack_into("<i", rec, idx["NameSubtext_Lang_Mask"] * 4, 16712190)

    def set_name(rec, text):
        off = addstr(text)
        for n, _t, _u in cols:
            if n.startswith("Name_Lang_") and not n.endswith("_Mask"):
                if struct.unpack_from("<i", rec, idx[n] * 4)[0]:
                    struct.pack_into("<i", rec, idx[n] * 4, off)

    def blank_descriptions(rec):
        for n, t, _u in cols:
            if n.startswith(("Description_Lang_", "AuraDescription_Lang_")) and not n.endswith("_Mask"):
                struct.pack_into("<i", rec, idx[n] * 4, 0)

    def set_description(rec, text):
        off = addstr(text)
        struct.pack_into("<i", rec, idx["Description_Lang_enUS"] * 4, off)
        struct.pack_into("<i", rec, idx["Description_Lang_Mask"] * 4, 16712190)

    def set_aura_description(rec, text):
        """Buff-bar mouseover text. blank_descriptions() zeroes this column, so
        without an explicit setter every custom buff ships a blank tooltip."""
        off = addstr(text)
        struct.pack_into("<i", rec, idx["AuraDescription_Lang_enUS"] * 4, off)
        struct.pack_into("<i", rec, idx["AuraDescription_Lang_Mask"] * 4, 16712190)

    def record_to_sql(rec, text_overrides=None):
        vals = []
        for i, (n, t, unsigned) in enumerate(cols):
            raw = rec[i * 4:i * 4 + 4]
            if t in ("varchar", "text"):
                vals.append("'%s'" % esc(sval(struct.unpack("<i", raw)[0]) if struct.unpack("<i", raw)[0] else ""))
            elif t == "float":
                vals.append(repr(struct.unpack("<f", raw)[0]))
            elif unsigned:
                vals.append(str(struct.unpack("<I", raw)[0]))
            else:
                vals.append(str(struct.unpack("<i", raw)[0]))
        return "INSERT INTO spell_dbc (%s) VALUES (%s);" % (
            ", ".join("`%s`" % n for n, _, _ in cols), ", ".join(vals))

    all_new_spell_ids = []

    # ---- talent ranks -------------------------------------------------------
    for ext in TALENT_RANKS:
        toff = talent_off.get(ext["talent_id"]) or sys.exit("talent missing")
        ranks = list(struct.unpack_from("<9i", tblob, toff + 16))
        used = sum(1 for r in ranks if r)
        assert used + len(ext["new_ranks"]) <= MAX_RANK_SLOTS
        for slot, (sid, _) in enumerate(ext["new_ranks"], start=used):
            struct.pack_into("<i", tblob, toff + (4 + slot) * 4, sid)
        sql.append("DELETE FROM spell_dbc WHERE ID IN (%s);" %
                   ", ".join(str(s) for s, _ in ext["new_ranks"]))
        for rank_no, (sid, value) in enumerate(ext["new_ranks"], start=used + 1):
            overrides = {"ID": sid}
            if isinstance(value, dict):
                for field, v in value.items():
                    overrides[field] = v - 1
            else:
                overrides[ext["value_field"]] = value - 1
            rec = clone_record(ext["clone_spell"], overrides)
            set_subtext(rec, "Rank %d" % rank_no)
            new_records.extend(rec)
            sql.append(record_to_sql(rec))
            all_new_spell_ids.append(sid)
        t = struct.unpack_from("<%di" % TALENT_FIELDS, tblob, toff)
        sql += ["DELETE FROM talent_dbc WHERE ID = %d;" % ext["talent_id"],
                "INSERT INTO talent_dbc (ID, TabID, TierID, ColumnIndex, "
                "SpellRank_1, SpellRank_2, SpellRank_3, SpellRank_4, SpellRank_5, "
                "SpellRank_6, SpellRank_7, SpellRank_8, SpellRank_9, "
                "PrereqTalent_1, PrereqTalent_2, PrereqTalent_3, PrereqRank_1, PrereqRank_2, PrereqRank_3, "
                "Flags, RequiredSpellID, CategoryMask_1, CategoryMask_2) VALUES (%s);" % (
                    ", ".join(str(v) for v in t))]

    # ---- brand-new talents --------------------------------------------------
    # (Row, Col) already taken per tab, so the playerbot-ordering invariant and
    # cell collisions are checked by code rather than by comment alone.
    occupied = {}
    for _tid, _toff in talent_off.items():
        _tab, _row, _col = struct.unpack_from("<3i", tblob, _toff + 4)
        occupied.setdefault(_tab, set()).add((_row, _col))

    for nt in NEW_TALENTS:
        assert nt["talent_id"] not in talent_off, "talent id already exists"
        assert len(nt["ranks"]) <= MAX_RANK_SLOTS
        cells = occupied.setdefault(nt["tab"], set())
        cell = (nt["tier"], nt["column"])
        assert cell not in cells, "talent cell %s already occupied in tab %d" % (cell, nt["tab"])
        # MUST sort last by (Row, Col): mod-playerbots maps the Nth character
        # of the premade spec-link segment to the Nth sorted talent, so any
        # earlier insertion shifts every paladin bot's build by one.
        assert cell > max(cells), (
            "talent cell %s does not sort last in tab %d (max is %s) — this "
            "would shift the mod-playerbots spec-link mapping"
            % (cell, nt["tab"], max(cells)))
        cells.add(cell)
        rank_ids = [sid for sid, _ in nt["ranks"]]

        sql.append("DELETE FROM spell_dbc WHERE ID IN (%s);"
                   % ", ".join(str(s) for s in rank_ids + [nt["buff_id"]]))

        # the five passive proc-trigger ranks
        for rank_no, (sid, chance) in enumerate(nt["ranks"], start=1):
            rec = clone_record(nt["rank_clone"], {
                "ID": sid,
                "SpellClassSet": 10,          # paladin
                "AttributesEx3": 0,           # drop the priest-specific bits of 33150
                "ProcChance": chance,         # $h in the description renders this
                "ProcTypeMask": nt["proc_type_mask"],
                "EffectTriggerSpell_1": nt["buff_id"],
                "SpellIconID": nt["icon"],
                "SchoolMask": 1,
            })
            set_name(rec, nt["name"])
            set_subtext(rec, "Rank %d" % rank_no)
            blank_descriptions(rec)
            set_description(rec, nt["description"])
            new_records.extend(rec)
            sql.append(record_to_sql(rec))
            all_new_spell_ids.append(sid)

        # the instant-cast buff
        rec = clone_record(nt["buff_clone"], {
            "ID": nt["buff_id"],
            # Holy Light's family bit (0x80000000, signed) — verified to cover
            # every Holy Light rank including the custom 1900020 / 1900091
            "EffectSpellClassMaskA_1": -2147483648,
            # 59578's effect-1 mask is the TRIPLE (A_1, A_2, A_3); its A_2 bit
            # 0x2 is EXORCISM, which is why its tooltip reads "Flash of Light
            # or Exorcism". Left in place it would also make Exorcism instant.
            "EffectSpellClassMaskA_2": 0,
            "EffectSpellClassMaskA_3": 0,
            # MUST be 0: AzerothCore auto-generates a spell_proc entry for any
            # spell with proc flags, and Player::RemoveSpellMods explicitly
            # SKIPS spells that have one — the charge would then never drop
            # and the buff would be permanent.
            "ProcTypeMask": 0,
            "SpellClassMask_3": 0,            # drop Art of War's own family flag
            "SpellIconID": nt["icon"],
        })
        set_name(rec, nt["name"])
        set_subtext(rec, "")
        blank_descriptions(rec)
        set_description(rec, nt["buff_description"])
        set_aura_description(rec, nt["buff_aura_description"])
        new_records.extend(rec)
        sql.append(record_to_sql(rec))
        all_new_spell_ids.append(nt["buff_id"])

        # the Talent.dbc row (client) + talent_dbc row (server)
        trec = [0] * TALENT_FIELDS
        trec[0], trec[1], trec[2], trec[3] = (
            nt["talent_id"], nt["tab"], nt["tier"], nt["column"])
        for n, sid in enumerate(rank_ids):
            trec[4 + n] = sid
        pending_talent_rows.append((nt["tab"], struct.pack("<%di" % TALENT_FIELDS, *trec)))
        sql += ["DELETE FROM talent_dbc WHERE ID = %d;" % nt["talent_id"],
                "INSERT INTO talent_dbc (ID, TabID, TierID, ColumnIndex, "
                "SpellRank_1, SpellRank_2, SpellRank_3, SpellRank_4, SpellRank_5, "
                "SpellRank_6, SpellRank_7, SpellRank_8, SpellRank_9, "
                "PrereqTalent_1, PrereqTalent_2, PrereqTalent_3, PrereqRank_1, PrereqRank_2, PrereqRank_3, "
                "Flags, RequiredSpellID, CategoryMask_1, CategoryMask_2) VALUES (%s);"
                % ", ".join(str(v) for v in trec)]

        # one negative (= all ranks of the chain) spell_proc row. The chain is
        # built by SpellMgr::LoadSpellTalentRanks from the Talent.dbc rank
        # slots, which runs before LoadSpellProcs. Chance 0 and ProcFlags 0
        # mean "inherit per rank from each rank's own DBC row", so this single
        # row yields 2/4/6/8/10%. HitMask 2 = PROC_HIT_CRITICAL is the ONLY
        # encoding of crit-only; leaving it 0 would proc on every hit.
        sql += ["DELETE FROM spell_proc WHERE SpellId = %d;" % -rank_ids[0],
                "INSERT INTO spell_proc (SpellId, SchoolMask, SpellFamilyName, "
                "SpellFamilyMask0, SpellFamilyMask1, SpellFamilyMask2, ProcFlags, "
                "SpellTypeMask, SpellPhaseMask, HitMask, AttributesMask, "
                "DisableEffectsMask, ProcsPerMinute, Chance, Cooldown, Charges) VALUES "
                "(%d, 0, 0, 0, 0, 0, 0, 1, 2, 2, 2, 0, 0, 0, %d, 0);"
                % (-rank_ids[0], nt["proc_cooldown"])]

    # ---- markers ------------------------------------------------------------
    for m in MARKERS:
        overrides = {
            "ID": m["id"], "Attributes": HIDDEN_PASSIVE_ATTR,
            "Effect_1": 6, "EffectAura_1": 4, "EffectBasePoints_1": 0,
            "EffectDieSides_1": 1, "ImplicitTargetA_1": 1,
            "Effect_2": 0, "EffectAura_2": 0, "Effect_3": 0, "EffectAura_3": 0,
            "DurationIndex": 21, "RangeIndex": 1, "EquippedItemClass": -1,
            "ManaCost": 0, "ManaCostPct": 0, "SpellClassSet": 0,
            "SpellClassMask_1": 0, "SpellClassMask_2": 0, "SpellClassMask_3": 0,
            "CastingTimeIndex": 1, "ProcChance": 101,
        }
        overrides.update(m.get("overrides", {}))
        rec = clone_record(m["clone"], overrides)
        set_name(rec, m["name"])
        set_subtext(rec, "")
        blank_descriptions(rec)
        new_records.extend(rec)
        sql.append("DELETE FROM spell_dbc WHERE ID = %d;" % m["id"])
        sql.append(record_to_sql(rec))
        all_new_spell_ids.append(m["id"])

    # ---- server-only spells (SQL only, never staged into the MPQs) ---------
    for m in SERVER_SPELLS:
        overrides = {
            "ID": m["id"], "Attributes": HIDDEN_PASSIVE_ATTR,
            "Effect_1": 6, "EffectAura_1": 4, "EffectBasePoints_1": 0,
            "EffectDieSides_1": 1, "ImplicitTargetA_1": 1,
            "Effect_2": 0, "EffectAura_2": 0, "Effect_3": 0, "EffectAura_3": 0,
            "DurationIndex": 21, "RangeIndex": 1, "EquippedItemClass": -1,
            "ManaCost": 0, "ManaCostPct": 0, "SpellClassSet": 0,
            "SpellClassMask_1": 0, "SpellClassMask_2": 0, "SpellClassMask_3": 0,
            "CastingTimeIndex": 1, "ProcChance": 101,
        }
        overrides.update(m.get("overrides", {}))
        rec = clone_record(m["clone"], overrides)
        set_name(rec, m["name"])
        set_subtext(rec, "")
        blank_descriptions(rec)
        # deliberately NOT added to new_records / all_new_spell_ids: the
        # client keeps no entry for these
        sql.append("DELETE FROM spell_dbc WHERE ID = %d;" % m["id"])
        sql.append(record_to_sql(rec))

    # ---- full custom abilities ---------------------------------------------
    for cs in CUSTOM_SPELLS:
        overrides = {"ID": cs["id"]}
        overrides.update(cs.get("overrides", {}))
        rec = clone_record(cs["clone"], overrides)
        set_name(rec, cs["name"])
        set_subtext(rec, cs.get("subtext", ""))
        blank_descriptions(rec)
        if cs.get("description"):
            set_description(rec, cs["description"])
        new_records.extend(rec)
        sql.append("DELETE FROM spell_dbc WHERE ID = %d;" % cs["id"])
        sql.append(record_to_sql(rec))
        all_new_spell_ids.append(cs["id"])
        if "bonus" in cs:
            b = cs["bonus"]
            sql.append("DELETE FROM spell_bonus_data WHERE entry = %d;" % cs["id"])
            sql.append("INSERT INTO spell_bonus_data (entry, direct_bonus, dot_bonus, "
                       "ap_bonus, ap_dot_bonus, comments) VALUES (%d, %s, %s, %s, %s, '%s');" % (
                           cs["id"], b.get("direct", 0), b.get("dot", 0),
                           b.get("ap", 0), b.get("ap_dot", 0), esc(b.get("comment", ""))))

    # ---- trainable spell ranks ---------------------------------------------
    rank_ids = [r["new_id"] for r in SPELL_RANKS]
    sql.append("DELETE FROM spell_dbc WHERE ID IN (%s);" % ", ".join(map(str, rank_ids)))
    sql.append("DELETE FROM spell_ranks WHERE spell_id IN (%s);" % ", ".join(map(str, rank_ids)))
    sql.append("DELETE FROM trainer_spell WHERE SpellId IN (%s);" % ", ".join(map(str, rank_ids)))
    sql.append("DELETE FROM npc_trainer WHERE SpellID IN (%s); -- legacy table, dead on this core" % ", ".join(map(str, rank_ids)))
    sql.append("DELETE FROM spell_bonus_data WHERE entry IN (%s);" % ", ".join(map(str, rank_ids)))
    sql.append("DELETE FROM spell_threat WHERE entry IN (%s);" % ", ".join(map(str, rank_ids)))
    # Faithful Leap rank chain root: rank 1 (1900030, CUSTOM_SPELLS) is
    # milestone-taught rather than stock, so its chain-root spell_ranks row
    # must be emitted here or the 1900106 rank-2 chain fails validation.
    sql.append("DELETE FROM spell_ranks WHERE first_spell_id = 1900030;")
    sql.append("INSERT INTO spell_ranks (first_spell_id, spell_id, `rank`) VALUES (1900030, 1900030, 1);")
    # Three batched lookups instead of three per rank. At 306 ranks the
    # per-entry version meant ~900 `docker exec mysql` round-trips; this is
    # three, and the loop below just reads dictionaries.
    _clones = sorted(set(r["clone"] for r in SPELL_RANKS))
    _in = ", ".join(str(c) for c in _clones)
    trainer_of = {}
    for _sid, _tid in mysql("SELECT SpellId, TrainerId FROM trainer_spell "
                            "WHERE SpellId IN (%s);" % _in):
        trainer_of.setdefault(int(_sid), []).append(int(_tid))
    bonus_of = {int(row[0]): row[1:] for row in mysql(
        "SELECT entry, direct_bonus, dot_bonus, ap_bonus, ap_dot_bonus "
        "FROM spell_bonus_data WHERE entry IN (%s);" % _in)}
    threat_of = {int(row[0]): row[1:] for row in mysql(
        "SELECT entry, flatMod, pctMod, apPctMod FROM spell_threat "
        "WHERE entry IN (%s);" % _in)}

    for r in SPELL_RANKS:
        overrides = {"ID": r["new_id"], "SpellLevel": 80, "BaseLevel": 80}
        overrides.update(r["values"])
        rec = clone_record(r["clone"], overrides)
        set_subtext(rec, "Rank %d" % r["rank"])
        if r.get("description"):
            set_description(rec, r["description"])
        new_records.extend(rec)
        sql.append(record_to_sql(rec))
        sql.append("INSERT INTO spell_ranks (first_spell_id, spell_id, `rank`) VALUES (%d, %d, %d);" % (
            r["first"], r["new_id"], r["rank"]))

        # !! COEFFICIENT INHERITANCE IS A TRAP !!
        # SpellMgr::GetSpellBonusData falls back to GetFirstSpellInChain when a
        # rank has no row of its own -- and GetSpellThreatEntry does the exact
        # same thing (SpellMgr.cpp). That fallback lands on RANK 1, not on the
        # rank we cloned, and rank 1 of a low-level spell often carries a
        # deliberately downscaled coefficient. Holy Light shipped this way for
        # three milestones: stock ranks 4-13 all carry direct_bonus 1.66, but
        # rank 1 (635) carries 0.481, so custom ranks 14/15/16 silently ran at
        # ~29% of the intended spellpower scaling and rank 13 out-healed all
        # three of them. Nothing errors, nothing logs -- the spell just gets
        # quietly worse as you rank it up.
        # Copying the CLONE's rows removes the fallback from the picture
        # entirely. A clone with no row of its own still needs none: then the
        # clone and the new rank fall back to the same place by construction.
        for _tbl, _tcols, _src in (
                ("spell_bonus_data",
                 "direct_bonus, dot_bonus, ap_bonus, ap_dot_bonus", bonus_of),
                ("spell_threat", "flatMod, pctMod, apPctMod", threat_of)):
            _row = _src.get(r["clone"])
            if _row:
                _vals = ", ".join("NULL" if v == "NULL" else v for v in _row)
                _extra = ", comments" if _tbl == "spell_bonus_data" else ""
                _extraval = (", 'Paragon - %s Rank %d (inherited from %d)'"
                             % (esc(r["name"]), r["rank"], r["clone"])) if _extra else ""
                sql.append("INSERT INTO %s (entry, %s%s) VALUES (%d, %s%s);" % (
                    _tbl, _tcols, _extra, r["new_id"], _vals, _extraval))

        if not r.get("hidden"):
            trainer_ids = sorted(set(trainer_of.get(r["clone"], [])))
            if not trainer_ids:
                sys.exit("no trainer_spell rows found for clone spell %d" % r["clone"])
            for tid in trainer_ids:
                sql.append("INSERT INTO trainer_spell (TrainerId, SpellId, MoneyCost, ReqSkillLine, ReqSkillRank, "
                           "ReqAbility1, ReqAbility2, ReqAbility3, ReqLevel) VALUES (%d, %d, %d, 0, 0, %d, %d, 0, 80);" % (
                    tid, r["new_id"], r["cost"], r.get("req", TRAINER_REQ_MARKER), r.get("req2", 0)))
        all_new_spell_ids.append(r["new_id"])

    # ---- SkillLineAbility.dbc: the 3.3.5 client only DISPLAYS trainer entries
    # for spells present in this DBC (it drives the trainer window's category
    # grouping; spells without a row fall out of every group and the list
    # renders empty with a greyed-out expander). Client-only data: the server
    # trainer path works without it. One cloned row per new trainable rank.
    sla_blob = open(extract_dbc("SkillLineAbility.dbc"), "rb").read()
    amagic, anrec, anf, arsz, assz = struct.unpack_from("<4sIIII", sla_blob, 0)
    assert amagic == b"WDBC" and anf == 14, (amagic, anf)
    sla_by_spell = {}
    for i in range(anrec):
        off = 20 + i * arsz
        row = struct.unpack_from("<14i", sla_blob, off)
        sla_by_spell.setdefault(row[2], []).append(off)
    sla_new = bytearray()
    for r in SPELL_RANKS:
        if r.get("hidden"):
            continue
        offs = sla_by_spell.get(r["clone"])
        if not offs:
            sys.exit("clone spell %d has no SkillLineAbility row" % r["clone"])
        for n, off in enumerate(offs):
            rec = bytearray(sla_blob[off:off + arsz])
            struct.pack_into("<i", rec, 0, r["new_id"] + n)   # new unique SLA id
            struct.pack_into("<i", rec, 2 * 4, r["new_id"])   # spellId
            sla_new.extend(rec)
    sla_out = bytearray()
    sla_out.extend(struct.pack("<4sIIII", b"WDBC", anrec + len(sla_new) // arsz, anf, arsz, assz))
    sla_out.extend(sla_blob[20:20 + anrec * arsz])
    sla_out.extend(sla_new)
    sla_out.extend(sla_blob[20 + anrec * arsz:])
    print("staged SkillLineAbility.dbc %d -> %d records" % (anrec, anrec + len(sla_new) // arsz))

    # ---- CharTitles.dbc: milestone 1000's exclusive "Paragon" title --------
    # Layout (3.3.5, 37 int fields): ID, ConditionID, NameMale_Lang[16],
    # male flags, NameFemale_Lang[16], female flags, MaskID. enUS is locale
    # slot 0; the locale-presence flags are copied from record 0. The staged
    # copy rides the locale MPQ like Spell.dbc; the SAME bytes go to
    # generated/CharTitles.dbc for the worldserver, whose dbc dir lives in a
    # docker VOLUME (not host-mounted) — deploy with:
    #   docker cp Tools/generated/CharTitles.dbc \
    #     ac-worldserver:/azerothcore/env/dist/data/dbc/CharTitles.dbc
    ct_blob = open(extract_dbc("CharTitles.dbc"), "rb").read()
    cmagic, cnrec, cnf, crsz, cssz = struct.unpack_from("<4sIIII", ct_blob, 0)
    assert cmagic == b"WDBC" and cnf == CHAR_TITLES_FIELDS, (cmagic, cnf)
    ct_str_base = 20 + cnrec * crsz
    ct_strings = bytearray(ct_blob[ct_str_base:ct_str_base + cssz])
    ct_max_id = ct_max_mask = 0
    for i in range(cnrec):
        off = 20 + i * crsz
        ct_max_id = max(ct_max_id, struct.unpack_from("<i", ct_blob, off)[0])
        ct_max_mask = max(ct_max_mask, struct.unpack_from("<i", ct_blob, off + 36 * 4)[0])
    assert ct_max_id < PARAGON_TITLE_ID and ct_max_mask + 1 == PARAGON_TITLE_MASK < 192, \
        (ct_max_id, ct_max_mask)
    assert PINNACLE_TITLE_ID == PARAGON_TITLE_ID + 1 \
        and PINNACLE_TITLE_MASK == PARAGON_TITLE_MASK + 1 < 192
    ct_flags = struct.unpack_from("<i", ct_blob, 20 + 18 * 4)[0]
    ct_new = bytearray()
    for tid, tmask, tname in ((PARAGON_TITLE_ID, PARAGON_TITLE_MASK, PARAGON_TITLE_NAME),
                              (PINNACLE_TITLE_ID, PINNACLE_TITLE_MASK, PINNACLE_TITLE_NAME)):
        ct_name_off = len(ct_strings)
        ct_strings.extend(tname.encode("utf-8") + b"\0")
        ct_rec = bytearray(crsz)
        struct.pack_into("<i", ct_rec, 0, tid)
        struct.pack_into("<i", ct_rec, 2 * 4, ct_name_off)    # male name, enUS
        struct.pack_into("<i", ct_rec, 18 * 4, ct_flags)
        struct.pack_into("<i", ct_rec, 19 * 4, ct_name_off)   # female name, enUS
        struct.pack_into("<i", ct_rec, 35 * 4, ct_flags)
        struct.pack_into("<i", ct_rec, 36 * 4, tmask)
        ct_new.extend(ct_rec)
    ct_out = bytearray()
    ct_out.extend(struct.pack("<4sIIII", b"WDBC", cnrec + 2, cnf, crsz, len(ct_strings)))
    ct_out.extend(ct_blob[20:ct_str_base])
    ct_out.extend(ct_new)
    ct_out.extend(ct_strings)
    os.makedirs(os.path.join(HERE, "generated"), exist_ok=True)
    open(os.path.join(STAGE_LOCALE, "CharTitles.dbc"), "wb").write(bytes(ct_out))
    open(os.path.join(HERE, "generated", "CharTitles.dbc"), "wb").write(bytes(ct_out))
    print("staged CharTitles.dbc %d -> %d records (id %d '%s' mask %d; id %d '%s' mask %d) + server copy in generated/"
          % (cnrec, cnrec + 2, PARAGON_TITLE_ID, PARAGON_TITLE_NAME, PARAGON_TITLE_MASK,
             PINNACLE_TITLE_ID, PINNACLE_TITLE_NAME, PINNACLE_TITLE_MASK))
    # SERVER side of the Pinnacle title: the worldserver's dbc directory is a
    # READ-ONLY docker volume (the docker cp path used for the Paragon title
    # in 2026-08 no longer works — that file is baked in at 143 records), so
    # new titles go through the chartitles_dbc override table, which
    # DBCStores.cpp merges on top of the file. Only ids NOT already in the
    # baked file may be listed here (200 is; 201 is not).
    sql.append("DELETE FROM chartitles_dbc WHERE ID = %d;" % PINNACLE_TITLE_ID)
    sql.append("INSERT INTO chartitles_dbc (ID, Name_Lang_enUS, Name_Lang_Mask, "
               "Name1_Lang_enUS, Name1_Lang_Mask, Mask_ID) VALUES (%d, '%s', %d, '%s', %d, %d);"
               % (PINNACLE_TITLE_ID, esc(PINNACLE_TITLE_NAME), LOC_MASK,
                  esc(PINNACLE_TITLE_NAME), LOC_MASK, PINNACLE_TITLE_MASK))

    # ---- Lockpicking class gate, CLIENT half (Codex node 56) ---------------
    # The server override row below is necessary but NOT sufficient. The
    # 3.3.5 client filters its OWN skill list through its OWN copy of
    # SkillRaceClassInfo.dbc, and nothing in the core sends CHAT_MSG_SKILL —
    # the "Your skill in X has increased to Y" line is generated client-side
    # by diffing that list. So with the stock rogue-only row the client drops
    # skill 633 entirely and the player gets NO Skills-pane bar and NO
    # skill-up messages, while the server is happily tracking and raising the
    # skill underneath.
    #
    # FIELD-PROVEN, and the diagnosis hinges on it: picking a lock still
    # WORKED (Spell::CanOpenLock reads the server-side skill value, which the
    # client never sees), while the skill was invisible in the UI. Server-side
    # success plus client-side invisibility is the signature of the two DBC
    # copies disagreeing — always mirror a gate change into both.
    LOCKPICK_SKILL = 633
    LOCKPICK_SRCI_ROW = 601
    srci_blob = open(extract_dbc("SkillRaceClassInfo.dbc"), "rb").read()
    kmagic, knrec, knf, krsz, kssz = struct.unpack_from("<4sIIII", srci_blob, 0)
    assert kmagic == b"WDBC" and knf == 8, (kmagic, knf)
    srci_out = bytearray(srci_blob)
    srci_hits = []
    for i in range(knrec):
        off = 20 + i * krsz
        rid, skill_id = struct.unpack_from("<ii", srci_blob, off)
        if skill_id == LOCKPICK_SKILL:
            # fields 2 and 3 = RaceMask, ClassMask; 0 means "no restriction"
            struct.pack_into("<ii", srci_out, off + 8, 0, 0)
            srci_hits.append(rid)
    # exactly one row governs skill 633; if the client data ever grows another
    # one, the masks below would only half-open the gate — fail loudly instead
    assert srci_hits == [LOCKPICK_SRCI_ROW], srci_hits
    print("staged SkillRaceClassInfo.dbc: row %d (skill %d) opened to all races/classes"
          % (LOCKPICK_SRCI_ROW, LOCKPICK_SKILL))

    # ---- Lockpicking class gate, SERVER half (Codex node 56) ---------------
    # Skill 633's class restriction is PURE DATA: SkillRaceClassInfo.dbc row
    # 601 is the only row for the skill and carries ClassMask 8 (rogue).
    # Every gate that would strip lockpicking from a paladin funnels through
    # the same function, GetSkillRaceClassInfo (DBCStores.cpp:943):
    #   Player::_LoadSkills           deletes the character_skills row at login
    #   Player::CheckSkillLearnedBySpell   drops spell 1804 at login
    #   Player::LearnDefaultSkill     early-returns, so the grant never happens
    #   Player::UpdateSkillsForLevel  skips the max-value update
    # That function skips the mask test entirely when the mask is zero
    # (`if (mask && !(mask & bit)) continue`), so ONE override row with
    # ClassMask 0 opens all four at once. RaceMask 0 for the same reason
    # (and it dodges storing 0xFFFFFFFF in a signed int column); Flags 128
    # keeps SKILL_FLAG_INCLUDE_IN_SORT, matching the stock row.
    #
    # DB rows override the DBC file BY ID rather than appending
    # (DBCDatabaseLoader.cpp:135-140, indexTable[newIndexes[i]] = ...), and
    # SkillRaceClassInfoBySkill is built AFTER the load (DBCStores.cpp:368 vs
    # :459-464), so this replaces row 601 cleanly — skill 633 still maps to
    # exactly one entry, with no multimap iteration-order ambiguity.
    #
    # Blast radius is deliberately tight: trainers still refuse Pick Lock to
    # non-rogues, because IsSpellFitByClassAndRace tests SkillLineAbility
    # 8439's ClassMask 8 BEFORE it reaches the skill check (Player.cpp:12654
    # vs :12658), and mod-playerbots only rolls lockpicking inside
    # `case CLASS_ROGUE:` (PlayerbotFactory.cpp:3153). The Codex node stays
    # the only route.
    #
    # THIS ROW IS LOAD-BEARING AND MUST NOT BE LOST. Node 56 is
    # non-refundable, so if the row ever goes missing the skill is deleted at
    # the next login and the 25 spent points buy nothing, with no way to
    # reclaim them. That is why it lives in the generator and not just in the
    # live DB. Takes effect only after a worldserver restart (DBC stores load
    # once at startup; `.reload ale` does nothing for this).
    # ---- Arcane class gate, SERVER HALF ONLY (Codex node 57) ---------------
    # Node 57 "Ley Line Atlas" teaches the STOCK mage city teleports. They all
    # sit on SkillLine 237 ("Arcane"), whose SkillRaceClassInfo row 55 is
    # ClassMask 128 (mage) -- so Player::CheckSkillLearnedBySpell refuses them
    # for any other class at every login. Opening row 55 fixes that; three of
    # the four GetSkillRaceClassInfo consumers are inert for skill 237 because
    # only mages ever hold it as a real character skill (playercreateinfo_skills
    # gates it by classmask), and trainers stay shut because
    # IsSpellFitByClassAndRace tests the SkillLineAbility ClassMask 128 first.
    #
    # !!! Flags MUST stay 1040 (0x400 MONO_VALUE | 0x10 ALWAYS_MAX_VALUE) !!!
    # Mages hold skill 237 for real, and LearnDefaultSkill / UpdateSkillsForLevel
    # both branch on ALWAYS_MAX_VALUE. Copying row 601's Flags=128 here would
    # silently break mage Arcane skill maxing.
    #
    # NO CLIENT HALF, and that is deliberate -- do NOT "fix" it by mirroring
    # this row into the MPQ the way §2h does for row 601. That rule exists
    # because a granted SKILL is filtered through the client's own SRCI in the
    # Skills pane. This node grants only SPELLS (AcquireMethod 0, so no skill is
    # ever created), and the client's spellbook-tab routine tests the
    # SkillLineAbility's own ClassMask FIRST and short-circuits before SRCI --
    # so the teleports land in the General tab and a mirrored row would be a
    # no-op. Field-corroborated: Pick Lock sits in General today.
    #
    # LOAD-BEARING, and the failure mode is worse than node 56's: without this
    # row the refused spell leaves an ORPHANED character_spell row, which the
    # next save re-INSERTs -> duplicate key -> the character's ENTIRE save
    # transaction rolls back, every save, forever.
    sql.append("DELETE FROM skillraceclassinfo_dbc WHERE ID = 55;")
    sql.append("INSERT INTO skillraceclassinfo_dbc (ID, SkillID, RaceMask, ClassMask, "
               "Flags, MinLevel, SkillTierID, SkillCostIndex) "
               "VALUES (55, 237, 0, 0, 1040, 0, 0, 0);")

    sql.append("DELETE FROM skillraceclassinfo_dbc WHERE ID = 601;")
    sql.append("INSERT INTO skillraceclassinfo_dbc (ID, SkillID, RaceMask, ClassMask, "
               "Flags, MinLevel, SkillTierID, SkillCostIndex) "
               "VALUES (601, 633, 0, 0, 128, 0, 0, 0);")

    # ---- Racial ability lines, SERVER half (Milestone 1400) ---------------
    # Milestone 1400 "Racially Ambiguous" grants ONE ACTIVE racial of another
    # race, using the ORIGINAL stock spell ids (cloning is wrong here -- the
    # actives are exactly the racials the core hardcodes by spell id; see the
    # header of modules/paragon_racial_pick.lua).
    #
    # Player::CheckSkillLearnedBySpell runs from _LoadSpells at EVERY LOGIN
    # and deletes any spell whose SkillLineAbility skill line fails
    # GetSkillRaceClassInfo. Every racial hangs off its own race's line, so
    # without these rows a granted racial works until the next relog and then
    # vanishes -- leaving an ORPHANED character_spell row that the next save
    # re-INSERTs, rolling the character's ENTIRE save transaction back, every
    # save, forever. Same failure mode as row 55 above.
    #
    # Each row is the STOCK row with RaceMask widened to 0 and NOTHING ELSE
    # TOUCHED. ClassMask is already 1535 (all classes). Flags differ per line
    # -- 1170 (0x400 MONO_VALUE | 0x80 | 0x10 ALWAYS_MAX_VALUE | 0x2) for
    # seven of them, 146 (the same minus MONO_VALUE) for Orc/Blood Elf/
    # Draenei -- and MUST be copied verbatim: GetSkillRangeType branches on
    # MONO_VALUE and ALWAYS_MAX_VALUE, and SkillTierID (0 here) would flip it
    # to SKILL_RANGE_RANK. Verified one row per line against the client DBC.
    #
    # !! NO CLIENT HALF, AND THAT IS DELIBERATE -- IT INVERTS THE 2h RULE !!
    # 2h says to mirror a gate row into the MPQ when granting a SKILL, since
    # the Skills pane filters through the client's own SRCI. These racials
    # carry AcquireMethod 2 (LEARNED_ON_SKILL_LEARN), so _addSpell DOES create
    # the foreign line server-side, and all ten sit in SkillLineCategory 9
    # "Secondary Skills" -- displayed, like First Aid and Riding. Leaving the
    # client copy race-locked is what keeps "Racial - Troll" OUT of a
    # Draenei's Skills pane. Mirroring would add the clutter, not remove it.
    for _rid, _skill, _race, _cls, _flags, _min, _tier, _cost in [
            ( 68, 101, 0, 1535, 1170, 0, 0, 0),   # Dwarven Racial
            ( 71, 124, 0, 1535, 1170, 0, 0, 0),   # Tauren Racial
            ( 70, 125, 0, 1535,  146, 0, 0, 0),   # Orc Racial
            ( 69, 126, 0, 1535, 1170, 0, 0, 0),   # Night Elf Racial
            ( 72, 220, 0, 1535, 1170, 0, 0, 0),   # Racial - Undead
            (841, 733, 0, 1535, 1170, 0, 0, 0),   # Racial - Troll
            (861, 753, 0, 1535, 1170, 0, 0, 0),   # Racial - Gnome
            (862, 754, 0, 1535, 1170, 0, 0, 0),   # Racial - Human
            (867, 756, 0, 1535,  146, 0, 0, 0),   # Blood Elf Racial
            (877, 760, 0, 1535,  146, 0, 0, 0),   # Draenei Racial
    ]:
        sql.append("DELETE FROM skillraceclassinfo_dbc WHERE ID = %d;" % _rid)
        sql.append("INSERT INTO skillraceclassinfo_dbc (ID, SkillID, RaceMask, "
                   "ClassMask, Flags, MinLevel, SkillTierID, SkillCostIndex) "
                   "VALUES (%d, %d, %d, %d, %d, %d, %d, %d);"
                   % (_rid, _skill, _race, _cls, _flags, _min, _tier, _cost))

    # ---- Solo-clear achievements (milestone 1200 companion) -----------------
    # Achievement.dbc record (62 ints): 0 ID, 1 Faction, 2 Instance,
    # 3 Supercedes, 4..19 Title langs, 20 Title mask, 21..36 Desc langs,
    # 37 Desc mask, 38 Category, 39 Points, 40 Ui_Order, 41 Flags, 42 IconID,
    # 43..58 Reward langs, 59 Reward mask, 60 Min_Criteria, 61 Shares_Criteria.
    ach_blob = open(extract_dbc("Achievement.dbc"), "rb").read()
    hmagic, hnrec, hnf, hrsz, hssz = struct.unpack_from("<4sIIII", ach_blob, 0)
    assert hmagic == b"WDBC" and hnf == 62, (hmagic, hnf)
    ach_str_base = 20 + hnrec * hrsz
    ach_strings = bytearray(ach_blob[ach_str_base:ach_str_base + hssz])

    def ach_sval(off):
        return bytes(ach_strings[off:ach_strings.index(b"\0", off)]).decode("utf-8", "replace")

    def ach_addstr(text):
        off = len(ach_strings)
        ach_strings.extend(text.encode("utf-8") + b"\0")
        return off

    stock_icon = {}
    ach_max_id = 0
    for i in range(hnrec):
        off = 20 + i * hrsz
        ach_max_id = max(ach_max_id, struct.unpack_from("<i", ach_blob, off)[0])
        noff = struct.unpack_from("<i", ach_blob, off + 4 * 4)[0]
        if noff:
            name = ach_sval(noff)
            if name not in stock_icon:   # first (lowest-id) occurrence wins
                stock_icon[name] = struct.unpack_from("<i", ach_blob, off + 42 * 4)[0]
    assert ach_max_id < SOLO_ACH_BASE, ach_max_id
    assert len(SOLO_DUNGEONS) == 92, len(SOLO_DUNGEONS)
    assert [sum(1 for _d, e, _n in SOLO_DUNGEONS if e == era)
            for era in ("classic", "tbc", "wotlk")] == [28, 32, 32]

    icon_misses = []

    def solo_icon(display, era):
        heroic = display.endswith(" (Heroic)")
        base = display[:-9] if heroic else display
        base = SOLO_ICON_NAME.get(base, base)
        if era == "classic" and ":" in base:
            stripped = base.split(":")[0].strip()
            base = SOLO_ICON_NAME.get(stripped, stripped)
        for cand in (["Heroic: " + base, base] if heroic else [base]):
            if cand in stock_icon:
                return stock_icon[cand]
        icon_misses.append(display)
        return stock_icon.get("Deadmines", 1)

    def named_icon(cands, label):
        for cand in cands:
            if cand in stock_icon:
                return stock_icon[cand]
        icon_misses.append(label)
        return stock_icon.get("Deadmines", 1)

    # Category subtab under the stock "Dungeons & Raids" category.
    cat_blob = open(extract_dbc("Achievement_Category.dbc"), "rb").read()
    gmagic, gnrec, gnf, grsz, gssz = struct.unpack_from("<4sIIII", cat_blob, 0)
    assert gmagic == b"WDBC" and gnf == 20, (gmagic, gnf)
    cat_str_base = 20 + gnrec * grsz
    cat_strings = bytearray(cat_blob[cat_str_base:cat_str_base + gssz])
    # TRAP: TWO categories are named "Dungeons & Raids" — id 168 (parent -1,
    # the ACHIEVEMENTS tab) and id 14807 (parent 1 = Statistics, the STATS
    # tab). Matching on name alone picks the wrong one and the subtab lands
    # under Statistics, where the achievement UI never renders it and its
    # points count toward nothing. Only a top-level (parent == -1) category
    # is a valid achievement-tab parent: Blizzard_AchievementUI.lua's
    # AchievementFrameCategories_GetCategoryList seeds the tree with the
    # parent == -1 rows and then attaches children to rows already in it.
    dnr_id = None
    cat_max_id = 0
    for i in range(gnrec):
        off = 20 + i * grsz
        cid = struct.unpack_from("<i", cat_blob, off)[0]
        cat_max_id = max(cat_max_id, cid)
        noff = struct.unpack_from("<i", cat_blob, off + 2 * 4)[0]
        parent = struct.unpack_from("<i", cat_blob, off + 1 * 4)[0]
        if (noff and parent == -1
                and bytes(cat_strings[noff:cat_strings.index(b"\0", noff)]) == b"Dungeons & Raids"):
            dnr_id = cid
    assert dnr_id, "top-level 'Dungeons & Raids' category not found"
    assert cat_max_id < SOLO_ACH_CATEGORY, cat_max_id
    dnr_max_order = 0
    for i in range(gnrec):
        off = 20 + i * grsz
        if struct.unpack_from("<i", cat_blob, off + 1 * 4)[0] == dnr_id:
            dnr_max_order = max(dnr_max_order,
                                struct.unpack_from("<i", cat_blob, off + 19 * 4)[0])

    # DELETEs precede the generated INSERTs in the SQL stream
    sql.append("DELETE FROM achievement_dbc WHERE ID BETWEEN %d AND %d;"
               % (SOLO_ACH_BASE, SOLO_ACH_CAPSTONE))
    sql.append("DELETE FROM achievement_criteria_dbc WHERE ID BETWEEN %d AND %d;"
               % (SOLO_ACH_CRIT_BASE, SOLO_ACH_CAPSTONE_CRIT + 2))
    sql.append("DELETE FROM achievement_category_dbc WHERE ID = %d;" % SOLO_ACH_CATEGORY)
    sql.append("DELETE FROM achievement_reward WHERE ID = %d;" % SOLO_ACH_CAPSTONE)

    cat_name_off = len(cat_strings)
    cat_strings.extend(b"Solo Clears\0")
    cat_rec = bytearray(grsz)
    struct.pack_into("<i", cat_rec, 0, SOLO_ACH_CATEGORY)
    struct.pack_into("<i", cat_rec, 1 * 4, dnr_id)
    struct.pack_into("<i", cat_rec, 2 * 4, cat_name_off)
    struct.pack_into("<i", cat_rec, 18 * 4, LOC_MASK)
    struct.pack_into("<i", cat_rec, 19 * 4, dnr_max_order + 1)
    cat_out = bytearray()
    cat_out.extend(struct.pack("<4sIIII", b"WDBC", gnrec + 1, gnf, grsz, len(cat_strings)))
    cat_out.extend(cat_blob[20:cat_str_base])
    cat_out.extend(cat_rec)
    cat_out.extend(cat_strings)
    open(os.path.join(STAGE_LOCALE, "Achievement_Category.dbc"), "wb").write(bytes(cat_out))
    sql.append("INSERT INTO achievement_category_dbc (ID, Parent, Name_Lang_enUS, Name_Lang_Mask, Ui_Order) "
               "VALUES (%d, %d, 'Solo Clears', %d, %d);"
               % (SOLO_ACH_CATEGORY, dnr_id, LOC_MASK, dnr_max_order + 1))

    ach_new = bytearray()

    def ach_record(aid, name, desc, points, order, icon, reward_text=None):
        rec = bytearray(hrsz)
        struct.pack_into("<i", rec, 0, aid)
        struct.pack_into("<i", rec, 1 * 4, -1)
        struct.pack_into("<i", rec, 2 * 4, -1)
        struct.pack_into("<i", rec, 4 * 4, ach_addstr(name))
        struct.pack_into("<i", rec, 20 * 4, LOC_MASK)
        struct.pack_into("<i", rec, 21 * 4, ach_addstr(desc))
        struct.pack_into("<i", rec, 37 * 4, LOC_MASK)
        struct.pack_into("<i", rec, 38 * 4, SOLO_ACH_CATEGORY)
        struct.pack_into("<i", rec, 39 * 4, points)
        struct.pack_into("<i", rec, 40 * 4, order)
        struct.pack_into("<i", rec, 42 * 4, icon)
        if reward_text:
            struct.pack_into("<i", rec, 43 * 4, ach_addstr(reward_text))
            struct.pack_into("<i", rec, 59 * 4, LOC_MASK)
        ach_new.extend(rec)
        sql.append("INSERT INTO achievement_dbc (ID, Faction, Instance_Id, Title_Lang_enUS, Title_Lang_Mask, "
                   "Description_Lang_enUS, Description_Lang_Mask, Category, Points, Ui_Order, IconID%s) VALUES "
                   "(%d, -1, -1, '%s', %d, '%s', %d, %d, %d, %d, %d%s);"
                   % (", Reward_Lang_enUS, Reward_Lang_Mask" if reward_text else "",
                      aid, esc(name), LOC_MASK, esc(desc), LOC_MASK, SOLO_ACH_CATEGORY,
                      points, order, icon,
                      (", '%s', %d" % (esc(reward_text), LOC_MASK)) if reward_text else ""))

    # Achievement_Criteria.dbc record (31 ints): 0 ID, 1 Achievement, 2 Type,
    # 3 Asset, 4 Quantity, 5..8 start/fail events, 9..24 Desc langs, 25 mask,
    # 26 Flags, 27..29 timer, 30 Ui_Order. Type 8 = COMPLETE_ACHIEVEMENT.
    q_blob = open(extract_dbc("Achievement_Criteria.dbc"), "rb").read()
    qmagic, qnrec, qnf, qrsz, qssz = struct.unpack_from("<4sIIII", q_blob, 0)
    assert qmagic == b"WDBC" and qnf == 31, (qmagic, qnf)
    q_str_base = 20 + qnrec * qrsz
    crit_strings = bytearray(q_blob[q_str_base:q_str_base + qssz])
    crit_max_id = 0
    for i in range(qnrec):
        crit_max_id = max(crit_max_id, struct.unpack_from("<i", q_blob, 20 + i * qrsz)[0])
    assert crit_max_id < SOLO_ACH_CRIT_BASE, crit_max_id

    def crit_addstr(text):
        off = len(crit_strings)
        crit_strings.extend(text.encode("utf-8") + b"\0")
        return off

    crit_new = bytearray()

    def crit_record(cid, ach_id, linked_ach, label, order):
        rec = bytearray(qrsz)
        struct.pack_into("<i", rec, 0, cid)
        struct.pack_into("<i", rec, 1 * 4, ach_id)
        struct.pack_into("<i", rec, 2 * 4, 8)
        struct.pack_into("<i", rec, 3 * 4, linked_ach)
        struct.pack_into("<i", rec, 4 * 4, 1)
        struct.pack_into("<i", rec, 9 * 4, crit_addstr(label))
        struct.pack_into("<i", rec, 25 * 4, LOC_MASK)
        struct.pack_into("<i", rec, 30 * 4, order)
        crit_new.extend(rec)
        sql.append("INSERT INTO achievement_criteria_dbc (ID, Achievement_Id, Type, Asset_Id, Quantity, "
                   "Description_Lang_enUS, Description_Lang_Mask, Ui_Order) VALUES "
                   "(%d, %d, 8, %d, 1, '%s', %d, %d);"
                   % (cid, ach_id, linked_ach, esc(label), LOC_MASK, order))

    # capstone + era metas head the subtab (orders 0-3), dungeons follow (10+)
    ach_record(SOLO_ACH_CAPSTONE, "Pinnacle",
               "Solo-clear every dungeon in the game, on every difficulty.",
               SOLO_ACH_POINTS[2], 0,
               named_icon(SOLO_CAPSTONE_ICON_NAMES, "capstone icon"),
               "Title Reward: Pinnacle")
    for order, era in enumerate(("classic", "tbc", "wotlk"), start=1):
        meta_name, meta_desc = SOLO_ACH_META_TEXT[era]
        ach_record(SOLO_ACH_META[era], meta_name, meta_desc, SOLO_ACH_POINTS[1],
                   order, named_icon(SOLO_META_ICON_NAMES[era], era + " meta icon"))
    for seq, (did, era, display) in enumerate(SOLO_DUNGEONS):
        ach_record(SOLO_ACH_BASE + did, "Solo: " + display,
                   "Clear %s alone: no other player or bot may be inside the "
                   "instance when the final encounter falls." % display,
                   SOLO_ACH_POINTS[0], 10 + seq, solo_icon(display, era))

    era_seq = {"classic": 0, "tbc": 0, "wotlk": 0}
    for did, era, display in SOLO_DUNGEONS:
        crit_record(SOLO_ACH_CRIT_BASE + did, SOLO_ACH_META[era],
                    SOLO_ACH_BASE + did, display, era_seq[era])
        era_seq[era] += 1
    for n, era in enumerate(("classic", "tbc", "wotlk")):
        crit_record(SOLO_ACH_CAPSTONE_CRIT + n, SOLO_ACH_CAPSTONE,
                    SOLO_ACH_META[era], SOLO_ACH_META_TEXT[era][0], n)

    sql.append("INSERT INTO achievement_reward (ID, TitleA, TitleH) VALUES (%d, %d, %d);"
               % (SOLO_ACH_CAPSTONE, PINNACLE_TITLE_ID, PINNACLE_TITLE_ID))

    ach_out = bytearray()
    ach_out.extend(struct.pack("<4sIIII", b"WDBC", hnrec + len(ach_new) // hrsz, hnf, hrsz, len(ach_strings)))
    ach_out.extend(ach_blob[20:ach_str_base])
    ach_out.extend(ach_new)
    ach_out.extend(ach_strings)
    open(os.path.join(STAGE_LOCALE, "Achievement.dbc"), "wb").write(bytes(ach_out))
    crit_out = bytearray()
    crit_out.extend(struct.pack("<4sIIII", b"WDBC", qnrec + len(crit_new) // qrsz, qnf, qrsz, len(crit_strings)))
    crit_out.extend(q_blob[20:q_str_base])
    crit_out.extend(crit_new)
    crit_out.extend(crit_strings)
    open(os.path.join(STAGE_LOCALE, "Achievement_Criteria.dbc"), "wb").write(bytes(crit_out))
    print("staged Achievement.dbc %d -> %d, Achievement_Criteria.dbc %d -> %d, "
          "Achievement_Category.dbc %d -> %d (category %d 'Solo Clears' under %d)"
          % (hnrec, hnrec + len(ach_new) // hrsz, qnrec, qnrec + len(crit_new) // qrsz,
             gnrec, gnrec + 1, SOLO_ACH_CATEGORY, dnr_id))
    if icon_misses:
        print("WARNING: no stock icon match (Deadmines fallback used): %s" % ", ".join(icon_misses))

    # ---- write SQL ----------------------------------------------------------
    os.makedirs(os.path.dirname(OUT_SQL), exist_ok=True)
    with open(OUT_SQL, "w", encoding="utf-8") as f:
        f.write("\n".join(sql) + "\n")
    print("wrote", OUT_SQL)

    # ---- stage DBCs + build MPQs -------------------------------------------
    # Talent.dbc grows when NEW_TALENTS added rows. A new record CANNOT simply
    # be appended: the file is strictly GROUPED BY TabID (33 contiguous runs
    # for 33 tabs) and sorted by (Row, Col) within each run, and the client
    # builds its per-tab talent lists from that grouping — a row parked at EOF
    # makes its whole tab render EMPTY (field-proven: the Retribution tree went
    # blank). Splice each new row in directly after the last row of its own
    # tab; the NEW_TALENTS asserts already guarantee it sorts last within that
    # tab, which is also what mod-playerbots' spec-link mapping requires.
    t_str_base = 20 + tnrec * trsz
    records = [bytes(tblob[20 + i * trsz:20 + (i + 1) * trsz]) for i in range(tnrec)]
    for tab, rec in pending_talent_rows:
        last = max(i for i, r in enumerate(records)
                   if struct.unpack_from("<i", r, 4)[0] == tab)
        records.insert(last + 1, rec)
    talent_out = bytearray()
    talent_out.extend(struct.pack("<4sIIII", b"WDBC", len(records), tnf, trsz, tssz))
    for r in records:
        talent_out.extend(r)
    talent_out.extend(tblob[t_str_base:])
    if pending_talent_rows:
        print("staged Talent.dbc %d -> %d records (new: %s, spliced into their tab runs)"
              % (tnrec, len(records), [nt["talent_id"] for nt in NEW_TALENTS]))

    for stage in (STAGE_LOCALE, STAGE_GENERAL):
        os.makedirs(stage, exist_ok=True)
        open(os.path.join(stage, "Talent.dbc"), "wb").write(bytes(talent_out))
        open(os.path.join(stage, "SkillLineAbility.dbc"), "wb").write(bytes(sla_out))
        open(os.path.join(stage, "SkillRaceClassInfo.dbc"), "wb").write(bytes(srci_out))
    # ---- mount move-cast pass (milestone 750, CLIENT records only) ---------
    # The server makes mount casts instant per-player (§1 core patch, markers
    # 1900005/1900071), but the 3.3.5 client blocks mount casts while moving
    # BEFORE sending the cast packet (packet-probe proven; no server
    # producer of cast result 51 is reachable for a 0ms mount cast). The
    # client's pre-send check keys on the MOVEMENT bit (0x1) in the
    # record's InterruptFlags — cast time is irrelevant to it — so
    # stripping that one bit client-side lifts the block (verified
    # in-game). The SERVER reads stock InterruptFlags from its own DBC
    # files and stays the sole authority: below 750 its Spell::prepare gate
    # still rejects moving casts (nonzero cast time there) and Spell::update
    # still interrupts mid-cast movement; at 750 the 0ms cast makes moving
    # casts land. Cast times stay STOCK client-side (tooltips stay honest).
    # NOT mirrored into the spell_dbc SQL.
    # History: this pass was once reverted on a false charge of stance-bar
    # pollution. The real culprit was the milestone-600 quest-credit grant:
    # sequential quest ids included OTHER classes' teaching quests, and the
    # stock core re-casts every rewarded quest's RewardSpell at EVERY login
    # with no class check (Player::learnQuestRewardedSpells, non-persisted)
    # — quest credit grants must use RewardSpell=0, class-neutral quests.
    # No mount record ever appeared on the stance bar.
    srec = bytearray(sblob[20:sstr_base])
    aura_offs = [idx["EffectAura_%d" % n] * 4 for n in (1, 2, 3)]
    intr_off = idx["InterruptFlags"] * 4
    mounts_unflagged = 0
    for i in range(snrec):
        base = i * srsz
        if any(struct.unpack_from("<i", srec, base + off)[0] == 78 for off in aura_offs):
            flags = struct.unpack_from("<i", srec, base + intr_off)[0]
            if flags & 0x1:
                struct.pack_into("<i", srec, base + intr_off, flags & ~0x1)
                mounts_unflagged += 1
    print("mount move-cast pass: %d client mount records stripped of INTERRUPT_FLAG_MOVEMENT" % mounts_unflagged)

    # ---- azeroth flight pass (codex node 59, CLIENT records only) ----------
    # The 3.3.5 client refuses to SEND a flying-mount cast on maps 0/1.
    # PACKET-PROBE PROVEN: with the 1r core patch live and marker 1900130 held,
    # casting 32235 in Elwynn produced NO SpellInfo::CheckLocation call at all,
    # while the same cast in Hellfire produced one (strict=1). Meanwhile the
    # area-update path (strict=0) DID fire in Elwynn and waived correctly --
    # which is why a gryphon ridden in from Outland keeps flying there.
    #
    # Exactly the same shape as the mount move-cast pass above: the client
    # pre-checks the spell RECORD, so clearing the bit client-side lifts the
    # block. The SERVER reads stock attributes from its own dbc/ files and
    # remains the sole authority -- CheckLocation still runs and still demands
    # the marker, so a NON-holder simply gets the server's "You are in the
    # wrong zone" instead of a client-side refusal. NOT mirrored into the
    # spell_dbc SQL, and deliberately NOT done via AreaTable.dbc: flagging
    # ~972 Azeroth areas AREA_FLAG_OUTLAND has unmeasured client-side blast
    # radius (world map, music, flight-path UI) for no extra benefit.
    ATTR4_ONLY_FLYING_AREAS = 0x04000000
    attr4_off = idx["AttributesEx4"] * 4
    flight_ungated = []
    if CLIENT_FLIGHT_UNGATE_ALL:
        for i in range(snrec):
            base = i * srsz
            a4 = struct.unpack_from("<i", srec, base + attr4_off)[0]
            if a4 & ATTR4_ONLY_FLYING_AREAS:
                struct.pack_into("<i", srec, base + attr4_off,
                                 a4 & ~ATTR4_ONLY_FLYING_AREAS)
                flight_ungated.append(struct.unpack_from("<i", srec, base)[0])
    print("azeroth flight pass: %d client records stripped of "
          "ONLY_FLYING_AREAS" % len(flight_ungated))
    assert len(flight_ungated) >= CLIENT_FLIGHT_UNGATE_MIN, (
        "azeroth flight pass hit only %d records (expected >= %d) -- the "
        "Spell.dbc layout or the attribute assumption changed"
        % (len(flight_ungated), CLIENT_FLIGHT_UNGATE_MIN))

    total = len(new_records) // srsz
    out = bytearray()
    out.extend(struct.pack("<4sIIII", b"WDBC", snrec + total, SPELL_FIELDS, srsz, len(sstrings)))
    out.extend(srec)
    out.extend(new_records)
    out.extend(sstrings)
    open(os.path.join(STAGE_LOCALE, "Spell.dbc"), "wb").write(bytes(out))
    print("staged Spell.dbc %d -> %d records: %s" % (snrec, snrec + total, sorted(all_new_spell_ids)))

    # !! THE GENERAL PATCH MUST BE A SUPERSET OF THE LOCALE PATCH !!
    # The client sorts its patch list by FULL PATH, so "Data\p..." outranks
    # "Data\enUS\..." at EVERY letter -- even stock patch-2.MPQ mounts above
    # our locale archive. A DBC that exists ONLY in the locale patch can
    # therefore be shadowed by any third-party GENERAL patch (an HD model pack
    # dumping its own DBFilesClient is the realistic case), and it would fail
    # SILENTLY -- the same shape of bug as the Holy Light coefficient fallback.
    # Mirroring the locale stage into the general stage puts every file we own
    # on the top rung; the locale copy stays as a harmless lower-priority
    # fallback. Done by directory walk rather than a hardcoded list, so a DBC
    # added to the locale stage later is covered without anyone remembering to.
    mirrored = [n for n in sorted(os.listdir(STAGE_LOCALE))
                if os.path.isfile(os.path.join(STAGE_LOCALE, n))]
    for name in mirrored:
        shutil.copyfile(os.path.join(STAGE_LOCALE, name),
                        os.path.join(STAGE_GENERAL, name))
    print("mirrored %d locale DBCs into the general stage: %s"
          % (len(mirrored), ", ".join(mirrored)))

    for old_mpq in LEGACY_MPQS:
        if os.path.exists(old_mpq):
            try:
                os.remove(old_mpq)
                print("retired legacy " + os.path.basename(old_mpq))
            except OSError as exc:
                print("WARNING: legacy %s still present (%s) -- close the client "
                      "and delete it by hand" % (os.path.basename(old_mpq), exc))

    for stage, mpq in ((os.path.dirname(STAGE_GENERAL), MPQ_GENERAL),
                       (os.path.dirname(STAGE_LOCALE), MPQ_LOCALE)):
        r = subprocess.run([sys.executable, os.path.join(HERE, "build_mpq.py"), stage, mpq],
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode != 0:
            sys.exit("MPQ build failed")

    if args.apply:
        with open(OUT_SQL, encoding="utf-8") as f:
            mysql(f.read())
        print("SQL applied to acore_world")
    else:
        print("SQL NOT applied (rerun with --apply)")
    print("\nremember: worldserver restart + full client restart;")
    print("Lua gates: EXTENDED_TALENTS / LEARNED_SPELL_SPECIALS / CONSECRATION_BURST.totals")
    print("CharTitles: server side rides chartitles_dbc (SQL, applied above) — the "
          "worldserver dbc volume is READ-ONLY; generated/CharTitles.dbc is kept only "
          "as the client-side reference copy")


if __name__ == "__main__":
    main()
