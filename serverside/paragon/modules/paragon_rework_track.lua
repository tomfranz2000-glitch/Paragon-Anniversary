--[[
    Paragon Rework: reward track milestones (local module, see
    "Paragon Progression Design.md")

    Ten placeholder milestones at paragon levels 25..250 (step 25). Reaching a
    milestone permanently grants its bonus. Stat bonuses are stateless: derived
    from the paragon level and applied on login, removed on logout and cycled
    on stat reallocation via OnAfterUpdatePlayerStatistics — no DB rows.
    SPECIAL rewards are exceptions with their own lifecycles: TALENT_POINTS
    rides the core's persistent extraBonusTalentCount field and is reconciled,
    never cycled; aura specials (OOC_MOVE_SPEED, SWIM_SPEED) are permanent
    server-side auras — combat-gated ones are removed on entering combat and
    restored on leaving it.

    Client contract: the track definitions are pushed as opcode 7 on the addon
    prefix during the normal client load request (OnAfterClientLoadRequest),
    as one array table sorted ascending by level:
        { { level, icon, rewards = { { type, value, amount } } }, ... }
    The server-only "application" field is stripped from the client payload.
]]

local Constant = require("paragon_constant")
local Hook = require("paragon_hook")

local APPLIED_KEY = "ParagonTrackApplied"

-- ============================================================================
-- TRACK DEFINITIONS
-- ============================================================================

-- application values mirror the matching paragon_config_statistic rows (all 0;
-- stats without a row default to 0). Icons are reused from those rows where
-- one exists; ATTACK_POWER / HEALTH have no row, so stock 3.3.5 icons are
-- used instead.
-- Class IDs (3.3.5): 1 Warrior, 2 Paladin, 3 Hunter, 4 Rogue, 5 Priest,
-- 6 Death Knight, 7 Shaman, 8 Mage, 9 Warlock, 11 Druid.
-- Milestone 125 is the first class-dependent reward. For Paladins it unlocks
-- Divine Strength ranks 6-9 — enforced by the EXTENDED_TALENTS gate below,
-- the reward entry itself is informational; everyone else gets a stat
-- placeholder until their own bonus is designed.
local MILESTONE_75 = {
    [2] = {
        title = "Strength of the Divine",
        icon = "Interface/Icons/Spell_Holy_BlessingOfStrength",
        -- talent: client-side coordinates for the addon's talent-frame mask
        -- (tab/tier/column are the CLIENT's 1-based talent pane position;
        -- base is the rank cap shown while the milestone is locked)
        rewards = { { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
                      label = "Maximum rank of the Divine Strength talent increased by 4",
                      talent = { tab = 2, tier = 1, column = 3, base = 5 } } },
    },
}

-- Milestone 125: Paladins' Consecration also detonates instantly for its full
-- 8-second damage on cast (see the SPELL MODIFICATIONS section); other
-- classes keep the armor placeholder.
local MILESTONE_125 = {
    [2] = {
        title = "Sacred Ground",
        -- Holy Nova icon (user pick): the previous Spell_Holy_InnocenceBlessing
        -- never rendered on the 3.3.5 client (empty ring in the track UI)
        icon = "Interface/Icons/Spell_Holy_HolyNova",
        rewards = { { type = "SPECIAL", value = "CONSECRATION_BURST", amount = 1,
                      label = "Consecration also deals its full damage instantly when cast" } },
    },
}

-- Milestone 175: Paladins unlock six new trainable spell ranks at their class
-- trainer (Holy Light 14, Flash of Light 10, Consecration 9, Shield of
-- Righteousness 3, Hammer of Wrath 7, Exorcism 10; 2500-10000g each). The
-- gate is marker spell 1900007 "Paragon Level 175" (npc_trainer.ReqSpell),
-- taught/untaught by the reconcile below. Data: Tools/paragon_client_patch.py.
local MILESTONE_175 = {
    [2] = {
        icon = "Interface/Icons/INV_Misc_Book_11",
        rewards = { { type = "SPECIAL", value = "TRAINER_RANKS", amount = 6,
                      label = "New ranks for six spells can be learned at your class trainer" } },
    },
}

-- Milestone 200: Paladins may keep two class auras (Devotion, Retribution,
-- Concentration, the Resistances, Crusader) active at once; casting a third
-- swaps out the oldest. The mechanic is a core patch (Aura::CanStackWith,
-- see Paragon Core Patches.md) that activates for players who know marker
-- spell 1900008 "Paragon Dual Aura" — this side only teaches/unteaches it.
local MILESTONE_225 = {
    [2] = {
        title = "Twin Devotions",
        icon = "Interface/Icons/Spell_Holy_DevotionAura",
        rewards = { { type = "SPECIAL", value = "DUAL_AURA", amount = 2,
                      label = "You can activate two Auras at the same time" } },
    },
}

-- Milestone 350: Paladins gain Faithful Leap (spell 1900030) — the 3.3.5
-- client's own wrath-beta Heroic Leap prototype (6544) reskinned holy: jump
-- to the clicked location, Holy burst + 8s consecrated ground at the
-- destination (impact spell 1900031, triggered by the leap's own eff2).
-- Data lives in Tools/paragon_client_patch.py CUSTOM_SPELLS; this side only
-- teaches the spell. Other classes get a haste placeholder.
local MILESTONE_325 = {
    [2] = {
        title = "Leap of Faith",
        icon = "Interface/Icons/Ability_HeroicLeap",
        rewards = { { type = "SPECIAL", value = "FAITHFUL_LEAP", amount = 1,
                      label = "Gain the Faithful Leap ability" } },
    },
}

-- Milestone 450: Paladins — Avenger's Shield jumps to 2 more targets (5
-- total): visible passive 1900038 (client patch CUSTOM_SPELLS), the exact
-- inverse of Blizzard's Glyph of Avenger's Shield (54930, -2 through the
-- same SPELLMOD_JUMP_TARGETS pipeline; flat mods SUM, so glyph + milestone
-- = stock 3). Pure spell data, zero core changes. Others: placeholder crit.
local MILESTONE_575 = {
    [2] = {
        title = "Wall of Retribution",
        icon = "Interface/Icons/Spell_Holy_AvengersShield",
        rewards = { { type = "SPECIAL", value = "AVENGER_TARGETS", amount = 2,
                      label = "Avenger's Shield affects 5 targets instead of 3" } },
    },
}

-- Milestone 525: Paladins — Greater Blessings need no Symbol of Kings:
-- visible passive 1900040 (client patch CUSTOM_SPELLS) carrying aura 256
-- masked to all 12 Greater Blessing ranks. Pure spell data, zero core
-- changes; the client greys the reagent line by itself. Others:
-- placeholder stamina.
local MILESTONE_425 = {
    [2] = {
        title = "Unburdened Blessings",
        icon = "Interface/Icons/Spell_Magic_GreaterBlessingofKings",
        rewards = { { type = "SPECIAL", value = "LIVING_SYMBOL", amount = 1,
                      label = "Your Blessings no longer require Symbol of Kings" } },
    },
}

-- Milestones 550-675: the five-talent extension package (Paladins) — rank
-- caps raised inversely to talent power (research: 3.3.5 cookie-cutter
-- builds; every spec's build gains at least two). Data in
-- Tools/paragon_client_patch.py TALENT_RANKS; gates in EXTENDED_TALENTS
-- below; talent coords are the client pane positions for the mask addon.
local MILESTONE_275 = {
    [2] = {
        title = "Deeper Benediction",
        icon = "Interface/Icons/Spell_Frost_WindWalkOn",
        rewards = { { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
                      label = "Maximum rank of the Benediction talent increased by 4",
                      talent = { tab = 3, tier = 1, column = 3, base = 5 } } },
    },
}
local MILESTONE_475 = {
    [2] = {
        title = "Touched by Divinity",
        icon = "Interface/Icons/Spell_Holy_BlindingHeal",
        rewards = { { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
                      label = "Maximum rank of the Divinity talent increased by 4",
                      talent = { tab = 2, tier = 1, column = 2, base = 5 } } },
    },
}
local MILESTONE_625 = {
    [2] = {
        title = "Ever Vigilant",
        icon = "Interface/Icons/Spell_Magic_LesserInvisibilty",
        rewards = { { type = "SPECIAL", value = "TALENT_MASTERY", amount = 3,
                      label = "Maximum rank of the Anticipation talent increased by 3",
                      talent = { tab = 2, tier = 2, column = 3, base = 5 } } },
    },
}
local MILESTONE_725 = {
    [2] = {
        title = "Purity of Seals",
        icon = "Interface/Icons/Ability_ThunderBolt",
        rewards = { { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
                      label = "Maximum rank of the Seals of the Pure talent increased by 2",
                      talent = { tab = 1, tier = 1, column = 3, base = 5 } } },
    },
}
local MILESTONE_825 = {
    [2] = {
        title = "Unshakable Conviction",
        icon = "Interface/Icons/Spell_Holy_RetributionAura",
        rewards = { { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
                      label = "Maximum rank of the Conviction talent increased by 2",
                      talent = { tab = 3, tier = 3, column = 2, base = 5 } } },
    },
}

-- Milestone 1025: Paladins — Toughness ranks 6-9 (+12/14/16/18% armor from
-- items, slow-duration reduction continues its retail cadence). Data in
-- Tools/paragon_client_patch.py TALENT_RANKS (spells 1900100-03); the gate
-- is the EXTENDED_TALENTS entry below. Others: armor placeholder.
local MILESTONE_1025 = {
    [2] = {
        title = "Immovable Object",
        icon = "Interface/Icons/Spell_Holy_Devotion",
        rewards = { { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
                      label = "Maximum rank of the Toughness talent increased by 4",
                      talent = { tab = 2, tier = 3, column = 3, base = 5 } } },
    },
}

-- Milestone 1075: Paladins — Faithful Leap's cooldown ends 5s early.
-- Informational label only: enforcement is the timed cooldown-clear in
-- paragon_faithful_leap.lua, keyed on this level. Others: stat placeholder.
local MILESTONE_1075 = {
    [2] = {
        title = "Leap of Devotion",
        icon = "Interface/Icons/Ability_HeroicLeap",
        rewards = { { type = "SPECIAL", value = "LEAP_COOLDOWN", amount = 5,
                      label = "You learn Faithful Leap (Rank 2) — a new rank of the spell with its cooldown reduced by 5 seconds" } },
    },
}

-- Milestone 1175 "Beyond Mastery V" (fourth gated trainer wave): GBoW R6,
-- GBoM R6, Redemption R8, Holy Shield R8, Hammer of Wrath R9 — 26-37k gold
-- with a +2k premium per prior custom rank in the chain. Data in
-- Tools/paragon_client_patch.py SPELL_RANKS (1900112-16, gate GATE_1175).
local MILESTONE_1175 = {
    [2] = {
        icon = "Interface/Icons/Spell_Holy_SurgeOfLight",
        rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1175", amount = 5,
                      label = "New ranks for five more spells can be learned at your class trainer" } },
    },
}

-- Milestone 1125: Paladins — Stoicism ranks 4-5 (stun duration -40/-50%,
-- dispel resistance +40/+50%: both effects continue the retail 10-per-rank
-- cadence). Data in Tools/paragon_client_patch.py TALENT_RANKS (spells
-- 1900108-09); the gate is the EXTENDED_TALENTS entry below, whose base = 3
-- marks the first three-rank talent extended. Others: stat placeholder.
local MILESTONE_1125 = {
    [2] = {
        title = "Stone Resolve",
        icon = "Interface/Icons/Spell_Holy_Stoicism",
        rewards = { { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
                      label = "Maximum rank of the Stoicism talent increased by 2",
                      talent = { tab = 2, tier = 2, column = 1, base = 3 } } },
    },
}

-- Milestone 1225: Paladins — TWO talents raised by 2 ranks each. Swift
-- Retribution 4-5 (aura haste 4%/5%, spells 1900119-20) and Improved
-- Blessing of Might 3-4 (+37%/+50% blessing AP, spells 1900121-22). Both
-- gated by their EXTENDED_TALENTS entries below; Improved Blessing of
-- Might is the second two-rank talent shape (base = 2). Others: stat
-- placeholder.
local MILESTONE_1225 = {
    [2] = {
        title = "Zealous Command",
        icon = "Interface/Icons/Ability_Paladin_SwiftRetribution",
        rewards = {
            { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
              label = "Maximum rank of the Swift Retribution talent increased by 2",
              talent = { tab = 3, tier = 9, column = 1, base = 3 } },
            { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
              label = "Maximum rank of the Improved Blessing of Might talent increased by 2",
              talent = { tab = 3, tier = 2, column = 3, base = 2 } },
        },
    },
}

-- Milestone 1325: Paladins — a BRAND-NEW Retribution talent, "Sudden Light"
-- (talent 2286 at DBC row 10 / column 2, payload coords tier 11 / column 3):
-- critical strikes have a 2/4/6/8/10% chance to make the next Holy Light
-- instant. Spells 1900124-28 (ranks) + 1900129 (buff) come from
-- Tools/paragon_client_patch.py NEW_TALENTS. The talent row lives in the
-- static client DBC, so it would otherwise be visible to every paladin: the
-- `hidden` flag tells Paragon_TalentMask.lua to hide the button entirely
-- until the milestone lands, and EXTENDED_TALENTS[2286] with base = 0 makes
-- the server refuse EVERY rank below it. Others: stat placeholder.
local MILESTONE_1325 = {
    [2] = {
        title = "Sudden Light",
        icon = "Interface/Icons/Spell_Holy_SurgeOfLight",
        rewards = { { type = "SPECIAL", value = "NEW_TALENT", amount = 5,
                      label = "New Retribution talent: Sudden Light — your critical strikes can make your next Holy Light instant",
                      talent = { tab = 3, tier = 11, column = 3, base = 0, hidden = true } } },
    },
}

-- Milestone 700: Paladins — two same-caster Blessings per target: §1h core
-- patch in Aura::CanStackWith wraps the spell-group-1010 same-caster-
-- exclusive branch with a marker check (1900056) + newest-survives cap 2
-- (the §1d dual-aura contract). Same-kind pairs (Might vs Greater Might)
-- resolve through their subgroup rules and stay exclusive. Others:
-- placeholder stat.
local MILESTONE_675 = {
    [2] = {
        title = "Twofold Blessing",
        icon = "Interface/Icons/Spell_Holy_GreaterBlessingofKings",
        rewards = { { type = "SPECIAL", value = "DUAL_BLESSING", amount = 2,
                      label = "Two of your Blessings can be active on a target at the same time" } },
    },
}

-- Milestones 900/925/950 "Beyond Mastery I-III": trainer-rank waves on the
-- milestone-175 blueprint — five new spell ranks each, gated by markers
-- 1900076/77/78 via trainer_spell.ReqAbility1 (ReqAbility2 carries the
-- previous rank: no rank-skipping, talent chains stay closed). All data
-- lives in Tools/paragon_client_patch.py SPELL_RANKS; custom-effect
-- carryover: Avenger's Reach (450) matches new ranks by inherited family
-- mask, the Consecration burst (150) needs the totals entry below.
local MILESTONE_525 = {
    [2] = {
        icon = "Interface/Icons/Spell_Holy_SearingLight",
        rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_525", amount = 5,
                      label = "New ranks for five spells can be learned at your class trainer" } },
    },
}
local MILESTONE_775 = {
    [2] = {
        icon = "Interface/Icons/Spell_Holy_InnerFire",
        rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_775", amount = 5,
                      label = "New ranks for five more spells can be learned at your class trainer" } },
    },
}
local MILESTONE_900 = {
    [2] = {
        icon = "Interface/Icons/Spell_Holy_GreaterHeal",
        rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_900", amount = 5,
                      label = "New ranks for five more spells can be learned at your class trainer" } },
    },
}

-- Milestone 1375: Paladins -- THREE talents raised by 2 ranks each, all of
-- them 3-rank talents, so every gate below carries base = 3. One-Handed
-- Weapon Specialization 4/7/10% -> 13/16% (spells 1900132-33), Two-Handed
-- Weapon Specialization 2/4/6% -> 8/10% (1900134-35) and Combat Expertise
-- 2/4/6 -> 8/10 (1900136-37, three effects moved together so the stamina
-- half keeps pace with the expertise half). Data comes from
-- Tools/paragon_client_patch.py TALENT_RANKS; the gates are the
-- EXTENDED_TALENTS entries below. Others: stat placeholder.
local MILESTONE_1375 = {
    [2] = {
        title = "Master at Arms",
        icon = "Interface/Icons/Ability_Warrior_WeaponMastery",
        rewards = {
            { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
              label = "Maximum rank of the One-Handed Weapon Specialization talent increased by 2",
              talent = { tab = 2, tier = 6, column = 3, base = 3 } },
            { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
              label = "Maximum rank of the Two-Handed Weapon Specialization talent increased by 2",
              talent = { tab = 3, tier = 5, column = 1, base = 3 } },
            { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
              label = "Maximum rank of the Combat Expertise talent increased by 2",
              talent = { tab = 2, tier = 8, column = 3, base = 3 } },
        },
    },
}

