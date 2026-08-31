--[[
    Durable account-wide one-time reputation rewards.

    ALE event 82 is emitted only after AzerothCore commits the final absolute
    standing. Each faction pays 50 flat Paragon XP for every point above the
    account's durable high-water. Loss and regain therefore cannot be farmed,
    and faction-change counterparts share one canonical ledger entry.

    Existing standings across every account character are seeded without
    backpay when a real player logs in. Pre-minimum-level rewards are stored as
    exact pending XP and use the same DB-first settlement protocol as the other
    one-time systems.
]]

local Constant = require("paragon_constant")
local Config = require("paragon_config")
local Hook = require("paragon_hook")

local DB = Constant.DB_NAME
local PROGRESS_TABLE = DB .. ".paragon_reputation_progress"
local SOURCE_REPUTATION = Hook.ExperienceSource.REPUTATION
local DEFAULT_XP_PER_POINT = 50
local REPUTATION_BOTTOM = -42000
local REPUTATION_CAP = 42999

local SCOPE_CACHE_KEY = "ParagonReputationRewardScopeV1"
local SCOPES = {}

local function Integer(value)
    value = tonumber(value)
    if not value or value ~= value then
        return nil
    end
    local integer = math.floor(value)
    if value ~= integer then
        return nil
    end
    return integer
end

local function PersistedExperience(value)
    value = tonumber(value)
    if not value or value ~= value or value < 0
            or value == math.huge or value == -math.huge then
        return nil
    end
    return math.floor(value)
end

local function IsBot(player)
    return player and player.IsPlayerBot and player:IsPlayerBot()
end

local function SystemEnabled()
    return (tonumber(Config:GetByField("ENABLE_PARAGON_SYSTEM")) or 1) ~= 0
end

local function SourceEnabled()
    return SystemEnabled()
        and (tonumber(Config:GetByField("PARAGON_REPUTATION_XP_ENABLED")) or 1) ~= 0
end

local function XPPerPoint()
    return math.max(0, Integer(
        Config:GetByField("PARAGON_REPUTATION_XP_PER_POINT"))
        or DEFAULT_XP_PER_POINT)
end

local function MinLevel()
    return tonumber(Config:GetByField("MINIMUM_LEVEL_FOR_PARAGON_XP")) or 80
end

local function ExecuteSync(sql)
    CharDBQuery(sql)
end

local function Comma(value)
    local text, count = tostring(value), nil
    repeat
        text, count = text:gsub("^(-?%d+)(%d%d%d)", "%1,%2")
    until count == 0
    return text
end

local function Scalar(sql)
    local result = CharDBQuery(sql)
    if not result then
        return nil
    end
    return tonumber(result:GetString(0))
end

local CANONICAL_FACTION = {}
do
    local result = WorldDBQuery(
        "SELECT alliance_id, horde_id "
            .. "FROM player_factionchange_reputations;")
    if result then
        repeat
            local alliance = result:GetUInt32(0)
            local horde = result:GetUInt32(1)
            local canonical = math.min(alliance, horde)
            CANONICAL_FACTION[alliance] = canonical
            CANONICAL_FACTION[horde] = canonical
        until not result:NextRow()
    end
end

local function CanonicalFaction(faction_id)
    return CANONICAL_FACTION[faction_id] or faction_id
end

local function Scope(player)
    local account_id = player and Integer(player:GetAccountId())
    if not account_id or account_id <= 0 then
        return nil
    end
    local scope = SCOPES[account_id]
    if not scope then
        scope = { account_id = account_id, settling = false }
        SCOPES[account_id] = scope
    end
    player:SetData(SCOPE_CACHE_KEY, scope)
    return scope
end

local function ClampStanding(value)
    return math.max(REPUTATION_BOTTOM, math.min(REPUTATION_CAP, value))
end

