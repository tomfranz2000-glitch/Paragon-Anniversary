--[[
    Paragon Rework: the Codex — point-spending node system (2026-08-18)

    Replaces the upstream flat statistics spender with tiered nodes
    (user design):

      ATTRIBUTES  per primary stat a two-node chain: Greater (+5/rank,
                  cap 20) gating Endless (+1/rank, uncapped)
      WARFARE     ten rating nodes, 1 rating/point, cap 50; each node
                  feeds every variant of its rating (crit = melee +
                  ranged + spell, resilience = the three crit-taken)
      WARDS       Prismatic Ward center (5 pts/rank, +1 ALL resistances,
                  cap 25) + six school petals (1 pt -> +1, cap 20)
      MASTERY     exotic costs: Boundless Knowledge (+1 talent point,
                  cost 10 and +2 per rank bought, uncapped) and
                  Timeless Body (-1 effective stat-scaling level,
                  50 pts/rank, cap 3 — markers 1900096-98 summed by the
                  §1g core patch with the milestone markers)

    PERMANENT nodes (`permanent = true`): a node whose effect is one-way
    persistent character state rather than a reversible modset. Both the
    single-rank refund AND the respec branch must honour the flag — guarding
    only one leaves the other as a free rebate. Currently: Sticky Fingers
    (56), which teaches Lockpicking, and Ley Line Atlas (57), which teaches
    the mage city teleports. Both depend on a skillraceclassinfo_dbc override
    row -- see each node's comment.

    Points: the upstream pool (level x POINTS_PER_LEVEL). The old
    statistics spender is retired (its allocations were zeroed at
    migration and its UI is hidden client-side), so paragon:GetPoints()
    returns the full pool and the codex's own spend is subtracted here:
    available = GetPoints() - codex spend. (The original design note said
    all capped content totalled 995 points; nodes added since have moved
    that -- recompute from NODES rather than trusting a number here.)

    Persistence: acore_ale.paragon_codex_alloc (guid, node_id, node_rank)
    — per CHARACTER, like the old spender. Loaded once per session.

    Application: ladder-module pattern — a diff-gated modset reconcile
    on OnAfterUpdatePlayerStatistics (stats/ratings/resists), aura
    markers for Timeless Body, and the talent ranks join the track's
    OwedBonusTalents through the ParagonCodex_BonusTalents global.

    Protocol (prefix "ParagonCodex"):
      S->C [1] state { available, ranks = { [id] = rank } }
           [2] definitions (static node table, pushed at client load)
      C->S [1] { action = "buy"|"refund"|"respec", node = id,
                 count = n (buy only, default 1) }
]]

local Constant = require("paragon_constant")

local DB = Constant.DB_NAME
local PREFIX = "ParagonCodex"
local RANKS_KEY = "ParagonCodexRanks"
local APPLIED_KEY = "ParagonCodexApplied"

local ALL_RES = { "RESISTANCE_HOLY", "RESISTANCE_FIRE", "RESISTANCE_NATURE",
                  "RESISTANCE_FROST", "RESISTANCE_SHADOW", "RESISTANCE_ARCANE" }

-- Paragon Ascendance (node 58) source data: what each class gains in
-- attributes and in FLAT base health/mana across levels 1 -> 80, read out of
-- acore_world.player_class_stats (the Level 80 row minus the Level 1 row).
-- RACE IS DELIBERATELY ABSENT: player_race_stats is a flat per-race offset
-- that never varies with level, so the level-derived gain is purely a
-- function of class.
--
-- Class 6 (Death Knight) has NO level-1 row -- player_class_stats only
-- carries levels 55-80 for it, because DKs start at 55. Its curve is however
-- identical to the warrior's everywhere the two overlap (level 55:
-- 108/73/99/29/42 vs 109/73/100/29/42, BaseHP 1359 for both; level 80 BaseHP
-- 8121 for both), so the row below is the DK's own level-80 stats minus the
-- WARRIOR's level-1 baseline (23/20/22/20/20, BaseHP 20).
--
-- Zero mana for warrior/rogue/DK is correct rather than a hole: those classes
-- gain no mana at all from levelling (rage/energy/runic power are not level
-- scaled here). They draw the three largest health numbers instead.
--
-- Values line up with CLASS_LEVEL_KEYS index for index. Keep BOTH as ARRAYS:
-- SameMods diffs the applied modset positionally, so the emission order has
-- to be deterministic -- a pairs() walk over a hash would churn the whole set
-- (and with it the health-preserving strip/reapply) on every reconcile.
local CLASS_LEVEL_KEYS = { "STAT_STRENGTH", "STAT_AGILITY", "STAT_STAMINA",
                           "STAT_INTELLECT", "STAT_SPIRIT", "HEALTH", "MANA" }
local CLASS_LEVEL_GAIN = {
    [1]  = { 151,  93, 137,  16,  39, 8101,    0 },  -- Warrior
    [2]  = { 129,  70, 121,  78,  84, 6906, 4334 },  -- Paladin
    [3]  = {  54, 158, 107,  70,  76, 7278, 4981 },  -- Hunter
    [4]  = {  92, 166,  84,  23,  47, 7579,    0 },  -- Rogue
    [5]  = {  23,  31,  47, 152, 158, 6908, 3790 },  -- Priest
    [6]  = { 152,  92, 138,  15,  39, 8101,    0 },  -- Death Knight (see above)
    [7]  = {  99,  54, 115, 107, 121, 6899, 4311 },  -- Shaman
    [8]  = {  16,  23,  39, 158, 152, 6931, 3168 },  -- Mage
    [9]  = {  39,  47,  68, 137, 144, 7113, 3766 },  -- Warlock
    [11] = {  68,  62,  78, 121, 137, 7373, 3436 },  -- Druid
}
-- Paragon levels per full 1-80 lifetime. PARAGON_LEVEL_CAP is 10000 on this
-- realm, so the node tops out at ten lifetimes; that is the intended design.
local CLASS_LEVEL_PERIOD = 1000
local CLASS_LEVEL_WORDS_LONG  = { "Strength", "Agility", "Stamina",
                                  "Intellect", "Spirit", "health", "mana" }
local CLASS_LEVEL_WORDS_SHORT = { "Str", "Agi", "Sta", "Int", "Spi",
                                  "health", "mana" }