-- Milestone 1425 "Beyond Mastery VI" (fifth gated trainer wave): Avenger's
-- Shield R7, Holy Wrath R7, Exorcism R12, Consecration R11, Holy Light R16 --
-- 36-45k gold on the established shape (base band 34/35/37/39/41k, +2k per
-- prior custom rank in the chain). Data in Tools/paragon_client_patch.py
-- SPELL_RANKS (1900141-45, gate GATE_1425 = 1900146).
-- Consecration R11 also gains the milestone-125 burst: its totals entry is
-- in CONSECRATION_BURST below, without which the new rank would be the only
-- one that does not detonate.
local MILESTONE_1425 = {
    [2] = {
        icon = "Interface/Icons/Spell_Holy_ChampionsBond",
        rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1425", amount = 5,
                      label = "New ranks for five more spells can be learned at your class trainer" } },
    },
}

-- Milestone 1475: Paladins -- Touched by the Light rank 4 (spell power from
-- strength 60 -> 80%, crit 30 -> 40%, healing power 60 -> 80%) and Holy
-- Guidance ranks 6-9 (spell power + healing power from intellect 20 -> 36%,
-- flat +4 per rank). Data in Tools/paragon_client_patch.py TALENT_RANKS
-- (spells 1900151 and 1900147-50); the gates are the EXTENDED_TALENTS
-- entries below. Holy Guidance reaches NINE ranks, exactly filling
-- Talent.dbc's rank slots -- it can never be extended again.
local MILESTONE_1475 = {
    [2] = {
        title = "Vessel of Light",
        icon = "Interface/Icons/Spell_Holy_HolyGuidance",
        rewards = {
            { type = "SPECIAL", value = "TALENT_MASTERY", amount = 1,
              label = "Maximum rank of the Touched by the Light talent increased by 1",
              talent = { tab = 2, tier = 9, column = 1, base = 3 } },
            { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
              label = "Maximum rank of the Holy Guidance talent increased by 4",
              talent = { tab = 1, tier = 8, column = 3, base = 5 } },
        },
    },
}

-- ==========================================================================
-- NON-PALADIN CLASS REWARDS
-- ==========================================================================
--
-- GENERATED by Tools/gen_class_track_lua.py from the same data that
-- produced the spell rows -- do not hand-edit, regenerate. Every entry
-- mirrors the Paladin milestone it sits beside: where a Paladin
-- milestone raises one talent by four ranks, so does every other
-- class's; where it touches three talents, so do theirs.
--
-- The ONE deliberate break is the Death Knight's trainer waves: four
-- spells per wave instead of five or six. It has eight trainer-taught
-- rank chains where every other class has fourteen or more, because
-- its spells all start at level 55, and holding it to the same count
-- would drive single chains five ranks deep.
--
-- Milestones with no entry here are the ones whose Paladin reward is a
-- bespoke mechanic; they get NO_REWARD below.

local CLASS_REWARDS = {}

local function ClassReward(level, class_id, entry)
    CLASS_REWARDS[level] = CLASS_REWARDS[level] or {}
    CLASS_REWARDS[level][class_id] = entry
end

-- ---- milestone 75 ----------------------------------------------------------
ClassReward(75, 1, {
    title = "Deflection",
    icon = "Interface/Icons/Ability_Parry",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Deflection talent increased by 4",
          talent = { tab = 1, tier = 1, column = 2, base = 5 } },
    },
})   -- Warrior
ClassReward(75, 3, {
    title = "Endurance Training",
    icon = "Interface/Icons/Spell_Nature_Reincarnation",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Endurance Training talent increased by 4",
          talent = { tab = 1, tier = 1, column = 3, base = 5 } },
    },
})   -- Hunter
ClassReward(75, 4, {
    title = "Deadliness",
    icon = "Interface/Icons/INV_Weapon_Crossbow_11",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Deadliness talent increased by 4",
          talent = { tab = 3, tier = 6, column = 3, base = 5 } },
    },
})   -- Rogue
ClassReward(75, 5, {
    title = "Twin Disciplines",
    icon = "Interface/Icons/Spell_Holy_SealOfVengeance",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Twin Disciplines talent increased by 4",
          talent = { tab = 1, tier = 1, column = 3, base = 5 } },
    },
})   -- Priest
ClassReward(75, 6, {
    title = "Subversion",
    icon = "Interface/Icons/Spell_DeathKnight_Subversion",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Subversion talent increased by 4",
          talent = { tab = 1, tier = 1, column = 2, base = 3 } },
    },
})   -- Death Knight
ClassReward(75, 7, {
    title = "Ancestral Knowledge",
    icon = "Interface/Icons/Spell_Shadow_GrimWard",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Ancestral Knowledge talent increased by 4",
          talent = { tab = 2, tier = 1, column = 3, base = 5 } },
    },
})   -- Shaman
ClassReward(75, 8, {
    title = "Arcane Mind",
    icon = "Interface/Icons/Spell_Shadow_Charm",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Arcane Mind talent increased by 4",
          talent = { tab = 1, tier = 5, column = 4, base = 5 } },
    },
})   -- Mage
ClassReward(75, 9, {
    title = "Demonic Embrace",
    icon = "Interface/Icons/Spell_Shadow_Metamorphosis",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Demonic Embrace talent increased by 4",
          talent = { tab = 2, tier = 1, column = 3, base = 3 } },
    },
})   -- Warlock
ClassReward(75, 11, {
    title = "Naturalist",
    icon = "Interface/Icons/Spell_Nature_HealingTouch",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Naturalist talent increased by 4",
          talent = { tab = 3, tier = 2, column = 1, base = 5 } },
    },
})   -- Druid

-- ---- milestone 175 ---------------------------------------------------------
ClassReward(175, 1, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS", amount = 6,
                  label = "New ranks for six spells can be learned at your class trainer" } },
})   -- Warrior: Heroic Strike R14, Mortal Strike R9, Shield Slam R9, Execute R10, Revenge R10, Thunder Clap R10
ClassReward(175, 3, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS", amount = 6,
                  label = "New ranks for six spells can be learned at your class trainer" } },
})   -- Hunter: Serpent Sting R13, Arcane Shot R12, Steady Shot R5, Multi-Shot R9, Aspect of the Hawk R9, Raptor Strike R12
ClassReward(175, 4, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS", amount = 6,
                  label = "New ranks for six spells can be learned at your class trainer" } },
})   -- Rogue: Sinister Strike R13, Eviscerate R13, Backstab R13, Rupture R10, Ambush R11, Envenom R5
ClassReward(175, 5, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS", amount = 6,
                  label = "New ranks for six spells can be learned at your class trainer" } },
})   -- Priest: Mind Blast R14, Shadow Word: Pain R13, Flash Heal R12, Greater Heal R10, Renew R15, Power Word: Shield R15
ClassReward(175, 6, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS", amount = 4,
                  label = "New ranks for four spells can be learned at your class trainer" } },
})   -- Death Knight: Obliterate R5, Death Strike R6, Icy Touch R6, Plague Strike R7
ClassReward(175, 7, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS", amount = 6,
                  label = "New ranks for six spells can be learned at your class trainer" } },
})   -- Shaman: Lightning Bolt R15, Healing Wave R15, Earth Shock R11, Chain Lightning R9, Flame Shock R10, Chain Heal R8
ClassReward(175, 8, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS", amount = 6,
                  label = "New ranks for six spells can be learned at your class trainer" } },
})   -- Mage: Frostbolt R17, Fireball R17, Arcane Blast R5, Pyroblast R13, Scorch R12, Fire Blast R12
ClassReward(175, 9, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS", amount = 6,
                  label = "New ranks for six spells can be learned at your class trainer" } },
})   -- Warlock: Shadow Bolt R14, Corruption R11, Immolate R12, Incinerate R5, Curse of Agony R10, Unstable Affliction R6
ClassReward(175, 11, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS", amount = 6,
                  label = "New ranks for six spells can be learned at your class trainer" } },
})   -- Druid: Wrath R13, Starfire R11, Moonfire R15, Healing Touch R16, Rejuvenation R16, Regrowth R13

-- ---- milestone 275 ---------------------------------------------------------
ClassReward(275, 1, {
    title = "Tactical Mastery",
    icon = "Interface/Icons/Spell_Nature_EnchantArmor",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Tactical Mastery talent increased by 4",
          talent = { tab = 1, tier = 2, column = 3, base = 3 } },
    },
})   -- Warrior
ClassReward(275, 3, {
    title = "Efficiency",
    icon = "Interface/Icons/Spell_Frost_WizardMark",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Efficiency talent increased by 4",
          talent = { tab = 2, tier = 4, column = 3, base = 5 } },
    },
})   -- Hunter
ClassReward(275, 4, {
    title = "Serrated Blades",
    icon = "Interface/Icons/INV_Sword_17",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Serrated Blades talent increased by 4",
          talent = { tab = 3, tier = 3, column = 3, base = 3 } },
    },
})   -- Rogue
ClassReward(275, 5, {
    title = "Shadow Focus",
    icon = "Interface/Icons/Spell_Shadow_BurningSpirit",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Shadow Focus talent increased by 4",
          talent = { tab = 3, tier = 2, column = 3, base = 3 } },
    },
})   -- Priest
ClassReward(275, 6, {
    title = "Runic Power Mastery",
    icon = "Interface/Icons/Spell_Arcane_Arcane01",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Runic Power Mastery talent increased by 4",
          talent = { tab = 2, tier = 1, column = 2, base = 2 } },
    },
})   -- Death Knight
ClassReward(275, 7, {
    title = "Convection",
    icon = "Interface/Icons/Spell_Nature_WispSplode",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Convection talent increased by 4",
          talent = { tab = 1, tier = 1, column = 2, base = 5 } },
    },
})   -- Shaman
ClassReward(275, 8, {
    title = "Arcane Concentration",
    icon = "Interface/Icons/Spell_Shadow_ManaBurn",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Arcane Concentration talent increased by 4",
          talent = { tab = 1, tier = 2, column = 3, base = 5 } },
    },
})   -- Mage
ClassReward(275, 9, {
    title = "Improved Life Tap",
    icon = "Interface/Icons/Spell_Shadow_BurningSpirit",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Improved Life Tap talent increased by 4",
          talent = { tab = 1, tier = 2, column = 3, base = 2 } },
    },
})   -- Warlock
ClassReward(275, 11, {
    title = "Moonglow",
    icon = "Interface/Icons/Spell_Nature_Sentinal",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Moonglow talent increased by 4",
          talent = { tab = 1, tier = 2, column = 1, base = 3 } },
    },
})   -- Druid

-- ---- milestone 475 ---------------------------------------------------------
ClassReward(475, 1, {
    title = "Vitality",
    icon = "Interface/Icons/INV_Helmet_21",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Vitality talent increased by 4",
          talent = { tab = 3, tier = 8, column = 2, base = 3 } },
    },
})   -- Warrior
ClassReward(475, 3, {
    title = "Survivalist",
    icon = "Interface/Icons/Spell_Shadow_Twilight",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Survivalist talent increased by 4",
          talent = { tab = 3, tier = 3, column = 1, base = 5 } },
    },
})   -- Hunter
ClassReward(475, 4, {
    title = "Vitality",
    icon = "Interface/Icons/Ability_Warrior_Revenge",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Vitality talent increased by 4",
          talent = { tab = 2, tier = 7, column = 1, base = 3 } },
    },
})   -- Rogue
ClassReward(475, 5, {
    title = "Spiritual Healing",
    icon = "Interface/Icons/Spell_Nature_MoonGlow",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Spiritual Healing talent increased by 4",
          talent = { tab = 2, tier = 6, column = 3, base = 5 } },
    },
})   -- Priest
ClassReward(475, 6, {
    title = "Veteran of the Third War",
    icon = "Interface/Icons/Spell_Misc_WarsongFocus",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Veteran of the Third War talent increased by 4",
          talent = { tab = 1, tier = 5, column = 3, base = 3 } },
    },
})   -- Death Knight
ClassReward(475, 7, {
    title = "Purification",
    icon = "Interface/Icons/Spell_Frost_WizardMark",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Purification talent increased by 4",
          talent = { tab = 3, tier = 6, column = 3, base = 5 } },
    },
})   -- Shaman
ClassReward(475, 8, {
    title = "Molten Shields",
    icon = "Interface/Icons/Spell_Fire_FireArmor",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Molten Shields talent increased by 4",
          talent = { tab = 2, tier = 4, column = 2, base = 2 } },
    },
})   -- Mage
ClassReward(475, 9, {
    title = "Fel Vitality",
    icon = "Interface/Icons/Spell_Holy_MagicalSentry",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Fel Vitality talent increased by 4",
          talent = { tab = 2, tier = 2, column = 3, base = 3 } },
    },
})   -- Warlock
ClassReward(475, 11, {
    title = "Gift of Nature",
    icon = "Interface/Icons/Spell_Nature_ProtectionformNature",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Gift of Nature talent increased by 4",
          talent = { tab = 3, tier = 5, column = 2, base = 5 } },
    },
})   -- Druid

-- ---- milestone 525 ---------------------------------------------------------
ClassReward(525, 1, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_525", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Warrior: Slam R9, Cleave R9, Rend R11, Battle Shout R10, Demoralizing Shout R9
ClassReward(525, 3, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_525", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Hunter: Mongoose Bite R7, Volley R7, Black Arrow R7, Mend Pet R11, Counterattack R7
ClassReward(525, 4, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_525", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Rogue: Hemorrhage R6, Garrote R11, Deadly Throw R4, Slice and Dice R3, Feint R9
ClassReward(525, 5, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_525", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Priest: Mind Flay R10, Smite R13, Holy Fire R12, Devouring Plague R10, Prayer of Healing R8
ClassReward(525, 6, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_525", amount = 4,
                  label = "New ranks for four more spells can be learned at your class trainer" } },
})   -- Death Knight: Blood Strike R7, Death Coil R7, Blood Boil R5, Death and Decay R5
ClassReward(525, 7, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_525", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Shaman: Lesser Healing Wave R10, Lightning Shield R12, Frost Shock R8, Earth Shield R6, Stoneclaw Totem R11
ClassReward(525, 8, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_525", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Mage: Frostfire Bolt R3, Ice Lance R4, Cone of Cold R9, Blizzard R10, Flamestrike R10
ClassReward(525, 9, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_525", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Warlock: Haunt R5, Chaos Bolt R5, Searing Pain R11, Shadowburn R11, Drain Life R10
ClassReward(525, 11, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_525", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Druid: Shred R10, Mangle (Cat) R6, Mangle (Bear) R6, Maul R11, Rip R10

-- ---- milestone 625 ---------------------------------------------------------
ClassReward(625, 1, {
    title = "Anticipation",
    icon = "Interface/Icons/Spell_Nature_MirrorImage",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 3,
          label = "Maximum rank of the Anticipation talent increased by 3",
          talent = { tab = 3, tier = 2, column = 3, base = 5 } },
    },
})   -- Warrior
ClassReward(625, 3, {
    title = "Lightning Reflexes",
    icon = "Interface/Icons/Spell_Nature_Invisibilty",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 3,
          label = "Maximum rank of the Lightning Reflexes talent increased by 3",
          talent = { tab = 3, tier = 6, column = 1, base = 5 } },
    },
})   -- Hunter
ClassReward(625, 4, {
    title = "Lightning Reflexes",
    icon = "Interface/Icons/Spell_Nature_Invisibilty",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 3,
          label = "Maximum rank of the Lightning Reflexes talent increased by 3",
          talent = { tab = 2, tier = 4, column = 3, base = 3 } },
    },
})   -- Rogue
ClassReward(625, 5, {
    title = "Blessed Resilience",
    icon = "Interface/Icons/Spell_Holy_BlessedResillience",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 3,
          label = "Maximum rank of the Blessed Resilience talent increased by 3",
          talent = { tab = 2, tier = 7, column = 3, base = 3 } },
    },
})   -- Priest
ClassReward(625, 6, {
    title = "Spell Deflection",
    icon = "Interface/Icons/Spell_DeathKnight_SpellDeflection",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 3,
          label = "Maximum rank of the Spell Deflection talent increased by 3",
          talent = { tab = 1, tier = 4, column = 3, base = 3 } },
    },
})   -- Death Knight
ClassReward(625, 7, {
    title = "Nature's Guardian",
    icon = "Interface/Icons/Spell_Nature_NatureGuardian",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 3,
          label = "Maximum rank of the Nature's Guardian talent increased by 3",
          talent = { tab = 3, tier = 7, column = 1, base = 5 } },
    },
})   -- Shaman
ClassReward(625, 8, {
    title = "Prismatic Cloak",
    icon = "Interface/Icons/Spell_Arcane_PrismaticCloak",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 3,
          label = "Maximum rank of the Prismatic Cloak talent increased by 3",
          talent = { tab = 1, tier = 6, column = 1, base = 3 } },
    },
})   -- Mage
ClassReward(625, 9, {
    title = "Soul Leech",
    icon = "Interface/Icons/Spell_Shadow_SoulLeech_3",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 3,
          label = "Maximum rank of the Soul Leech talent increased by 3",
          talent = { tab = 3, tier = 7, column = 3, base = 3 } },
    },
})   -- Warlock
ClassReward(625, 11, {
    title = "Feral Swiftness",
    icon = "Interface/Icons/Spell_Nature_SpiritWolf",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 3,
          label = "Maximum rank of the Feral Swiftness talent increased by 3",
          talent = { tab = 2, tier = 3, column = 1, base = 2 } },
    },
})   -- Druid

