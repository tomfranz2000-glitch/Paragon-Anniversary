--[[
    Paragon Rework: Racially Ambiguous — milestone 1400 (2026-08-20)

    One ACTIVE racial ability belonging to another race, freely swappable
    out of combat. TWELVE options, not ten: Dwarf and Undead each have two
    actives, and Human "Perception" (58985) is a PASSIVE in 3.3.5 so it
    does not qualify. Classified from SPELL_ATTR0_PASSIVE (Spell.dbc field
    4), never from memory.

    !! THE ORIGINAL SPELL IDS ARE GRANTED, NOT CLONES !!
    Cloning is the obvious plan and it is WRONG here, because the ACTIVE
    racials are precisely the ones the core hardcodes BY SPELL ID:
      * Every Man for Himself's CC break is not spell data at all. The
        mask is assigned by literal id in Spell.cpp (::CheckCast) AND in
        SpellInfo.cpp (_immuneInfo), plus a SpellInfoCorrections attribute
        fix. A clone would break free of precisely nothing.
      * Gift of the Naaru's whole heal comes from spell_gen_gift_of_naaru
        switching on the spell's OWN SpellFamilyName -- all seven rows are
        otherwise byte-identical (eff 6 / bp 9 / aura 8). A clone tagged
        GENERIC hits the script's `default: break` and heals ZERO.
      * Blood Fury and Arcane Torrent variants differ for real (AP+RAP vs
        AP+spellpower vs spellpower; 15 energy vs 6% mana vs 150 runic).
    Granting originals resolves every variant natively -- see PICKS below.

    THE GATE: Player::CheckSkillLearnedBySpell runs from _LoadSpells at
    EVERY LOGIN and deletes any spell whose SkillLineAbility skill line
    fails GetSkillRaceClassInfo(skill, race, class). Every racial hangs off
    its own race's line, so with no help a granted racial works perfectly
    until the next relog and then silently vanishes. Ten
    skillraceclassinfo_dbc override rows (RaceMask 0) open the ten lines --
    same mechanism as codex nodes 56/57 (doc 2h/2i), same catastrophic
    failure mode when a row goes missing, hence REQUIRED_SRCI at the foot
    of this file.

    !! NO CLIENT MIRROR, DELIBERATELY -- THIS INVERTS THE 2h RULE !!
    2h says to mirror a gate row into the client MPQ when granting a SKILL,
    because the Skills pane filters through the client's own SRCI. Here
    that filtering is exactly what we want. These racials carry
    AcquireMethod 2 (LEARNED_ON_SKILL_LEARN), so _addSpell really does
    create the foreign racial skill line server-side, and all ten lines sit
    in SkillLineCategory 9 "Secondary Skills" -- the same category as First
    Aid and Riding, i.e. DISPLAYED. Leaving the client copy race-locked
    keeps "Racial - Troll" out of a Draenei's Skills pane while the server
    keeps the row that makes the spell survive login. Mirroring it would
    ADD the clutter, not remove it.

    Persistence: acore_ale.paragon_racial_pick (guid, pick_key), one row
    per character.

    Protocol (prefix "ParagonRacial"):
      S->C [1] state { unlocked, milestone, pick, spell,
                       options = { { key, race, raceName, spell }, ... } }
      C->S [1] { action = "pick", key = "..." } | { action = "clear" }
    Options are resolved FOR THE PLAYER'S CLASS server-side and carry only
    ids: the client reads name/icon/tooltip straight out of its own DBC
    with GetSpellInfo, so no name or icon text is ever shipped.
]]

local Constant = require("paragon_constant")

local DB = Constant.DB_NAME
local PREFIX = "ParagonRacial"

--- Must match the TRACK row in paragon_rework_track.lua. Same shape as
--- paragon_solo_dungeon.lua's MILESTONE: the reward-track entry at this
--- level is purely informational (SPECIAL rewards are skipped by
--- ApplyReward), this module is the enforcement.
local MILESTONE = 1400

local PICK_KEY = "ParagonRacialPick"
local LOADED_KEY = "ParagonRacialLoaded"

-- 3.3.5 race ids (9 is unused/Goblin, hence the gap)
local HUMAN, ORC, DWARF, NIGHTELF, UNDEAD = 1, 2, 3, 4, 5
local TAUREN, GNOME, TROLL, BLOODELF, DRAENEI = 6, 7, 8, 10, 11