-- The whole design in one table. per = amount per rank, cap 0 = uncapped,
-- cost = points for rank 1, step = cost increase per further rank,
-- requires = { node, rank } gate. kind drives application:
--   stat    UNIT_MODS[stat] x per x rank
--   ratings every listed combat rating x per x rank
--   resist  every listed resistance unit-mod x per x rank
--   talent  joins the track's bonus-talent reconcile (global below)
--   scaling aura markers auras[1..rank] (§1g core patch sums their bp)
--   classlevel CLASS_LEVEL_GAIN[class] x paragon level / CLASS_LEVEL_PERIOD
local NODES = {
    { id = 1,  family = "attr", name = "Greater Might",      icon = "Interface/Icons/Ability_Warrior_InnerRage",     kind = "stat", stat = "STAT_STRENGTH", per = 5, cap = 20, cost = 1 },
    { id = 2,  family = "attr", name = "Endless Might",      icon = "Interface/Icons/Ability_Warrior_InnerRage",     kind = "stat", stat = "STAT_STRENGTH", per = 1, cap = 0,  cost = 1, requires = { node = 1, rank = 20 } },
    { id = 3,  family = "attr", name = "Greater Grace",      icon = "Interface/Icons/Ability_Rogue_Sprint",          kind = "stat", stat = "STAT_AGILITY",  per = 5, cap = 20, cost = 1 },
    { id = 4,  family = "attr", name = "Endless Grace",      icon = "Interface/Icons/Ability_Rogue_Sprint",          kind = "stat", stat = "STAT_AGILITY",  per = 1, cap = 0,  cost = 1, requires = { node = 3, rank = 20 } },
    { id = 5,  family = "attr", name = "Greater Fortitude",  icon = "Interface/Icons/Spell_Holy_WordFortitude",      kind = "stat", stat = "STAT_STAMINA",  per = 5, cap = 20, cost = 1 },
    { id = 6,  family = "attr", name = "Endless Fortitude",  icon = "Interface/Icons/Spell_Holy_WordFortitude",      kind = "stat", stat = "STAT_STAMINA",  per = 1, cap = 0,  cost = 1, requires = { node = 5, rank = 20 } },
    { id = 7,  family = "attr", name = "Greater Brilliance", icon = "Interface/Icons/Spell_Holy_MagicalSentry",      kind = "stat", stat = "STAT_INTELLECT", per = 5, cap = 20, cost = 1 },
    { id = 8,  family = "attr", name = "Endless Brilliance", icon = "Interface/Icons/Spell_Holy_MagicalSentry",      kind = "stat", stat = "STAT_INTELLECT", per = 1, cap = 0,  cost = 1, requires = { node = 7, rank = 20 } },
    { id = 9,  family = "attr", name = "Greater Serenity",   icon = "Interface/Icons/Spell_Holy_SpiritualGuidence",  kind = "stat", stat = "STAT_SPIRIT",   per = 5, cap = 20, cost = 1 },
    { id = 10, family = "attr", name = "Endless Serenity",   icon = "Interface/Icons/Spell_Holy_SpiritualGuidence",  kind = "stat", stat = "STAT_SPIRIT",   per = 1, cap = 0,  cost = 1, requires = { node = 9, rank = 20 } },

    { id = 20, family = "war", name = "Deadly Precision",  icon = "Interface/Icons/Ability_CriticalStrike",        kind = "ratings", ratings = { "CRIT_MELEE", "CRIT_RANGED", "CRIT_SPELL" },                   per = 1, cap = 50, cost = 1 },
    { id = 21, family = "war", name = "Quickened Hands",   icon = "Interface/Icons/Spell_Nature_Bloodlust",        kind = "ratings", ratings = { "HASTE_MELEE", "HASTE_RANGED", "HASTE_SPELL" },                per = 1, cap = 50, cost = 1 },
    { id = 22, family = "war", name = "True Aim",          icon = "Interface/Icons/Ability_Marksmanship",          kind = "ratings", ratings = { "HIT_MELEE", "HIT_RANGED", "HIT_SPELL" },                      per = 1, cap = 50, cost = 1 },
    { id = 23, family = "war", name = "Seasoned Blade",    icon = "Interface/Icons/Spell_Holy_BlessingOfStrength", kind = "ratings", ratings = { "EXPERTISE" },                                                 per = 1, cap = 50, cost = 1 },
    { id = 24, family = "war", name = "Sunder Armor",      icon = "Interface/Icons/Ability_Warrior_Riposte",       kind = "ratings", ratings = { "ARMOR_PENETRATION" },                                         per = 1, cap = 50, cost = 1 },
    { id = 25, family = "war", name = "Stalwart Guard",    icon = "Interface/Icons/Spell_Holy_MindSooth",          kind = "ratings", ratings = { "DEFENSE_SKILL" },                                             per = 1, cap = 50, cost = 1 },
    { id = 26, family = "war", name = "Fleet Footwork",    icon = "Interface/Icons/Spell_Arcane_Blink",            kind = "ratings", ratings = { "DODGE" },                                                     per = 1, cap = 50, cost = 1 },
    { id = 27, family = "war", name = "Riposte",           icon = "Interface/Icons/Ability_Parry",                 kind = "ratings", ratings = { "PARRY" },                                                     per = 1, cap = 50, cost = 1 },
    { id = 28, family = "war", name = "Shield Discipline", icon = "Interface/Icons/Ability_Defend",                kind = "ratings", ratings = { "BLOCK" },                                                     per = 1, cap = 50, cost = 1 },
    { id = 29, family = "war", name = "Iron Will",         icon = "Interface/Icons/Spell_Holy_ArdentDefender",     kind = "ratings", ratings = { "CRIT_TAKEN_MELEE", "CRIT_TAKEN_RANGED", "CRIT_TAKEN_SPELL" }, per = 1, cap = 50, cost = 1 },
    -- Warfare column 6 (2026-08-19): flat sustain nodes. Lifeblood is a plain
    -- HEALTH unit-mod; Meditation has no mp5 UNIT_MOD/rating so it joins
    -- Petting Zoo's summed total on the 1900099 regen aura (kind "regen").
    { id = 38, family = "war", name = "Lifeblood",         icon = "Interface/Icons/Ability_Warrior_RallyingCry",   kind = "stat",  stat = "HEALTH",                                                            per = 20, cap = 50, cost = 1 },
    { id = 39, family = "war", name = "Meditation",        icon = "Interface/Icons/Spell_Nature_ManaRegenTotem",   kind = "regen",                                                                             per = 2,  cap = 50, cost = 1 },

    { id = 30, family = "ward", name = "Prismatic Ward", icon = "Interface/Icons/Spell_Arcane_PrismaticCloak",  kind = "resist", resist = ALL_RES,                  per = 1, cap = 25, cost = 5 },
    { id = 31, family = "ward", name = "Ward of Light",  icon = "Interface/Icons/Spell_Holy_BlessingOfProtection", kind = "resist", resist = { "RESISTANCE_HOLY" },   per = 1, cap = 20, cost = 1 },
    { id = 32, family = "ward", name = "Ward of Flame",  icon = "Interface/Icons/Spell_Fire_FireArmor",         kind = "resist", resist = { "RESISTANCE_FIRE" },   per = 1, cap = 20, cost = 1 },
    { id = 33, family = "ward", name = "Ward of Thorns", icon = "Interface/Icons/Spell_Nature_ResistNature",    kind = "resist", resist = { "RESISTANCE_NATURE" }, per = 1, cap = 20, cost = 1 },
    { id = 34, family = "ward", name = "Ward of Frost",  icon = "Interface/Icons/Spell_Frost_FrostWard",        kind = "resist", resist = { "RESISTANCE_FROST" },  per = 1, cap = 20, cost = 1 },
    { id = 35, family = "ward", name = "Ward of Shadow", icon = "Interface/Icons/Spell_Shadow_AntiShadow",      kind = "resist", resist = { "RESISTANCE_SHADOW" }, per = 1, cap = 20, cost = 1 },
    { id = 36, family = "ward", name = "Ward of Runes",  icon = "Interface/Icons/Spell_Shadow_AntiMagicShell",  kind = "resist", resist = { "RESISTANCE_ARCANE" }, per = 1, cap = 20, cost = 1 },

    -- Mastery plaque order = array order (client 2-per-row grid, row-major;
    -- keep in sync with the twin comment in Paragon_Codex.lua):
    --   row 0  Starter Pack / Eternal Student
    --   row 1  Petting Zoo / Cute Companions
    --   row 2  Paragon Ascendance / Skies of Azeroth
    --   row 3  Sticky Fingers / Ley Line Atlas
    --   row 4  Boundless Knowledge / Timeless Body
    -- Rows 2 and 4 were swapped on request (2026-08-19), which is why the
    -- 58/59 pair now sits mid-table and the 50/51 pair last. POSITION IN THIS
    -- ARRAY IS THE ONLY THING THAT PLACES A PLAQUE -- moving an entry moves
    -- its tile, and inserting rather than appending reshuffles every pair
    -- below it. Nothing else depends on this order (NODE_BY_ID is keyed, and
    -- ComputeMods only needs SameMods to see a consistent order within one
    -- session, which it does).
    -- Starter Pack (2026-08-19): cheap opener bundle. Talents ride the
    -- Boundless Knowledge reconcile (talents field), stats are plain mods
    -- (scale list x rank), and the XP share joins the single additive
    -- factor in paragon_collection_xp.lua via ParagonCodex_ExperiencePercent.
    { id = 54, family = "mastery", name = "Starter Pack", icon = "Interface/Icons/INV_Misc_Gift_01",
      kind = "bundle", per = 1, cap = 1, cost = 1, talents = 2, xp_percent = 10,
      scale = { { key = "STAT_STRENGTH",  per = 10 }, { key = "STAT_AGILITY", per = 10 },
                { key = "STAT_STAMINA",   per = 10 }, { key = "STAT_INTELLECT", per = 10 },
                { key = "STAT_SPIRIT",    per = 10 } } },
    -- Eternal Student (2026-08-19): pure XP node — kind "xp" is a no-op in
    -- ComputeMods; the whole effect flows through ParagonCodex_
    -- ExperiencePercent's ranks x xp_percent sum into the additive factor.
    { id = 55, family = "mastery", name = "Eternal Student", icon = "Interface/Icons/INV_Enchant_EssenceEternalLarge",
      kind = "xp", per = 5, cap = 20, cost = 5, xp_percent = 5 },
    -- Collection-scaling mastery (2026-08-19): magnitude = scale entries x
    -- the LIVE mount/companion count (spellbook scan, same source of truth
    -- as paragon_collection_xp.lua), re-read on every reconcile. Petting
    -- Zoo's mp5 half has no UNIT_MOD/rating/binding — it rides the
    -- server-only aura 1900099 (MOD_POWER_REGEN) via a custom-bp cast.
    { id = 52, family = "mastery", name = "Petting Zoo",     icon = "Interface/Icons/Ability_Mount_RidingHorse",
      kind = "collection", collection = "mounts", per = 1, cap = 1, cost = 10,
      scale = { { key = "HEALTH", per = 20 } }, regen = 5 },
    { id = 53, family = "mastery", name = "Cute Companions", icon = "Interface/Icons/INV_Box_PetCarrier_01",
      kind = "collection", collection = "companions", per = 1, cap = 1, cost = 50,
      scale = { { key = "HASTE_MELEE",  per = 1, rating = true },
                { key = "HASTE_RANGED", per = 1, rating = true },
                { key = "HASTE_SPELL",  per = 1, rating = true } } },
    -- Paragon Ascendance (2026-08-19): the first LEVEL-DERIVED node. Its
    -- magnitude is not per x rank at all -- it is the player's OWN class gain
    -- from levels 1-80 (CLASS_LEVEL_GAIN above), scaled by paragon level over
    -- CLASS_LEVEL_PERIOD. 1000 paragon levels therefore pay exactly one
    -- lifetime of level-ups, and the realm's 10000 cap pays ten. `per` is
    -- carried only to satisfy the shared node shape; ComputeMods ignores it.
    --
    -- Refundable ON PURPOSE, unlike 56/57: every component is a plain
    -- reversible UNIT_MODS flat modifier, so there is no irreversible
    -- character state to strand and no reason to charge the player forever.
    --
    -- This node is why OnParagonLevelChanged now calls Reconcile and not just
    -- PushState: the amount moves with paragon level, so without that the
    -- bonus silently lags behind until the next full statistics pass.
    { id = 58, family = "mastery", name = "Paragon Ascendance",
      icon = "Interface/Icons/Spell_Holy_EmpowerChampion",
      kind = "classlevel", per = 1, cap = 1, cost = 10 },
    -- Skies of Azeroth (2026-08-19): 100 pts, PERMANENT. Pure marker node --
    -- it teaches server-only spell 1900130 and nothing else; all the work is
    -- in core patch §1r (SpellInfo::CheckLocation), which waives the
    -- AREA_FLAG_OUTLAND term of AreaTableEntry::IsFlyable() on maps 0 and 1
    -- for holders of that marker.
    --
    -- kind "skill" with node.skill DELIBERATELY unset: ReconcileGrants'
    -- scalar arm then does exactly one thing -- LearnSpell the marker if it
    -- is missing -- and HandleBuy's already-owned guard collapses to
    -- HasSpell(1900130). No skillraceclassinfo_dbc row and no REQUIRED_SRCI
    -- entry, because there is no skill line involved.
    --
    -- MARKER MUST BE LEARNED, NOT AN AURA: _LoadSpells runs before _LoadAuras
    -- at login, so a learned marker is already present when the core re-runs
    -- CheckLocation; an aura marker would lose that race.
    --
    -- The 9 scaling "hybrid" mounts driven by spell_gen_mount (Celestial
    -- Steed, X-53, Invincible, Headless Horseman's Mount, Winged Steed,
    -- Blazing Hippogryph, Big Love Rocket, Magic Broom, Big Blizzard Bear)
    -- compute their own canFly and never consult CheckLocation, so
    -- spell_generic.cpp carries a SECOND copy of the same marker waiver.
    -- Shipping only the first one left three of them (Winged Steed, Blazing
    -- Hippogryph, X-53) actively broken rather than merely grounded: those
    -- three have no ground variant at all, so the cast summoned a mount model
    -- at walking speed. Change one waiver, change the other.
    --
    -- Scope is the VIRTUAL continent, so it also covers the seven playable
    -- zones that physically sit on map 530 (Eversong, Ghostlands, Silvermoon,
    -- Quel'Danas -> 0; Azuremyst, Bloodmyst, Exodar -> 1). Real Outland and
    -- all of Northrend are untouched.
    { id = 59, family = "mastery", name = "Skies of Azeroth",
      icon = "Interface/Icons/Ability_Mount_Gryphon_01",
      kind = "skill", per = 1, cap = 1, cost = 100, permanent = true,
      spell = 1900130, teaches = "flight over Azeroth" },
    -- Sticky Fingers (2026-08-19): the first PERMANENT node. Its plaque
    -- position comes purely from where this entry sits in the array — see
    -- the row map at the top of the mastery block.
    --
    -- kind "skill" is a deliberate no-op in ComputeMods: this is not a
    -- reversible stat modset but one-way persistent character state (a
    -- character_skills row + a learned spell), applied by ReconcileGrants.
    -- Non-refundable is not a flavour choice — ALE exposes no skill-removal
    -- method at all, and the core's only removal idiom (SetSkill with currVal
    -- 0) cascades through every SkillLineAbility on the line calling
    -- removeSpell, far too blunt to hang a refund on.
    --
    -- Requires the skillraceclassinfo_dbc override row emitted by
    -- Tools/paragon_client_patch.py. WITHOUT IT the core silently deletes
    -- both the skill and spell 1804 at the next login and these 25 points buy
    -- nothing, permanently. Skill starts at value 1 / max 400 (level x 5) and
    -- is raised by picking locks, exactly as a rogue does it.
    { id = 56, family = "mastery", name = "Sticky Fingers", icon = "Interface/Icons/Spell_Nature_MoonKey",
      kind = "skill", per = 1, cap = 1, cost = 25, permanent = true,
      spell = 1804, skill = 633, teaches = "Lockpicking" },
    -- Ley Line Atlas (2026-08-19): second PERMANENT node, and the first
    -- MULTI-rank one. Each rank teaches the next stock mage city teleport for
    -- the player's own faction; `spells` is keyed by Player:GetTeam() ->
    -- TeamId (0 = ALLIANCE, 1 = HORDE).
    --
    -- STOCK spell ids on purpose, not 1900xxx clones: the "you already know
    -- this" refusal is HasSpell(<stock id>), which is only meaningful against
    -- the ids a real mage actually owns, and every id + icon already ships in
    -- the client so no MPQ patch is needed at all.
    --
    -- Rank order is the spellLevel ladder (20/20/20/30/35/60) AND matches
    -- acore_world.player_factionchange_spells index-for-index, so a paid
    -- faction change swaps each granted teleport to its counterpart and the
    -- rank stays coherent. NEVER persist a faction on the grant - resolve the
    -- list live from GetTeam().
    --
    -- Requires the skillraceclassinfo_dbc row 55 (skill 237 "Arcane",
    -- ClassMask 0) emitted by Tools/paragon_client_patch.py. WITHOUT IT
    -- CheckSkillLearnedBySpell refuses these spells at every login and the
    -- resulting orphaned character_spell row breaks the character's ENTIRE
    -- save transaction on a duplicate key - see the doc, this one is nasty.
    { id = 57, family = "mastery", name = "Ley Line Atlas",
      icon = "Interface/Icons/Spell_Arcane_TeleportShattrath",
      kind = "skill", per = 1, cap = 6, cost = 10, permanent = true,
      teaches = "the mage teleports",
      -- Declared as DATA so the client can grey the plaque and skip the
      -- irreversible-purchase popup, instead of the server silently refusing
      -- after the player has already confirmed. The old message ("You already
      -- know the mage teleports") was also a lie for a mage who never trained
      -- Shattrath -- the real reason is the class, not the spellbook.
      -- Mages are blocked from EVERY rank, by user decision: the node is
      -- redundant content for them (their own trainer sells all six), and a
      -- per-rank refusal cannot work on a sequential ladder anyway -- refusing
      -- one rung would strand every higher rank on a node that cannot be
      -- refunded. Same shape as node 56 refusing a rogue.
      deniedClass = 8,   -- CLASS_MAGE
      deniedText = "You're a mage - go to your class trainer instead.",
      spells = {
        [0] = { { 3561,  "Teleport: Stormwind"     },
                { 3562,  "Teleport: Ironforge"     },
                { 32271, "Teleport: Exodar"        },
                { 3565,  "Teleport: Darnassus"     },
                { 49359, "Teleport: Theramore"     },
                { 33690, "Teleport: Shattrath"     } },
        [1] = { { 3567,  "Teleport: Orgrimmar"     },
                { 3563,  "Teleport: Undercity"     },
                { 32272, "Teleport: Silvermoon"    },
                { 3566,  "Teleport: Thunder Bluff" },
                { 49358, "Teleport: Stonard"       },
                { 35715, "Teleport: Shattrath"     } },
      } },
    { id = 50, family = "mastery", name = "Boundless Knowledge", icon = "Interface/Icons/INV_Misc_Book_09",       kind = "talent",  per = 1, cap = 0, cost = 10, step = 2, talents = 1 },
    { id = 51, family = "mastery", name = "Timeless Body",       icon = "Interface/Icons/Spell_Holy_BorrowedTime", kind = "scaling", per = 1, cap = 3, cost = 50,
      auras = { 1900096, 1900097, 1900098 } },
    -- Ward of Ages (2026-08-20): 1% less damage taken per rank, flat 25
    -- points each (no `step`), cap 10. kind "mitigation" is a no-op in the
    -- modset -- there is no UNIT_MOD or rating for damage reduction, so it
    -- rides the server-only aura 1900138 exactly as node 52's mp5 half rides
    -- 1900099: a hidden non-passive aura recast with the wanted value as
    -- custom basepoints (ReconcileMitigation below).
    --
    -- ONE aura carrying -rank, never `rank` stacks of -1%: aura 87 stacks
    -- MULTIPLICATIVELY (Unit::GetTotalAuraMultiplier AddPct's each effect),
    -- so ten -1% auras would come to 0.99^10 = -9.56%, not -10%.
    --
    -- Appended LAST on purpose: the mastery plaque grid is 2-per-row in
    -- ARRAY ORDER, so this lands alone on row 5 at y -506..-550, still well
    -- inside the 680-tall content frame.
    { id = 60, family = "mastery", name = "Ward of Ages",       icon = "Interface/Icons/Spell_Nature_SkinofEarth", kind = "mitigation", per = 1, cap = 10, cost = 25 },
    -- Fleet of Ages (2026-08-20): +1% movement speed per rank, flat 10
    -- points each, cap 25. Aura 1900140 carries THREE effects because
    -- Unit::UpdateSpeed reads a different aura per movement mode --
    -- 129 unmounted run, 130 mounted run, 209 mounted flight -- and they do
    -- not cross over. All three ride the one CastCustomSpell (bp0/bp1/bp2).
    --
    -- The *_ALWAYS family is load-bearing: the plain MOD_INCREASE_SPEED (31)
    -- family is max-picked (GetMaxPositiveAuraModifier), so a 1% aura there
    -- would be permanently masked by Sprint / Aspect of the Cheetah /
    -- milestone 50 and do nothing at all. *_ALWAYS goes through
    -- GetTotalAuraMultiplier and then multiplies ON TOP of the max-picked
    -- winner, which is what makes this genuinely additive to other sources.
    --
    -- Pairs with node 60 on plaque row 5 (2 per row, ARRAY ORDER).
    { id = 61, family = "mastery", name = "Fleet of Ages",      icon = "Interface/Icons/Spell_Nature_Swiftness",   kind = "movespeed",  per = 1, cap = 25, cost = 10 },
}

local NODE_BY_ID = {}
for _, node in ipairs(NODES) do
    NODE_BY_ID[node.id] = node
end

-- A short per-faction grant list would strand PAID, UNREFUNDABLE points:
-- HandleBuy commits the rank and Persist() writes it BEFORE Reconcile() runs,
-- so a nil hole would charge the player and then throw inside a pcall that
-- only prints to console. Fail at load instead.
for _, node in ipairs(NODES) do
    if node.spells then
        for team, list in pairs(node.spells) do
            assert(#list == node.cap, string.format(
                "codex node %d: team %d grant list has %d entries, cap %d",
                node.id, team, #list, node.cap))
        end
    end
end

-- Tooltip copy, pushed with the definitions (user spec: every node says
-- what it actually does): desc = the per-rank effect sentence, unit = the
-- stat word for the client's "Current bonus: +N <unit>" line.
local NODE_DESC = {
    [1]  = "+5 Strength per rank.",
    [2]  = "+1 Strength per rank. No limit.",
    [3]  = "+5 Agility per rank.",
    [4]  = "+1 Agility per rank. No limit.",
    [5]  = "+5 Stamina per rank.",
    [6]  = "+1 Stamina per rank. No limit.",
    [7]  = "+5 Intellect per rank.",
    [8]  = "+1 Intellect per rank. No limit.",
    [9]  = "+5 Spirit per rank.",
    [10] = "+1 Spirit per rank. No limit.",
    [60] = "Reduces all damage you take by 1% per rank. Stacks multiplicatively with other damage reduction, so each rank always removes 1% of whatever still gets through.",
    [61] = "+1% movement speed per rank, on foot and mounted, in the air and on the ground. Multiplies with other speed effects rather than competing with them.",
    [20] = "+1 melee, ranged and spell critical strike rating per rank.",
    [21] = "+1 melee, ranged and spell haste rating per rank.",
    [22] = "+1 melee, ranged and spell hit rating per rank.",
    [23] = "+1 expertise rating per rank.",
    [24] = "+1 armor penetration rating per rank.",
    [25] = "+1 defense rating per rank.",
    [26] = "+1 dodge rating per rank.",
    [27] = "+1 parry rating per rank.",
    [28] = "+1 shield block rating per rank.",
    [29] = "+1 resilience rating per rank (reduces melee, ranged and spell critical strikes against you).",
    [30] = "+1 resistance to all six magic schools per rank.",
    [31] = "+1 Holy Resistance per rank.",
    [32] = "+1 Fire Resistance per rank.",
    [33] = "+1 Nature Resistance per rank.",
    [34] = "+1 Frost Resistance per rank.",
    [35] = "+1 Shadow Resistance per rank.",
    [36] = "+1 Arcane Resistance per rank.",
    [38] = "+20 maximum Health per rank.",
    [39] = "+2 mana per 5 seconds per rank.",
    [50] = "+1 Talent Point per rank. Every rank bought raises the next rank's cost by 2 Paragon points.",
    [51] = "Each rank lets your attributes scale as if you were 1 additional level lower — stronger stat conversions across the board. Stacks with the milestone scaling bonuses.",
    [52] = "+20 maximum Health and +5 mana per 5 seconds for every mount you have collected. Grows as your stable does.",
    [53] = "+1 melee, ranged and spell haste rating for every companion you have collected. Grows as your menagerie does.",
    [54] = "+2 Talent Points, +10 to all attributes and +10% Paragon experience from all sources (additive with your other Paragon experience bonuses).",
    [55] = "+5% Paragon experience from all sources per rank (additive with your other Paragon experience bonuses).",
    -- Built from the node's own grant list so the copy can never drift from
    -- what is actually taught. A function because the city names differ by
    -- faction; PushDefinitions resolves it per player.
    [57] = function(player)
        local list = NODE_BY_ID[57].spells[player:GetTeam()] or NODE_BY_ID[57].spells[0]
        local cities = {}
        for i, entry in ipairs(list) do
            cities[i] = (entry[2]:gsub("^Teleport: ", ""))
        end
        return string.format(
            "Teaches one mage city teleport per rank, in order: %s. Each takes 10 seconds "
            .. "to cast, cannot be used in combat or while moving, and consumes a Rune of "
            .. "Teleportation. |cffff4040Permanent: this node can never be refunded, and a "
            .. "Respec will not reclaim its points.|r",
            table.concat(cities, ", "))
    end,
    [58] = function(player)
        local gain = CLASS_LEVEL_GAIN[player:GetClass()]
        if not gain then
            return "Grants a share of your class's own level-up gains, scaled "
                .. "by your Paragon level."
        end
        local parts = {}
        for i, amount in ipairs(gain) do
            if amount > 0 then
                parts[#parts + 1] = string.format("%d %s", amount,
                    CLASS_LEVEL_WORDS_LONG[i])
            end
        end
        return string.format(
            "Your Paragon levels grant the very same gains your class earns "
            .. "from levelling 1 to 80. Every %d Paragon levels are worth one "
            .. "full lifetime of them: %s. Partial progress counts, rounded "
            .. "down.", CLASS_LEVEL_PERIOD, table.concat(parts, ", "))
    end,
    [59] = "Lets you use flying mounts across Eastern Kingdoms and Kalimdor, and in the blood elf and draenei starting zones \226\128\148 all of which stay grounded for everyone else. Outland is unchanged, Northrend still requires Cold Weather Flying, and no-fly areas such as Dalaran remain closed. |cffff4040Permanent: this node can never be refunded, and a Respec will not reclaim its points.|r",
    [56] = "Teaches Lockpicking and the Pick Lock ability — normally a rogue-only skill. It starts at 1 and rises as you pick locks, up to 400. |cffff4040Permanent: this node can never be refunded, and a Respec will not reclaim its points.|r",
}
local NODE_UNIT = {
    [1] = "Strength", [2] = "Strength", [3] = "Agility", [4] = "Agility",
    [5] = "Stamina", [6] = "Stamina", [7] = "Intellect", [8] = "Intellect",
    [9] = "Spirit", [10] = "Spirit",
    [20] = "Critical Strike Rating", [21] = "Haste Rating", [22] = "Hit Rating",
    [23] = "Expertise Rating", [24] = "Armor Penetration Rating",
    [25] = "Defense Rating", [26] = "Dodge Rating", [27] = "Parry Rating",
    [28] = "Block Rating", [29] = "Resilience Rating",
    [30] = "to all Resistances", [31] = "Holy Resistance",
    [32] = "Fire Resistance", [33] = "Nature Resistance",
    [34] = "Frost Resistance", [35] = "Shadow Resistance",
    [36] = "Arcane Resistance",
    [38] = "Health", [39] = "mana per 5 sec",
    [50] = "Talent Points", [51] = "effective levels",
    [52] = "Health", [53] = "Haste Rating",
    [55] = "% Paragon experience",
}

-- ============================================================================
-- COSTS
-- ============================================================================

local function RankCost(node, rank)
    return node.cost + (node.step or 0) * (rank - 1)
end

local function SpendOn(node, rank)
    local total = 0
    for r = 1, rank do
        total = total + RankCost(node, r)
    end
    return total
end

-- ============================================================================
-- PER-SESSION STATE
-- ============================================================================

--- Rank table { [node_id] = rank }, loaded once per session.
local function Ranks(player)
    local cached = player:GetData(RANKS_KEY)
    if cached ~= nil then
        return cached
    end
    local ranks = {}
    local q = CharDBQuery(string.format(
        "SELECT node_id, node_rank FROM %s.paragon_codex_alloc WHERE guid = %d;",
        DB, player:GetGUIDLow()))
    if q then
        repeat
            local node = NODE_BY_ID[q:GetUInt32(0)]
            if node then
                local rank = q:GetUInt32(1)
                ranks[node.id] = node.cap > 0 and math.min(rank, node.cap) or rank
            end
        until not q:NextRow()
    end
    player:SetData(RANKS_KEY, ranks)
    return ranks
end

local function TotalSpend(ranks)
    local total = 0
    for id, rank in pairs(ranks) do
        total = total + SpendOn(NODE_BY_ID[id], rank)
    end
    return total
end

local function Available(player, paragon)
    return (paragon and paragon:GetPoints() or 0) - TotalSpend(Ranks(player))
end

--- Consumed by the track's OwedBonusTalents (single writer of the core's
--- extraBonusTalentCount field — the codex only reports its share): the
--- sum of every talent-granting node's ranks x its talents field
--- (Boundless Knowledge 1/rank, Starter Pack 2).
function ParagonCodex_BonusTalents(player)
    local ranks, total = Ranks(player), 0
    for _, node in ipairs(NODES) do
        if node.talents then
            total = total + (ranks[node.id] or 0) * node.talents
        end
    end
    return total
end

--- Additive percent joined into ParagonCollectionXP_Factor's single sum
--- (paragon_collection_xp.lua) — again, the codex only reports its share.
function ParagonCodex_ExperiencePercent(player)
    local ranks, total = Ranks(player), 0
    for _, node in ipairs(NODES) do
        if node.xp_percent then
            total = total + (ranks[node.id] or 0) * node.xp_percent
        end
    end
    return total
end

-- ============================================================================
-- COLLECTION COUNTS (Petting Zoo / Cute Companions)
-- ============================================================================

-- Same source of truth as paragon_collection_xp.lua: "collected" = spells
-- in the spellbook (account-wide via mod-collections), classified by the
-- generated ParagonMountSpells / ParagonCompanionSpells sets. Cached per
-- session; the learn hook in LIFECYCLE clears the cache on growth.
local COUNT_KEY = "ParagonCodexCollectionCounts"
local COLLECTION_SETS = {
    mounts = function() return ParagonMountSpells end,
    companions = function() return ParagonCompanionSpells end,
}

local function CollectionCount(player, which)
    local counts = player:GetData(COUNT_KEY)
    if not counts then
        counts = {}
        for key, set in pairs(COLLECTION_SETS) do
            local n = 0
            for spell in pairs(set() or {}) do
                if player:HasSpell(spell) then
                    n = n + 1
                end
            end
            counts[key] = n
        end
        player:SetData(COUNT_KEY, counts)
    end
    return counts[which] or 0
end

-- ============================================================================
-- APPLICATION (ladder-module pattern: diff-gated modset)
-- ============================================================================

local function ComputeMods(player, paragon)
    local ranks = Ranks(player)
    local mods, auras, regen, mitigation, movespeed = {}, {}, 0, 0, 0
    for _, node in ipairs(NODES) do
        local rank = ranks[node.id] or 0
        if rank > 0 then
            local amount = node.per * rank
            if node.kind == "stat" then
                mods[#mods + 1] = { key = node.stat, amount = amount }
            elseif node.kind == "ratings" then
                for _, cr in ipairs(node.ratings) do
                    mods[#mods + 1] = { key = cr, amount = amount, rating = true }
                end
            elseif node.kind == "resist" then
                for _, school in ipairs(node.resist) do
                    mods[#mods + 1] = { key = school, amount = amount }
                end
            elseif node.kind == "scaling" then
                for r = 1, rank do
                    auras[#auras + 1] = node.auras[r]
                end
            elseif node.kind == "regen" then
                regen = regen + amount
            elseif node.kind == "mitigation" then
                mitigation = mitigation + amount
            elseif node.kind == "movespeed" then
                movespeed = movespeed + amount
            elseif node.kind == "bundle" then
                for _, entry in ipairs(node.scale) do
                    mods[#mods + 1] = { key = entry.key, amount = entry.per * rank,
                        rating = entry.rating }
                end
            elseif node.kind == "collection" then
                local count = CollectionCount(player, node.collection)
                for _, entry in ipairs(node.scale) do
                    mods[#mods + 1] = { key = entry.key, amount = entry.per * count,
                        rating = entry.rating }
                end
                if node.regen then
                    regen = regen + node.regen * count
                end
            elseif node.kind == "classlevel" then
                -- Emit EVERY key unconditionally, including the zeros a low
                -- paragon level (or a manaless class) produces: a constant
                -- list length keeps SameMods diffing on amounts alone, and a
                -- 0 flat modifier is a no-op in HandleStatFlatModifier.
                local gain = CLASS_LEVEL_GAIN[player:GetClass()]
                local plevel = paragon and paragon:GetLevel() or 0
                if gain then
                    for i, key in ipairs(CLASS_LEVEL_KEYS) do
                        mods[#mods + 1] = { key = key, amount =
                            math.floor(gain[i] * plevel / CLASS_LEVEL_PERIOD) }
                    end
                else
                    print(string.format("[Paragon] !! CODEX NODE %d (%s) has no "
                        .. "level-gain row for class %d - it is paying nothing",
                        node.id, node.name, player:GetClass()))
                end
            end
        end
    end
    return mods, auras, regen, mitigation, movespeed
end

local function SameMods(a, b)
    if not a or not b or #a ~= #b then
        return false
    end
    for i, mod in ipairs(a) do
        local other = b[i]
        if mod.key ~= other.key or mod.amount ~= other.amount
                or mod.rating ~= other.rating then
            return false
        end
    end
    return true
end

local function ApplyMods(player, mods, apply)
    for _, mod in ipairs(mods) do
        if mod.rating then
            player:ApplyRatingMod(Constant.STATISTICS.COMBAT_RATING[mod.key], mod.amount, apply)
        else
            player:HandleStatFlatModifier(Constant.STATISTICS.UNIT_MODS[mod.key], 0, mod.amount, apply)
        end
    end
end

--- Timeless Body markers: ensure exactly the owned ranks' auras (idempotent;
--- passives survive death like every §1-family marker).
local function ReconcileAuras(player, want)
    local wanted = {}
    for _, id in ipairs(want) do
        wanted[id] = true
    end
    for _, id in ipairs(NODE_BY_ID[51].auras) do
        if wanted[id] and not player:HasAura(id) then
            player:AddAura(id, player)
        elseif not wanted[id] and player:HasAura(id) then
            player:RemoveAura(id)
        end
    end
end

--- Petting Zoo mp5: server-only aura 1900099 (MOD_POWER_REGEN mana, hidden,
--- infinite, death-persistent) cast with the wanted total as custom
--- basepoints — the one dynamic-amount channel, since no mp5 UNIT_MOD,
--- rating or binding exists. Amounts are session-tracked (aura effect
--- amounts are unreadable from Lua), so a stale relog-restored aura is
--- always recast on the first reconcile.
local REGEN_SPELL = 1900099
local REGEN_KEY = "ParagonCodexRegen"

local function ReconcileRegen(player, want)
    local applied = player:GetData(REGEN_KEY) or 0
    local has = player:HasAura(REGEN_SPELL)
    if want == applied and has == (want > 0) then
        return
    end
    if has then
        player:RemoveAura(REGEN_SPELL)
    end
    if want > 0 then
        player:CastCustomSpell(player, REGEN_SPELL, true, want)
    end
    player:SetData(REGEN_KEY, want > 0 and want or nil)
end

--- Ward of Ages (node 60): damage reduction has no UNIT_MOD, rating or
--- binding, so it rides the same dynamic-amount channel as the mp5 above --
--- server-only aura 1900138 (MOD_DAMAGE_PERCENT_TAKEN, misc 127 = all
--- schools, hidden, infinite, death-persistent) recast with the wanted
--- value as custom basepoints.
---
--- `want` is the RANK (positive); the aura amount must be NEGATIVE to
--- reduce damage, hence the -want in the cast. The spell row carries
--- EffectDieSides 0 so the basepoint passes through exactly -- and since
--- ALE cannot read aura effect amounts back, that row is the ONLY place
--- the value is verifiable. Amounts are session-tracked for the same
--- reason, so a stale relog-restored aura is always recast.
local MITIGATION_SPELL = 1900138
local MITIGATION_KEY = "ParagonCodexMitigation"

local function ReconcileMitigation(player, want)
    local applied = player:GetData(MITIGATION_KEY) or 0
    local has = player:HasAura(MITIGATION_SPELL)
    if want == applied and has == (want > 0) then
        return
    end
    if has then
        player:RemoveAura(MITIGATION_SPELL)
    end
    if want > 0 then
        player:CastCustomSpell(player, MITIGATION_SPELL, true, -want)
    end
    player:SetData(MITIGATION_KEY, want > 0 and want or nil)
end

--- Fleet of Ages (node 61): the THIRD user of the dynamic-amount channel
--- (see ReconcileRegen for the original). If a fourth ever appears, fold
--- the three into one table-driven reconcile -- they only differ in the
--- spell id, the sign, and how many basepoints the cast carries.
---
--- Three effects, one per movement mode, all set from the same rank:
--- CastCustomSpell takes bp0/bp1/bp2 (mod-ale UnitMethods.h) and the spell
--- row carries die 0 on all three, so each amount is exactly the rank.
local MOVEMENT_SPELL = 1900140
local MOVEMENT_KEY = "ParagonCodexMovement"

local function ReconcileMovement(player, want)
    local applied = player:GetData(MOVEMENT_KEY) or 0
    local has = player:HasAura(MOVEMENT_SPELL)
    if want == applied and has == (want > 0) then
        return
    end
    if has then
        player:RemoveAura(MOVEMENT_SPELL)
    end
    if want > 0 then
        player:CastCustomSpell(player, MOVEMENT_SPELL, true, want, want, want)
    end
    player:SetData(MOVEMENT_KEY, want > 0 and want or nil)
end

--- Permanent one-way grants (kind "skill"). Unlike every other node kind
--- these are NOT part of the diff-gated modset: a taught skill is a row in
--- character_skills and a learned spell, both of which persist on their own
--- and must never be taken back — hence `permanent = true` on the node.
---
--- Runs on every apply-side reconcile rather than only at purchase, so a
--- grant that got stripped comes back. Note the LIMIT of that self-heal: it
--- restores EXISTENCE, not VALUE. Once _LoadSkills has deleted the
--- character_skills row the trained value is gone from the DB, and both
--- re-grant paths re-seed at 1 (LearnDefaultSkill uses
--- max(1, GetSkillValue(633)), which reads 0 after the deletion). Losing the
--- override row therefore costs the player their whole grind, not just a
--- relog. Guard the row, do not rely on this.
---
--- LearnSpell comes first because Player::addSpell special-cases lockpicking
--- (Player.cpp:3378) and calls LearnDefaultSkill, which seeds value 1 /
--- max level x 5 and never lowers an existing value.
--- (The ReconcileGrants contract continues below GrantList.)

--- The grant list for this player's faction. Player:GetTeam() returns a
--- TeamId (0 = ALLIANCE, 1 = HORDE); a player is never TEAM_NEUTRAL, but fall
--- back to Alliance rather than indexing nil. Resolved LIVE on every call and
--- never persisted, so a paid faction change simply starts returning the
--- counterpart list -- which is coherent because the ranks are ordered to
--- match acore_world.player_factionchange_spells index-for-index.
local function GrantList(player, node)
    return node.spells[player:GetTeam()] or node.spells[0]
end

local function ReconcileGrants(player)
    local ranks = Ranks(player)
    for _, node in ipairs(NODES) do
        if node.kind == "skill" and (ranks[node.id] or 0) > 0 then
          if node.spells then
            -- Multi-rank grant: rank N owns the first N spells of the
            -- faction list. Deliberately NOT routed through the spell->skill
            -- bridge below; these spells teach no skill (AcquireMethod 0), so
            -- there is nothing to seed and no gate-closed probe to make.
            local list = GrantList(player, node)
            for r = 1, math.min(ranks[node.id], #list) do
                if not player:HasSpell(list[r][1]) then
                    player:LearnSpell(list[r][1])
                end
            end
          else
            local fresh = node.spell and not player:HasSpell(node.spell)
            if fresh then
                player:LearnSpell(node.spell)
            end
            if node.skill and not player:HasSkill(node.skill) then
                if fresh then
                    -- PRECISE PROBE, do not soften it: addSpell routes
                    -- lockpicking through LearnDefaultSkill, which early-
                    -- returns when GetSkillRaceClassInfo refuses the class.
                    -- So a missing skill right after a FRESH learn means the
                    -- skillraceclassinfo_dbc override row is not live in the
                    -- running DBC store. Fabricating it with SetSkill here
                    -- would "work" until the next login, when _LoadSkills
                    -- deletes it again -- turning a loud failure into a
                    -- silent one on a node that cannot be refunded. Shout
                    -- instead.
                    print(string.format(
                        "[Paragon] codex node %d: skill %d REFUSED for guid %d - the "
                        .. "skillraceclassinfo_dbc row 601 (ClassMask 0) is missing from "
                        .. "the running DBC store. Restore it and restart the worldserver.",
                        node.id, node.skill, player:GetGUIDLow()))
                else
                    -- Spell already known, so addSpell early-returned and no
                    -- LearnDefaultSkill ran: seed the skill ourselves.
                    player:SetSkill(node.skill, 0, 1, player:GetLevel() * 5)
                end
            end
          end
        end
    end
end

local function Reconcile(player, paragon, apply)
    local want, auras, regen, mitigation, movespeed = nil, {}, 0, 0, 0
    if apply and paragon then
        want, auras, regen, mitigation, movespeed = ComputeMods(player, paragon)
    end

    local applied = player:GetData(APPLIED_KEY)
    if applied or want then
        if not (applied and want and SameMods(applied, want)) then
            -- swapping the set momentarily drops max health and the core
            -- clamps current health to the lowered max (Unit::SetMaxHealth),
            -- so a count change (learning a mount with node 52 owned) would
            -- visibly drain the player — preserve health across the swap
            local health = apply and player:IsAlive() and player:GetHealth() or nil
            -- Mana needs the SAME rescue as health, for the same reason:
            -- lowering max mana clamps current mana and nothing puts it back.
            -- Node 58 is the first mod whose amount moves on EVERY paragon
            -- level-up, so without this a caster would leak a level's worth of
            -- mana at each one. POWER_MANA = 0. Mind the argument order, it
            -- differs between the two calls: GetPower(type) but
            -- SetPower(amount, type) (mod-ale UnitMethods.h:810 / :1543).
            local mana = apply and player:IsAlive() and player:GetPower(0) or nil
            if applied then
                ApplyMods(player, applied, false)
            end
            if want then
                ApplyMods(player, want, true)
            end
            player:SetData(APPLIED_KEY, want)
            if health then
                player:SetHealth(math.min(health, player:GetMaxHealth()))
            end
            -- > 0 skips rage/energy/runic-power classes, whose mana is 0
            if mana and mana > 0 then
                player:SetPower(math.min(mana, player:GetMaxPower(0)), 0)
            end
        end
    end
    if apply then
        ReconcileAuras(player, auras)
        ReconcileGrants(player)
    end
    ReconcileRegen(player, regen)
    ReconcileMitigation(player, mitigation)
    ReconcileMovement(player, movespeed)
end

-- ============================================================================
-- CLIENT SYNC
-- ============================================================================

--- Ready-made tooltip lines for the collection nodes (copy lives server-
--- side like NODE_DESC; the client prefixes "Current bonus:" / "At rank 1:"
--- by owned rank).
local function CollectionBonusText(player, node)
    local count = CollectionCount(player, node.collection)
    if node.id == 52 then
        return string.format("+%d Health and +%d mana per 5 sec (%d mounts)",
            20 * count, 5 * count, count)
    end
    return string.format("+%d melee, ranged and spell haste rating (%d companions)",
        count, count)
end

--- Live magnitude for node 58's green tooltip line. Recomputed on every
--- push, so it tracks paragon level without the client knowing the formula.
local function ClassLevelBonusText(player, paragon)
    local gain = CLASS_LEVEL_GAIN[player:GetClass()]
    if not gain then
        return "Scales with your Paragon level"
    end
    local plevel = paragon and paragon:GetLevel() or 0
    local parts = {}
    for i, per in ipairs(gain) do
        local amount = math.floor(per * plevel / CLASS_LEVEL_PERIOD)
        if amount > 0 then
            parts[#parts + 1] = string.format("+%d %s", amount,
                CLASS_LEVEL_WORDS_SHORT[i])
        end
    end
    if #parts == 0 then
        return "Nothing yet at your Paragon level"
    end
    return string.format("%s (Paragon %d)", table.concat(parts, ", "), plevel)
end

local function PushState(player, paragon)
    local bonus = {}
    for _, node in ipairs(NODES) do
        if node.kind == "collection" then
            bonus[node.id] = CollectionBonusText(player, node)
        elseif node.kind == "classlevel" then
            bonus[node.id] = ClassLevelBonusText(player, paragon)
        end
    end
    player:SendServerResponse(PREFIX, 1, {
        available = Available(player, paragon),
        ranks = Ranks(player),
        bonus = bonus,
    })
end

--- A node's description may be a function when the copy depends on the
--- player (node 57's city list is faction-specific).
local function ResolveDesc(player, id)
    local d = NODE_DESC[id]
    if type(d) == "function" then
        local ok, text = pcall(d, player)
        if not ok then
            print("[Paragon] codex desc error (node " .. tostring(id) .. "): " .. tostring(text))
            return nil
        end
        return text
    end
    return d
end

local function PushDefinitions(player)
    local defs = {}
    for i, node in ipairs(NODES) do
        defs[i] = { id = node.id, family = node.family, name = node.name,
            icon = node.icon, kind = node.kind, per = node.per, cap = node.cap,
            cost = node.cost, step = node.step or 0, requires = node.requires,
            permanent = node.permanent, deniedClass = node.deniedClass,
            deniedText = node.deniedText,
            desc = ResolveDesc(player, node.id), unit = NODE_UNIT[node.id] }
    end
    player:SendServerResponse(PREFIX, 2, defs)
end

-- ============================================================================
-- REQUESTS
-- ============================================================================

local function Deny(player, text)
    player:SendBroadcastMessage("|cffff4444[Paragon]|r " .. text)
end

local function HandleBuy(player, paragon, node, count)
    local ranks = Ranks(player)
    local bought = 0
    for _ = 1, count do
        local rank = ranks[node.id] or 0
        if node.cap > 0 and rank >= node.cap then
            break
        end
        if node.requires then
            local gate = ranks[node.requires.node] or 0
            if gate < node.requires.rank then
                if bought == 0 then
                    Deny(player, string.format("%s requires %s at rank %d.",
                        node.name, NODE_BY_ID[node.requires.node].name, node.requires.rank))
                end
                break
            end
        end
        -- kind "skill" nodes are one-way AND non-refundable, so a sale that
        -- grants nothing is unrecoverable. A rogue already owns both skill
        -- 633 and spell 1804, so ReconcileGrants' HasSpell/HasSkill guards
        -- would both no-op while the 25 points stayed spent forever. Refuse
        -- BEFORE any points are committed.
        if node.kind == "skill" then
            local blocked, what = false, node.teaches or node.name
            local denyText = nil
            if node.spells then
                -- NOTE the scalar form below INVERTS for a list node: with
                -- neither node.spell nor node.skill set, both halves read
                -- `not nil` = true and the node would be unbuyable.
                if node.deniedClass and player:GetClass() == node.deniedClass then
                    blocked, denyText = true, node.deniedText
                else
                    local list = GrantList(player, node)
                    if not list[rank + 1] then
                        blocked, denyText = true,
                            string.format("%s has nothing further to teach you.", node.name)
                    else
                        -- Block ONLY when NOTHING in the rest of the ladder is
                        -- left to teach. Blocking on the NEXT rung alone is a
                        -- TRAP: ranks are strictly sequential and this node is
                        -- permanent, so refusing one mid-ladder rung whose
                        -- spell arrived from OUTSIDE the node (a GM grant, a
                        -- future reward) would lock the character out of every
                        -- HIGHER rank forever, with no refund and no respec to
                        -- unwind it. Paying for a rung you already own is a far
                        -- smaller harm than a permanently bricked node -- and
                        -- the rank IS the price here (SpendOn sums RankCost
                        -- over 1..rank), so a free skip cannot be expressed.
                        local remaining = false
                        for r = rank + 1, #list do
                            if not player:HasSpell(list[r][1]) then
                                remaining = true
                                break
                            end
                        end
                        if not remaining then
                            blocked = true
                        end
                    end
                end
            else
                blocked = (not node.spell or player:HasSpell(node.spell))
                    and (not node.skill or player:HasSkill(node.skill))
            end
            if blocked then
                if bought == 0 then
                    Deny(player, denyText or string.format("You already know %s.", what))
                end
                break
            end
        end
        local cost = RankCost(node, rank + 1)
        if Available(player, paragon) < cost then
            if bought == 0 then
                Deny(player, string.format("Not enough Paragon points (%s costs %d).", node.name, cost))
            end
            break
        end
        ranks[node.id] = rank + 1
        bought = bought + 1
    end
    return bought > 0
end

local function HandleRefund(player, paragon, node)
    local ranks = Ranks(player)
    local rank = ranks[node.id] or 0
    if rank == 0 then
        return false
    end
    -- Permanent nodes hand out irreversible character state (a taught skill),
    -- so there is nothing to take back in exchange for the points. Guarded
    -- HERE and in the respec branch — guarding only one leaves the other as a
    -- free refund.
    if node.permanent then
        Deny(player, string.format("%s is permanent and cannot be refunded.", node.name))
        return false
    end
    -- refunding a Greater below an owned Endless gate would strand the
    -- chain: block while the dependent node has ranks
    for _, other in ipairs(NODES) do
        if other.requires and other.requires.node == node.id
                and (ranks[other.id] or 0) > 0 and rank - 1 < other.requires.rank then
            Deny(player, string.format("Refund %s first.", other.name))
            return false
        end
    end
    -- Talent-granting nodes (Boundless Knowledge, Starter Pack) hand out
    -- live talent points; if they are already spent in the tree, refunding
    -- would leave the talents learned for free (the reconcile clamps free
    -- points at zero). The points must be back in the pool first.
    if node.talents and player:GetFreeTalentPoints() < node.talents then
        Deny(player, "Unspend your talent points first (reset talents at your class trainer).")
        return false
    end
    ranks[node.id] = rank > 1 and rank - 1 or nil
    return true
end

local function Persist(player)
    local guid = player:GetGUIDLow()
    local ranks = Ranks(player)
    local rows = {}
    for id, rank in pairs(ranks) do
        rows[#rows + 1] = string.format("(%d,%d,%d)", guid, id, rank)
    end
    CharDBExecute(string.format("DELETE FROM %s.paragon_codex_alloc WHERE guid = %d;", DB, guid))
    if #rows > 0 then
        CharDBExecute(string.format(
            "INSERT INTO %s.paragon_codex_alloc (guid, node_id, node_rank) VALUES %s;",
            DB, table.concat(rows, ",")))
    end
end

function OnParagonCodexClientRequest(player, arg_table)
    local ok, err = pcall(function()
        local data = arg_table and arg_table[1]
        if not player or type(data) ~= "table" then
            return
        end
        local paragon = player:GetData("Paragon")
        if not paragon then
            return
        end
        if player:IsInCombat() then
            Deny(player, "You cannot reshape the Codex in combat.")
            return
        end
        -- account-gate contract (paragon_account_gate.lua): handlers outside
        -- the statistics apply chain carry their own inline level gate — a
        -- sub-80 alt shares the account's points and must get no benefit
        if player:GetLevel() < 80 then
            Deny(player, "The Codex answers only max-level characters.")
            return
        end

        local action = data.action
        local changed = false
        if action == "buy" then
            local node = NODE_BY_ID[tonumber(data.node) or 0]
            local count = math.min(math.max(tonumber(data.count) or 1, 1), 100)
            if node then
                changed = HandleBuy(player, paragon, node, count)
            end
        elseif action == "refund" then
            local node = NODE_BY_ID[tonumber(data.node) or 0]
            if node then
                changed = HandleRefund(player, paragon, node)
            end
        elseif action == "respec" then
            -- same talent guard as the single-rank refund: a full reset
            -- takes back every codex-granted talent point at once
            local owed = ParagonCodex_BonusTalents(player)
            if owed > 0 and player:GetFreeTalentPoints() < owed then
                Deny(player, string.format(
                    "Unspend your talent points first — resetting the Codex reclaims %d granted talent point%s.",
                    owed, owed == 1 and "" or "s"))
            else
                -- Permanent nodes SURVIVE a respec, keeping both their rank
                -- and their spend. Wiping to {} would hand back the points
                -- while the granted skill stayed learned — a free rebate, and
                -- the exact bypass the HandleRefund guard exists to prevent.
                local ranks, kept = Ranks(player), {}
                for _, n in ipairs(NODES) do
                    if n.permanent and (ranks[n.id] or 0) > 0 then
                        kept[n.id] = ranks[n.id]
                    end
                end
                player:SetData(RANKS_KEY, kept)
                changed = true
            end
        end

        if changed then
            Persist(player)
            Reconcile(player, paragon, true)
            if ParagonRework_PokeBonusTalents then
                ParagonRework_PokeBonusTalents(player)
            end
        end
        PushState(player, paragon)
    end)
    if not ok then
        print("[Paragon] codex request error: " .. tostring(err))
    end
end

RegisterClientRequests({
    Prefix = PREFIX,
    Functions = { [1] = "OnParagonCodexClientRequest" },
})

-- ============================================================================
-- LIFECYCLE
-- ============================================================================

RegisterMediatorEvent("OnAfterUpdatePlayerStatistics", function(player, paragon, apply)
    local ok, err = pcall(function()
        if player and paragon then
            Reconcile(player, paragon, apply)
        end
    end)
    if not ok then
        print("[Paragon] codex statistics error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnAfterClientLoadRequest", function(player, paragon)
    local ok, err = pcall(function()
        if player then
            PushDefinitions(player)
            PushState(player, paragon or player:GetData("Paragon"))
        end
    end)
    if not ok then
        print("[Paragon] codex load-push error: " .. tostring(err))
    end
end)

local function IsBot(player)
    return player.IsPlayerBot and player:IsPlayerBot()
end

-- Level-ups change the available pool: keep the open panel honest. Since
-- node 58 (Paragon Ascendance) derives its magnitude FROM the paragon level,
-- a level-up also has to re-run the modset here -- PushState alone would
-- leave the panel telling the truth while the character kept the previous
-- level's stats until the next full statistics pass. Same shape as
-- paragon_ilvl_bonus.lua's level-change handler. Reconcile is a no-op when
-- nothing moved (SameMods), so this stays cheap for everyone else.
RegisterMediatorEvent("OnParagonLevelChanged", function(player, paragon)
    local ok, err = pcall(function()
        if player and paragon then
            -- INLINE BOT/LEVEL GATE, per the account-gate contract: this
            -- handler fires outside the statistics apply chain, so the
            -- OnBeforeUpdatePlayerStatistics apply=false flip that protects
            -- sub-80 characters never runs for it. Without this the whole
            -- codex modset -- all 38 nodes, not just 58 -- would land on a
            -- sub-80 character on the account the moment paragon level moved.
            -- Same gate as the event-44 handler below.
            if not (IsBot(player) or player:GetLevel() < 80) then
                Reconcile(player, paragon, true)
            end
            -- PushState stays UNGATED, exactly as it was before node 58: it
            -- only refreshes the panel's numbers and the gate module already
            -- masks what a sub-80 client is shown.
            PushState(player, paragon)
        end
    end)
    if not ok then
        print("[Paragon] codex level-push error: " .. tostring(err))
    end
end)

-- A newly learned mount/companion changes node 52/53 magnitudes. Cache-clear
-- always; reconcile only once the paragon dataset is loaded — the login
-- re-teach storm runs before it, and each of those learns must stay cheap.
-- Inline bot/level gate per the account-gate contract (this handler fires
-- outside the statistics apply chain). IsBot is declared up by the
-- OnParagonLevelChanged handler, which needs the same gate.
RegisterPlayerEvent(44, function(event, player, spellId)
    local ok, err = pcall(function()
        local mounts, companions = ParagonMountSpells, ParagonCompanionSpells
        if not ((mounts and mounts[spellId]) or (companions and companions[spellId])) then
            return
        end
        player:SetData(COUNT_KEY, nil)
        if IsBot(player) or player:GetLevel() < 80 then
            return
        end
        local paragon = player:GetData("Paragon")
        if paragon then
            Reconcile(player, paragon, true)
            PushState(player, paragon)
        end
    end)
    if not ok then
        print("[Paragon] codex collection error: " .. tostring(err))
    end
end)

CharDBExecute(string.format(
    "CREATE TABLE IF NOT EXISTS %s.paragon_codex_alloc ("
    .. "guid INT UNSIGNED NOT NULL, node_id INT UNSIGNED NOT NULL, "
    .. "node_rank INT UNSIGNED NOT NULL, PRIMARY KEY (guid, node_id));", DB))

-- Both permanent nodes hang off a skillraceclassinfo_dbc override row that
-- opens a class-gated skill line. If a world-DB re-import ever drops one, the
-- core silently refuses the granted spell/skill at EVERY login -- and for node
-- 57 the refused spell leaves an ORPHANED character_spell row that the next
-- save re-INSERTs, rolling the character's ENTIRE save transaction back, every
-- save, forever. Both nodes are non-refundable, so the points are gone too.
--
-- Checked once at load and only against the DB (the running DBC store is not
-- readable from Lua), so this catches the realistic failure -- the row being
-- dropped -- but NOT "row present, worldserver never restarted".
local REQUIRED_SRCI = {
    { id = 55,  skill = 237, node = 57, what = "the mage teleports" },
    { id = 601, skill = 633, node = 56, what = "Lockpicking" },
}
for _, req in ipairs(REQUIRED_SRCI) do
    local q = WorldDBQuery(string.format(
        "SELECT ClassMask FROM skillraceclassinfo_dbc WHERE ID = %d AND SkillID = %d;",
        req.id, req.skill))
    if not q or q:GetUInt32(0) ~= 0 then
        print(string.format(
            "[Paragon] !! CODEX NODE %d (%s) IS BROKEN: skillraceclassinfo_dbc row %d "
            .. "(SkillID %d) is missing or its ClassMask is not 0. Re-apply it from "
            .. "Tools/paragon_client_patch.py and RESTART the worldserver.",
            req.node, req.what, req.id, req.skill))
    end
end

-- Node 59 carries no skill line, but it hangs off a spell_dbc row in exactly
-- the same way and fails just as silently: with 1900130 missing from the
-- running spell store, LearnSpell grants nothing, the §1r core patch's
-- HasSpell(1900130) is never true, and 100 NON-REFUNDABLE points buy a no-op
-- with no error anywhere. Same caveat as REQUIRED_SRCI above -- this reads the
-- DB, so it catches a dropped row but NOT "row present, worldserver never
-- restarted" (spell_dbc merges into the store at startup only).
do
    -- Checked against the RUNNING SPELL STORE, not just the DB: the ALE global
    -- GetSpellInfo goes straight to sSpellMgr->GetSpellInfo (mod-ale
    -- methods/GlobalMethods.h:3527-3532) and returns nil for an unknown id.
    -- That closes the blind spot the REQUIRED_SRCI loop above still has --
    -- spell_dbc merges into the store at STARTUP ONLY, so a row that is
    -- present in the DB but was added since the last boot is NOT live, and a
    -- DB-only check would call that healthy. The two probes are combined
    -- below so the message names the actual remedy.
    local live = GetSpellInfo and GetSpellInfo(1900130)
    if not live then
        local q = WorldDBQuery("SELECT COUNT(*) FROM spell_dbc WHERE ID = 1900130;")
        local inDB = q and q:GetUInt32(0) > 0
        print(string.format(
            "[Paragon] !! CODEX NODE 59 (Skies of Azeroth) IS BROKEN: flight marker "
            .. "1900130 is not in the running spell store, so LearnSpell grants nothing "
            .. "and 100 NON-REFUNDABLE points buy a no-op. %s",
            inDB and "The spell_dbc row EXISTS -- just RESTART the worldserver."
                 or "The spell_dbc row is MISSING -- re-apply it from "
                    .. "Tools/paragon_client_patch.py, then restart."))
    end
end

print(string.format("[Paragon] Rework: codex module loaded (%d nodes)", #NODES))