-- ---- milestone 725 ---------------------------------------------------------
ClassReward(725, 1, {
    title = "Precision",
    icon = "Interface/Icons/Ability_Marksmanship",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Precision talent increased by 2",
          talent = { tab = 2, tier = 5, column = 1, base = 3 } },
    },
})   -- Warrior
ClassReward(725, 3, {
    title = "Mortal Shots",
    icon = "Interface/Icons/Ability_PierceDamage",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Mortal Shots talent increased by 2",
          talent = { tab = 2, tier = 2, column = 3, base = 5 } },
    },
})   -- Hunter
ClassReward(725, 4, {
    title = "Lethality",
    icon = "Interface/Icons/Ability_CriticalStrike",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Lethality talent increased by 2",
          talent = { tab = 1, tier = 3, column = 3, base = 5 } },
    },
})   -- Rogue
ClassReward(725, 5, {
    title = "Darkness",
    icon = "Interface/Icons/Spell_Shadow_Twilight",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Darkness talent increased by 2",
          talent = { tab = 3, tier = 1, column = 3, base = 5 } },
    },
})   -- Priest
ClassReward(725, 6, {
    title = "Necrosis",
    icon = "Interface/Icons/INV_Weapon_Shortblade_60",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Necrosis talent increased by 2",
          talent = { tab = 3, tier = 3, column = 2, base = 5 } },
    },
})   -- Death Knight
ClassReward(725, 7, {
    title = "Concussion",
    icon = "Interface/Icons/Spell_Fire_Fireball",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Concussion talent increased by 2",
          talent = { tab = 1, tier = 1, column = 3, base = 5 } },
    },
})   -- Shaman
ClassReward(725, 8, {
    title = "Fire Power",
    icon = "Interface/Icons/Spell_Fire_Immolation",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Fire Power talent increased by 2",
          talent = { tab = 2, tier = 6, column = 3, base = 5 } },
    },
})   -- Mage
ClassReward(725, 9, {
    title = "Shadow Mastery",
    icon = "Interface/Icons/Spell_Shadow_ShadeTrueSight",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Shadow Mastery talent increased by 2",
          talent = { tab = 1, tier = 6, column = 2, base = 5 } },
    },
})   -- Warlock
ClassReward(725, 11, {
    title = "Moonfury",
    icon = "Interface/Icons/Spell_Nature_MoonGlow",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Moonfury talent increased by 2",
          talent = { tab = 1, tier = 6, column = 2, base = 3 } },
    },
})   -- Druid

-- ---- milestone 775 ---------------------------------------------------------
ClassReward(775, 1, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_775", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Warrior: Devastate R6, Commanding Shout R4, Charge R4, Heroic Strike R15, Mortal Strike R10
ClassReward(775, 3, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_775", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Hunter: Hunter's Mark R6, Aspect of the Dragonhawk R3, Aspect of the Wild R5, Serpent Sting R14, Arcane Shot R13
ClassReward(775, 4, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_775", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Rogue: Sprint R4, Sinister Strike R14, Eviscerate R14, Backstab R14, Rupture R11
ClassReward(775, 5, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_775", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Priest: Circle of Healing R8, Vampiric Touch R6, Shadow Word: Death R5, Holy Nova R10, Binding Heal R4
ClassReward(775, 6, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_775", amount = 4,
                  label = "New ranks for four more spells can be learned at your class trainer" } },
})   -- Death Knight: Obliterate R6, Death Strike R7, Icy Touch R7, Plague Strike R8
ClassReward(775, 7, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_775", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Shaman: Rockbiter Weapon R5, Ancestral Spirit R8, Lightning Bolt R16, Healing Wave R16, Earth Shock R12
ClassReward(775, 8, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_775", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Mage: Arcane Explosion R11, Ice Barrier R9, Blast Wave R10, Living Bomb R4, Arcane Barrage R4
ClassReward(775, 9, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_775", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Warlock: Soul Fire R7, Rain of Fire R8, Life Tap R9, Seed of Corruption R4, Drain Soul R7
ClassReward(775, 11, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_775", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Druid: Lifebloom R4, Wild Growth R5, Insect Swarm R8, Ferocious Bite R9, Swipe (Bear) R9

-- ---- milestone 825 ---------------------------------------------------------
ClassReward(825, 1, {
    title = "Cruelty",
    icon = "Interface/Icons/Ability_Rogue_Eviscerate",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Cruelty talent increased by 2",
          talent = { tab = 2, tier = 1, column = 3, base = 5 } },
    },
})   -- Warrior
ClassReward(825, 3, {
    title = "Lethal Shots",
    icon = "Interface/Icons/Ability_SearingArrow",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Lethal Shots talent increased by 2",
          talent = { tab = 2, tier = 1, column = 3, base = 5 } },
    },
})   -- Hunter
ClassReward(825, 4, {
    title = "Malice",
    icon = "Interface/Icons/Ability_Racial_BloodRage",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Malice talent increased by 2",
          talent = { tab = 1, tier = 1, column = 3, base = 5 } },
    },
})   -- Rogue
ClassReward(825, 5, {
    title = "Holy Specialization",
    icon = "Interface/Icons/Spell_Holy_SealOfSalvation",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Holy Specialization talent increased by 2",
          talent = { tab = 2, tier = 1, column = 3, base = 5 } },
    },
})   -- Priest
ClassReward(825, 6, {
    title = "Dark Conviction",
    icon = "Interface/Icons/Spell_DeathKnight_DarkConviction",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Dark Conviction talent increased by 2",
          talent = { tab = 1, tier = 3, column = 2, base = 5 } },
    },
})   -- Death Knight
ClassReward(825, 7, {
    title = "Tidal Mastery",
    icon = "Interface/Icons/Spell_Nature_Tranquility",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Tidal Mastery talent increased by 2",
          talent = { tab = 3, tier = 4, column = 3, base = 5 } },
    },
})   -- Shaman
ClassReward(825, 8, {
    title = "Critical Mass",
    icon = "Interface/Icons/Spell_Nature_WispHeal",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Critical Mass talent increased by 2",
          talent = { tab = 2, tier = 5, column = 2, base = 3 } },
    },
})   -- Mage
ClassReward(825, 9, {
    title = "Improved Shadow Bolt",
    icon = "Interface/Icons/Spell_Shadow_ShadowBolt",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Improved Shadow Bolt talent increased by 2",
          talent = { tab = 3, tier = 1, column = 2, base = 5 } },
    },
})   -- Warlock
ClassReward(825, 11, {
    title = "Nature's Majesty",
    icon = "Interface/Icons/INV_Staff_01",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Nature's Majesty talent increased by 2",
          talent = { tab = 1, tier = 2, column = 2, base = 2 } },
    },
})   -- Druid

-- ---- milestone 900 ---------------------------------------------------------
ClassReward(900, 1, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_900", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Warrior: Shield Slam R10, Execute R11, Revenge R11, Thunder Clap R11, Slam R10
ClassReward(900, 3, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_900", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Hunter: Steady Shot R6, Multi-Shot R10, Aspect of the Hawk R10, Raptor Strike R13, Mongoose Bite R8
ClassReward(900, 4, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_900", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Rogue: Ambush R12, Envenom R6, Hemorrhage R7, Garrote R12, Deadly Throw R5
ClassReward(900, 5, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_900", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Priest: Mind Blast R15, Shadow Word: Pain R14, Flash Heal R13, Greater Heal R11, Renew R16
ClassReward(900, 6, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_900", amount = 4,
                  label = "New ranks for four more spells can be learned at your class trainer" } },
})   -- Death Knight: Blood Strike R8, Death Coil R8, Blood Boil R6, Death and Decay R6
ClassReward(900, 7, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_900", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Shaman: Chain Lightning R10, Flame Shock R11, Chain Heal R9, Lesser Healing Wave R11, Lightning Shield R13
ClassReward(900, 8, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_900", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Mage: Frostbolt R18, Fireball R18, Arcane Blast R6, Pyroblast R14, Scorch R13
ClassReward(900, 9, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_900", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Warlock: Shadow Bolt R15, Corruption R12, Immolate R13, Incinerate R6, Curse of Agony R11
ClassReward(900, 11, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_900", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Druid: Wrath R14, Starfire R12, Moonfire R16, Healing Touch R17, Rejuvenation R17

-- ---- milestone 1025 --------------------------------------------------------
ClassReward(1025, 1, {
    title = "Toughness",
    icon = "Interface/Icons/Spell_Holy_Devotion",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Toughness talent increased by 4",
          talent = { tab = 3, tier = 3, column = 4, base = 5 } },
    },
})   -- Warrior
ClassReward(1025, 3, {
    title = "Thick Hide",
    icon = "Interface/Icons/INV_Misc_Pelt_Bear_03",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Thick Hide talent increased by 4",
          talent = { tab = 1, tier = 2, column = 3, base = 3 } },
    },
})   -- Hunter
ClassReward(1025, 4, {
    title = "Deadened Nerves",
    icon = "Interface/Icons/Ability_Rogue_DeadenedNerves",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Deadened Nerves talent increased by 4",
          talent = { tab = 1, tier = 7, column = 3, base = 3 } },
    },
})   -- Rogue
ClassReward(1025, 5, {
    title = "Spell Warding",
    icon = "Interface/Icons/Spell_Holy_SpellWarding",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Spell Warding talent increased by 4",
          talent = { tab = 2, tier = 2, column = 2, base = 5 } },
    },
})   -- Priest
ClassReward(1025, 6, {
    title = "Toughness",
    icon = "Interface/Icons/Spell_Holy_Devotion",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Toughness talent increased by 4",
          talent = { tab = 2, tier = 1, column = 3, base = 5 } },
    },
})   -- Death Knight
ClassReward(1025, 7, {
    title = "Elemental Warding",
    icon = "Interface/Icons/Spell_Nature_SpiritArmor",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Elemental Warding talent increased by 4",
          talent = { tab = 1, tier = 2, column = 2, base = 3 } },
    },
})   -- Shaman
ClassReward(1025, 8, {
    title = "Frost Warding",
    icon = "Interface/Icons/Spell_Frost_FrostWard",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Frost Warding talent increased by 4",
          talent = { tab = 3, tier = 2, column = 2, base = 2 } },
    },
})   -- Mage
ClassReward(1025, 9, {
    title = "Molten Skin",
    icon = "Interface/Icons/Ability_Mage_MoltenArmor",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Molten Skin talent increased by 4",
          talent = { tab = 3, tier = 2, column = 2, base = 3 } },
    },
})   -- Warlock
ClassReward(1025, 11, {
    title = "Thick Hide",
    icon = "Interface/Icons/INV_Misc_Pelt_Bear_03",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Thick Hide talent increased by 4",
          talent = { tab = 2, tier = 2, column = 3, base = 3 } },
    },
})   -- Druid

-- ---- milestone 1125 --------------------------------------------------------
ClassReward(1125, 1, {
    title = "Iron Will",
    icon = "Interface/Icons/Spell_Magic_MageArmor",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Iron Will talent increased by 2",
          talent = { tab = 1, tier = 2, column = 2, base = 3 } },
    },
})   -- Warrior
ClassReward(1125, 3, {
    title = "Surefooted",
    icon = "Interface/Icons/Ability_Kick",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Surefooted talent increased by 2",
          talent = { tab = 3, tier = 2, column = 1, base = 3 } },
    },
})   -- Hunter
ClassReward(1125, 4, {
    title = "Nerves of Steel",
    icon = "Interface/Icons/Ability_Rogue_NervesOfSteel",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Nerves of Steel talent increased by 2",
          talent = { tab = 2, tier = 7, column = 3, base = 2 } },
    },
})   -- Rogue
ClassReward(1125, 5, {
    title = "Unbreakable Will",
    icon = "Interface/Icons/Spell_Magic_MageArmor",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Unbreakable Will talent increased by 2",
          talent = { tab = 1, tier = 1, column = 2, base = 5 } },
    },
})   -- Priest
ClassReward(1125, 6, {
    title = "Frigid Dreadplate",
    icon = "Interface/Icons/INV_CHEST_MAIL_04",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Frigid Dreadplate talent increased by 2",
          talent = { tab = 2, tier = 5, column = 2, base = 3 } },
    },
})   -- Death Knight
ClassReward(1125, 7, {
    title = "Focused Mind",
    icon = "Interface/Icons/Spell_Nature_FocusedMind",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Focused Mind talent increased by 2",
          talent = { tab = 3, tier = 5, column = 4, base = 3 } },
    },
})   -- Shaman
ClassReward(1125, 8, {
    title = "Burning Determination",
    icon = "Interface/Icons/Spell_Fire_TotemOfWrath",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Burning Determination talent increased by 2",
          talent = { tab = 2, tier = 2, column = 2, base = 2 } },
    },
})   -- Mage
ClassReward(1125, 9, {
    title = "Demonic Resilience",
    icon = "Interface/Icons/Spell_Shadow_DemonicFortitude",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Demonic Resilience talent increased by 2",
          talent = { tab = 2, tier = 7, column = 1, base = 3 } },
    },
})   -- Warlock
ClassReward(1125, 11, {
    title = "Primal Tenacity",
    icon = "Interface/Icons/Ability_Druid_PrimalTenacity",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Primal Tenacity talent increased by 2",
          talent = { tab = 2, tier = 7, column = 4, base = 3 } },
    },
})   -- Druid