-- 3.3.5 class ids (10 is unused, hence the gap before DRUID)
local WARRIOR, PALADIN, HUNTER, ROGUE, PRIEST = 1, 2, 3, 4, 5
local DK, SHAMAN, MAGE, WARLOCK, DRUID = 6, 7, 8, 9, 11

local RACE_NAME = {
    [HUMAN] = "Human", [ORC] = "Orc", [DWARF] = "Dwarf",
    [NIGHTELF] = "Night Elf", [UNDEAD] = "Undead", [TAUREN] = "Tauren",
    [GNOME] = "Gnome", [TROLL] = "Troll", [BLOODELF] = "Blood Elf",
    [DRAENEI] = "Draenei",
}

--- Racial ABILITY skill line per race. NOT the language line: Night Elf
--- 113 and Tauren 115 are Darnassian and Taurahe. playercreateinfo_skills
--- lists two lines per race and only SkillLine.dbc's name separates them;
--- these ten are the ones whose DisplayName reads "<Race> Racial".
local RACE_LINE = {
    [HUMAN] = 754, [ORC] = 125, [DWARF] = 101, [NIGHTELF] = 126,
    [UNDEAD] = 220, [TAUREN] = 124, [GNOME] = 753, [TROLL] = 733,
    [BLOODELF] = 756, [DRAENEI] = 760,
}

--- Every spell on each racial line, straight out of SkillLineAbility.dbc.
--- Needed because opening a line is not surgical: LearnSpell -> _addSpell
--- -> LearnDefaultSkill -> SetSkill -> learnSkillRewardedSpells hands over
--- EVERY sibling on the line whose own RaceMask is 0. Only two lines carry
--- such siblings today -- 756 leaks Magic Resistance (822) and Arcane
--- Affinity (28877), 760 leaks Gemcutting (28875) -- but the scrub below
--- walks the whole list so a future data change cannot leak past it.
local LINE_SPELLS = {
    [754] = { 20599, 20597, 20598, 20864, 58985, 59752 },
    [125] = { 20572, 20573, 20574, 20575, 20576, 21563, 33702, 33697, 54562, 65222 },
    [101] = { 2481, 20596, 20595, 20594, 59224 },
    [126] = { 20583, 20582, 20585, 58984, 21009 },
    [220] = { 5227, 7744, 20577, 20579 },
    [124] = { 20549, 20550, 20551, 20552 },
    [753] = { 20589, 20591, 20593, 20592 },
    [733] = { 20555, 20557, 20558, 26290, 26297, 58943 },
    [756] = { 25046, 28730, 822, 28877, 50613 },
    [760] = { 6562, 28878, 28880, 28875, 59221, 59539, 59536, 59541, 59535,
              59538, 59540, 59545, 59543, 59548, 59542, 59544, 59547 },
}

--- The twelve picks. `byClass` resolves the class variant; `spell` is the
--- fallback for classes the original data never had to cover (an Orc can
--- never be a Paladin, so stock Blood Fury has no Paladin row). Every
--- fallback is a REAL stock spell id, so the core's id-keyed handling
--- applies untouched.
local PICKS = {
    { key = "emfh",          race = HUMAN,    spell = 59752 },

    -- Blood Fury: three genuinely different auras. 20572 = attack power +
    -- ranged attack power, 33697 = attack power + spell damage, 33702 =
    -- spell damage only. Mapped by what the class actually scales with.
    { key = "blood_fury",    race = ORC,      spell = 33697, byClass = {
        [WARRIOR] = 20572, [HUNTER] = 20572, [ROGUE] = 20572, [DK] = 20572,
        [PALADIN] = 33697, [SHAMAN] = 33697, [DRUID] = 33697,
        [PRIEST]  = 33702, [MAGE]   = 33702, [WARLOCK] = 33702,
    } },

    { key = "stoneform",     race = DWARF,    spell = 20594 },
    { key = "find_treasure", race = DWARF,    spell = 2481  },
    { key = "shadowmeld",    race = NIGHTELF, spell = 58984 },
    { key = "wotf",          race = UNDEAD,   spell = 7744  },
    { key = "cannibalize",   race = UNDEAD,   spell = 20577 },
    { key = "war_stomp",     race = TAUREN,   spell = 20549 },
    { key = "escape_artist", race = GNOME,    spell = 20589 },
    { key = "berserking",    race = TROLL,    spell = 26297 },

    -- Arcane Torrent: the silence is identical, the restore is not --
    -- 25046 gives energy, 50613 gives 150 runic power, 28730 gives 6%
    -- mana. Warriors have no matching variant in stock data and keep the
    -- mana row: the silence still works, the restore is simply inert.
    { key = "arcane_torrent", race = BLOODELF, spell = 28730, byClass = {
        [ROGUE] = 25046, [DK] = 50613,
    } },

    -- Gift of the Naaru: all seven rows are identical spell data. The heal
    -- is computed by spell_gen_gift_of_naaru from the SPELL's OWN
    -- SpellFamilyName -- WARRIOR/HUNTER/DEATHKNIGHT scale off attack
    -- power, MAGE/WARLOCK/PRIEST off spell power, PALADIN/SHAMAN off
    -- max(spell power, attack power). Verified from Spell.dbc field 208.
    -- The three classes with no stock row inherit the family that matches
    -- how they scale: Rogue -> the warrior row (AP), Warlock -> the priest
    -- row (SP), Druid -> the paladin row (hybrid).
    { key = "gift_of_naaru", race = DRAENEI, spell = 59542, byClass = {
        [WARRIOR] = 28880, [PALADIN] = 59542, [HUNTER] = 59543,
        [PRIEST]  = 59544, [DK]      = 59545, [SHAMAN] = 59547,
        [MAGE]    = 59548,
        [ROGUE]   = 28880, [WARLOCK] = 59544, [DRUID]  = 59542,
    } },
}

