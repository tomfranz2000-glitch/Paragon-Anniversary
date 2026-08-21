--[[
    Paragon Rework: Big Game Hunter (milestone 1300, universal)

    Every unique rare creature the character kills for the FIRST time
    grants +10 armor, +0.25 resilience and +0.25 haste. Each creature
    entry counts once, ever. 426 rares actually spawn on this world
    (129 rare elite + 297 rare; 391 open world, 35 inside dungeons), so a
    full sweep is +4,260 armor, +106 resilience and +106 haste.

    "Rare" is the core's own concept: creature_template.rank 2
    (CREATURE_ELITE_RAREELITE) and 4 (CREATURE_ELITE_RARE) —
    SharedDefines.h:2966-2968, the silver/gold dragon portraits.
    Calibrated against Time-Lost Proto Drake (32491, rank 2) and the
    classic rares Bjarn / Ravasaur Matriarch / Bro'Gaz (rank 4). World
    bosses are rank 3 and deliberately excluded.

    RATING GRANULARITY: combat ratings are int32 all the way down
    (Player::ApplyRatingMod takes int32; the ALE wrapper's float argument
    is truncated at the call), so 0.25 cannot be applied per kill.
    Resilience and haste therefore accumulate: every 4th unique rare
    grants +1 of each — the same accepted rounding as milestone 1200's
    crit damage. Armor at 10 apiece is exact. Resilience and haste each
    apply to all three sub-ratings, per the codex convention.

    HOT PATH: this hook fires for EVERY creature kill by EVERY player,
    and this realm runs ~2500 playerbots. The handler is therefore
    ordered cheapest-first — the rank check rejects ~99.9% of kills with
    a single call, before any bot test, DB read or table lookup.

    Detection blind spot (inherited, accepted): a guardian minion's
    killing blow fires neither PLAYER_EVENT_ON_KILL_CREATURE (7, literal
    player killing blow only) nor PLAYER_EVENT_ON_PET_KILL (58) — both of
    which are registered here. Kills stolen by another player or a bot
    correctly grant nothing: you must land the blow.

    No retroactivity — rares killed before this module existed carry no
    record. The milestone gates the STATS, not the registry, so kills
    banked below 1300 all count the moment it unlocks.

    Tooltip: prefix "ParagonRares" fn 1 pushes { count, total, armor,
    resil, haste, next_at }; Paragon_RewardTrack.lua renders the live
    numbers under the milestone-1300 track node.
]]

local Constant = require("paragon_constant")

local PREFIX = "ParagonRares"
local MILESTONE = 1300
local ARMOR_PER_KILL = 10
local KILLS_PER_RATING = 4 -- 4 x 0.25 = 1 (integer rating granularity)

local APPLIED_KEY = "ParagonRareApplied"
local PUSHED_KEY = "ParagonRarePushed"
local KILLS_KEY = "ParagonRareKills"

-- creature_template.rank values that count as "rare"
local RARE_RANKS = { [2] = true, [4] = true }

local RESIL_MODS = { "CRIT_TAKEN_MELEE", "CRIT_TAKEN_RANGED", "CRIT_TAKEN_SPELL" }
local HASTE_MODS = { "HASTE_MELEE", "HASTE_RANGED", "HASTE_SPELL" }

local TABLE_SQL = "CREATE TABLE IF NOT EXISTS `" .. Constant.DB_NAME .. "`.`paragon_rare_kills` ("
    .. "guid INT UNSIGNED NOT NULL, entry INT UNSIGNED NOT NULL, "
    .. "PRIMARY KEY (guid, entry)) ENGINE=InnoDB;"

CharDBExecute(TABLE_SQL)

-- Total reachable rares, read once at load so the tooltip denominator
-- tracks the actual world data instead of a hardcoded number.
local TOTAL = 0
do
    local q = WorldDBQuery("SELECT COUNT(DISTINCT ct.entry) FROM creature_template ct "
        .. "JOIN creature c ON c.id = ct.entry WHERE ct.`rank` IN (2, 4);")
    if q then
        TOTAL = q:GetUInt32(0)
    end
end

local function IsBot(player)
    return player.IsPlayerBot and player:IsPlayerBot()
end

-- ============================================================================
-- KILL REGISTRY
-- ============================================================================

--- Session cache: { set = { [entry] = true }, count = n }. Loaded once per
--- session on first use (sync read), written through CharDBExecute.
local function Kills(player)
    local cached = player:GetData(KILLS_KEY)
    if cached then
        return cached
    end
    local kills = { set = {}, count = 0 }
    local q = CharDBQuery("SELECT entry FROM `" .. Constant.DB_NAME
        .. "`.`paragon_rare_kills` WHERE guid = " .. player:GetGUIDLow())
    if q then
        repeat
            kills.set[q:GetUInt32(0)] = true
            kills.count = kills.count + 1
        until not q:NextRow()
    end
    player:SetData(KILLS_KEY, kills)
    return kills
end

-- ============================================================================
-- APPLY
-- ============================================================================