-- ---- milestone 1175 --------------------------------------------------------
ClassReward(1175, 1, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1175", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Warrior: Cleave R10, Rend R12, Battle Shout R11, Demoralizing Shout R10, Devastate R7
ClassReward(1175, 3, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1175", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Hunter: Volley R8, Black Arrow R8, Mend Pet R12, Counterattack R8, Hunter's Mark R7
ClassReward(1175, 4, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1175", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Rogue: Slice and Dice R4, Feint R10, Sprint R5, Sinister Strike R15, Eviscerate R15
ClassReward(1175, 5, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1175", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Priest: Power Word: Shield R16, Mind Flay R11, Smite R14, Holy Fire R13, Devouring Plague R11
ClassReward(1175, 6, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1175", amount = 4,
                  label = "New ranks for four more spells can be learned at your class trainer" } },
})   -- Death Knight: Obliterate R7, Death Strike R8, Icy Touch R8, Plague Strike R9
ClassReward(1175, 7, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1175", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Shaman: Frost Shock R9, Earth Shield R7, Stoneclaw Totem R12, Rockbiter Weapon R6, Ancestral Spirit R9
ClassReward(1175, 8, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1175", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Mage: Fire Blast R13, Frostfire Bolt R4, Ice Lance R5, Cone of Cold R10, Blizzard R11
ClassReward(1175, 9, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1175", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Warlock: Unstable Affliction R7, Haunt R6, Chaos Bolt R6, Searing Pain R12, Shadowburn R12
ClassReward(1175, 11, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1175", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Druid: Regrowth R14, Shred R11, Mangle (Cat) R7, Mangle (Bear) R7, Maul R12

-- ---- milestone 1225 --------------------------------------------------------
ClassReward(1225, 1, {
    icon = "Interface/Icons/Spell_Nature_FocusedMind",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Commanding Presence talent increased by 2",
          talent = { tab = 2, tier = 3, column = 4, base = 5 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Booming Voice talent increased by 2",
          talent = { tab = 2, tier = 1, column = 2, base = 2 } },
    },
})   -- Warrior
ClassReward(1225, 3, {
    icon = "Interface/Icons/Ability_Hunter_FerociousInspiration",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Ferocious Inspiration talent increased by 2",
          talent = { tab = 1, tier = 7, column = 1, base = 3 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Improved Steady Shot talent increased by 2",
          talent = { tab = 2, tier = 9, column = 3, base = 3 } },
    },
})   -- Hunter
ClassReward(1225, 4, {
    icon = "Interface/Icons/Ability_Warrior_Riposte",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Improved Expose Armor talent increased by 2",
          talent = { tab = 1, tier = 3, column = 2, base = 2 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Master Poisoner talent increased by 2",
          talent = { tab = 1, tier = 9, column = 1, base = 3 } },
    },
})   -- Rogue
ClassReward(1225, 5, {
    icon = "Interface/Icons/Spell_Holy_WordFortitude",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Improved Power Word: Fortitude talent increased by 2",
          talent = { tab = 1, tier = 2, column = 3, base = 2 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Divine Providence talent increased by 2",
          talent = { tab = 2, tier = 10, column = 2, base = 5 } },
    },
})   -- Priest
ClassReward(1225, 6, {
    icon = "Interface/Icons/Ability_Warrior_IntensifyRage",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Abomination's Might talent increased by 2",
          talent = { tab = 1, tier = 6, column = 3, base = 2 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Virulence talent increased by 2",
          talent = { tab = 3, tier = 1, column = 2, base = 3 } },
    },
})   -- Death Knight
ClassReward(1225, 7, {
    icon = "Interface/Icons/Spell_Nature_UnleashedRage",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Unleashed Rage talent increased by 2",
          talent = { tab = 2, tier = 6, column = 1, base = 3 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Improved Windfury Totem talent increased by 2",
          talent = { tab = 2, tier = 5, column = 1, base = 2 } },
    },
})   -- Shaman
ClassReward(1225, 8, {
    icon = "Interface/Icons/Spell_Nature_StarFall",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Arcane Empowerment talent increased by 2",
          talent = { tab = 1, tier = 7, column = 1, base = 3 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Netherwind Presence talent increased by 2",
          talent = { tab = 1, tier = 10, column = 2, base = 3 } },
    },
})   -- Mage
ClassReward(1225, 9, {
    icon = "Interface/Icons/Spell_Shadow_DemonicPact",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Demonic Pact talent increased by 2",
          talent = { tab = 2, tier = 10, column = 2, base = 5 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Malediction talent increased by 2",
          talent = { tab = 1, tier = 8, column = 3, base = 3 } },
    },
})   -- Warlock
ClassReward(1225, 11, {
    icon = "Interface/Icons/Spell_Nature_Regeneration",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Improved Mark of the Wild talent increased by 2",
          talent = { tab = 3, tier = 1, column = 1, base = 2 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Improved Leader of the Pack talent increased by 2",
          talent = { tab = 2, tier = 7, column = 3, base = 2 } },
    },
})   -- Druid

-- ---- milestone 1375 --------------------------------------------------------
ClassReward(1375, 1, {
    icon = "Interface/Icons/INV_Axe_09",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Two-Handed Weapon Specialization talent increased by 2",
          talent = { tab = 1, tier = 4, column = 2, base = 3 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the One-Handed Weapon Specialization talent increased by 2",
          talent = { tab = 3, tier = 6, column = 3, base = 5 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Impale talent increased by 2",
          talent = { tab = 1, tier = 3, column = 3, base = 2 } },
    },
})   -- Warrior
ClassReward(1375, 3, {
    icon = "Interface/Icons/INV_Weapon_Rifle_06",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Ranged Weapon Specialization talent increased by 2",
          talent = { tab = 2, tier = 6, column = 4, base = 3 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Savage Strikes talent increased by 2",
          talent = { tab = 3, tier = 1, column = 3, base = 2 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Improved Aspect of the Hawk talent increased by 2",
          talent = { tab = 1, tier = 1, column = 2, base = 5 } },
    },
})   -- Hunter
ClassReward(1375, 4, {
    icon = "Interface/Icons/Ability_DualWield",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Dual Wield Specialization talent increased by 2",
          talent = { tab = 2, tier = 1, column = 3, base = 5 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Mace Specialization talent increased by 2",
          talent = { tab = 2, tier = 5, column = 1, base = 5 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Close Quarters Combat talent increased by 2",
          talent = { tab = 2, tier = 3, column = 3, base = 5 } },
    },
})   -- Rogue
ClassReward(1375, 5, {
    icon = "Interface/Icons/Spell_Shadow_ShadowPower",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Shadow Power talent increased by 2",
          talent = { tab = 3, tier = 7, column = 3, base = 5 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Divine Fury talent increased by 2",
          talent = { tab = 2, tier = 2, column = 3, base = 5 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Improved Flash Heal talent increased by 2",
          talent = { tab = 1, tier = 7, column = 3, base = 3 } },
    },
})   -- Priest
ClassReward(1375, 6, {
    icon = "Interface/Icons/INV_Sword_68",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Two-Handed Weapon Specialization talent increased by 2",
          talent = { tab = 1, tier = 2, column = 3, base = 2 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Nerves of Cold Steel talent increased by 2",
          talent = { tab = 2, tier = 2, column = 4, base = 3 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Vicious Strikes talent increased by 2",
          talent = { tab = 3, tier = 1, column = 1, base = 2 } },
    },
})   -- Death Knight
ClassReward(1375, 7, {
    icon = "Interface/Icons/Ability_Hunter_SwiftStrike",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Weapon Mastery talent increased by 2",
          talent = { tab = 2, tier = 6, column = 3, base = 3 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Dual Wield Specialization talent increased by 2",
          talent = { tab = 2, tier = 7, column = 1, base = 3 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Elemental Weapons talent increased by 2",
          talent = { tab = 2, tier = 3, column = 1, base = 3 } },
    },
})   -- Shaman
ClassReward(1375, 8, {
    icon = "Interface/Icons/Spell_Fire_FlameBolt",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Improved Fireball talent increased by 2",
          talent = { tab = 2, tier = 1, column = 3, base = 5 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Improved Frostbolt talent increased by 2",
          talent = { tab = 3, tier = 1, column = 2, base = 5 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Piercing Ice talent increased by 2",
          talent = { tab = 3, tier = 3, column = 1, base = 3 } },
    },
})   -- Mage
ClassReward(1375, 9, {
    icon = "Interface/Icons/Spell_Shadow_DeathPact",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Bane talent increased by 2",
          talent = { tab = 3, tier = 1, column = 3, base = 5 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Emberstorm talent increased by 2",
          talent = { tab = 3, tier = 6, column = 3, base = 5 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Improved Corruption talent increased by 2",
          talent = { tab = 1, tier = 1, column = 3, base = 5 } },
    },
})   -- Warlock
ClassReward(1375, 11, {
    icon = "Interface/Icons/Ability_Hunter_Pet_Hyena",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Ferocity talent increased by 2",
          talent = { tab = 2, tier = 1, column = 2, base = 5 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Feral Aggression talent increased by 2",
          talent = { tab = 2, tier = 1, column = 3, base = 5 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 2,
          label = "Maximum rank of the Savage Fury talent increased by 2",
          talent = { tab = 2, tier = 2, column = 2, base = 2 } },
    },
})   -- Druid

-- ---- milestone 1425 --------------------------------------------------------
ClassReward(1425, 1, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1425", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Warrior: Commanding Shout R5, Charge R5, Heroic Strike R16, Mortal Strike R11, Shield Slam R11
ClassReward(1425, 3, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1425", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Hunter: Aspect of the Dragonhawk R4, Aspect of the Wild R6, Serpent Sting R15, Arcane Shot R14, Steady Shot R7
ClassReward(1425, 4, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1425", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Rogue: Backstab R15, Rupture R12, Ambush R13, Envenom R7, Hemorrhage R8
ClassReward(1425, 5, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1425", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Priest: Prayer of Healing R9, Circle of Healing R9, Vampiric Touch R7, Shadow Word: Death R6, Holy Nova R11
ClassReward(1425, 6, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1425", amount = 4,
                  label = "New ranks for four more spells can be learned at your class trainer" } },
})   -- Death Knight: Blood Strike R9, Death Coil R9, Blood Boil R7, Death and Decay R7
ClassReward(1425, 7, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1425", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Shaman: Lightning Bolt R17, Healing Wave R17, Earth Shock R13, Chain Lightning R11, Flame Shock R12
ClassReward(1425, 8, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1425", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Mage: Flamestrike R11, Arcane Explosion R12, Ice Barrier R10, Blast Wave R11, Living Bomb R5
ClassReward(1425, 9, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1425", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Warlock: Drain Life R11, Soul Fire R8, Rain of Fire R9, Life Tap R10, Seed of Corruption R5
ClassReward(1425, 11, {
    rewards = { { type = "SPECIAL", value = "TRAINER_RANKS_1425", amount = 5,
                  label = "New ranks for five more spells can be learned at your class trainer" } },
})   -- Druid: Rip R11, Lifebloom R5, Wild Growth R6, Insect Swarm R9, Ferocious Bite R10

-- ---- milestone 1475 --------------------------------------------------------
ClassReward(1475, 1, {
    icon = "Interface/Icons/Ability_Warrior_OffensiveStance",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 1,
          label = "Maximum rank of the Strength of Arms talent increased by 1",
          talent = { tab = 1, tier = 7, column = 3, base = 2 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Improved Berserker Stance talent increased by 4",
          talent = { tab = 2, tier = 8, column = 4, base = 5 } },
    },
})   -- Warrior
ClassReward(1475, 3, {
    icon = "Interface/Icons/Ability_Hunter_ZenArchery",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 1,
          label = "Maximum rank of the Careful Aim talent increased by 1",
          talent = { tab = 2, tier = 2, column = 1, base = 3 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Hunter vs. Wild talent increased by 4",
          talent = { tab = 3, tier = 5, column = 1, base = 3 } },
    },
})   -- Hunter
ClassReward(1475, 4, {
    icon = "Interface/Icons/Ability_Rogue_FindWeakness",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 1,
          label = "Maximum rank of the Find Weakness talent increased by 1",
          talent = { tab = 1, tier = 8, column = 3, base = 3 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Sinister Calling talent increased by 4",
          talent = { tab = 3, tier = 8, column = 2, base = 5 } },
    },
})   -- Rogue
ClassReward(1475, 5, {
    icon = "Interface/Icons/Spell_Nature_EnchantArmor",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 1,
          label = "Maximum rank of the Mental Strength talent increased by 1",
          talent = { tab = 1, tier = 5, column = 2, base = 5 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Spiritual Guidance talent increased by 4",
          talent = { tab = 2, tier = 5, column = 3, base = 5 } },
    },
})   -- Priest
ClassReward(1475, 6, {
    icon = "Interface/Icons/Spell_DeathKnight_Gnaw_Ghoul",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 1,
          label = "Maximum rank of the Ravenous Dead talent increased by 1",
          talent = { tab = 3, tier = 2, column = 4, base = 3 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Impurity talent increased by 4",
          talent = { tab = 3, tier = 5, column = 2, base = 5 } },
    },
})   -- Death Knight
ClassReward(1475, 7, {
    icon = "Interface/Icons/Spell_Nature_NatureBlessing",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 1,
          label = "Maximum rank of the Nature's Blessing talent increased by 1",
          talent = { tab = 3, tier = 8, column = 3, base = 3 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Mental Quickness talent increased by 4",
          talent = { tab = 2, tier = 9, column = 1, base = 3 } },
    },
})   -- Shaman
ClassReward(1475, 8, {
    icon = "Interface/Icons/Spell_Arcane_ArcaneTorrent",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 1,
          label = "Maximum rank of the Spell Power talent increased by 1",
          talent = { tab = 1, tier = 10, column = 3, base = 2 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Mind Mastery talent increased by 4",
          talent = { tab = 1, tier = 8, column = 3, base = 5 } },
    },
})   -- Mage
ClassReward(1475, 9, {
    icon = "Interface/Icons/Spell_Shadow_ShadowWordDominate",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 1,
          label = "Maximum rank of the Unholy Power talent increased by 1",
          talent = { tab = 2, tier = 4, column = 2, base = 5 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Demonic Knowledge talent increased by 4",
          talent = { tab = 2, tier = 7, column = 3, base = 3 } },
    },
})   -- Warlock
ClassReward(1475, 11, {
    icon = "Interface/Icons/Ability_Druid_BalanceofPower",
    rewards = {
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 1,
          label = "Maximum rank of the Balance of Power talent increased by 1",
          talent = { tab = 1, tier = 6, column = 3, base = 2 } },
        { type = "SPECIAL", value = "TALENT_MASTERY", amount = 4,
          label = "Maximum rank of the Lunar Guidance talent increased by 4",
          talent = { tab = 1, tier = 5, column = 1, base = 3 } },
    },
})   -- Druid

-- ---- milestones with no class reward yet ---------------------------------
--
-- The Paladin reward at each of these is a bespoke mechanic -- a custom
-- spell, a core patch, a brand-new talent -- not something that
-- generalises by picking a different talent or spell rank. Rather than
-- hand every other class a consolation stat, the milestone says so.
--
-- Grants nothing on purpose: ApplyReward only acts on UNIT_MODS and
-- COMBAT_RATING, the talent reconciler only matches TALENT_POINTS, and
-- every other SPECIAL handler matches its own value -- so an unknown
-- value is inert everywhere without needing a special case anywhere.
--
-- Plain ASCII <3 rather than a heart: the 3.3.5 UI font (FRIZQT__.TTF
-- in locale-enUS.MPQ) has no glyph for U+2764, and none for the plain
-- card-suit U+2665 either -- both would draw as an empty box.
local NO_REWARD_LEVELS = { 125, 225, 325, 425, 575, 675, 1075, 1325 }

for _, level in ipairs(NO_REWARD_LEVELS) do
    for _, class_id in ipairs({ 1, 3, 4, 5, 6, 7, 8, 9, 11 }) do
        ClassReward(level, class_id, {
            rewards = { { type = "SPECIAL", value = "NO_REWARD", amount = 0,
                          label = "If you want this to have a cool effect, hmu <3 - Tom" } },
        })
    end
end

-- Fold the generated entries into the milestone tables declared above.
-- Kept as a data table plus one loop rather than 153 assignments so the
-- shape stays visible and a regenerated block is a clean diff.
local MILESTONE_TABLES = {
    [75] = MILESTONE_75,
    [125] = MILESTONE_125,
    [175] = MILESTONE_175,
    [225] = MILESTONE_225,
    [275] = MILESTONE_275,
    [325] = MILESTONE_325,
    [425] = MILESTONE_425,
    [475] = MILESTONE_475,
    [525] = MILESTONE_525,
    [575] = MILESTONE_575,
    [625] = MILESTONE_625,
    [675] = MILESTONE_675,
    [725] = MILESTONE_725,
    [775] = MILESTONE_775,
    [825] = MILESTONE_825,
    [900] = MILESTONE_900,
    [1025] = MILESTONE_1025,
    [1075] = MILESTONE_1075,
    [1125] = MILESTONE_1125,
    [1175] = MILESTONE_1175,
    [1225] = MILESTONE_1225,
    [1325] = MILESTONE_1325,
    [1375] = MILESTONE_1375,
    [1425] = MILESTONE_1425,
    [1475] = MILESTONE_1475,
}

for level, per_class in pairs(CLASS_REWARDS) do
    local tbl = MILESTONE_TABLES[level]
    if not tbl then
        print(string.format(
            "[Paragon] !! class rewards generated for milestone %d, which has no "
            .. "MILESTONE_%d table -- those rewards are silently lost", level, level))
    else
        for class_id, entry in pairs(per_class) do
            tbl[class_id] = entry
        end
    end
end

