--[[
    Account-wide, one-time achievement XP settlement.

    Every new reward is first inserted into
    acore_ale.paragon_rewarded_achievement with its authoritative XP in
    pending_xp. The pending ledger belongs to the account, not to the character
    that earned it, so any eligible real character on that account can settle
    it.

    Settlement is commit-before-memory:

      1. project the exact post-award Paragon level/experience;
      2. atomically persist that state and clear all account pending rows;
      3. replay the same flat award through the live Paragon pipeline.

    A crash before step 2 leaves the write-ahead entitlement payable. A crash
    after step 2 reloads the already-awarded progression with no pending row.
    There is no point at which a durable claim is consumed without its XP, or
    durable XP exists alongside a payable copy of the same claim.

    Existing achievements are seeded by sql/07_add_achievement_reward_claims.sql
    with pending_xp = 0. This is forward-only and performs no reconciliation.
    Alliance/Horde faction-change counterparts share their smaller ID.
]]

local Config = require("paragon_config")
local Constant = require("paragon_constant")
local Hook = require("paragon_hook")

local DB = Constant.DB_NAME
local CLAIM_TABLE = DB .. ".paragon_rewarded_achievement"
local SOURCE = Hook.ExperienceSource.ACHIEVEMENT
local SCOPE_CACHE_KEY = "ParagonAchievementRewardScopeV2"
local SCOPES = setmetatable({}, { __mode = "v" })
local CANONICAL_ACHIEVEMENT = {}
local CUSTOM_COUNTER = {}
local STOCK_COUNTER = {}

-- Positive-point ACHIEVEMENT_FLAG_COUNTER rows from the active 3.3.5a
-- Achievement.dbc. Zero-point counters already fail the value check below.
-- Keeping the positive set explicit makes the reward path fail closed even if
-- an integration invokes event 45 for a statistic the core normally rejects.
for id in string.gmatch([[
1068,1091,1242,2856,1069,1092,1504,1525,2857,1070,1093,1231,2858,1071,1094,
1505,2859,1072,1095,1232,2860,1073,1097,1506,2861,1074,1096,1233,2868,1075,
1102,1507,2862,1076,1098,1234,2863,1077,1099,1508,1526,2864,1078,1100,1235,
2865,1079,1101,1467,1509,2866,1080,1236,2869,1081,1510,2867,1082,1237,2872,
1083,1511,2873,1084,1238,2874,1085,1512,2884,1086,1239,2885,1087,1513,2875,
1088,1240,2882,1089,1514,3256,1090,1241,3257,1515,3258,1361,2879,1372,2880,
1366,2883,1362,2881,1371,2870,1369,3236,1375,1374,1370,1363,1365,1364,1373,
4074,1376,4075,1377,2596,1368,1378,1379,1380,1381,1382,1383,1384,1385,1386,
1387,1367,1388,1389,1390,1392,1393,1391,1394,1753,1754
]], "%d+") do
    STOCK_COUNTER[tonumber(id)] = true
end

local function Integer(value, minimum)
    value = tonumber(value)
    if not value or value ~= value then
        return nil
    end
    local integer = math.floor(value)
    if integer ~= value or integer < (minimum or 0) then
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

local function SystemEnabled()
    return (tonumber(Config:GetByField("ENABLE_PARAGON_SYSTEM")) or 1) ~= 0
end

local function MinLevel()
    return tonumber(Config:GetByField("MINIMUM_LEVEL_FOR_PARAGON_XP")) or 80
end

local function IsBot(player)
    return player and player.IsPlayerBot and player:IsPlayerBot()
end

local function ExecuteSync(sql)
    -- ALE's synchronous character-DB primitive. DML returns no row set, but
    -- the statement has completed before control returns.
    CharDBQuery(sql)
end

local function Scalar(sql)
    local result = CharDBQuery(sql)
    if not result then
        return nil
    end
    -- ALE represents uint64 as userdata. Read its decimal SQL text instead of
    -- applying tonumber directly to GetUInt64(), which always yields nil.
    return tonumber(result:GetString(0))
end

do
    local result = WorldDBQuery(
        "SELECT alliance_id, horde_id FROM player_factionchange_achievement;")
    if result then
        repeat
            local alliance_id = result:GetUInt32(0)
            local horde_id = result:GetUInt32(1)
            local canonical = math.min(alliance_id, horde_id)
            CANONICAL_ACHIEVEMENT[alliance_id] = canonical
            CANONICAL_ACHIEVEMENT[horde_id] = canonical
        until not result:NextRow()
    end

    result = WorldDBQuery(
        "SELECT ID FROM achievement_dbc WHERE (Flags & 1) <> 0;")
    if result then
        repeat
            CUSTOM_COUNTER[result:GetUInt32(0)] = true
        until not result:NextRow()
    end