local function ApplyBonus(player, armor, rating, apply)
    if armor > 0 then
        player:HandleStatFlatModifier(Constant.STATISTICS.UNIT_MODS["ARMOR"], 0, armor, apply)
    end
    if rating > 0 then
        for _, key in ipairs(RESIL_MODS) do
            player:ApplyRatingMod(Constant.STATISTICS.COMBAT_RATING[key], rating, apply)
        end
        for _, key in ipairs(HASTE_MODS) do
            player:ApplyRatingMod(Constant.STATISTICS.COMBAT_RATING[key], rating, apply)
        end
    end
end

-- ============================================================================
-- PUSH (track tooltip, delta-suppressed)
-- ============================================================================

local function Push(player, count, armor, rating, force)
    local sig = count .. ";" .. armor .. ";" .. rating
    if not force and player:GetData(PUSHED_KEY) == sig then
        return
    end
    player:SetData(PUSHED_KEY, sig)
    player:SendServerResponse(PREFIX, 1, {
        count = count,
        total = TOTAL,
        armor = armor,
        resil = rating,
        haste = rating,
        -- kills until the next whole point of resilience/haste
        next_at = (count < TOTAL)
            and (math.floor(count / KILLS_PER_RATING) + 1) * KILLS_PER_RATING
            or nil,
    })
end

-- ============================================================================
-- RECONCILE
-- ============================================================================

local function Reconcile(player, force)
    -- account-gate contract: bots and sub-80 alts compute nothing, but a
    -- de-levelled character with stats still applied falls through to strip
    local eligible = not IsBot(player) and player:GetLevel() >= 80
    local prev = player:GetData(APPLIED_KEY)
    if not eligible and not prev then
        return -- fast path: nothing applied, nothing owed (every bot)
    end

    local paragon = eligible and player:GetData("Paragon") or nil
    local owed = paragon and paragon:GetLevel() >= MILESTONE
    local kills = eligible and Kills(player) or nil
    local count = kills and kills.count or 0
    local armor = owed and count * ARMOR_PER_KILL or 0
    local rating = owed and math.floor(count / KILLS_PER_RATING) or 0

    if not prev or prev.armor ~= armor or prev.rating ~= rating then
        -- swapping the set momentarily drops max health (armor does not
        -- feed health, but resilience swaps are cheap either way) — keep
        -- the codex health-preserve habit for safety
        local health = player:IsAlive() and player:GetHealth() or nil
        if prev then
            ApplyBonus(player, prev.armor, prev.rating, false)
        end
        ApplyBonus(player, armor, rating, true)
        player:SetData(APPLIED_KEY, { armor = armor, rating = rating })
        if health then
            player:SetHealth(math.min(health, player:GetMaxHealth()))
        end
    end
    if kills then
        Push(player, count, armor, rating, force)
    end
end

local function SafeReconcile(player, force, label)
    local ok, err = pcall(Reconcile, player, force)
    if not ok then
        print("[Paragon] rare hunter " .. label .. " error: " .. tostring(err))
    end
end

-- ============================================================================
-- KILL HOOKS
-- ============================================================================

--- Ordered cheapest-first: the rank test rejects almost every kill on the
--- realm (bots included) before anything more expensive runs.
local function OnRareKill(player, killed)
    if not killed or not RARE_RANKS[killed:GetRank()] then
        return
    end
    if IsBot(player) then
        return
    end
    local entry = killed:GetEntry()
    local kills = Kills(player)
    if kills.set[entry] then
        return -- already banked
    end
    kills.set[entry] = true
    kills.count = kills.count + 1
    CharDBExecute("INSERT IGNORE INTO `" .. Constant.DB_NAME
        .. "`.`paragon_rare_kills` (guid, entry) VALUES ("
        .. player:GetGUIDLow() .. ", " .. entry .. ");")
    player:SendBroadcastMessage(string.format(
        "|cffffd100[Paragon]|r Rare slain: %s (%d/%d)",
        killed:GetName(), kills.count, TOTAL))
    SafeReconcile(player, false, "kill")
end

RegisterPlayerEvent(7, function(event, killer, killed)
    local ok, err = pcall(OnRareKill, killer, killed)
    if not ok then
        print("[Paragon] rare hunter kill error: " .. tostring(err))
    end
end)

RegisterPlayerEvent(58, function(event, player, killed)
    local ok, err = pcall(OnRareKill, player, killed)
    if not ok then
        print("[Paragon] rare hunter pet-kill error: " .. tostring(err))
    end
end)

-- ============================================================================
-- LIFECYCLE
-- ============================================================================

RegisterMediatorEvent("OnAfterClientLoadRequest", function(player, paragon)
    if player then
        SafeReconcile(player, true, "load")
    end
end)

RegisterMediatorEvent("OnParagonLevelChanged", function(player, paragon)
    if player then
        SafeReconcile(player, false, "level")
    end
end)

-- ReloadALE wipes the session store while the stat mods stay applied on
-- live players; unapply before the state closes (event 16 fires before
-- lua_close, so GetData still reads).
RegisterServerEvent(16, function()
    local players = GetPlayersInWorld()
    if not players then
        return
    end
    for _, player in pairs(players) do
        local prev = player:GetData(APPLIED_KEY)
        if prev then
            pcall(ApplyBonus, player, prev.armor, prev.rating, false)
        end
    end
end)

print("[Paragon] Rework: rare hunter module loaded (" .. TOTAL .. " rares)")