local TRACK = {
    -- Reordered 2026-08-18 (user design pass): recurring reward types are
    -- spread out, class-specific and universal milestones alternate, a
    -- QoL reward lands roughly every fourth slot, and every collection-
    -- scaling ladder sits at a century mark of the first half (100
    -- mounts, 200 companions, 300 achievements, 400 quests, 500
    -- transmog). MILESTONE_* tables are named by their CURRENT level.
    { level =  25, title = "Awakened Potential", icon = "Interface/Icons/INV_Misc_Book_09",             rewards = { { type = "SPECIAL",       value = "TALENT_POINTS",  amount = 5                     } } },
    { level =  50, title = "Wanderer's Haste", icon = "Interface/Icons/Ability_Rogue_Sprint",         rewards = { { type = "SPECIAL",       value = "OOC_MOVE_SPEED", amount = 50, label = "50% increased movement speed while out of combat" } } },
    { level =  75, title = "Foundations of Power", icon = "Interface/Icons/Spell_Holy_MagicalSentry",     class_rewards = MILESTONE_75 },
    -- Milestone 100: universal — additive paragon XP per collected mount
    -- (+1%), account-wide via mod-collections' spellbook sync. Whole
    -- lifecycle in paragon_collection_xp.lua; informational entry only.
    { level = 100, title = "The Growing Stable", icon = "Interface/Icons/Ability_Mount_Drake_Proto",     rewards = { { type = "SPECIAL",       value = "MOUNT_XP",       amount = 1, label = "Paragon Experience gained increased by 1% for every mount you have collected" } } },
    { level = 125, title = "Scorched Earth", icon = "Interface/Icons/Spell_Holy_HolyNova",          class_rewards = MILESTONE_125 },
    { level = 150, title = "Swift Currents", icon = "Interface/Icons/Ability_Druid_AquaticForm",    rewards = { { type = "SPECIAL",       value = "SWIM_SPEED",     amount = 100, label = "100% increased swim speed" } } },
    { level = 175, title = "Beyond Mastery I", icon = "Interface/Icons/Ability_Warrior_BattleShout",  class_rewards = MILESTONE_175 },
    -- Milestone 200: universal — additive paragon XP per collected
    -- companion (+0.5%); twin of milestone 100, same module
    -- (paragon_collection_xp.lua). Informational entry only.
    { level = 200, title = "The Menagerie", icon = "Interface/Icons/INV_Box_PetCarrier_01",         rewards = { { type = "SPECIAL",       value = "COMPANION_XP",   amount = 1, label = "Paragon Experience gained increased by 0.5% for every companion you have collected" } } },
    { level = 225, title = "Twofold Focus", icon = "Interface/Icons/Ability_CriticalStrike",       class_rewards = MILESTONE_225 },
    { level = 250, title = "Quick to the Saddle", icon = "Interface/Icons/Ability_Mount_RidingHorse",    rewards = { { type = "SPECIAL",       value = "QUICK_MOUNT",    amount = 1, label = "Mount casting time reduced by 1 second" } } },
    { level = 275, title = "Practiced Discipline", icon = "Interface/Icons/Spell_Frost_WindWalkOn",       class_rewards = MILESTONE_275 },
    -- Milestone 300: universal — the achievement ladder: 11 bonuses
    -- unlocked every 100 total achievements, each scaling with the
    -- achievements earned past its own threshold (XP tiers at 0/1000,
    -- stats between). Whole lifecycle in paragon_achievement_bonus.lua;
    -- the XP tiers join paragon_collection_xp.lua's single modifier.
    -- This entry is informational for the track UI/broadcast only.
    { level = 300, title = "Deeds Rewarded", icon = "Interface/Icons/Achievement_General",           rewards = { { type = "SPECIAL",       value = "ACHIEVEMENT_BONUS", amount = 1, label = "Gain bonuses that scale with the number of Achievements you have earned" } } },
    { level = 325, title = "Bold Advance", icon = "Interface/Icons/Ability_HeroicLeap",            class_rewards = MILESTONE_325 },
    -- Milestone 350: universal — a seventh, major-only glyph slot drawn in the
    -- center of the glyph flower. Fully handled by paragon_glyph_slot.lua
    -- (server) + Paragon_GlyphSlot.lua (client); the SPECIAL value here is
    -- informational for the track UI/broadcast only.
    { level = 350, title = "The Seventh Sigil", icon = "Interface/Icons/INV_Inscription_Tradeskill01",  rewards = { { type = "SPECIAL",       value = "CUSTOM_GLYPH_SLOT", amount = 1, label = "A fourth Major Glyph slot, in the center of your glyph panel" } } },
    -- Milestone 375: UNIVERSAL since 2026-08-20. Every class has an average
    -- item level, so this never had cause to be class-specific; the per-class
    -- weighting (primary/Stamina/secondary) lives in paragon_ilvl_bonus.lua
    -- CLASS_WEIGHTS, which is also where the whole lifecycle sits. This entry
    -- is informational for the track UI/broadcast only -- the client appends
    -- the live per-stat numbers from the ParagonIlvl payload underneath it,
    -- which is why the label names no stats.
    { level = 375, title = "Attuned Steel", icon = "Interface/Icons/Spell_Nature_HealingTouch",    rewards = { { type = "SPECIAL", value = "ILVL_ATTUNEMENT", amount = 1, label = "Gain attributes scaling with your average item level" } } },
    -- Milestone 400: universal — Loremaster's Ledger: nine bonuses keyed
    -- to the character's rewarded-quest count (per-character by design),
    -- each scaling past its own threshold. Whole lifecycle in
    -- paragon_quest_bonus.lua; the XP tier joins the single modifier in
    -- paragon_collection_xp.lua. Informational entry for the UI only.
    { level = 400, title = "Loremaster's Ledger", icon = "Interface/Icons/INV_Misc_Book_11",              rewards = { { type = "SPECIAL",       value = "QUEST_BONUS",    amount = 1, label = "Gain bonuses that scale with the number of quests you have completed" } } },
    { level = 425, title = "Lightened Load", icon = "Interface/Icons/Spell_Magic_GreaterBlessingofKings", class_rewards = MILESTONE_425 },
    -- Milestone 450: universal — +1 bonus talent point per full 100 paragon
    -- levels, forever. amount = points per 100. No new plumbing: the owed
    -- total is derived from the LIVE level in OwedBonusTalents, and
    -- OnParagonLevelChanged already reconciles on every level change, so
    -- each 100-threshold crossing lands automatically (the reconcile is
    -- idempotent and writes the pool + free points straight to the client).
    { level = 450, title = "Endless Growth", icon = "Interface/Icons/INV_Misc_Book_09",              rewards = { { type = "SPECIAL",       value = "TALENT_POINTS_PER_100", amount = 1, label = "Gain 1 additional Talent Point for every 100 Paragon levels" } } },
    { level = 475, title = "Steady Hands", icon = "Interface/Icons/Spell_Holy_BlindingHeal",       class_rewards = MILESTONE_475 },
    -- Milestone 500: universal — Collector's Wardrobe: nine bonuses keyed
    -- to the ACCOUNT-WIDE transmog appearance count (mod-transmog
    -- collection system, thresholds 0..15000), each scaling past its own
    -- threshold. Whole lifecycle in paragon_transmog_bonus.lua; the XP
    -- tier joins paragon_collection_xp.lua's single modifier.
    -- Informational entry for the track UI/broadcast only.
    { level = 500, title = "Collector's Wardrobe", icon = "Interface/Icons/INV_Shirt_Purple_01",           rewards = { { type = "SPECIAL",       value = "TRANSMOG_BONUS", amount = 1, label = "Gain bonuses that scale with the number of Transmog appearances you have collected" } } },
    { level = 525, title = "Beyond Mastery II", icon = "Interface/Icons/Spell_Holy_SearingLight",       class_rewards = MILESTONE_525 },
    -- Milestone 550: universal — a second +5 talent points, identical to
    -- milestone 25. OwedBonusTalents SUMS every TALENT_POINTS reward on
    -- the track, so this is pure data: the reconcile lands 5 more points
    -- (idempotently) the moment the threshold is crossed.
    { level = 550, title = "Widened Horizons", icon = "Interface/Icons/INV_Misc_Book_07",              rewards = { { type = "SPECIAL",       value = "TALENT_POINTS",  amount = 5                     } } },
    { level = 575, title = "Wider Reach", icon = "Interface/Icons/Spell_Holy_AvengersShield",     class_rewards = MILESTONE_575 },
    -- Milestone 600: universal — 3x regen from the same Spirit, via the
    -- visible passive 1900036 (aura 110 mana / aura 88 health, +200% each;
    -- the core multiplies exactly the spirit-derived regen terms by those
    -- auras — MP5/food/drinks are unaffected). Pure spell data, no core
    -- change; spirit-tooltip correction in Paragon_SpiritTooltip.lua.
    { level = 600, title = "Wellspring of Spirit", icon = "Interface/Icons/Spell_Holy_DivineSpirit",       rewards = { { type = "SPECIAL",       value = "SPIRIT_REGEN",   amount = 1, label = "Spirit now grants more Mana and Health Regeneration" } } },
    { level = 625, title = "Sharpened Instincts", icon = "Interface/Icons/Spell_Magic_LesserInvisibilty", class_rewards = MILESTONE_625 },
    -- Milestone 650: universal — a second permanent weapon enchant. The core
    -- mechanism (SpellEffects.cpp, Paragon Core Patches.md §1e) is generic and
    -- marker-driven: marker 1900009's own equipped-item masks (spell_dbc)
    -- define eligible items, and each further marker id (1900010+) would add
    -- one more slot — future expansion is data + a LEARNED_SPELL_SPECIALS
    -- line, no rebuild. This side only teaches/unteaches the marker.
    { level = 650, title = "Twice-Tempered Blade", icon = "Interface/Icons/Trade_Engraving",               rewards = { { type = "SPECIAL",       value = "DUAL_ENCHANT",   amount = 2, label = "You can put two enchantments on your weapon" } } },
    { level = 675, title = "Doubled Boon", icon = "Interface/Icons/Spell_Holy_GreaterBlessingofKings", class_rewards = MILESTONE_675 },
    -- Milestone 700: universal — every gt-table stat conversion runs at
    -- character level minus marker aura 1900039's bp (2): all combat
    -- ratings uniformly x1.1577 at 80, agi/int->crit ~x1.16, spirit mana
    -- regen x1.109 (multiplies milestone 600's 3x). Core patch §1g reads
    -- the aura; the stat-refresh poke on grant lives in
    -- paragon_scaling_level.lua, the client rating-tooltip correction in
    -- the addon's Paragon_ScalingLevel.lua.
    { level = 700, title = "Timeless Vigor", icon = "Interface/Icons/Achievement_Level_70",          rewards = { { type = "SPECIAL",       value = "SCALING_LEVEL",  amount = 2, label = "Your attributes scale as if you were 2 levels lower than you actually are" } } },
    { level = 725, title = "Refined Technique", icon = "Interface/Icons/Ability_ThunderBolt",           class_rewards = MILESTONE_725 },
    -- Milestone 750: universal — another 0.5s off mount casts, stacking
    -- with milestone 250's 1s: the standard 1500ms mounts become INSTANT
    -- (the §1 core patch floors at 0, a state 67 stock mount spells already
    -- occupy) and castable WHILE MOVING. The moving half needs the client
    -- MPQ: the client pre-blocks moving mount casts on the MOVEMENT bit of
    -- InterruptFlags, stripped by the generator's mount move-cast pass
    -- (client-only; the server keeps stock flags and still gates sub-750
    -- players itself). See the §1 notes in "Paragon Core Patches.md".
    { level = 750, title = "Ride Like the Wind", icon = "Interface/Icons/Ability_Mount_Charger",         rewards = { { type = "SPECIAL",       value = "SWIFT_MOUNT",    amount = 1, label = "Mount casting time reduced by an additional 0.5 seconds" } } },
    { level = 775, title = "Beyond Mastery III", icon = "Interface/Icons/Spell_Holy_InnerFire",          class_rewards = MILESTONE_775 },
    -- Milestone 800: universal — death perks via marker 1900073 (§1i core
    -- patch, two reads of the same passive aura, which survives death
    -- because RemoveAllAurasOnDeath spares passives): (1) the spirit-healer
    -- path's resurrection sickness is waived in Player::ResurrectPlayer
    -- (durability loss stays); (2) while dead, Unit::UpdateSpeed ADDS the
    -- marker's bp (60) on top of ghost form's own +50% run speed — +110%
    -- total, wisp/racial bonuses stack the same way. bp retune = spell_dbc
    -- edit + restart, no rebuild.
    { level = 800, title = "Restless Spirit", icon = "Interface/Icons/Spell_Holy_GuardianSpirit",     rewards = { { type = "SPECIAL",       value = "GHOST_SPRINT",   amount = 60, label = "Resurrecting at a Spirit Healer no longer applies Resurrection Sickness, and your movement speed while dead is increased by 60%" } } },
    { level = 825, title = "Killer Instinct", icon = "Interface/Icons/Spell_Holy_RetributionAura",    class_rewards = MILESTONE_825 },
    -- Milestone 850: universal — the enchant-slot ladder: eleven extra
    -- permanent-enchant slots (chest -> third weapon enchant) unlocked by
    -- average-item-level thresholds. Gate + marker teaching in
    -- paragon_enchant_slots.lua (§1e capacity markers 1900057-66 +
    -- reserved 1900033); display rides the generalized dual-enchant push.
    -- This entry is informational for the track UI/broadcast only.
    { level = 850, title = "Runes Upon Runes", icon = "Interface/Icons/INV_Enchant_FormulaSuperior_01", rewards = { { type = "SPECIAL",       value = "ENCHANT_SLOTS",  amount = 1, label = "Unlock additional enchantment slots as your average item level rises" } } },
    -- Milestone 875: universal — 75% less durability loss from ALL
    -- sources, via marker 1900074 read by the §1j patch in
    -- Player::DurabilityPointsLoss (the one funnel: combat ticks, death
    -- 10%, spirit-healer 25%). Probabilistic rounding keeps 1-point ticks
    -- statistically exact; the stock 100%-prevention aura check sits above
    -- and still wins outright. bp retune = spell_dbc edit + restart.
    { level = 875, title = "Built to Last", icon = "Interface/Icons/Trade_BlackSmithing",           rewards = { { type = "SPECIAL",       value = "DURABILITY_GUARD", amount = 75, label = "Your Equipment's Durability loss is reduced by 75%" } } },
    { level = 900, title = "Beyond Mastery IV", icon = "Interface/Icons/Spell_Holy_GreaterHeal",        class_rewards = MILESTONE_900 },
    -- Milestone 925: universal — a second stat-scaling step, stacking with
    -- milestone 700 to -4 effective levels. Separate marker 1900072 (an
    -- aura cannot stack with itself); the §1g core patch SUMS both
    -- markers' bp. Same refresh plumbing (paragon_scaling_level.lua
    -- watches both), addon factor tables carry the -4 tier.
    { level = 925, title = "Primal Vigor", icon = "Interface/Icons/Achievement_Level_60",          rewards = { { type = "SPECIAL",       value = "SCALING_LEVEL_2", amount = 2, label = "Your attributes scale as if you were 2 additional levels lower than you actually are" } } },
    -- Milestone 950: universal — fall damage halved via marker 1900075
    -- read by the §1k patch in Player::HandleFall (after Safe Fall yard
    -- reduction and the fork's Divine Protection halving — those stack
    -- multiplicatively with this). bp retune = spell_dbc edit + restart.
    { level = 950, title = "Feather's Grace", icon = "Interface/Icons/Spell_Magic_FeatherFall",       rewards = { { type = "SPECIAL",       value = "SOFT_LANDING",   amount = 50, label = "Fall damage is reduced by 50%" } } },
    -- Milestone 975: universal — incoming slow effects weakened by 25%.
    -- Carried by invisible server aura 1900037 (SPECIAL_AURAS below); the
    -- Unit::UpdateSpeed core patch (§1f) reads the REDUCTION PERCENT from
    -- that aura's own basepoints, so retuning is a spell_dbc edit +
    -- restart, no rebuild. Stock 3.3.5 has no slow-magnitude-reduction
    -- aura type — this marker is the only source of the mechanic. Note:
    -- granted mid-snare, it bites on the next speed update (slows
    -- refresh/expire constantly, so effectively instant).
    { level = 975, title = "Unstoppable Momentum", icon = "Interface/Icons/Spell_Holy_SealOfValor",        rewards = { { type = "SPECIAL",       value = "SLOW_ATTENUATION", amount = 25, label = "The effect of slowing effects on you is reduced by 25%" } } },
    -- Milestone 1000: universal — the capstone: the exclusive "Paragon %s"
    -- title (custom CharTitles.dbc row 200 / mask bit 143 — the mask MUST
    -- be contiguous with stock, see the generator comment; generated AND
    -- deployed to both sides by Tools/paragon_client_patch.py — the server
    -- validates titles against its own store on grant and on dropdown
    -- selection) plus ten more talent points on the milestone-25/550
    -- reconcile lifecycle.
    { level = 1000, title = "Paragon", icon = "Interface/Icons/Achievement_Level_80",         rewards = { { type = "SPECIAL",       value = "TALENT_POINTS",  amount = 10 },
                                                                                       { type = "SPECIAL",       value = "TITLE",          amount = 200, label = "Exclusive Title: Paragon" } } },
    { level = 1025, title = "Hardened Resolve", icon = "Interface/Icons/INV_Shield_06",       class_rewards = MILESTONE_1025 },
    -- Milestone 1050: universal — Provocation, an activatable infinite
    -- self-buff whose aura 152 (MOD_DETECTED_RANGE) widens every
    -- creature's aggro radius against you by 30 yd (stock
    -- Creature::GetAttackDistance hook; only creatures <= level 75
    -- consult it — exactly the ones that ignore a level 80). Spell data
    -- in Tools/paragon_client_patch.py CUSTOM_SPELLS (1900104); zero Lua
    -- beyond the LEARNED_SPELL_SPECIALS teach.
    { level = 1050, title = "Magnetic Presence", icon = "Interface/Icons/Ability_BullRush",   rewards = { { type = "SPECIAL",       value = "PROVOCATION",    amount = 1, label = "New ability: Provocation — enemies notice you from much farther away" } } },
    { level = 1075, title = "Restless Advance", icon = "Interface/Icons/Ability_HeroicLeap",  class_rewards = MILESTONE_1075 },
    -- Milestone 1100: universal — a second Eternal Belt Buckle fits the
    -- belt, opening one more prismatic socket. The client's socket UI
    -- hardcodes template+1, so the socket is server-owned: full design in
    -- modules/paragon_double_buckle.lua (marker 1900107 gates the act).
    { level = 1100, title = "Twice-Girded", icon = "Interface/Icons/INV_Belt_27",             rewards = { { type = "SPECIAL",       value = "DOUBLE_BUCKLE",  amount = 1, label = "A second Eternal Belt Buckle fits your belt, opening one more prismatic socket" } } },
    { level = 1125, title = "Steadfast", icon = "Interface/Icons/Spell_Holy_Stoicism",        class_rewards = MILESTONE_1125 },
    -- Milestone 1150: universal — every socketed gem's flat stats count
    -- twice. Enforcement + live tooltip push live in
    -- modules/paragon_gem_double.lua (prefix "ParagonGems"); this label is
    -- informational, the addon appends the live doubled list under it.
    { level = 1150, title = "Facets Unbound", icon = "Interface/Icons/INV_Misc_Gem_Variety_02", rewards = { { type = "SPECIAL",     value = "GEM_DOUBLE",     amount = 2, label = "The stats of every gem socketed in your equipment are doubled" } } },
    { level = 1175, title = "Beyond Mastery V", icon = "Interface/Icons/Spell_Holy_SurgeOfLight", class_rewards = MILESTONE_1175 },
    -- Milestone 1200: universal — every unique 5-man dungeon cleared SOLO
    -- (heroics count separately; 92 total) grants +2 resilience and +0.25%
    -- crit damage, applied in whole percents (4 clears = 1%, integer aura
    -- granularity — accepted design). Detection is the §1p ALE
    -- encounter-complete hook; registry, stats and the live tooltip push
    -- live in modules/paragon_solo_dungeon.lua (prefix "ParagonSolo").
    { level = 1200, title = "Lone Conqueror", icon = "Interface/Icons/Achievement_Dungeon_GloryoftheHero", rewards = { { type = "SPECIAL", value = "SOLO_DUNGEON", amount = 2, label = "Each unique dungeon cleared solo grants +2 Resilience and +0.25% Critical Strike Damage" } } },
    { level = 1225, title = "Zealous Command", icon = "Interface/Icons/Ability_Paladin_SwiftRetribution", class_rewards = MILESTONE_1225 },
    -- Milestone 1250: universal — three more talent points. Pure track data:
    -- OwedBonusTalents sums every TALENT_POINTS reward at or below the level
    -- and ReconcileBonusTalents writes the single total (idempotent, sole
    -- writer of the core field), so no spell, client patch or rebuild.
    { level = 1250, title = "Unfolding Potential", icon = "Interface/Icons/INV_Misc_Book_09",   rewards = { { type = "SPECIAL",       value = "TALENT_POINTS",  amount = 3                     } } },
    -- Milestone 1275: universal — the last 25% of durability loss, on top of
    -- milestone 875's 75%. Implemented as the stock prevent-durability-loss
    -- aura (1900123) rather than a second percentage marker, so the result is
    -- exact: equipment never loses durability again, including the 10% death
    -- hit and the 25% spirit-healer resurrect.
    { level = 1275, title = "Everlasting", icon = "Interface/Icons/INV_Shield_23",              rewards = { { type = "SPECIAL",       value = "DURABILITY_IMMUNE", amount = 25, label = "Your Equipment's Durability loss is reduced by a further 25% — your gear no longer takes any durability damage" } } },
    -- Milestone 1300: universal — each unique rare creature (creature_template
    -- rank 2/4) killed for the first time grants +10 armor, +0.25 resilience
    -- and +0.25 haste. Registry, kill hooks and the live tooltip push live in
    -- modules/paragon_rare_hunter.lua (prefix "ParagonRares"); ratings are
    -- int32 in the core, so the quarter-points bank into +1 every 4th kill.
    { level = 1300, title = "Big Game Hunter", icon = "Interface/Icons/Ability_Hunter_MarkedForDeath", rewards = { { type = "SPECIAL", value = "RARE_HUNTER", amount = 10, label = "Each unique rare creature slain grants +10 Armor, +0.25 Resilience and +0.25 Haste" } } },
    { level = 1325, title = "Sudden Light", icon = "Interface/Icons/Spell_Holy_SurgeOfLight", class_rewards = MILESTONE_1325 },
    -- Milestone 1350: universal -- THIRD stat-scaling marker (1900131), one
    -- more effective level on top of 700's and 925's two apiece, so -5 from
    -- milestones alone. Player::GetStatScalingLevel sums them; its marker
    -- list is hardcoded, so 1900131 had to be added there too (core patch).
    { level = 1350, title = "Ageless Might", icon = "Interface/Icons/Achievement_Level_50",       rewards = { { type = "SPECIAL",       value = "SCALING_LEVEL_3", amount = 1, label = "Your attributes scale as if you were 1 additional level lower than you actually are" } } },
    { level = 1375, title = "Master at Arms", icon = "Interface/Icons/Ability_Warrior_WeaponMastery", class_rewards = MILESTONE_1375 },    -- Milestone 1400: universal -- ONE ACTIVE racial ability belonging to
    -- another race, freely swappable out of combat. Enforcement, the class
    -- variant resolution and the picker protocol all live in
    -- modules/paragon_racial_pick.lua (prefix "ParagonRacial"); this SPECIAL
    -- is INFORMATIONAL ONLY -- ApplyReward skips SPECIAL rewards and no
    -- registry entry exists for RACIAL_PICK, exactly like GEM_DOUBLE. The
    -- addon appends the live choice under the label.
    -- TWELVE options, not ten: Dwarf (Stoneform, Find Treasure) and Undead
    -- (Will of the Forsaken, Cannibalize) each have two actives, and Human
    -- Perception is a PASSIVE in 3.3.5 so it does not qualify.
    { level = 1400, title = "Racially Ambiguous", icon = "Interface/Icons/Ability_Racial_Avatar", rewards = { { type = "SPECIAL", value = "RACIAL_PICK", amount = 1, label = "Learn one active racial ability of another race — click this milestone to choose, and change it freely while out of combat" } } },
    { level = 1425, title = "Beyond Mastery VI", icon = "Interface/Icons/Spell_Holy_ChampionsBond", class_rewards = MILESTONE_1425 },
    -- Milestone 1450: universal -- a flat 5% less damage taken, carried by
    -- the invisible server aura 1900139 (SPECIAL_AURAS below). The value is
    -- BAKED INTO THE SPELL ROW (bp -5 + die 0), because ApplySpecialAura
    -- uses player:AddAura, which cannot pass custom basepoints -- unlike
    -- codex node 60, whose amount varies by rank and so needs a
    -- CastCustomSpell channel of its own.
    --
    -- Stacks MULTIPLICATIVELY with node 60 and with the Paladin talents:
    -- Unit::GetTotalAuraMultiplier AddPct's every aura-87 effect in turn, so
    -- this 5% and a rank-10 node come to 0.95 x 0.90 = 0.855, i.e. 14.5%
    -- rather than 15%. Accepted by design -- keeping the two auras separate
    -- means neither module has to know the other exists.
    { level = 1450, title = "Bulwark of Ages", icon = "Interface/Icons/Ability_Warrior_ShieldWall", rewards = { { type = "SPECIAL", value = "DAMAGE_REDUCTION", amount = 5, label = "All damage you take is reduced by 5%" } } },
    { level = 1475, title = "Vessel of Light", icon = "Interface/Icons/Spell_Holy_HolyGuidance", class_rewards = MILESTONE_1475 },
    -- Milestone 1500: universal -- the track's FIRST real item payout.
    -- MAILED, not pushed into bags (mail cannot fail on a full inventory
    -- and carries flavour text); handled by modules/paragon_milestone_items.lua,
    -- which owns the item list. This SPECIAL is INFORMATIONAL ONLY, same
    -- as GEM_DOUBLE / SOLO_DUNGEON / RACIAL_PICK -- the track module has
    -- no DB or mail layer, so payout data lives with the payout code.
    -- !! The payout fires on the CROSSING only. `.paragon setlevel` raises
    -- no event, so setting the level straight to 1500 pays nothing --
    -- use `setlevel 1499` then `addlevel 1`.
    { level = 1500, title = "The Heavens Take Notice", icon = "Interface/Icons/Ability_Mount_CelestialHorse", rewards = { { type = "SPECIAL", value = "MILESTONE_ITEM", amount = 1, label = "A Celestial Steed, delivered by mail — a mount that matches your riding skill on the ground and in the air" } } },
}