-- Seed every stored account character, not just the active one. Character DB
-- stores offsets from race/class base reputation, so the native helper is
-- required to reconstruct exact absolute standings.
local function SeedAccount(player)
    local scope = Scope(player)
    if not scope or type(GetFactionBaseReputation) ~= "function" then
        return false
    end

    local maxima = {}
    local result = CharDBQuery(string.format([[
        SELECT c.race, c.`class`, cr.faction, cr.standing
        FROM acore_characters.characters c
        JOIN acore_characters.character_reputation cr ON cr.guid = c.guid
        WHERE c.account = %d;]], scope.account_id))
    if result then
        repeat
            local race_id = result:GetUInt32(0)
            local class_id = result:GetUInt32(1)
            local faction_id = result:GetUInt32(2)
            local relative = result:GetInt32(3)
            local base = Integer(GetFactionBaseReputation(
                faction_id, race_id, class_id))
            if base then
                local canonical = CanonicalFaction(faction_id)
                local absolute = ClampStanding(base + relative)
                maxima[canonical] = math.max(
                    maxima[canonical] or REPUTATION_BOTTOM, absolute)
            end
        until not result:NextRow()
    end

    local values = {}
    for faction_id, high_water in pairs(maxima) do
        values[#values + 1] = string.format(
            "(%d,%d,%d,0)", scope.account_id, faction_id, high_water)
    end
    if #values > 0 then
        ExecuteSync(string.format([[
            INSERT INTO %s
                (account_id, faction_id, high_water, pending_xp)
            VALUES %s
            ON DUPLICATE KEY UPDATE
                high_water = GREATEST(high_water, VALUES(high_water));]],
            PROGRESS_TABLE, table.concat(values, ",")))
    end
    return true
end

local function ProgressionTarget(player)
    if tonumber(Config:GetByField("LEVEL_LINKED_TO_ACCOUNT")) == 1 then
        return DB .. ".account_paragon", "account_id", Integer(player:GetAccountId())
    end
    return DB .. ".character_paragon", "guid", Integer(player:GetGUIDLow())
end

local function CurrentProgression(player)
    local paragon = player and player:GetData("Paragon")
    local level = paragon and Integer(paragon:GetLevel())
    local experience = paragon and PersistedExperience(paragon:GetExperience())
    local table_name, id_column, owner_id = ProgressionTarget(player)
    if not level or level <= 0 or experience == nil
            or not owner_id or owner_id <= 0 then
        return nil
    end
    return {
        paragon = paragon,
        level = level,
        experience = experience,
        table_name = table_name,
        id_column = id_column,
        owner_id = owner_id,
    }
end

local function SyncCurrentProgression(current)
    ExecuteSync(string.format(
        "INSERT INTO %s (%s, level, experience) VALUES (%d, %d, %d) "
            .. "ON DUPLICATE KEY UPDATE level = VALUES(level), "
            .. "experience = VALUES(experience);",
        current.table_name, current.id_column, current.owner_id,
        current.level, current.experience))
    return Scalar(string.format(
        "SELECT COUNT(*) FROM %s WHERE %s = %d "
            .. "AND level = %d AND experience = %d;",
        current.table_name, current.id_column, current.owner_id,
        current.level, current.experience)) == 1
end

local function RefreshPending(account_id)
    return Scalar(string.format(
        "SELECT COALESCE(SUM(pending_xp), 0) FROM %s "
            .. "WHERE account_id = %d AND pending_xp > 0;",
        PROGRESS_TABLE, account_id))
end

local function ProjectProgression(current, amount)
    if type(ParagonRework_CurveCost) ~= "function" then
        return nil
    end
    local cap = Integer(Config:GetByField("PARAGON_LEVEL_CAP")) or 0
    local level = current.level
    local experience = current.experience + amount
    local cost = Integer(ParagonRework_CurveCost(level))
    if not cost or cost <= 0 then
        return nil
    end
    while experience >= cost do
        experience = experience - cost
        if cap <= 0 or level < cap then
            level = level + 1
        end
        cost = Integer(ParagonRework_CurveCost(level))
        if not cost or cost <= 0 then
            return nil
        end
    end
    return level, experience
end

local function CommitPending(scope, current, level, experience)
    ExecuteSync(string.format([[
        UPDATE %s progression
        JOIN %s reputation
          ON reputation.account_id = %d
         AND reputation.pending_xp > 0
        SET progression.level = %d,
            progression.experience = %d,
            reputation.pending_xp = 0
        WHERE progression.%s = %d
          AND progression.level = %d
          AND progression.experience = %d;]],
        current.table_name, PROGRESS_TABLE, scope.account_id,
        level, experience, current.id_column, current.owner_id,
        current.level, current.experience))

    local remaining = RefreshPending(scope.account_id)
    local persisted = Scalar(string.format(
        "SELECT COUNT(*) FROM %s WHERE %s = %d "
            .. "AND level = %d AND experience = %d;",
        current.table_name, current.id_column, current.owner_id,
        level, experience))
    return remaining == 0 and persisted == 1
end

local function CanPayNow(player)
    return SourceEnabled() and player and not IsBot(player)
        and player:GetLevel() >= MinLevel()
        and player:GetData("Paragon") ~= nil
end