local PICK_BY_KEY = {}
for _, pick in ipairs(PICKS) do
    PICK_BY_KEY[pick.key] = pick
end

-- ============================================================================
-- HELPERS
-- ============================================================================

local function IsBot(player)
    return player.IsPlayerBot and player:IsPlayerBot()
end

--- The class-resolved spell id for a pick.
local function SpellFor(pick, class_id)
    return (pick.byClass and pick.byClass[class_id]) or pick.spell
end

--- Session-cached; the DB is read once per character per session.
local function LoadPick(player)
    if player:GetData(LOADED_KEY) then
        return player:GetData(PICK_KEY)
    end
    local q = CharDBQuery(string.format(
        "SELECT pick_key FROM %s.paragon_racial_pick WHERE guid = %d;",
        DB, player:GetGUIDLow()))
    local key = q and q:GetString(0) or nil
    -- A key retired by a later data pass must not resurrect as a nil pick
    -- that still blocks the panel: drop it and let the player choose again.
    if key and not PICK_BY_KEY[key] then
        key = nil
    end
    player:SetData(PICK_KEY, key)
    player:SetData(LOADED_KEY, true)
    return key
end

--- key is ALWAYS a validated PICKS key by the time it reaches here (the
--- request handler refuses anything else), so it can never carry SQL.
local function Persist(player, key)
    local guid = player:GetGUIDLow()
    if key then
        CharDBExecute(string.format(
            "REPLACE INTO %s.paragon_racial_pick (guid, pick_key) VALUES (%d, '%s');",
            DB, guid, key))
    else
        CharDBExecute(string.format(
            "DELETE FROM %s.paragon_racial_pick WHERE guid = %d;", DB, guid))
    end
end

-- ============================================================================
-- GRANT / SCRUB
-- ============================================================================