-- Broadcast-only display names (client uses its own Locale.STATISTICS)
local LABELS = {
    TALENT_POINTS  = "Talent Points",
    STAT_STRENGTH  = "Strength",
    STAT_AGILITY   = "Agility",
    STAT_STAMINA   = "Stamina",
    STAT_INTELLECT = "Intellect",
    STAT_SPIRIT    = "Spirit",
    ARMOR          = "Armor",
    ATTACK_POWER   = "Attack Power",
    CRIT_MELEE     = "Melee Critical Strike Rating",
    HASTE_MELEE    = "Melee Haste Rating",
    HEALTH         = "Health",
}

-- ============================================================================
-- CLASS RESOLUTION
-- ============================================================================

local EMPTY = {}

--- The rewards of one milestone as they apply to a single class. Milestones
--- carrying class_rewards resolve per class (a class with no entry gets
--- nothing); everything else is universal.
local function ResolveRewards(milestone, class_id)
    if not milestone.class_rewards then
        return milestone.rewards
    end
    local entry = milestone.class_rewards[class_id]
    return entry and entry.rewards or EMPTY
end

local function ResolveIcon(milestone, class_id)
    if milestone.class_rewards then
        local entry = milestone.class_rewards[class_id]
        if entry and entry.icon then
            return entry.icon
        end
    end
    return milestone.icon
end

--- Tooltip flavor title: a class entry's own title wins (paladin flavor on
--- class milestones), else the row's neutral title; nil-safe for rows
--- without one (the addon falls back to the legacy headline).
local function ResolveTitle(milestone, class_id)
    if milestone.class_rewards then
        local entry = milestone.class_rewards[class_id]
        if entry and entry.title then
            return entry.title
        end
    end
    return milestone.title
end

-- ============================================================================
-- STAT APPLICATION (guarded by a per-session applied set)
-- ============================================================================

-- SPECIAL rewards (TALENT_POINTS) are persistent and handled by the talent
-- reconciler below — the per-session stat cycle deliberately skips them.
local function ApplyReward(player, reward, apply)
    if reward.type == "UNIT_MODS" then
        player:HandleStatFlatModifier(
            Constant.STATISTICS.UNIT_MODS[reward.value], reward.application, reward.amount, apply)
    elseif reward.type == "COMBAT_RATING" then
        player:ApplyRatingMod(
            Constant.STATISTICS.COMBAT_RATING[reward.value], reward.amount, apply)
    end
end

--- Applies or removes one milestone, tracked in a session set stored on the
--- player object (dies with it at logout). Returns true if state changed.
local function SetMilestone(player, milestone, apply)
    local applied = player:GetData(APPLIED_KEY)
    if not applied then
        applied = {}
    end

    if apply and applied[milestone.level] then
        return false
    end
    if not apply and not applied[milestone.level] then
        return false
    end

    for _, reward in ipairs(ResolveRewards(milestone, player:GetClass())) do
        ApplyReward(player, reward, apply)
    end

    applied[milestone.level] = apply or nil
    player:SetData(APPLIED_KEY, applied)
    return true
end

-- ============================================================================
-- TALENT POINT REWARDS (persistent, reconciled — never part of the stat cycle)
-- ============================================================================

-- extraBonusTalentCount is added inside the core's talent pool calculation
-- (Player::CalculateTalentsPoints) and persisted with the character, so
-- spending the points is legal and respecs keep them. The track is the sole
-- writer of the field on this server: reconcile sets it to the total owed for
-- the current paragon level (idempotent, self-heals any past drift). There is
-- no removal at logout — that would race the character save.

local function OwedBonusTalents(player, level)
    local class_id = player:GetClass()
    local total = 0
    for _, milestone in ipairs(TRACK) do
        if milestone.level <= level then
            for _, reward in ipairs(ResolveRewards(milestone, class_id)) do
                if reward.type == "SPECIAL" and reward.value == "TALENT_POINTS" then
                    total = total + reward.amount
                elseif reward.type == "SPECIAL" and reward.value == "TALENT_POINTS_PER_100" then
                    -- dynamic: scales with the live paragon level forever
                    total = total + reward.amount * math.floor(level / 100)
                end
            end
        end
    end
    -- the codex's Boundless Knowledge ranks (paragon_codex.lua) join the
    -- single extraBonusTalentCount total — this reconciler stays the one
    -- writer of the core field
    if ParagonCodex_BonusTalents then
        total = total + ParagonCodex_BonusTalents(player)
    end
    return total
end

-- The milestone level and rate of the per-100 reward (for the threshold
-- toast below; math.huge when the track carries none)
local PER100_LEVEL, PER100_AMOUNT = math.huge, 0
for _, milestone in ipairs(TRACK) do
    for _, reward in ipairs(milestone.rewards or EMPTY) do
        if reward.type == "SPECIAL" and reward.value == "TALENT_POINTS_PER_100" then
            PER100_LEVEL, PER100_AMOUNT = milestone.level, reward.amount
        end
    end
end

local function ReconcileBonusTalents(player, paragon)
    local owed = OwedBonusTalents(player, paragon:GetLevel())
    local current = player:GetBonusTalentCount()
    if current == owed then
        return
    end

    player:SetBonusTalentCount(owed)

    -- The setter widens the pool but does not recompute free points: mirror
    -- the InitTalentForLevel math and resync (SetFreeTalentPoints resends
    -- talent data to the client). Any later natural recalculation by the
    -- core arrives at the same numbers.
    local free = player:GetFreeTalentPoints() + (owed - current)
    if free < 0 then
        free = 0
    end
    player:SetFreeTalentPoints(free)
end

--- Exposed for the codex (paragon_codex.lua): re-runs the talent reconcile
--- after a Boundless Knowledge purchase/refund, outside the normal
--- level-change and statistics beats.
function ParagonRework_PokeBonusTalents(player)
    local paragon = player:GetData("Paragon")
    if paragon then
        ReconcileBonusTalents(player, paragon)
    end
end

-- ============================================================================
-- SPECIAL AURA REWARDS (server-side spells, invisible to the client)
-- ============================================================================