local function PayPending(player, faction_id)
    if not CanPayNow(player) then
        return false, 0
    end
    local scope = Scope(player)
    if not scope or scope.settling then
        return false, 0
    end

    scope.settling = true
    local ok, paid, paid_amount = pcall(function()
        local pending = RefreshPending(scope.account_id)
        local current = CurrentProgression(player)
        if not pending or pending <= 0 or not current
                or not SyncCurrentProgression(current) then
            return false, 0
        end

        local level, experience = ProjectProgression(current, pending)
        if not level or not CommitPending(
                scope, current, level, experience) then
            return false, 0
        end

        local awarded, amount = Hook.AwardFlatExperience(
            player, SOURCE_REPUTATION, faction_id or 0, pending)
        if not awarded or current.paragon:GetLevel() ~= level
                or current.paragon:GetExperience() ~= experience then
            current.paragon:SetLevel(level)
            current.paragon:SetExperience(experience)
        end
        return true, amount or pending
    end)
    scope.settling = false
    if not ok then
        print("[Paragon] reputation settlement error: " .. tostring(paid))
        return false, 0
    end
    if paid then
        player:SendBroadcastMessage(string.format(
            "|cff00ff00[Paragon]|r Reputation progress \226\128\148 +%s Paragon XP!",
            Comma(paid_amount)))
    end
    return paid, paid_amount
end

local function PersistGain(player, faction_id, old_standing, new_standing)
    local scope = Scope(player)
    if not scope then
        return false
    end

    local canonical = CanonicalFaction(faction_id)
    local enabled = SourceEnabled()
    local point_xp = XPPerPoint()
    local high_water = math.max(old_standing, new_standing)
    local initial_xp = enabled
        and math.max(0, new_standing - old_standing) * point_xp or 0
    local update_xp = enabled and point_xp or 0

    ExecuteSync(string.format([[
        INSERT INTO %s
            (account_id, faction_id, high_water, pending_xp)
        VALUES (%d, %d, %d, %d)
        ON DUPLICATE KEY UPDATE
            pending_xp = pending_xp + GREATEST(
                0, %d - GREATEST(high_water, %d)) * %d,
            high_water = GREATEST(high_water, %d, %d);]],
        PROGRESS_TABLE, scope.account_id, canonical, high_water, initial_xp,
        new_standing, old_standing, update_xp,
        old_standing, new_standing))

    local result = CharDBQuery(string.format(
        "SELECT high_water, pending_xp FROM %s "
            .. "WHERE account_id = %d AND faction_id = %d;",
        PROGRESS_TABLE, scope.account_id, canonical))
    if not result then
        return false
    end
    local stored_high_water = result:GetInt32(0)
    local pending = tonumber(result:GetString(1))
    return stored_high_water >= high_water and pending ~= nil and pending >= 0
end

local function OnAfterReputationChange(event, player, faction_id,
        old_standing, new_standing, incremental)
    if not player or IsBot(player) then
        return
    end
    faction_id = Integer(faction_id)
    old_standing = Integer(old_standing)
    new_standing = Integer(new_standing)
    if not faction_id or faction_id <= 0 or old_standing == nil
            or new_standing == nil then
        return
    end
    old_standing = ClampStanding(old_standing)
    new_standing = ClampStanding(new_standing)

    local ok, err = pcall(function()
        if PersistGain(player, faction_id, old_standing, new_standing) then
            PayPending(player, CanonicalFaction(faction_id))
        end
    end)
    if not ok then
        print("[Paragon] reputation reward event error: " .. tostring(err))
    end
end

RegisterPlayerEvent(82, OnAfterReputationChange)

RegisterMediatorEvent("OnAfterPlayerStatReady", function(player, paragon)
    local ok, err = pcall(function()
        if player and paragon and not IsBot(player) then
            SeedAccount(player)
            PayPending(player)
        end
    end)
    if not ok then
        print("[Paragon] reputation seed/drain error: " .. tostring(err))
    end
end)

RegisterPlayerEvent(13, function(event, player, old_level)
    old_level = Integer(old_level)
    local minimum = MinLevel()
    if player and old_level and not IsBot(player) and old_level < minimum
            and player:GetLevel() >= minimum then
        PayPending(player)
    end
end)

print(string.format(
    "[Paragon] Reputation rewards loaded (%d flat XP per new point)",
    XPPerPoint()))

return {
    CanonicalFaction = CanonicalFaction,
    OnAfterReputationChange = OnAfterReputationChange,
    PayPending = PayPending,
    PersistGain = PersistGain,
    SeedAccount = SeedAccount,
}