end

local function CanonicalAchievement(achievement_id)
    return CANONICAL_ACHIEVEMENT[achievement_id] or achievement_id
end

local function Scope(player)
    local account_id = player and Integer(player:GetAccountId(), 1)
    if not account_id then
        return nil
    end
    local key = tostring(account_id)
    local scope = SCOPES[key]
    if not scope then
        scope = {
            account_id = account_id,
            known = {},
            pending = 0,
            loaded = false,
            settling = false,
        }
        SCOPES[key] = scope
    end
    player:SetData(SCOPE_CACHE_KEY, scope)
    return scope
end

local function LoadScope(player)
    local scope = Scope(player)
    if not scope or scope.loaded then
        return scope
    end

    local result = CharDBQuery(string.format(
        "SELECT achievement_id, pending_xp FROM %s WHERE account_id = %d;",
        CLAIM_TABLE, scope.account_id))
    if result then
        repeat
            local achievement_id = result:GetUInt32(0)
            local pending = tonumber(result:GetString(1)) or 0
            scope.known[achievement_id] = true
            scope.pending = scope.pending + pending
        until not result:NextRow()
    end
    scope.loaded = true
    return scope
end

local function RefreshPending(scope)
    local pending = Scalar(string.format(
        "SELECT COALESCE(SUM(pending_xp), 0) FROM %s WHERE account_id = %d;",
        CLAIM_TABLE, scope.account_id))
    if pending == nil then
        return nil
    end
    scope.pending = pending
    return pending
end

local function ProgressionTarget(player)
    if tonumber(Config:GetByField("LEVEL_LINKED_TO_ACCOUNT")) == 1 then
        return DB .. ".account_paragon", "account_id",
            Integer(player:GetAccountId(), 1)
    end
    return DB .. ".character_paragon", "guid",
        Integer(player:GetGUIDLow(), 1)
end

local function CurrentProgression(player)
    local paragon = player and player:GetData("Paragon")
    local level = paragon and Integer(paragon:GetLevel(), 1)
    local experience = paragon and PersistedExperience(paragon:GetExperience())
    local table_name, id_column, owner_id = ProgressionTarget(player)
    if not paragon or not level or experience == nil or not owner_id then
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

local function ProjectProgression(current, amount)
    local curve_cost = ParagonRework_CurveCost
    if type(curve_cost) ~= "function" then
        return nil
    end
    local cap = Integer(Config:GetByField("PARAGON_LEVEL_CAP"), 0) or 0
    local level = current.level
    local experience = current.experience + amount

    -- The live path self-heals to this generated curve before the award and
    -- after every level change. Project against that same authority. At the
    -- optional cap, excess still rolls around the capped level's bar exactly
    -- as the live OnUpdatePlayerExperience handler does.
    local cost = Integer(curve_cost(level), 1)
    if not cost then
        return nil
    end
    while experience >= cost do
        experience = experience - cost
        if cap <= 0 or level < cap then
            level = level + 1
        end
        cost = Integer(curve_cost(level), 1)
        if not cost then
            return nil
        end
    end
    return level, experience
end

local function EnsureProgressionRow(current)
    -- Ordinary Paragon gains are saved on logout, so the durable row can lag
    -- the authoritative live object. Synchronously checkpoint its persistable
    -- floor without changing memory before using it as the settlement CAS.
    ExecuteSync(string.format(
        "INSERT INTO %s (%s, level, experience) VALUES (%d, %d, %d) "
            .. "ON DUPLICATE KEY UPDATE level = VALUES(level), "
            .. "experience = VALUES(experience);",
        current.table_name, current.id_column, current.owner_id,
        current.level, current.experience))
    return Scalar(string.format(
        "SELECT COUNT(*) FROM %s WHERE %s = %d AND level = %d AND experience = %d;",
        current.table_name, current.id_column, current.owner_id,
        current.level, current.experience)) == 1
end

local function CommitPending(scope, current, level, experience)
    -- The old progression values are a compare-and-swap guard against a stale
    -- account character. Event callbacks are serialized in one world process;
    -- the pending sum is refreshed immediately before this statement.
    ExecuteSync(string.format([[
        UPDATE %s progression
        JOIN %s achievement
          ON achievement.account_id = %d
         AND achievement.pending_xp > 0
        SET progression.level = %d,
            progression.experience = %d,
            achievement.pending_xp = 0
        WHERE progression.%s = %d
          AND progression.level = %d
          AND progression.experience = %d;]],
        current.table_name, CLAIM_TABLE, scope.account_id, level, experience,
        current.id_column, current.owner_id, current.level, current.experience))

    local remaining = RefreshPending(scope)
    local persisted = Scalar(string.format(
        "SELECT COUNT(*) FROM %s WHERE %s = %d AND level = %d AND experience = %d;",
        current.table_name, current.id_column, current.owner_id,
        level, experience))
    return remaining == 0 and persisted == 1