-- Server-side custom spells in acore_world.spell_dbc (the module's reserved
-- 1900000+ range): self-target, permanent duration, dispel type none (immune
-- to purge and spellsteal). The client has no DBC entries for them, so the
-- buffs are invisible by design — the paragon window documents the bonuses.
-- spell_dbc merges into the spell store at worldserver STARTUP: new rows and
-- row changes need a restart (everything else here hot-reloads). Speed auras
-- coexist under the core's highest-wins rules, so mounts, sprints and
-- potions behave normally alongside these.
--   1900003 "Paragon Swiftness"      +50% run speed  (combat-gated in Lua)
--   1900004 "Paragon Aquatic Grace" +100% swim speed (unconditional)
--   1900005 "Paragon Quick Mount"    dummy marker aura, no effect of its own:
--           Unit::ModSpellCastTime checks for it and takes 1s off mount casts
--           (core patch, see "Paragon Core Patches.md" — needs a rebuild, not
--           just a restart, if that patch is ever lost to a core update)
local SPECIAL_AURAS = {
    OOC_MOVE_SPEED = { spell = 1900003, combat_gated = true },
    SWIM_SPEED     = { spell = 1900004, combat_gated = false },
    QUICK_MOUNT    = { spell = 1900005, combat_gated = false },
    --   1900037 "Paragon Slow Attenuation" dummy marker: Unit::UpdateSpeed
    --           reads its bp (25) as the slow-reduction percent (§1f core
    --           patch; the row is generated by paragon_client_patch.py
    --           SERVER_SPELLS — server-only, no client entry)
    SLOW_ATTENUATION = { spell = 1900037, combat_gated = false },
    --   1900039 "Paragon Stat Scaling" dummy marker: Player::
    --           GetStatScalingLevel reads its bp (2) as the level
    --           reduction for gt-table stat conversions (§1g core patch;
    --           SERVER_SPELLS row, server-only). Stats are refreshed on
    --           grant by paragon_scaling_level.lua.
    SCALING_LEVEL = { spell = 1900039, combat_gated = false },
    --   1900071 "Paragon Swift Mount" dummy marker: the same §1 core patch
    --           as QUICK_MOUNT takes another 0.5s off mount casts
    --           (SERVER_SPELLS row, server-only) — stacked with milestone
    --           100 that is instant mounting, castable while moving
    SWIFT_MOUNT = { spell = 1900071, combat_gated = false },
    --   1900072 "Paragon Stat Scaling II" dummy marker: clone of 1900039;
    --           §1g SUMS both markers' bp (2 + 2 = -4 effective levels at
    --           milestone 800). SERVER_SPELLS row, server-only; stats
    --           refreshed on grant by paragon_scaling_level.lua.
    SCALING_LEVEL_2 = { spell = 1900072, combat_gated = false },
    --   1900131 "Paragon Stat Scaling III" dummy marker: clone of 1900039
    --           with bp 0 (= 1 effective level, not 2); 1g SUMS all three
    --           for -5 at milestone 1350. SERVER_SPELLS row, server-only;
    --           stats refreshed on grant by paragon_scaling_level.lua.
    --           The core's marker list is HARDCODED -- the spell row alone
    --           is inert without the id in Player::GetStatScalingLevel.
    SCALING_LEVEL_3 = { spell = 1900131, combat_gated = false },
    --   1900139 "Bulwark of Ages" (milestone 1450): hidden PASSIVE carrying
    --           SPELL_AURA_MOD_DAMAGE_PERCENT_TAKEN (87) at -5, misc 127 =
    --           all seven schools. The core reads aura 87 in exactly two
    --           places, Unit::SpellDamageBonusTaken (spell hits AND periodic
    --           ticks) and Unit::MeleeDamageBonusTaken, so this covers all
    --           incoming combat damage; environmental damage bypasses both.
    --           NOT combat_gated -- mitigation that switched off in combat
    --           would be worse than useless.
    DAMAGE_REDUCTION = { spell = 1900139, combat_gated = false },
    --   1900073 "Paragon Ghost Sprint" dummy marker: §1i core patch reads
    --           it twice — waives spirit-healer resurrection sickness and
    --           adds its bp (60) to run speed while dead (SERVER_SPELLS
    --           row, server-only; passive, so it persists through death)
    GHOST_SPRINT = { spell = 1900073, combat_gated = false },
    --   1900074 "Paragon Durability Guard" dummy marker: §1j core patch
    --           scales every durability loss by its bp percent (75) in
    --           Player::DurabilityPointsLoss (SERVER_SPELLS row,
    --           server-only; passive, present at death/rez time)
    DURABILITY_GUARD = { spell = 1900074, combat_gated = false },
    --   1900123 "Paragon Durability Immunity": the milestone-1275 top-up of
    --           the 875 guard to a full 100%. Carries the STOCK aura 289
    --           (SPELL_AURA_PREVENT_DURABILITY_LOSS), which
    --           Player::DurabilityPointsLoss honours on its first line
    --           (HasPreventDurabilityLossAura) BEFORE the §1j percentage
    --           scaling — so no core change and no rounding leak. The 875
    --           marker stays applied and simply stops mattering.
    DURABILITY_IMMUNE = { spell = 1900123, combat_gated = false },
    --   1900075 "Paragon Soft Landing" dummy marker: §1k core patch scales
    --           fall damage down by its bp percent (50) in
    --           Player::HandleFall (SERVER_SPELLS row, server-only)
    SOFT_LANDING = { spell = 1900075, combat_gated = false },
}

-- ============================================================================
-- EXTENDED TALENT RANKS (server-authoritative gate; one table line per talent)
-- ============================================================================

-- Talents raised beyond the retail 5-rank cap. The DATA for each entry
-- (spell_dbc/talent_dbc rows + the client patch-X MPQs) comes from
-- Tools/paragon_client_patch.py; this table is the GATE. Reward entries of type
-- SPECIAL/TALENT_MASTERY are informational labels — enforcement is here.
-- Talent.dbc physically caps a talent at 9 ranks total.
local EXTENDED_TALENTS = {
    [2185] = { milestone = 75 },   -- Divine Strength ranks 6-9 (Paladin)
    [1407] = { milestone = 275 },   -- Benediction ranks 6-9 (Paladin)
    [1442] = { milestone = 475 },   -- Divinity ranks 6-9 (Paladin)
    [1629] = { milestone = 625 },   -- Anticipation ranks 6-8 (Paladin)
    [1463] = { milestone = 725 },   -- Seals of the Pure ranks 6-7 (Paladin)
    [1411] = { milestone = 825 },   -- Conviction ranks 6-7 (Paladin)
    [1423] = { milestone = 1025 },  -- Toughness ranks 6-9 (Paladin)
    [1748] = { milestone = 1125, base = 3 },  -- Stoicism ranks 4-5 (Paladin)
    [2148] = { milestone = 1225, base = 3 },  -- Swift Retribution ranks 4-5 (Paladin)
    [1401] = { milestone = 1225, base = 2 },  -- Improved Blessing of Might ranks 3-4 (Paladin)
    -- base = 0 gates EVERY rank: this talent does not exist at retail, so
    -- rank 0 upward is milestone-only. (Lua treats 0 as truthy, so the
    -- handler's `(def and def.base) or 5` correctly yields 0 here.)
    [2286] = { milestone = 1325, base = 0 },  -- Sudden Light, brand-new talent (Paladin)
    [1429] = { milestone = 1375, base = 3 },  -- One-Handed Weapon Specialization ranks 4-5 (Paladin)
    [1410] = { milestone = 1375, base = 3 },  -- Two-Handed Weapon Specialization ranks 4-5 (Paladin)
    [1753] = { milestone = 1375, base = 3 },  -- Combat Expertise ranks 4-5 (Paladin)
    [2195] = { milestone = 1475, base = 3 },  -- Touched by the Light rank 4 (Paladin)
    [1746] = { milestone = 1475, base = 5 },  -- Holy Guidance ranks 6-9 (Paladin)

    -- ---- the nine non-Paladin classes (generated: Tools/gen_class_track_lua.py) ----
    [130] = { milestone = 75, base = 5 },  -- Deflection (Warrior)
    [128] = { milestone = 275, base = 3 },  -- Tactical Mastery (Warrior)
    [1653] = { milestone = 475, base = 3 },  -- Vitality (Warrior)
    [138] = { milestone = 625, base = 5 },  -- Anticipation (Warrior)
    [1657] = { milestone = 725, base = 3 },  -- Precision (Warrior)
    [157] = { milestone = 825, base = 5 },  -- Cruelty (Warrior)
    [140] = { milestone = 1025, base = 5 },  -- Toughness (Warrior)
    [641] = { milestone = 1125, base = 3 },  -- Iron Will (Warrior)
    [154] = { milestone = 1225, base = 5 },  -- Commanding Presence (Warrior)
    [158] = { milestone = 1225, base = 2 },  -- Booming Voice (Warrior)
    [136] = { milestone = 1375, base = 3 },  -- Two-Handed Weapon Specialization (Warrior)
    [702] = { milestone = 1375, base = 5 },  -- One-Handed Weapon Specialization (Warrior)
    [662] = { milestone = 1375, base = 2 },  -- Impale (Warrior)
    [1862] = { milestone = 1475, base = 2 },  -- Strength of Arms (Warrior)
    [1658] = { milestone = 1475, base = 5 },  -- Improved Berserker Stance (Warrior)
    [1389] = { milestone = 75, base = 5 },  -- Endurance Training (Hunter)
    [1342] = { milestone = 275, base = 5 },  -- Efficiency (Hunter)
    [1622] = { milestone = 475, base = 5 },  -- Survivalist (Hunter)
    [1303] = { milestone = 625, base = 5 },  -- Lightning Reflexes (Hunter)
    [1349] = { milestone = 725, base = 5 },  -- Mortal Shots (Hunter)
    [1344] = { milestone = 825, base = 5 },  -- Lethal Shots (Hunter)
    [1395] = { milestone = 1025, base = 3 },  -- Thick Hide (Hunter)
    [1310] = { milestone = 1125, base = 3 },  -- Surefooted (Hunter)
    [1800] = { milestone = 1225, base = 3 },  -- Ferocious Inspiration (Hunter)
    [2133] = { milestone = 1225, base = 3 },  -- Improved Steady Shot (Hunter)
    [1362] = { milestone = 1375, base = 3 },  -- Ranged Weapon Specialization (Hunter)
    [1621] = { milestone = 1375, base = 2 },  -- Savage Strikes (Hunter)
    [1382] = { milestone = 1375, base = 5 },  -- Improved Aspect of the Hawk (Hunter)
    [1806] = { milestone = 1475, base = 3 },  -- Careful Aim (Hunter)
    [2228] = { milestone = 1475, base = 3 },  -- Hunter vs. Wild (Hunter)
    [1702] = { milestone = 75, base = 5 },  -- Deadliness (Rogue)
    [1123] = { milestone = 275, base = 3 },  -- Serrated Blades (Rogue)
    [1705] = { milestone = 475, base = 3 },  -- Vitality (Rogue)
    [186] = { milestone = 625, base = 3 },  -- Lightning Reflexes (Rogue)
    [269] = { milestone = 725, base = 5 },  -- Lethality (Rogue)
    [270] = { milestone = 825, base = 5 },  -- Malice (Rogue)
    [1723] = { milestone = 1025, base = 3 },  -- Deadened Nerves (Rogue)
    [1707] = { milestone = 1125, base = 2 },  -- Nerves of Steel (Rogue)
    [278] = { milestone = 1225, base = 2 },  -- Improved Expose Armor (Rogue)
    [1715] = { milestone = 1225, base = 3 },  -- Master Poisoner (Rogue)
    [221] = { milestone = 1375, base = 5 },  -- Dual Wield Specialization (Rogue)
    [184] = { milestone = 1375, base = 5 },  -- Mace Specialization (Rogue)
    [182] = { milestone = 1375, base = 5 },  -- Close Quarters Combat (Rogue)
    [1718] = { milestone = 1475, base = 3 },  -- Find Weakness (Rogue)
    [1712] = { milestone = 1475, base = 5 },  -- Sinister Calling (Rogue)
    [1898] = { milestone = 75, base = 5 },  -- Twin Disciplines (Priest)
    [463] = { milestone = 275, base = 3 },  -- Shadow Focus (Priest)
    [404] = { milestone = 475, base = 5 },  -- Spiritual Healing (Priest)
    [1765] = { milestone = 625, base = 3 },  -- Blessed Resilience (Priest)
    [462] = { milestone = 725, base = 5 },  -- Darkness (Priest)
    [401] = { milestone = 825, base = 5 },  -- Holy Specialization (Priest)
    [411] = { milestone = 1025, base = 5 },  -- Spell Warding (Priest)
    [342] = { milestone = 1125, base = 5 },  -- Unbreakable Will (Priest)
    [344] = { milestone = 1225, base = 2 },  -- Improved Power Word: Fortitude (Priest)
    [1905] = { milestone = 1225, base = 5 },  -- Divine Providence (Priest)
    [1778] = { milestone = 1375, base = 5 },  -- Shadow Power (Priest)
    [1181] = { milestone = 1375, base = 5 },  -- Divine Fury (Priest)
    [1773] = { milestone = 1375, base = 3 },  -- Improved Flash Heal (Priest)
    [1201] = { milestone = 1475, base = 5 },  -- Mental Strength (Priest)
    [402] = { milestone = 1475, base = 5 },  -- Spiritual Guidance (Priest)
    [1945] = { milestone = 75, base = 3 },  -- Subversion (Death Knight)
    [2020] = { milestone = 275, base = 2 },  -- Runic Power Mastery (Death Knight)
    [1950] = { milestone = 475, base = 3 },  -- Veteran of the Third War (Death Knight)
    [2018] = { milestone = 625, base = 3 },  -- Spell Deflection (Death Knight)
    [2047] = { milestone = 725, base = 5 },  -- Necrosis (Death Knight)
    [1943] = { milestone = 825, base = 5 },  -- Dark Conviction (Death Knight)
    [1968] = { milestone = 1025, base = 5 },  -- Toughness (Death Knight)
    [1990] = { milestone = 1125, base = 3 },  -- Frigid Dreadplate (Death Knight)
    [2105] = { milestone = 1225, base = 2 },  -- Abomination's Might (Death Knight)
    [1932] = { milestone = 1225, base = 3 },  -- Virulence (Death Knight)
    [2217] = { milestone = 1375, base = 2 },  -- Two-Handed Weapon Specialization (Death Knight)
    [2022] = { milestone = 1375, base = 3 },  -- Nerves of Cold Steel (Death Knight)
    [2082] = { milestone = 1375, base = 2 },  -- Vicious Strikes (Death Knight)
    [1934] = { milestone = 1475, base = 3 },  -- Ravenous Dead (Death Knight)
    [2005] = { milestone = 1475, base = 5 },  -- Impurity (Death Knight)
    [614] = { milestone = 75, base = 5 },  -- Ancestral Knowledge (Shaman)
    [564] = { milestone = 275, base = 5 },  -- Convection (Shaman)
    [592] = { milestone = 475, base = 5 },  -- Purification (Shaman)
    [1699] = { milestone = 625, base = 5 },  -- Nature's Guardian (Shaman)
    [563] = { milestone = 725, base = 5 },  -- Concussion (Shaman)
    [594] = { milestone = 825, base = 5 },  -- Tidal Mastery (Shaman)
    [1640] = { milestone = 1025, base = 3 },  -- Elemental Warding (Shaman)
    [1695] = { milestone = 1125, base = 3 },  -- Focused Mind (Shaman)
    [1689] = { milestone = 1225, base = 3 },  -- Unleashed Rage (Shaman)
    [1647] = { milestone = 1225, base = 2 },  -- Improved Windfury Totem (Shaman)
    [1643] = { milestone = 1375, base = 3 },  -- Weapon Mastery (Shaman)
    [1692] = { milestone = 1375, base = 3 },  -- Dual Wield Specialization (Shaman)
    [611] = { milestone = 1375, base = 3 },  -- Elemental Weapons (Shaman)
    [1696] = { milestone = 1475, base = 3 },  -- Nature's Blessing (Shaman)
    [1691] = { milestone = 1475, base = 3 },  -- Mental Quickness (Shaman)
    [77] = { milestone = 75, base = 5 },  -- Arcane Mind (Mage)
    [75] = { milestone = 275, base = 5 },  -- Arcane Concentration (Mage)
    [24] = { milestone = 475, base = 2 },  -- Molten Shields (Mage)
    [1726] = { milestone = 625, base = 3 },  -- Prismatic Cloak (Mage)
    [35] = { milestone = 725, base = 5 },  -- Fire Power (Mage)
    [33] = { milestone = 825, base = 3 },  -- Critical Mass (Mage)
    [70] = { milestone = 1025, base = 2 },  -- Frost Warding (Mage)
    [2212] = { milestone = 1125, base = 2 },  -- Burning Determination (Mage)
    [1727] = { milestone = 1225, base = 3 },  -- Arcane Empowerment (Mage)
    [1846] = { milestone = 1225, base = 3 },  -- Netherwind Presence (Mage)
    [26] = { milestone = 1375, base = 5 },  -- Improved Fireball (Mage)
    [37] = { milestone = 1375, base = 5 },  -- Improved Frostbolt (Mage)
    [61] = { milestone = 1375, base = 3 },  -- Piercing Ice (Mage)
    [1826] = { milestone = 1475, base = 2 },  -- Spell Power (Mage)
    [1728] = { milestone = 1475, base = 5 },  -- Mind Mastery (Mage)
    [1223] = { milestone = 75, base = 3 },  -- Demonic Embrace (Warlock)
    [1007] = { milestone = 275, base = 2 },  -- Improved Life Tap (Warlock)
    [1242] = { milestone = 475, base = 3 },  -- Fel Vitality (Warlock)
    [1678] = { milestone = 625, base = 3 },  -- Soul Leech (Warlock)
    [1042] = { milestone = 725, base = 5 },  -- Shadow Mastery (Warlock)
    [944] = { milestone = 825, base = 5 },  -- Improved Shadow Bolt (Warlock)
    [1887] = { milestone = 1025, base = 3 },  -- Molten Skin (Warlock)
    [1680] = { milestone = 1125, base = 3 },  -- Demonic Resilience (Warlock)
    [1885] = { milestone = 1225, base = 5 },  -- Demonic Pact (Warlock)
    [1667] = { milestone = 1225, base = 3 },  -- Malediction (Warlock)
    [943] = { milestone = 1375, base = 5 },  -- Bane (Warlock)
    [966] = { milestone = 1375, base = 5 },  -- Emberstorm (Warlock)
    [1003] = { milestone = 1375, base = 5 },  -- Improved Corruption (Warlock)
    [1262] = { milestone = 1475, base = 5 },  -- Unholy Power (Warlock)
    [1263] = { milestone = 1475, base = 3 },  -- Demonic Knowledge (Warlock)
    [824] = { milestone = 75, base = 5 },  -- Naturalist (Druid)
    [783] = { milestone = 275, base = 3 },  -- Moonglow (Druid)
    [828] = { milestone = 475, base = 5 },  -- Gift of Nature (Druid)
    [807] = { milestone = 625, base = 2 },  -- Feral Swiftness (Druid)
    [790] = { milestone = 725, base = 3 },  -- Moonfury (Druid)
    [1822] = { milestone = 825, base = 2 },  -- Nature's Majesty (Druid)
    [794] = { milestone = 1025, base = 3 },  -- Thick Hide (Druid)
    [1793] = { milestone = 1125, base = 3 },  -- Primal Tenacity (Druid)
    [821] = { milestone = 1225, base = 2 },  -- Improved Mark of the Wild (Druid)
    [1798] = { milestone = 1225, base = 2 },  -- Improved Leader of the Pack (Druid)
    [796] = { milestone = 1375, base = 5 },  -- Ferocity (Druid)
    [795] = { milestone = 1375, base = 5 },  -- Feral Aggression (Druid)
    [805] = { milestone = 1375, base = 2 },  -- Savage Fury (Druid)
    [1783] = { milestone = 1475, base = 2 },  -- Balance of Power (Druid)
    [1782] = { milestone = 1475, base = 3 },  -- Lunar Guidance (Druid)
}

-- PLAYER_EVENT_ON_CAN_LEARN_TALENT (74) — local ALE hook addition riding the
-- core's stock OnPlayerCanLearnTalent script call. Returning false refuses
-- the learn. rank is 0-based; extended ranks start at the talent's RETAIL
-- rank cap — `base` in its gate entry, default 5 (Stoicism is the first
-- three-rank talent extended, so its gate starts at index 3).
RegisterPlayerEvent(74, function(event, player, talent_id, rank)
    local def = EXTENDED_TALENTS[talent_id]
    if rank < ((def and def.base) or 5) then
        return -- retail ranks: stock rules only
    end

    if not def then
        return false -- extended data exists but no gate entry: stays locked
    end

    -- account-wide paragon: benefits require character level 80
    if player:GetLevel() < 80 then
        return false
    end

    local paragon = player:GetData("Paragon")
    if not paragon or paragon:GetLevel() < def.milestone then
        -- Brand-new milestone talents (base == 0) are hidden client-side by
        -- Paragon_TalentMask.lua, but that fails open: a paladin without the
        -- addon sees the talent and the core refuses the learn SILENTLY (the
        -- point simply snaps back). Say why for those, so the refusal is not
        -- mistaken for a bug. Extra ranks on retail talents stay silent —
        -- their masked tooltip already explains the cap.
        if def.base == 0 then
            player:SendBroadcastMessage(string.format(
                "|cffff4444[Paragon]|r That talent requires Paragon Level %d.",
                def.milestone))
        end
        return false -- below the milestone (or paragon data still loading)
    end
end)

-- ============================================================================
-- SPELL MODIFICATIONS (cast-event driven, per-player gated)
-- ============================================================================

-- Consecration burst (milestone 125, Paladin): every Consecration cast also
-- deals the rank's full 8-second damage instantly, via triggered server-side
-- spell 1900014 (instant holy AoE, radius matched to Consecration; its
-- spell_bonus_data row carries 8x the DoT tick coefficients — 32% SP + 32% AP
-- — so the doubling holds with gear). Generated by the unified
-- Tools/paragon_client_patch.py. Totals below = per-tick DBC damage x 8.
local CONSECRATION_BURST = {
    spell = 1900014,
    milestone = 125,
    totals = {
        [26573] = 72,  [20116] = 136, [20922] = 224, [20923] = 336,
        [20924] = 448, [27173] = 576, [48818] = 696, [48819] = 904,
        [1900022] = 1176, -- Rank 9 (147/tick x 8), the milestone-175 trainer rank
        [1900093] = 1528, -- Rank 10 (191/tick x 8), the milestone-950 trainer rank
        [1900144] = 1984, -- Rank 11 (248/tick x 8), the milestone-1425 trainer rank
    },
}

-- Fires for every player cast server-wide (bots included): the first line
-- must stay a cheap table-lookup rejection.
RegisterPlayerEvent(5, function(event, player, spell, skip_check)
    local total = CONSECRATION_BURST.totals[spell:GetEntry()]
    if not total then
        return
    end

    -- account-wide paragon: benefits require character level 80
    if player:GetLevel() < 80 then
        return
    end

    local paragon = player:GetData("Paragon")
    if not paragon or paragon:GetLevel() < CONSECRATION_BURST.milestone then
        return
    end

    player:CastCustomSpell(player, CONSECRATION_BURST.spell, true, total)
end)

local function SpecialRewardOwed(player, value, level)
    local class_id = player:GetClass()
    for _, milestone in ipairs(TRACK) do
        if milestone.level <= level then
            for _, reward in ipairs(ResolveRewards(milestone, class_id)) do
                if reward.type == "SPECIAL" and reward.value == value then
                    return true
                end
            end
        end
    end
    return false
end

local function ApplySpecialAura(player, spell)
    if player:HasAura(spell) then
        return
    end
    local aura = player:AddAura(spell, player)
    if aura then
        aura:SetMaxDuration(-1)
        aura:SetDuration(-1)
    end
end

local function ReconcileSpecialAuras(player, paragon)
    local level = paragon:GetLevel()
    for value, def in pairs(SPECIAL_AURAS) do
        if not SpecialRewardOwed(player, value, level) then
            if player:HasAura(def.spell) then
                player:RemoveAura(def.spell)
            end
        elseif not (def.combat_gated and player:IsInCombat()) then
            ApplySpecialAura(player, def.spell)
        end
    end
end

RegisterPlayerEvent(33, function(event, player, enemy)
    for _, def in pairs(SPECIAL_AURAS) do
        if def.combat_gated and player:HasAura(def.spell) then
            player:RemoveAura(def.spell)
        end
    end
end)

RegisterPlayerEvent(34, function(event, player)
    local paragon = player:GetData("Paragon")
    if not paragon then
        return
    end
    for value, def in pairs(SPECIAL_AURAS) do
        if def.combat_gated and SpecialRewardOwed(player, value, paragon:GetLevel()) then
            ApplySpecialAura(player, def.spell)
        end
    end
end)

-- ============================================================================
-- LEARNED-SPELL REWARDS (marker spells gating trainer content)
-- ============================================================================

-- Marker spells are invisible passives (hidden client-side) whose only job is
-- to flag an entitlement server-side: trainer_spell.ReqAbility1 gates
-- (TRAINER_RANKS, shown red as "Requires <marker name>" until taught) or
-- core patches keyed on Player::HasSpell (DUAL_AURA in Aura::CanStackWith).
-- Learned spells rather than auras because spells load before saved auras at
-- login, so a dual aura pair survives the relog. Reconciled like the other
-- persistent rewards: taught on the apply pass and on milestone crossing,
-- untaught if not owed. Ranks already purchased are never revoked — the
-- milestone gates the purchase, not ownership.
local LEARNED_SPELL_SPECIALS = {
    TRAINER_RANKS = { spell = 1900007 },
    DUAL_AURA     = { spell = 1900008 },
    DUAL_ENCHANT  = { spell = 1900009 },
    -- Faithful Leap is a real castable ability, not a hidden marker — the
    -- reconcile teaches/unteaches it all the same (spellbook entry via the
    -- client patch-X MPQs; the whole mechanic is spell data, zero Lua)
    FAITHFUL_LEAP = { spell = 1900030 },
    -- Empowered Spirit is a visible spellbook passive whose two percent
    -- auras do all the work (see the milestone 375 track comment)
    SPIRIT_REGEN  = { spell = 1900036 },
    -- Avenger's Reach: visible passive carrying the +2 jump-target
    -- spellmod (see the milestone 450 comment)
    AVENGER_TARGETS = { spell = 1900038 },
    -- Living Symbol: visible passive whose aura-256 effect frees the
    -- Greater Blessings of their reagent (see the milestone 525 comment)
    LIVING_SYMBOL = { spell = 1900040 },
    -- Dual Blessing: hidden marker read by the §1h core patch (see the
    -- milestone 700 comment)
    DUAL_BLESSING = { spell = 1900056 },
    -- Beyond Mastery trainer gates (milestones 900/925/950): each wave's
    -- trainer rows carry the marker in ReqAbility1
    TRAINER_RANKS_525 = { spell = 1900076 },
    TRAINER_RANKS_775 = { spell = 1900077 },
    TRAINER_RANKS_900 = { spell = 1900078 },
    TRAINER_RANKS_1175 = { spell = 1900111 },
    TRAINER_RANKS_1425 = { spell = 1900146 },
    -- Provocation: real castable ability (milestone 1050) — aura 152
    -- widens creature aggro radius vs the player, pure spell data
    PROVOCATION = { spell = 1900104 },
    -- Leap of Devotion: Faithful Leap RANK 2 (10s cooldown vs rank 1's
    -- 15s — pure spell data, both tooltips honest; the spell_ranks chain
    -- makes learn/unlearn swap the client's book and bars like any
    -- trained rank). The reconcile's RemoveSpell below the milestone
    -- reverts to rank 1 cleanly.
    LEAP_COOLDOWN = { spell = 1900106 },
    -- Double Buckle: hidden marker read by paragon_double_buckle.lua —
    -- gates attaching a second belt buckle and socketing its gem (the
    -- act, not ownership: opened sockets and gems survive a strip)
    DOUBLE_BUCKLE = { spell = 1900107 },
    -- Solo Conqueror: hidden marker mirroring milestone-1200 ownership —
    -- the stats themselves are reconciled by paragon_solo_dungeon.lua
    -- (clear registry keeps counting below the milestone; only the stats
    -- are gated)
    SOLO_DUNGEON = { spell = 1900117 },
}

local function ReconcileLearnedSpells(player, paragon)
    local level = paragon:GetLevel()
    for value, def in pairs(LEARNED_SPELL_SPECIALS) do
        local owed = SpecialRewardOwed(player, value, level)
        if owed and not player:HasSpell(def.spell) then
            player:LearnSpell(def.spell)
        elseif not owed and player:HasSpell(def.spell) then
            player:RemoveSpell(def.spell)
        end
    end
end

-- Milestone 1000's exclusive title. The custom CharTitles.dbc row (id 200,
-- mask bit 143) must exist on BOTH sides: the client displays it, the
-- server validates it here (ALE's SetKnownTitle looks the id up in
-- sCharTitlesStore — a missing server row makes the grant a silent no-op)
-- and again when the player picks the title from the character-pane
-- dropdown. Generated + deployed by Tools/paragon_client_patch.py.
-- Grant-only: titles are never revoked (the trainer-rank ownership
-- philosophy above; known titles persist in characters.knownTitles).
local TITLE_SPECIALS = {
    TITLE = { id = 200 },
}