--- Idempotent and self-healing: derives the whole spell state from the
--- stored pick every time, so a half-applied swap, a missed logout or a
--- de-level all converge on the next call.
local function Reconcile(player, paragon)
    local race_id = player:GetRace()
    local own_line = RACE_LINE[race_id]
    if not own_line then
        -- Unknown race: bail rather than risk the teardown loop below
        -- treating the player's NATIVE line as foreign and stripping it.
        return
    end

    local key = LoadPick(player)
    local pick = key and PICK_BY_KEY[key] or nil
    local unlocked = paragon and paragon:GetLevel() >= MILESTONE

    local want, want_line
    if pick and unlocked and pick.race ~= race_id then
        want = SpellFor(pick, player:GetClass())
        want_line = RACE_LINE[pick.race]
    end

    -- 1. Tear down every FOREIGN racial line that is not the one we want.
    --    SetSkill(line, 0, 0, 0) drops the skill and, in the same call,
    --    every spell on it (Player::SetSkill's remove branch walks
    --    GetSkillLineAbilitiesBySkillLine) -- so the old pick and any
    --    sibling it dragged in both go in one shot. own_line is excluded,
    --    which is what keeps a Blood Elf's real Magic Resistance safe.
    for _, line in pairs(RACE_LINE) do
        if line ~= own_line and line ~= want_line then
            if player:HasSkill(line) then
                player:SetSkill(line, 0, 0, 0)
            end
            -- Belt and braces for the case where the spell exists without
            -- the skill ever having been created (a pick granted before
            -- the override rows went live).
            for _, spell in ipairs(LINE_SPELLS[line]) do
                if player:HasSpell(spell) then
                    player:RemoveSpell(spell)
                end
            end
        end
    end

    if not want then
        return
    end

    -- 2. Grant. LearnSpell -> _addSpell -> LearnDefaultSkill creates the
    --    foreign line; the skillraceclassinfo_dbc override row is the only
    --    reason that succeeds, and also the only reason _LoadSpells keeps
    --    the spell at the next login.
    if not player:HasSpell(want) then
        player:LearnSpell(want)
    end

    -- 3. Scrub the freeloaders the line grant handed over. Unconditional
    --    is safe: want_line is never own_line, so nothing on it is native
    --    to this character. Sticks, because the only paths that re-run
    --    learnSkillRewardedSpells are SetSkill (ours) and UpdateSkillPro
    --    (use-based skill-ups, which racial lines never get).
    for _, spell in ipairs(LINE_SPELLS[want_line]) do
        if spell ~= want and player:HasSpell(spell) then
            player:RemoveSpell(spell)
        end
    end
end

-- ============================================================================
-- CLIENT PROTOCOL
-- ============================================================================

local function Options(player)
    local race_id = player:GetRace()
    local class_id = player:GetClass()
    local out = {}
    for _, pick in ipairs(PICKS) do
        if pick.race ~= race_id then
            out[#out + 1] = {
                key = pick.key,
                race = pick.race,
                raceName = RACE_NAME[pick.race],
                spell = SpellFor(pick, class_id),
            }
        end
    end
    return out
end

local function PushState(player, paragon)
    paragon = paragon or player:GetData("Paragon")
    local key = LoadPick(player)
    local pick = key and PICK_BY_KEY[key] or nil
    player:SendServerResponse(PREFIX, 1, {
        unlocked = (paragon and paragon:GetLevel() >= MILESTONE) or false,
        milestone = MILESTONE,
        pick = key,
        spell = pick and SpellFor(pick, player:GetClass()) or nil,
        options = Options(player),
    })
end

local function Deny(player, text)
    player:SendBroadcastMessage("|cffff4040[Paragon]|r " .. text)
end

function OnParagonRacialClientRequest(player, arg_table)
    local ok, err = pcall(function()
        local data = arg_table and arg_table[1]
        if not player or type(data) ~= "table" then
            return
        end
        local paragon = player:GetData("Paragon")
        if not paragon then
            return
        end
        if IsBot(player) or player:GetLevel() < 80 then
            return
        end
        if paragon:GetLevel() < MILESTONE then
            Deny(player, string.format(
                "Racially Ambiguous unlocks at Paragon level %d.", MILESTONE))
            return
        end

        local current = LoadPick(player)

        -- Swapping unlearns and relearns, and both clear the spell's
        -- cooldown -- so an unguarded swap is a free cooldown reset (use
        -- Berserking, swap away, swap back). Refusing while the CURRENT
        -- pick is still cooling down closes that completely: the player
        -- has to wait out the cooldown either way.
        if current then
            local held = SpellFor(PICK_BY_KEY[current], player:GetClass())
            if player:HasSpellCooldown(held) then
                Deny(player, "You cannot change racial abilities while the "
                    .. "one you have is on cooldown.")
                return
            end
        end
        if player:IsInCombat() then
            Deny(player, "You cannot change racial abilities in combat.")
            return
        end

        if data.action == "clear" then
            player:SetData(PICK_KEY, nil)
            Persist(player, nil)
        elseif data.action == "pick" then
            local pick = type(data.key) == "string" and PICK_BY_KEY[data.key]
            if not pick then
                return
            end
            if pick.race == player:GetRace() then
                Deny(player, "That is already your own race's ability.")
                return
            end
            if data.key == current then
                return
            end
            player:SetData(PICK_KEY, pick.key)
            Persist(player, pick.key)
        else
            return
        end

        Reconcile(player, paragon)
        PushState(player, paragon)
    end)
    if not ok then
        print("[Paragon] racial pick request error: " .. tostring(err))
    end
