--[[
    Paragon Rework: one-time collection XP rewards (2026-08-18 user spec)

    Flat paragon XP the first time an ACCOUNT obtains a mount, companion,
    or transmog appearance. Values are difficulty-scaled offline by
    Tools/paragon_collectible_xp.py (easiest-acquisition-path model,
    overrides for marquee items — Invincible 2M etc.) into:

        paragon_collectible_spell_xp   mount + companion spells (all)
        paragon_collectible_item_xp    appearances ABOVE the 1000 baseline

    FLAT by construction: awards go straight through the banking module's
    lump-sum pattern (Mediator "OnUpdatePlayerExperience"), which never
    passes OnExperienceCalculated — no collection/achievement/quest/
    transmog multiplier ever touches these, matching achievement XP.

    ONE-TIME per account via mirror tables (seeded by the classifier's
    --seed with the collections existing at deploy time, so nothing pays
    retroactively):

        paragon_rewarded_collectible_spell (account_id, spell_id)
        paragon_rewarded_appearance        (account_id, item_id)

    Mount/companion detection: PLAYER_EVENT_ON_LEARN_SPELL (44). The very
    first table lookup rejects 99.9% of learn events (bots learn spells
    constantly; mod-collections also re-teaches the whole collection at
    every login — those all hit the mirror). Sub-80 characters do not
    collect the reward NOR the mirror mark: mod-collections re-teaches at
    every login account-wide, so the 80 main's next login retries the
    grant. Paragon-not-yet-loaded works the same way (skip, retry later).

    Appearance detection: mod-transmog INSERTs custom_unlocked_appearances
    the instant an appearance unlocks but exposes no Lua hook, so this
    module rides the same beats as the milestone-975 ladder: store-new-
    item (53) / equip (29) set a dirty flag, a 10s ticker (humans only)
    diffs the account's unlocked set against the mirror and awards one
    combined lump per batch. Appearances above 5000 XP get their own
    toast line (the mythic moments deserve their fanfare).
]]

local Constant = require("paragon_constant")
local Hook = require("paragon_hook")

local DB = Constant.DB_NAME
local BASELINE_ITEM_XP = 1000
local NOTABLE_XP = 5000
local TICK_MS = 10000

local SPELL_MIRROR_KEY = "ParagonCollectSpellMirror"
local ITEM_MIRROR_KEY = "ParagonCollectItemMirror"
local DIRTY_KEY = "ParagonCollectRewardDirty"

-- ============================================================================
-- VALUE TABLES (loaded once at boot; restart to pick up classifier reruns)
-- ============================================================================

local SPELL_XP = {}   -- spell_id -> { kind, name, xp }
local ITEM_XP = {}    -- item_id  -> { name, xp } (above-baseline only)

do
    local q = CharDBQuery(string.format(
        "SELECT spell_id, kind, name, xp FROM %s.paragon_collectible_spell_xp;", DB))
    if q then
        repeat
            SPELL_XP[q:GetUInt32(0)] = {
                kind = q:GetString(1), name = q:GetString(2), xp = q:GetInt32(3) }
        until not q:NextRow()
    end
    q = CharDBQuery(string.format(
        "SELECT item_id, name, xp FROM %s.paragon_collectible_item_xp;", DB))
    if q then
        repeat
            ITEM_XP[q:GetUInt32(0)] = { name = q:GetString(1), xp = q:GetInt32(2) }
        until not q:NextRow()
    end
end

local function CountEntries(t)
    local n = 0
    for _ in pairs(t) do
        n = n + 1
    end
    return n
end

-- ============================================================================
-- SHARED HELPERS
-- ============================================================================

local function Comma(n)
    local s, k = tostring(n), nil
    repeat
        s, k = s:gsub("^(-?%d+)(%d%d%d)", "%1,%2")
    until k == 0
    return s
end

--- Lump-sum flat award through the banking module's pipeline (curve fix +
--- OnUpdatePlayerExperience + client pushes). Returns false when paragon
--- data is not loaded yet — callers skip and retry on a later beat.
local function AwardFlatXP(player, amount)
    local paragon = player:GetData("Paragon")
    if not paragon then
        return false
    end

    if ParagonRework_CurveCost then
        local expected = ParagonRework_CurveCost(paragon:GetLevel())
        if paragon:GetExperienceForNextLevel() ~= expected then
            paragon:SetExperienceForNextLevel(expected)
        end
    end

    paragon = Mediator.On("OnUpdatePlayerExperience", {
        arguments = { player, paragon, amount },
        defaults = { paragon },
    })

    player:SendServerResponse(Hook.Addon.Prefix, 1, paragon:GetLevel())
    player:SendServerResponse(Hook.Addon.Prefix, 2, paragon:GetExperience(), paragon:GetExperienceForNextLevel())
    player:SendServerResponse(Hook.Addon.Prefix, 4, paragon:GetPoints())
    player:SetData("Paragon", paragon)
    return true
end

local function IsBot(player)
    return player.IsPlayerBot and player:IsPlayerBot()
end