end

local function CanPayNow(player)
    return SystemEnabled() and player and not IsBot(player)
        and player:GetLevel() >= MinLevel()
        and player:GetData("Paragon") ~= nil
end

local function ForceLiveProgression(current, level, experience)
    current.paragon:SetLevel(level)
    current.paragon:SetExperience(experience)
end

local function PayPending(player)
    if not CanPayNow(player) then
        return false
    end
    local scope = LoadScope(player)
    if not scope or scope.settling then
        return false
    end

    scope.settling = true
    local ok, paid = pcall(function()
        local pending = RefreshPending(scope)
        local current = CurrentProgression(player)
        if not pending or pending <= 0 or not current
                or not EnsureProgressionRow(current) then
            return false
        end

        local level, experience = ProjectProgression(current, pending)
        if not level or experience == nil then
            return false
        end
        if not CommitPending(scope, current, level, experience) then
            return false
        end

        -- Durable state is authoritative now. Replaying through the live path
        -- supplies level-up effects, UI messages, and XP-drop notifications.
        local replay_ok, awarded = pcall(
            Hook.AwardFlatExperience, player, SOURCE, 0, pending)
        if not replay_ok or not awarded or current.paragon:GetLevel() ~= level
                or current.paragon:GetExperience() ~= experience then
            -- A future mediator must not make logout overwrite the committed
            -- result. Force the live object to the durable projection.
            ForceLiveProgression(current, level, experience)
        end
        return true
    end)
    scope.settling = false
    if not ok then
        print("[Paragon] achievement settlement error: " .. tostring(paid))
        return false
    end
    return paid
end

local function RewardValue(achievement_id)
    if STOCK_COUNTER[achievement_id] or CUSTOM_COUNTER[achievement_id] then
        return nil
    end
    local resolver = ParagonRework_AchievementValue
    if type(resolver) ~= "function" then
        return nil
    end
    return Integer(resolver(achievement_id), 1)
end

local function Claim(player, achievement_id)
    achievement_id = Integer(achievement_id, 1)
    if not player or not achievement_id or not SystemEnabled() or IsBot(player) then
        return false
    end
    local value = RewardValue(achievement_id)
    local scope = value and LoadScope(player)
    if not scope then
        return false
    end
    local canonical_id = CanonicalAchievement(achievement_id)
    if scope.known[canonical_id] then
        return false
    end

    -- Write ahead before either an eligibility decision or live-state change.
    ExecuteSync(string.format(
        "INSERT IGNORE INTO %s (account_id, achievement_id, pending_xp) "
            .. "VALUES (%d, %d, %d);",
        CLAIM_TABLE, scope.account_id, canonical_id, value))
    local stored = Scalar(string.format(
        "SELECT pending_xp FROM %s WHERE account_id = %d AND achievement_id = %d;",
        CLAIM_TABLE, scope.account_id, canonical_id))
    if stored == nil then
        return false
    end

    scope.known[canonical_id] = true
    RefreshPending(scope)
    return stored > 0
end

local function OnComplete(player, achievement_id)
    local claimed = Claim(player, achievement_id)
    -- A replay after an interrupted live-state step also gets a chance to
    -- drain the existing durable pending row.
    if player and not IsBot(player) then
        PayPending(player)
    end
    return claimed
end

-- paragon_hook owns event 45 for backward compatibility and delegates here.
ParagonAchievementReward_OnComplete = OnComplete
ParagonAchievementClaim_Try = Claim

RegisterMediatorEvent("OnAfterPlayerStatReady", function(player, paragon)
    if player and not IsBot(player) then
        LoadScope(player)
        PayPending(player)
    end
end)

RegisterPlayerEvent(13, function(event, player, old_level)
    old_level = Integer(old_level, 0)
    local minimum = MinLevel()
    if player and old_level and not IsBot(player) and old_level < minimum
            and player:GetLevel() >= minimum then
        PayPending(player)
    end
end)

print("[Paragon] Achievement reward settlement module loaded")

return {
    Claim = Claim,
    OnComplete = OnComplete,
    PayPending = PayPending,
    ProjectProgression = ProjectProgression,
}