end

RegisterClientRequests({
    Prefix = PREFIX,
    Functions = { [1] = "OnParagonRacialClientRequest" },
})

-- ============================================================================
-- LIFECYCLE
-- ============================================================================

RegisterMediatorEvent("OnAfterUpdatePlayerStatistics", function(player, paragon, apply)
    local ok, err = pcall(function()
        -- apply=false is the logout/reallocation strip pass. The pick is
        -- persistent character state, not a cycled stat bonus, so it is
        -- reconciled on the apply pass only -- stripping at logout would
        -- unlearn the ability every session for no reason.
        if apply and player and paragon and not IsBot(player) and player:GetLevel() >= 80 then
            Reconcile(player, paragon)
        end
    end)
    if not ok then
        print("[Paragon] racial pick statistics error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnAfterClientLoadRequest", function(player, paragon)
    local ok, err = pcall(function()
        if player then
            PushState(player, paragon)
        end
    end)
    if not ok then
        print("[Paragon] racial pick load-push error: " .. tostring(err))
    end
end)

-- Crossing 1400 has to grant immediately, and a de-level has to take it
-- back. INLINE BOT/LEVEL GATE per the account-gate contract: this handler
-- fires outside the statistics apply chain, so the sub-80 protection that
-- OnBeforeUpdatePlayerStatistics provides never runs for it.
RegisterMediatorEvent("OnParagonLevelChanged", function(player, paragon, old_level, new_level)
    local ok, err = pcall(function()
        if not (player and paragon) then
            return
        end
        -- Unlike the codex, PushState is INSIDE the gate here. The codex
        -- leaves its push ungated because it only refreshes panel numbers,
        -- but this module's PushState calls LoadPick, which issues a DB
        -- query the first time it runs for a character. Ungated that is one
        -- query per BOT per paragon level-up -- thousands of them on this
        -- server. A bot has no client to push to either way.
        if IsBot(player) or player:GetLevel() < 80 then
            return
        end
        Reconcile(player, paragon)
        if old_level and new_level and old_level < MILESTONE and MILESTONE <= new_level then
            player:SendBroadcastMessage("|cff00ff00[Paragon]|r Racially Ambiguous: "
                .. "open the Reward Track and click the Paragon "
                .. MILESTONE .. " milestone to choose a racial ability.")
        end
        PushState(player, paragon)
    end)
    if not ok then
        print("[Paragon] racial pick level-change error: " .. tostring(err))
    end
end)

CharDBExecute(string.format(
    "CREATE TABLE IF NOT EXISTS %s.paragon_racial_pick ("
    .. "guid INT UNSIGNED NOT NULL, pick_key VARCHAR(32) NOT NULL, "
    .. "PRIMARY KEY (guid));", DB))

-- Every granted racial hangs off one of these ten override rows. If a
-- world-DB re-import drops one, Player::CheckSkillLearnedBySpell refuses
-- that race's ability at EVERY login -- and the refused spell leaves an
-- ORPHANED character_spell row which the next save re-INSERTs, rolling the
-- character's ENTIRE save transaction back, every save, forever. Same
-- alarm shape (and same caveat) as the codex's REQUIRED_SRCI: this reads
-- the DB, so it catches a dropped row but NOT "row present, worldserver
-- never restarted" -- DBC overrides merge into the store at startup only.
do
    local missing = {}
    for race_id, line in pairs(RACE_LINE) do
        local q = WorldDBQuery(string.format(
            "SELECT RaceMask FROM skillraceclassinfo_dbc WHERE SkillID = %d;", line))
        if not q or q:GetUInt32(0) ~= 0 then
            missing[#missing + 1] = string.format("%s (skill %d)",
                RACE_NAME[race_id] or "?", line)
        end
    end
    if #missing > 0 then
        print(string.format(
            "[Paragon] !! MILESTONE %d (Racially Ambiguous) IS BROKEN: "
            .. "skillraceclassinfo_dbc override rows are missing or their RaceMask "
            .. "is not 0 for: %s. Picking one of those abilities will work until "
            .. "the next relog and then CORRUPT THE CHARACTER'S SAVES. Re-apply "
            .. "from Tools/paragon_client_patch.py and RESTART the worldserver.",
            MILESTONE, table.concat(missing, ", ")))
    end
end

print(string.format("[Paragon] Rework: racial pick module loaded (%d options)", #PICKS))