--- Lazy per-session mirror sets (one query each, then O(1) membership —
--- the login re-teach storm fires event 44 hundreds of times).
local function Mirror(player, key, sql)
    local cached = player:GetData(key)
    if cached ~= nil then
        return cached
    end
    local set = {}
    local q = CharDBQuery(string.format(sql, DB, player:GetAccountId()))
    if q then
        repeat
            set[q:GetUInt32(0)] = true
        until not q:NextRow()
    end
    player:SetData(key, set)
    return set
end

local function SpellMirror(player)
    return Mirror(player, SPELL_MIRROR_KEY,
        "SELECT spell_id FROM %s.paragon_rewarded_collectible_spell WHERE account_id = %d;")
end

local function ItemMirror(player)
    return Mirror(player, ITEM_MIRROR_KEY,
        "SELECT item_id FROM %s.paragon_rewarded_appearance WHERE account_id = %d;")
end

-- ============================================================================
-- MOUNTS + COMPANIONS (learn-spell event)
-- ============================================================================

RegisterPlayerEvent(44, function(event, player, spellId)
    local def = SPELL_XP[spellId]
    if not def then
        return
    end
    local ok, err = pcall(function()
        if IsBot(player) or player:GetLevel() < 80 then
            return
        end
        local mirror = SpellMirror(player)
        if mirror[spellId] then
            return
        end
        if not AwardFlatXP(player, def.xp) then
            return -- paragon still loading: retried by the next login re-teach
        end
        mirror[spellId] = true
        CharDBExecute(string.format(
            "INSERT IGNORE INTO %s.paragon_rewarded_collectible_spell VALUES (%d, %d);",
            DB, player:GetAccountId(), spellId))
        player:SendBroadcastMessage(string.format(
            "|cff00ff00[Paragon]|r New %s collected: %s \226\128\148 +%s Paragon XP!",
            def.kind, def.name, Comma(def.xp)))
    end)
    if not ok then
        print("[Paragon] collection reward learn-event error: " .. tostring(err))
    end
end)

-- ============================================================================
-- TRANSMOG APPEARANCES (dirty flag + ticker, humans only)
-- ============================================================================

for _, eventId in ipairs({ 53, 29 }) do
    RegisterPlayerEvent(eventId, function(event, player)
        player:SetData(DIRTY_KEY, true)
    end)
end

local function SettleAppearances(player)
    if not player:GetData(DIRTY_KEY) then
        return
    end
    player:SetData(DIRTY_KEY, nil)

    local mirror = ItemMirror(player)
    local q = CharDBQuery(string.format(
        "SELECT item_template_id FROM custom_unlocked_appearances WHERE account_id = %d;",
        player:GetAccountId()))
    if not q then
        return
    end

    local fresh, total, notable = {}, 0, {}
    repeat
        local item = q:GetUInt32(0)
        if not mirror[item] then
            local def = ITEM_XP[item]
            local xp = def and def.xp or BASELINE_ITEM_XP
            fresh[#fresh + 1] = item
            total = total + xp
            if def and def.xp >= NOTABLE_XP then
                notable[#notable + 1] = string.format("%s (+%s)", def.name, Comma(def.xp))
            end
        end
    until not q:NextRow()

    if #fresh == 0 then
        return
    end
    if not AwardFlatXP(player, total) then
        return -- paragon not loaded; stays unmarked and settles later
    end

    local values = {}
    local account = player:GetAccountId()
    for i, item in ipairs(fresh) do
        mirror[item] = true
        values[i] = string.format("(%d,%d)", account, item)
    end
    CharDBExecute(string.format(
        "INSERT IGNORE INTO %s.paragon_rewarded_appearance VALUES %s;",
        DB, table.concat(values, ",")))

    player:SendBroadcastMessage(string.format(
        "|cff00ff00[Paragon]|r %d new appearance%s collected \226\128\148 +%s Paragon XP!",
        #fresh, #fresh == 1 and "" or "s", Comma(total)))
    for _, line in ipairs(notable) do
        player:SendBroadcastMessage("|cff00ff00[Paragon]|r     " .. line)
    end
end

local tickers = {}

local function OnTick(eventId, delay, repeats, player)
    local ok, err = pcall(function()
        SettleAppearances(player)
    end)
    if not ok then
        print("[Paragon] collection reward tick error: " .. tostring(err))
    end
end

local function EnsureTicker(player)
    local guid = player:GetGUIDLow()
    if not tickers[guid] then
        tickers[guid] = player:RegisterEvent(OnTick, TICK_MS, 0)
    end
end

RegisterPlayerEvent(4, function(event, player)
    tickers[player:GetGUIDLow()] = nil
end)

RegisterMediatorEvent("OnAfterUpdatePlayerStatistics", function(player, paragon, apply)
    local ok, err = pcall(function()
        if apply and player and paragon and not IsBot(player) and player:GetLevel() >= 80 then
            EnsureTicker(player)
        end
    end)
    if not ok then
        print("[Paragon] collection reward ticker error: " .. tostring(err))
    end
end)

print(string.format("[Paragon] Rework: collection rewards module loaded (%d spells, %d notable items)",
    CountEntries(SPELL_XP), CountEntries(ITEM_XP)))