local function ReconcileTitles(player, paragon)
    local level = paragon:GetLevel()
    for value, def in pairs(TITLE_SPECIALS) do
        if SpecialRewardOwed(player, value, level) and not player:HasTitle(def.id) then
            player:SetKnownTitle(def.id)
        end
    end
end

-- ============================================================================
-- LIFECYCLE: login apply / logout remove / reallocation cycle
-- ============================================================================

-- Fired at login (after async paragon load), logout, stat reallocation and
-- Lua reload. Use the paragon argument: it is the authoritative object at
-- every call site. Return nothing (mediator merges returns).
RegisterMediatorEvent("OnAfterUpdatePlayerStatistics", function(player, paragon, apply)
    local ok, err = pcall(function()
        local level = paragon:GetLevel()
        for _, milestone in ipairs(TRACK) do
            if milestone.level <= level then
                SetMilestone(player, milestone, apply)
            end
        end
        -- Persistent rewards reconcile on the apply pass only; logout must
        -- leave the saved bonus untouched (and the aura may persist).
        if apply then
            ReconcileBonusTalents(player, paragon)
            ReconcileSpecialAuras(player, paragon)
            ReconcileLearnedSpells(player, paragon)
            ReconcileTitles(player, paragon)
        end
    end)
    if not ok then
        print("[Paragon] Rework: reward track statistics error: " .. tostring(err))
    end
end)

-- ============================================================================
-- ON-REACH GRANT (during XP cascades, never at login)
-- ============================================================================

local function MilestoneText(milestone, class_id)
    local parts = {}
    for _, reward in ipairs(ResolveRewards(milestone, class_id)) do
        if reward.label then
            table.insert(parts, reward.label)
        else
            table.insert(parts, string.format("+%d %s", reward.amount, LABELS[reward.value] or reward.value))
        end
    end
    return table.concat(parts, ", ")
end

-- player can be nil (resolved by GUID mid-cascade); the mediator re-raises
-- subscriber errors, so the body is pcall-wrapped to never abort a level-up.
RegisterMediatorEvent("OnParagonLevelChanged", function(player, paragon, old_level, new_level)
    local ok, err = pcall(function()
        if not player then
            return
        end
        for _, milestone in ipairs(TRACK) do
            if old_level < milestone.level and milestone.level <= new_level then
                if SetMilestone(player, milestone, true) then
                    -- A class with no reward at this milestone gets no message
                    local text = MilestoneText(milestone, player:GetClass())
                    if text ~= "" then
                        player:SendBroadcastMessage(string.format(
                            "|cff00ff00[Paragon]|r Reward Track unlocked: %s!", text))
                    end
                end
            end
        end
        -- Per-100 talent point crossings PAST the milestone get their own
        -- toast (the unlock message above covers the initial grant; the
        -- actual points land in the reconcile either way)
        if old_level >= PER100_LEVEL then
            local gained = (math.floor(new_level / 100) - math.floor(old_level / 100)) * PER100_AMOUNT
            if gained > 0 then
                player:SendBroadcastMessage(string.format(
                    "|cff00ff00[Paragon]|r +%d bonus Talent Point%s (%d Paragon levels reached)",
                    gained, gained == 1 and "" or "s", math.floor(new_level / 100) * 100))
            end
        end
        ReconcileBonusTalents(player, paragon)
        ReconcileSpecialAuras(player, paragon)
        ReconcileLearnedSpells(player, paragon)
        ReconcileTitles(player, paragon)
    end)
    if not ok then
        print("[Paragon] Rework: reward track level-change error: " .. tostring(err))
    end
end)

-- ============================================================================
-- CLIENT DEFINITIONS PUSH (opcode 7, sent during the client load request)
-- ============================================================================

-- Client payload derived from TRACK with server-only fields stripped. Built
-- per class (class-dependent milestones resolve to that class's reward, so a
-- character only ever sees what it will actually get) and cached.
local client_track_cache = {}

local function ClientTrack(class_id)
    local cached = client_track_cache[class_id]
    if cached then
        return cached
    end

    local out = {}
    for i, milestone in ipairs(TRACK) do
        local rewards = {}
        for j, reward in ipairs(ResolveRewards(milestone, class_id)) do
            rewards[j] = { type = reward.type, value = reward.value, amount = reward.amount,
                label = reward.label, talent = reward.talent }
        end
        out[i] = { level = milestone.level, icon = ResolveIcon(milestone, class_id),
            title = ResolveTitle(milestone, class_id), rewards = rewards }
    end

    client_track_cache[class_id] = out
    return out
end

-- An error here would re-raise through the mediator and abort the load reply
-- before opcodes 1-4 are sent, so it is pcall-wrapped like the other handlers.
RegisterMediatorEvent("OnAfterClientLoadRequest", function(player, paragon, categories)
    local ok, err = pcall(function()
        player:SendServerResponse(Hook.Addon.Prefix, 7, ClientTrack(player:GetClass()))
    end)
    if not ok then
        print("[Paragon] Rework: reward track client push error: " .. tostring(err))
    end
end)

print("[Paragon] Rework: reward track module loaded")
