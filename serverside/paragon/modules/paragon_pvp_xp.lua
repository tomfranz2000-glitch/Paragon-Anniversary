--[[
    Paragon PvP Merit

    Converts authoritative ALE PvP bridge settlements (player events 77-81)
    into repeatable Paragon XP. Every configured number is a stored base value;
    each semantic award crosses Hook.AwardExperience(..., true) once so normal
    personal Paragon XP modifiers compose once and only once.

    The account-scoped claim table is simultaneously:
      * a durable, restart-safe idempotency ledger keyed by bridge token;
      * the write-ahead queue for an interrupted payout;
      * the rolling honor-pair / arena-roster history;
      * the daily cap, duel-opponent, and weekly-breadth authority.

    Event 12 (PLAYER_EVENT_ON_GIVE_XP) is deliberately not registered. Core
    XPSOURCE_BATTLEGROUND XP and event 77's generatedBattlegroundXP flag are
    metadata, not a second Paragon award path.
]]

local Config = require("paragon_config")
local Constant = require("paragon_constant")
local Hook = require("paragon_hook")

ParagonPvPXP = {}
local M = ParagonPvPXP
local SOURCE = Hook.ExperienceSource
local DB = Constant.DB_NAME
local CLAIM_TABLE = DB .. ".paragon_pvp_reward_claim"

local MATCH_BATTLEGROUND = 1
local MATCH_ARENA = 2
local RESULT_LOSS = 0
local RESULT_WIN = 1
local RESULT_DRAW = 2
local OUTDOOR_STANDARD = 1
local OUTDOOR_MAJOR = 2

local MAP_AV = 30
local MAP_WSG = 489
local MAP_AB = 529
local MAP_EOTS = 566
local MAP_SOTA = 607
local MAP_IOC = 628

local TYPE_AV = 1
local TYPE_WSG = 2
local TYPE_AB = 3
local TYPE_EOTS = 7
local TYPE_SOTA = 9
local TYPE_IOC = 30

local HONOR_CONTEXT_OUTDOOR_PVP = 253
local HONOR_CONTEXT_BATTLEFIELD = 254
local RECOGNIZED_HONOR_CONTEXT = {
    [TYPE_AV] = true,
    [TYPE_WSG] = true,
    [TYPE_AB] = true,
    [TYPE_EOTS] = true,
    [TYPE_SOTA] = true,
    [TYPE_IOC] = true,
    [32] = true, -- random battleground queue
    [HONOR_CONTEXT_OUTDOOR_PVP] = true,
    [HONOR_CONTEXT_BATTLEFIELD] = true,
}

local COMPONENT = {
    HONOR = "honor",
    BATTLEGROUND = "battleground",
    ARENA_RATED = "arena_rated",
    ARENA_SKIRMISH = "arena_skirmish",
    WINTERGRASP = "wintergrasp",
    OUTDOOR = "outdoor",
    DUEL_WIN = "duel_win",
    DUEL_LOSS = "duel_loss",
    BREADTH = "breadth",
}

local last_cleanup = 0
local applied_guard = {}

local function Integer(value, minimum, maximum)
    value = tonumber(value)
    if not value or value ~= value or value == math.huge or value == -math.huge then
        return nil
    end
    value = math.floor(value)
    if minimum ~= nil and value < minimum then
        return nil
    end
    if maximum ~= nil and value > maximum then
        return nil
    end
    return value
end

local function Boolean(value)
    if type(value) == "boolean" then
        return value
    end
    value = tonumber(value)
    if value == 0 then
        return false
    end
    if value == 1 then
        return true
    end
    return nil
end

local function ConfigInteger(field, fallback, minimum, maximum)
    local value = Integer(Config:GetByField(field), minimum, maximum)
    if value == nil then
        return fallback
    end
    return value
end

local function StrictKey(value, maximum_length)
    -- Bridge identifiers are opaque strings. In particular, never pass them
    -- through tonumber: a uint64 token cannot be represented exactly by Lua.
    if type(value) ~= "string" or value == "" or #value > maximum_length then
        return nil
    end
    if not value:match("^[A-Za-z0-9:_%-]+$") then
        return nil
    end
    return value
end

local function OptionalStrictKey(value, maximum_length)
    if value == nil or value == "" then
        return ""
    end
    return StrictKey(value, maximum_length)
end

local function IsBot(player)
    return player and player.IsPlayerBot and player:IsPlayerBot()
end

local function SystemEnabled()
    return (tonumber(Config:GetByField("ENABLE_PARAGON_SYSTEM")) or 1) ~= 0
        and ConfigInteger("PARAGON_PVP_ENABLED", 1, 0, 1) ~= 0
end

local function MinimumLevel()
    return ConfigInteger("MINIMUM_LEVEL_FOR_PARAGON_XP", 80, 1, 255)
end

local function AccountId(player)
    if not player or not player.GetAccountId then
        return nil
    end
    return Integer(player:GetAccountId(), 1, 4294967295)
end

local function RecipientGuid(player)
    if not player or not player.GetGUIDLow then
        return nil
    end
    return Integer(player:GetGUIDLow(), 1, 4294967295)
end

local function AccountLinked()
    return tonumber(Config:GetByField("LEVEL_LINKED_TO_ACCOUNT")) == 1
end

local function ClaimOwnershipPredicate(player, qualifier)
    if AccountLinked() then
        return ""
    end
    local guid = RecipientGuid(player)
    if not guid then
        return nil
    end
    return string.format(" AND %srecipient_guid = %d", qualifier or "", guid)
end

local function CanReceive(player)
    return SystemEnabled() and player and not IsBot(player)
        and player.GetLevel and player:GetLevel() >= MinimumLevel()
        and player.GetData and player:GetData("Paragon") ~= nil
        and AccountId(player) ~= nil
        and RecipientGuid(player) ~= nil
end

-- ALE documents CharDBQuery as the synchronous character-DB primitive. DML
-- returns no rowset, but has completed when this call returns.
local function ExecuteSync(sql)
    CharDBQuery(sql)
end

local function ScalarOrNil(sql)
    local result = CharDBQuery(sql)
    if not result then
        return nil
    end
    return tonumber(result:GetString(0)) or 0
end

local function ProgressionTarget(player)
    if AccountLinked() then
        return DB .. ".account_paragon", "account_id", AccountId(player)
    end
    return DB .. ".character_paragon", "guid",
        RecipientGuid(player)
end

local function EnsureProgressionRow(player)
    local paragon = player and player:GetData("Paragon")
    local level = paragon and Integer(paragon:GetLevel(), 1, 4294967295)
    local experience = paragon and Integer(paragon:GetExperience(), 0, 9007199254740991)
    local table_name, id_column, owner_id = ProgressionTarget(player)
    if not level or not experience or not owner_id then
        return false
    end
    ExecuteSync(string.format(
        "INSERT IGNORE INTO %s (%s, level, experience) VALUES (%d, %d, %d);",
        table_name, id_column, owner_id, level, experience))
    return ScalarOrNil(string.format(
        "SELECT COUNT(*) FROM %s WHERE %s = %d;",
        table_name, id_column, owner_id)) == 1
end

-- The progression state and claim acknowledgement change in the same InnoDB
-- statement. A crash before it leaves the write-ahead claim payable; a crash
-- after it observes both the awarded state and paid marker.
local function PersistAwardAndAcknowledge(player, event_token, component, awarded_xp)
    local paragon = player and player:GetData("Paragon")
    local account_id = AccountId(player)
    local level = paragon and Integer(paragon:GetLevel(), 1, 4294967295)
    local experience = paragon and Integer(paragon:GetExperience(), 0, 9007199254740991)
    awarded_xp = Integer(awarded_xp, 1, 9007199254740991)
    local table_name, id_column, owner_id = ProgressionTarget(player)
    local ownership = ClaimOwnershipPredicate(player, "claim.")
    if not account_id or not level or not experience or not awarded_xp
            or not owner_id or ownership == nil then
        return false
    end

    ExecuteSync(string.format([[
        UPDATE %s progression
        JOIN %s claim
          ON claim.account_id = %d
         AND claim.event_token = '%s'
         AND claim.component = '%s'
         AND claim.paid_at IS NULL
         %s
        SET progression.level = %d,
            progression.experience = %d,
            claim.awarded_xp = %d,
            claim.paid_at = UTC_TIMESTAMP()
        WHERE progression.%s = %d;]],
        table_name, CLAIM_TABLE, account_id, event_token, component, ownership,
        level, experience, awarded_xp, id_column, owner_id))
    return ScalarOrNil(string.format([[
        SELECT COUNT(*) FROM %s
        WHERE account_id = %d AND event_token = '%s'
          AND component = '%s' AND paid_at IS NOT NULL;]],
        CLAIM_TABLE, account_id, event_token, component)) == 1
end

local function PayClaim(player, event_token, component)
    local account_id = AccountId(player)
    event_token = StrictKey(event_token, 191)
    component = StrictKey(component, 32)
    local ownership = ClaimOwnershipPredicate(player, "")
    if not CanReceive(player) or not account_id or not event_token or not component
            or ownership == nil then
        return false
    end

    local guard_key = string.format("%d:%s:%s", account_id, event_token, component)

    local result = CharDBQuery(string.format([[
        SELECT COUNT(*),
               COALESCE(MAX(paid_at IS NOT NULL), 0),
               COALESCE(MAX(source_type), 0),
               COALESCE(MAX(source_entry), 0),
               COALESCE(MAX(base_xp), 0)
        FROM %s
        WHERE account_id = %d
          AND event_token = '%s'
          AND component = '%s'%s;]],
        CLAIM_TABLE, account_id, event_token, component, ownership))
    if not result then
        return false
    end

    local row_count = result:GetUInt32(0)
    local paid = result:GetUInt32(1) ~= 0
    if row_count == 0 then
        -- A missing row is healthy only when no award was already applied in
        -- this Lua state. Preserve the guard on ambiguous/missing durability.
        return applied_guard[guard_key] == nil
    elseif row_count ~= 1 then
        return false
    elseif paid then
        applied_guard[guard_key] = nil
        return true
    end

    local source_type = result:GetUInt32(2)
    local source_entry = result:GetUInt32(3)
    local base_xp = tonumber(result:GetString(4)) or 0
    if source_type < SOURCE.PVP_HONOR or source_type > SOURCE.PVP_WINTERGRASP
            or base_xp <= 0 or not EnsureProgressionRow(player) then
        return false
    end


    -- If Hook succeeded but the acknowledgement query failed, a duplicate
    -- callback in this Lua state retries only the atomic persistence step. It
    -- must never run Hook twice against the same live Paragon object.
    if applied_guard[guard_key] then
        local persisted = PersistAwardAndAcknowledge(
            player, event_token, component, applied_guard[guard_key])
        if persisted then
            applied_guard[guard_key] = nil
        end
        return persisted
    end

    local awarded, applied_xp = Hook.AwardExperience(
        player, source_type, source_entry, base_xp, true)
    if not awarded then
        return false
    end
    applied_xp = Integer(applied_xp, 1, 9007199254740991)
    if not applied_xp then
        return false
    end
    applied_guard[guard_key] = applied_xp
    local persisted = PersistAwardAndAcknowledge(
        player, event_token, component, applied_xp)
    if persisted then
        applied_guard[guard_key] = nil
    end
    return persisted
end

function ParagonPvPXP.PayPendingClaims(player)
    if not CanReceive(player) then
        return false
    end
    local account_id = AccountId(player)
    local ownership = ClaimOwnershipPredicate(player, "")
    if ownership == nil then
        return false
    end
    local pending_count = ScalarOrNil(string.format(
        "SELECT COUNT(*) FROM %s WHERE account_id = %d AND paid_at IS NULL%s;",
        CLAIM_TABLE, account_id, ownership))
    if pending_count == nil then
        return false
    elseif pending_count == 0 then
        return true
    end
    local result = CharDBQuery(string.format([[
        SELECT event_token, component
        FROM %s
        WHERE account_id = %d AND paid_at IS NULL%s
        ORDER BY created_at, event_token, component;]],
        CLAIM_TABLE, account_id, ownership))

    if not result then
        return false
    end
    local pending = {}
    repeat
        pending[#pending + 1] = {
            token = result:GetString(0),
            component = result:GetString(1),
        }
    until not result:NextRow()
    if #pending ~= pending_count then
        return false
    end
    for _, claim in ipairs(pending) do
        if not PayClaim(player, claim.token, claim.component) then
            return false
        end
    end

    return ScalarOrNil(string.format(
        "SELECT COUNT(*) FROM %s WHERE account_id = %d AND paid_at IS NULL%s;",
        CLAIM_TABLE, account_id, ownership)) == 0
end

local function MaybeCleanup()
    local now = os and os.time and os.time() or 0
    local interval = ConfigInteger("PARAGON_PVP_CLEANUP_INTERVAL_SECONDS", 3600, 60, 604800)
    if now > 0 and last_cleanup > 0 and now - last_cleanup < interval then
        return
    end
    last_cleanup = now
    local paid_days = ConfigInteger("PARAGON_PVP_LEDGER_RETENTION_DAYS", 90, 7, 3650)
    local pending_days = ConfigInteger("PARAGON_PVP_PENDING_RETENTION_DAYS", 365, 30, 3650)
    CharDBExecute(string.format([[
        DELETE FROM %s
        WHERE (paid_at IS NOT NULL
               AND paid_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL %d DAY))
           OR (paid_at IS NULL
               AND created_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL %d DAY));]],
        CLAIM_TABLE, paid_days, pending_days))
end

local function PreparePlayer(player)
    if not CanReceive(player) then
        return false
    end
    MaybeCleanup()
    -- Do not let a persistent payout failure create an unbounded pending queue.
    return M.PayPendingClaims(player)
end

local function ClaimValue(spec)
    local entitlement = "NULL"
    if spec.entitlement_key then
        entitlement = "'" .. spec.entitlement_key .. "'"
    end
    local paid_at = spec.base_xp > 0 and "NULL" or "UTC_TIMESTAMP()"
    return string.format(
        "(%d,%d,'%s','%s',%d,%d,%d,0,%d,'%s','%s',%s,%d,UTC_TIMESTAMP(),%s)",
        spec.account_id, spec.recipient_guid, spec.event_token, spec.component, spec.source_type,
        spec.source_entry or 0, spec.base_xp, spec.counterpart_account_id or 0,
        spec.opponent_key or "", spec.period_key or "", entitlement,
        spec.same_ip_risk and 1 or 0, paid_at)
end

local function ValidateClaim(spec)
    return spec and Integer(spec.account_id, 1, 4294967295)
        and Integer(spec.recipient_guid, 1, 4294967295)
        and StrictKey(spec.event_token, 191)
        and StrictKey(spec.component, 32)
        and Integer(spec.source_type, SOURCE.PVP_HONOR, SOURCE.PVP_WINTERGRASP)
        and Integer(spec.source_entry or 0, 0, 4294967295)
        and Integer(spec.base_xp, 0, 9007199254740991)
        and Integer(spec.counterpart_account_id or 0, 0, 4294967295)
        and OptionalStrictKey(spec.opponent_key, 191) ~= nil
        and OptionalStrictKey(spec.period_key, 64) ~= nil
        and (not spec.entitlement_key or StrictKey(spec.entitlement_key, 191))
end

local function ReserveClaims(specs)
    local values = {}
    for _, spec in ipairs(specs) do
        if not ValidateClaim(spec) then
            return nil
        end
        values[#values + 1] = ClaimValue(spec)
    end
    if #values == 0 then
        return {}
    end
    ExecuteSync(string.format([[
        INSERT IGNORE INTO %s
            (account_id, recipient_guid, event_token, component, source_type, source_entry,
             base_xp, awarded_xp, counterpart_account_id, opponent_key,
             period_key, entitlement_key, same_ip_risk, created_at, paid_at)
        VALUES %s;]], CLAIM_TABLE, table.concat(values, ",")))
    local states = {}
    for index, spec in ipairs(specs) do
        local entitlement_expression = "0"
        local entitlement_predicate = ""
        if spec.entitlement_key then
            entitlement_expression = string.format(
                "COALESCE(SUM(entitlement_key = '%s'), 0)",
                spec.entitlement_key)
            entitlement_predicate = string.format(
                " OR entitlement_key = '%s'", spec.entitlement_key)
        end
        local result = CharDBQuery(string.format([[
            SELECT COALESCE(SUM(event_token = '%s' AND component = '%s'), 0),
                   %s
            FROM %s
            WHERE account_id = %d
              AND ((event_token = '%s' AND component = '%s')%s);]],
            spec.event_token, spec.component, entitlement_expression,
            CLAIM_TABLE, spec.account_id, spec.event_token, spec.component,
            entitlement_predicate))
        if not result then
            return nil
        end
        local exact = result:GetUInt32(0)
        local entitled = result:GetUInt32(1)
        if exact == 1 then
            states[index] = "exact"
        elseif exact == 0 and entitled >= 1 then
            states[index] = "entitled"
        else
            return nil
        end
    end
    return states
end

local function ReserveAndPay(players_and_claims)
    local specs = {}
    for _, item in ipairs(players_and_claims) do
        specs[#specs + 1] = item.claim
    end
    local states = ReserveClaims(specs)
    if not states then
        return false
    end
    local ok = true
    for index, item in ipairs(players_and_claims) do
        if states[index] == "exact" and not PayClaim(
                item.player, item.claim.event_token, item.claim.component) then
            ok = false
        end
    end
    return ok
end

function ParagonPvPXP.ResolvePeriodKey(now_epoch, next_reset_epoch, worldstate_id,
        interval_seconds, fallback_anchor_epoch)
    now_epoch = Integer(now_epoch, 0, 9007199254740991)
    next_reset_epoch = Integer(next_reset_epoch, 0, 9007199254740991) or 0
    worldstate_id = Integer(worldstate_id, 1, 4294967295)
    interval_seconds = Integer(interval_seconds, 60, 31536000)
    fallback_anchor_epoch = Integer(fallback_anchor_epoch, 0, 9007199254740991) or 0
    if not now_epoch or not worldstate_id or not interval_seconds then
        return nil
    end
    if next_reset_epoch > now_epoch
            and next_reset_epoch <= now_epoch + (interval_seconds * 2) then
        return string.format("ws%d_%d", worldstate_id, next_reset_epoch)
    end
    -- The worldstate update and a PvP callback can straddle the exact reset
    -- tick. Project a recently elapsed authoritative boundary forward by its
    -- configured cadence so that callback and the later refreshed worldstate
    -- resolve to the same new period instead of opening two short windows.
    if next_reset_epoch > 0 and next_reset_epoch <= now_epoch
            and now_epoch - next_reset_epoch <= interval_seconds * 2 then
        local steps = math.floor((now_epoch - next_reset_epoch) / interval_seconds) + 1
        local projected = next_reset_epoch + steps * interval_seconds
        return string.format("ws%d_%d", worldstate_id, projected)
    end
    local elapsed = math.max(0, now_epoch - fallback_anchor_epoch)
    return string.format("fb%d_%d", interval_seconds,
        math.floor(elapsed / interval_seconds))
end

local function CurrentPeriods()
    local daily_state = ConfigInteger("PARAGON_PVP_DAILY_RESET_WORLDSTATE", 20005, 1, 4294967295)
    local weekly_state = ConfigInteger("PARAGON_PVP_WEEKLY_RESET_WORLDSTATE", 20002, 1, 4294967295)
    local result = CharDBQuery(string.format([[
        SELECT UNIX_TIMESTAMP(UTC_TIMESTAMP()),
               COALESCE((SELECT `value` FROM `worldstates` WHERE `entry` = %d LIMIT 1), 0),
               COALESCE((SELECT `value` FROM `worldstates` WHERE `entry` = %d LIMIT 1), 0);]],
        daily_state, weekly_state))
    if not result then
        return nil, nil
    end
    local now = tonumber(result:GetString(0)) or 0
    local daily_next = tonumber(result:GetString(1)) or 0
    local weekly_next = tonumber(result:GetString(2)) or 0
    local anchor = ConfigInteger("PARAGON_PVP_RESET_FALLBACK_ANCHOR_UNIX", 0, 0, 4294967295)
    local daily = M.ResolvePeriodKey(now, daily_next, daily_state,
        ConfigInteger("PARAGON_PVP_DAILY_RESET_INTERVAL_SECONDS", 86400, 60, 31536000), anchor)
    local weekly = M.ResolvePeriodKey(now, weekly_next, weekly_state,
        ConfigInteger("PARAGON_PVP_WEEKLY_RESET_INTERVAL_SECONDS", 604800, 60, 31536000), anchor)
    return daily, weekly
end

function ParagonPvPXP.HonorDRPercent(prior_paid_credits)
    prior_paid_credits = Integer(prior_paid_credits, 0, 4294967295)
    if not prior_paid_credits then
        return 0
    end
    local full_through = ConfigInteger("PARAGON_PVP_HONOR_DR_FULL_CREDITS", 1, 0, 100)
    local half_through = ConfigInteger("PARAGON_PVP_HONOR_DR_HALF_CREDITS", 2, full_through, 100)
    local tenth_through = ConfigInteger("PARAGON_PVP_HONOR_DR_TENTH_CREDITS", 3, half_through, 100)
    local ordinal = prior_paid_credits + 1
    if ordinal <= full_through then
        return ConfigInteger("PARAGON_PVP_HONOR_DR_FULL_PERCENT", 100, 0, 100)
    elseif ordinal <= half_through then
        return ConfigInteger("PARAGON_PVP_HONOR_DR_HALF_PERCENT", 50, 0, 100)
    elseif ordinal <= tenth_through then
        return ConfigInteger("PARAGON_PVP_HONOR_DR_TENTH_PERCENT", 10, 0, 100)
    end
    return ConfigInteger("PARAGON_PVP_HONOR_DR_LATER_PERCENT", 0, 0, 100)
end

function ParagonPvPXP.ComputeHonorXP(final_honor, prior_paid_credits)
    final_honor = Integer(final_honor, 1, 1000000000)
    if not final_honor then
        return 0, 0
    end
    local percent = M.HonorDRPercent(prior_paid_credits)
    local per_point = ConfigInteger("PARAGON_PVP_HONOR_XP_PER_POINT", 8, 0, 1000000)
    return math.floor(final_honor * per_point * percent / 100), percent
end

function ParagonPvPXP.ArenaRosterDRPercent(prior_settlements)
    prior_settlements = Integer(prior_settlements, 0, 4294967295)
    if not prior_settlements then
        return 0
    end
    local full_through = ConfigInteger("PARAGON_PVP_ARENA_ROSTER_DR_FULL_SETTLEMENTS", 3, 0, 100)
    local half_through = ConfigInteger("PARAGON_PVP_ARENA_ROSTER_DR_HALF_SETTLEMENTS", 5, full_through, 100)
    local tenth_through = ConfigInteger("PARAGON_PVP_ARENA_ROSTER_DR_TENTH_SETTLEMENTS", 6, half_through, 100)
    local ordinal = prior_settlements + 1
    if ordinal <= full_through then
        return ConfigInteger("PARAGON_PVP_ARENA_ROSTER_DR_FULL_PERCENT", 100, 0, 100)
    elseif ordinal <= half_through then
        return ConfigInteger("PARAGON_PVP_ARENA_ROSTER_DR_HALF_PERCENT", 50, 0, 100)
    elseif ordinal <= tenth_through then
        return ConfigInteger("PARAGON_PVP_ARENA_ROSTER_DR_TENTH_PERCENT", 10, 0, 100)
    end
    return ConfigInteger("PARAGON_PVP_ARENA_ROSTER_DR_LATER_PERCENT", 0, 0, 100)
end

function ParagonPvPXP.IsMatchActive(duration_seconds, presence_seconds,
        presence_buckets, active_buckets, inactive, deserter)
    duration_seconds = Integer(duration_seconds, 0, 864000)
    presence_seconds = Integer(presence_seconds, 0, 864000)
    presence_buckets = Integer(presence_buckets, 1, 14400)
    active_buckets = Integer(active_buckets, 0, 14400)
    inactive = Boolean(inactive)
    deserter = Boolean(deserter)
    if not duration_seconds or not presence_seconds or not presence_buckets or not active_buckets
            or inactive == nil or deserter == nil or inactive or deserter
            or active_buckets > presence_buckets then
        return false
    end
    local minimum_seconds = ConfigInteger("PARAGON_PVP_MATCH_MIN_SECONDS", 60, 1, 3600)
    if duration_seconds < minimum_seconds or presence_seconds < minimum_seconds
            or active_buckets < ConfigInteger("PARAGON_PVP_MATCH_MIN_ACTIVE_BUCKETS", 2, 1, 120) then
        return false
    end
    local percent = ConfigInteger("PARAGON_PVP_MATCH_MIN_ACTIVE_PERCENT", 30, 1, 100)
    return active_buckets * 100 >= presence_buckets * percent
end

function ParagonPvPXP.MatchMinuteCap(battleground_type_id, map_id, is_wintergrasp)
    if Boolean(is_wintergrasp) then
        return ConfigInteger("PARAGON_PVP_WINTERGRASP_CAP_MINUTES", 40, 1, 1440)
    end
    battleground_type_id = Integer(battleground_type_id, 0, 4294967295) or 0
    map_id = Integer(map_id, 0, 4294967295) or 0
    if map_id == MAP_WSG or battleground_type_id == TYPE_WSG then
        return ConfigInteger("PARAGON_PVP_BG_CAP_WSG_MINUTES", 25, 1, 1440)
    elseif map_id == MAP_AB or battleground_type_id == TYPE_AB then
        return ConfigInteger("PARAGON_PVP_BG_CAP_AB_MINUTES", 30, 1, 1440)
    elseif map_id == MAP_EOTS or battleground_type_id == TYPE_EOTS then
        return ConfigInteger("PARAGON_PVP_BG_CAP_EOTS_MINUTES", 25, 1, 1440)
    elseif map_id == MAP_AV or battleground_type_id == TYPE_AV then
        return ConfigInteger("PARAGON_PVP_BG_CAP_AV_MINUTES", 45, 1, 1440)
    elseif map_id == MAP_SOTA or battleground_type_id == TYPE_SOTA then
        return ConfigInteger("PARAGON_PVP_BG_CAP_SOTA_MINUTES", 25, 1, 1440)
    elseif map_id == MAP_IOC or battleground_type_id == TYPE_IOC then
        return ConfigInteger("PARAGON_PVP_BG_CAP_IOC_MINUTES", 40, 1, 1440)
    end
    return ConfigInteger("PARAGON_PVP_BG_CAP_GENERIC_MINUTES", 30, 1, 1440)
end

function ParagonPvPXP.ClassifyBattlegroundObjectives(battleground_type_id, map_id,
        objective1, objective2, objective3, objective4, objective5)
    battleground_type_id = Integer(battleground_type_id, 0, 4294967295) or 0
    map_id = Integer(map_id, 0, 4294967295) or 0
    local values = {
        Integer(objective1, 0, 100000) or 0,
        Integer(objective2, 0, 100000) or 0,
        Integer(objective3, 0, 100000) or 0,
        Integer(objective4, 0, 100000) or 0,
        Integer(objective5, 0, 100000) or 0,
    }
    local major, standard, assist = 0, 0, 0
    if map_id == MAP_WSG or battleground_type_id == TYPE_WSG then
        major, assist = values[1], values[2]
    elseif map_id == MAP_EOTS or battleground_type_id == TYPE_EOTS then
        major = values[1]
    elseif map_id == MAP_AB or battleground_type_id == TYPE_AB
            or map_id == MAP_IOC or battleground_type_id == TYPE_IOC then
        standard = values[1] + values[2]
    elseif map_id == MAP_AV or battleground_type_id == TYPE_AV then
        major = values[3]
        standard = values[1] + values[4]
        assist = values[2] + values[5]
    elseif map_id == MAP_SOTA or battleground_type_id == TYPE_SOTA then
        standard, major = values[1], values[2]
    end
    return major, standard, assist
end

function ParagonPvPXP.ComputeBattlegroundBase(active_buckets, result, major_count,
        standard_count, assist_count, minute_cap)
    active_buckets = Integer(active_buckets, 0, 14400)
    result = Integer(result, RESULT_LOSS, RESULT_DRAW)
    major_count = Integer(major_count, 0, 100000)
    standard_count = Integer(standard_count, 0, 100000)
    assist_count = Integer(assist_count, 0, 100000)
    minute_cap = Integer(minute_cap, 1, 1440)
        or ConfigInteger("PARAGON_PVP_BG_CAP_GENERIC_MINUTES", 30, 1, 1440)
    if not active_buckets or not result or not major_count or not standard_count
            or not assist_count then
        return 0, 0, 0, 0, 0
    end

    local minutes = math.min(active_buckets, minute_cap)
    local per_minute = ConfigInteger("PARAGON_PVP_BG_XP_PER_ACTIVE_MINUTE",
        4000, 0, 100000000)
    local win_per_minute = ConfigInteger("PARAGON_PVP_BG_WIN_XP_PER_ACTIVE_MINUTE",
        1000, 0, 100000000)
    local draw_per_minute = ConfigInteger("PARAGON_PVP_BG_DRAW_XP_PER_ACTIVE_MINUTE",
        500, 0, 100000000)
    local major_xp = ConfigInteger("PARAGON_PVP_BG_OBJECTIVE_MAJOR_XP",
        8000, 0, 100000000)
    local standard_xp = ConfigInteger("PARAGON_PVP_BG_OBJECTIVE_STANDARD_XP",
        4000, 0, 100000000)
    local assist_xp = ConfigInteger("PARAGON_PVP_BG_OBJECTIVE_ASSIST_XP",
        2000, 0, 100000000)

    local active_base = minutes * per_minute
    local result_bonus = 0
    if result == RESULT_WIN then
        result_bonus = minutes * win_per_minute
    elseif result == RESULT_DRAW then
        result_bonus = minutes * draw_per_minute
    end
    local raw_objective = major_count * major_xp
        + standard_count * standard_xp + assist_count * assist_xp
    local cap_percent = ConfigInteger("PARAGON_PVP_BG_OBJECTIVE_CAP_PERCENT", 20, 0, 100)
    local objective_xp = math.min(raw_objective,
        math.floor(active_base * cap_percent / 100))
    return active_base + result_bonus + objective_xp,
        active_base, result_bonus, objective_xp, minutes
end

function ParagonPvPXP.IsArenaActive(duration_seconds, killing_blows, pvp_damage_done,
        pvp_healing_done, tactical_actions, inactive, deserter)
    duration_seconds = Integer(duration_seconds, 0, 864000)
    killing_blows = Integer(killing_blows, 0, 100000)
    pvp_damage_done = Integer(pvp_damage_done, 0, 9007199254740991)
    pvp_healing_done = Integer(pvp_healing_done, 0, 9007199254740991)
    tactical_actions = Integer(tactical_actions, 0, 100000)
    inactive = Boolean(inactive)
    deserter = Boolean(deserter)
    if not duration_seconds or not killing_blows or not pvp_damage_done
            or not pvp_healing_done or not tactical_actions
            or inactive == nil or deserter == nil or inactive or deserter then
        return false
    end
    if duration_seconds < ConfigInteger("PARAGON_PVP_ARENA_MIN_SECONDS", 15, 1, 3600) then
        return false
    end
    local threshold = ConfigInteger("PARAGON_PVP_ARENA_MIN_CONTRIBUTION", 10000, 0, 1000000000)
    return killing_blows > 0 or tactical_actions > 0
        or pvp_damage_done + pvp_healing_done >= threshold
end

function ParagonPvPXP.ComputeArenaBase(rated, bracket, result, prior_roster_settlements)
    rated = Boolean(rated)
    bracket = Integer(bracket, 0, 10)
    result = Integer(result, RESULT_LOSS, RESULT_DRAW)
    if rated == nil or not bracket or not result or result == RESULT_DRAW then
        return 0, 0
    end
    local base = 0
    if rated then
        if bracket ~= 2 and bracket ~= 3 and bracket ~= 5 then
            return 0, 0
        end
        local outcome = result == RESULT_WIN and "WIN" or "LOSS"
        local fallback = ({ [2] = { 37500, 26250 }, [3] = { 45000, 31500 },
            [5] = { 56250, 39000 } })[bracket]
        base = ConfigInteger(string.format("PARAGON_PVP_ARENA_%dV%d_%s_XP",
            bracket, bracket, outcome), result == RESULT_WIN and fallback[1] or fallback[2],
            0, 1000000000)
    else
        base = ConfigInteger(result == RESULT_WIN and "PARAGON_PVP_SKIRMISH_WIN_XP"
            or "PARAGON_PVP_SKIRMISH_LOSS_XP",
            result == RESULT_WIN and 11250 or 7500, 0, 1000000000)
    end
    local percent = M.ArenaRosterDRPercent(prior_roster_settlements)
    return math.floor(base * percent / 100), percent
end

local function BreadthClaim(account_id, recipient_guid, event_token, category, source_entry,
        weekly_period, same_ip_risk)
    category = StrictKey(category, 100)
    weekly_period = StrictKey(weekly_period, 64)
    if not category or not weekly_period then
        return nil
    end
    local entitlement = StrictKey("breadth:" .. weekly_period .. ":" .. category, 191)
    if not entitlement then
        return nil
    end
    local base_xp = ConfigInteger("PARAGON_PVP_WEEKLY_BREADTH_XP", 20000, 0, 1000000000)
    if base_xp <= 0 then
        return nil
    end
    return {
        account_id = account_id,
        recipient_guid = recipient_guid,
        event_token = event_token,
        component = COMPONENT.BREADTH,
        source_type = SOURCE.PVP_BREADTH,
        source_entry = source_entry or 0,
        base_xp = base_xp,
        period_key = weekly_period,
        entitlement_key = entitlement,
        same_ip_risk = same_ip_risk,
    }
end

function ParagonPvPXP.OnHonor(event, player, victim, final_honor, honor_source,
        battleground_type_id, arena_type, rated, generated_battleground_xp,
        event_token)
    if not PreparePlayer(player) then
        return
    end
    event_token = StrictKey(event_token, 191)
    final_honor = Integer(final_honor, 1, 1000000000)
    honor_source = Integer(honor_source, 1, 4)
    battleground_type_id = Integer(battleground_type_id, 0, 4294967295) or 0
    if not event_token or not final_honor or not honor_source then
        return
    end

    -- Source 4 includes arbitrary RewardHonor(nullptr, ...) callers (GM, item,
    -- quest, custom script). The bridge provides no proof of a recognized PvP
    -- context for them, so it fails closed. Source 3 must prove BG context.
    if honor_source == 4 or (honor_source == 3
            and not RECOGNIZED_HONOR_CONTEXT[battleground_type_id]) then
        return
    end

    local account_id = AccountId(player)
    local recipient_guid = RecipientGuid(player)
    local victim_account = 0
    local prior = 0
    if honor_source == 1 then
        if not victim or not victim.GetAccountId then
            return
        end
        victim_account = Integer(victim:GetAccountId(), 1, 4294967295) or 0
        if victim_account == 0 or victim_account == account_id then
            return
        end
        local window = ConfigInteger("PARAGON_PVP_HONOR_DR_WINDOW_MINUTES", 30, 1, 1440)
        prior = ScalarOrNil(string.format([[
            SELECT COUNT(*) FROM %s
            WHERE account_id = %d AND component = '%s'
              AND counterpart_account_id = %d AND base_xp > 0
              AND paid_at IS NOT NULL
              AND paid_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %d MINUTE);]],
            CLAIM_TABLE, account_id, COMPONENT.HONOR, victim_account, window))
        if prior == nil then
            return
        end
    end
    local base_xp = M.ComputeHonorXP(final_honor, prior)
    ReserveAndPay({ {
        player = player,
        claim = {
            account_id = account_id,
            recipient_guid = recipient_guid,
            event_token = event_token,
            component = COMPONENT.HONOR,
            source_type = SOURCE.PVP_HONOR,
            source_entry = honor_source,
            base_xp = base_xp,
            counterpart_account_id = victim_account,
        },
    } })
end

function ParagonPvPXP.OnMatchComplete(event, player, match_kind, result, duration_seconds,
        active_seconds, presence_buckets, active_buckets, tactical_actions,
        battleground_type_id, map_id, instance_id, arena_type, rated, bracket_id,
        player_team, winner_team, killing_blows, deaths, honorable_kills,
        bonus_honor, damage_done, healing_done, pvp_damage_done, pvp_healing_done,
        objective1, objective2, objective3, objective4, objective5, is_bot,
        account_id_argument, opponent_count, real_opponent_count,
        bot_opponent_count, unique_opponent_accounts, same_account_opponent,
        same_ip_opponent, inactive, deserter, opponent_roster_key, event_token)
    if not PreparePlayer(player) then
        return
    end
    match_kind = Integer(match_kind, MATCH_BATTLEGROUND, MATCH_ARENA)
    result = Integer(result, RESULT_LOSS, RESULT_DRAW)
    local account_id = AccountId(player)
    local recipient_guid = RecipientGuid(player)
    account_id_argument = Integer(account_id_argument, 1, 4294967295)
    event_token = StrictKey(event_token, 191)
    is_bot = Boolean(is_bot)
    same_account_opponent = Boolean(same_account_opponent)
    local same_ip = Boolean(same_ip_opponent)
    inactive = Boolean(inactive)
    deserter = Boolean(deserter)
    local real_opponents = Integer(real_opponent_count, 0, 1000)
    local bot_opponents = Integer(bot_opponent_count, 0, 1000)
    local opponents = Integer(opponent_count, 0, 1000)
    if not match_kind or not result or not event_token or not account_id_argument
            or account_id_argument ~= account_id or is_bot == nil or is_bot
            or same_account_opponent == nil or same_account_opponent
            or same_ip == nil or inactive == nil or deserter == nil
            or not real_opponents or not bot_opponents or not opponents
            or real_opponents + bot_opponents <= 0 then
        return
    end

    if match_kind == MATCH_ARENA then
        rated = Boolean(rated)
        arena_type = Integer(arena_type, 0, 10)
        opponent_roster_key = StrictKey(opponent_roster_key, 191)
        if rated == nil or not arena_type or not opponent_roster_key
                or not M.IsArenaActive(duration_seconds, killing_blows,
                    pvp_damage_done, pvp_healing_done, tactical_actions,
                    inactive, deserter) then
            return
        end
        local window = ConfigInteger("PARAGON_PVP_ARENA_ROSTER_DR_WINDOW_MINUTES", 60, 1, 1440)
        local prior = ScalarOrNil(string.format([[
            SELECT COUNT(*) FROM %s
            WHERE account_id = %d
              AND component IN ('%s','%s')
              AND opponent_key = '%s'
              AND created_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %d MINUTE);]],
            CLAIM_TABLE, account_id, COMPONENT.ARENA_RATED,
            COMPONENT.ARENA_SKIRMISH, opponent_roster_key, window))
        if prior == nil then
            return
        end
        local base_xp = M.ComputeArenaBase(rated, arena_type, result, prior)
        local daily_period, weekly_period = CurrentPeriods()
        if not daily_period or not weekly_period then
            return
        end
        local component = rated and COMPONENT.ARENA_RATED or COMPONENT.ARENA_SKIRMISH
        if not rated then
            local used = ScalarOrNil(string.format([[
                SELECT COALESCE(SUM(base_xp), 0) FROM %s
                WHERE account_id = %d AND component = '%s' AND period_key = '%s';]],
                CLAIM_TABLE, account_id, component, daily_period))
            if used == nil then
                return
            end
            local cap = ConfigInteger("PARAGON_PVP_SKIRMISH_DAILY_CAP_XP", 56250, 0, 1000000000)
            base_xp = math.min(base_xp, math.max(0, cap - used))
        end
        local claims = { {
            player = player,
            claim = {
                account_id = account_id,
                recipient_guid = recipient_guid,
                event_token = event_token,
                component = component,
                source_type = SOURCE.PVP_ARENA,
                source_entry = arena_type,
                base_xp = base_xp,
                opponent_key = opponent_roster_key,
                period_key = rated and "" or daily_period,
                same_ip_risk = same_ip,
            },
        } }
        if rated and result == RESULT_WIN then
            local breadth = BreadthClaim(account_id, recipient_guid, event_token,
                "arena:" .. arena_type, arena_type, weekly_period, same_ip)
            if breadth then
                claims[#claims + 1] = { player = player, claim = breadth }
            end
        end
        ReserveAndPay(claims)
        return
    end

    if not M.IsMatchActive(duration_seconds, active_seconds, presence_buckets, active_buckets,
            inactive, deserter) then
        return
    end
    battleground_type_id = Integer(battleground_type_id, 0, 4294967295) or 0
    map_id = Integer(map_id, 0, 4294967295) or 0
    local major, standard, assist = M.ClassifyBattlegroundObjectives(
        battleground_type_id, map_id, objective1, objective2, objective3,
        objective4, objective5)
    local base_xp = M.ComputeBattlegroundBase(active_buckets, result,
        major, standard, assist,
        M.MatchMinuteCap(battleground_type_id, map_id, false))
    local _, weekly_period = CurrentPeriods()
    if not weekly_period then
        return
    end
    local claims = { {
        player = player,
        claim = {
            account_id = account_id,
            recipient_guid = recipient_guid,
            event_token = event_token,
            component = COMPONENT.BATTLEGROUND,
            source_type = SOURCE.PVP_BATTLEGROUND,
            source_entry = map_id > 0 and map_id or battleground_type_id,
            base_xp = base_xp,
            same_ip_risk = same_ip,
        },
    } }
    local breadth = BreadthClaim(account_id, recipient_guid, event_token,
        string.format("bg:%d:%d", battleground_type_id, map_id),
        map_id > 0 and map_id or battleground_type_id, weekly_period, same_ip)
    if breadth then
        claims[#claims + 1] = { player = player, claim = breadth }
    end
    ReserveAndPay(claims)
end

function ParagonPvPXP.OnBattlefieldComplete(event, player, battlefield_type_id, battle_id,
        zone_id, map_id, result, duration_seconds, active_seconds,
        presence_buckets, active_buckets, tactical_actions, player_team,
        winner_team, attacker_team, defender_team_at_start, ended_by_timer,
        is_bot, account_id_argument, player_kills, pvp_damage_done,
        pvp_healing_done, objective_major, objective_standard, objective_assist,
        real_opponent_count, bot_opponent_count, unique_opponent_accounts,
        same_account_opponent, same_ip_opponent, inactive, deserter,
        opponent_roster_key, event_token)
    if not PreparePlayer(player) then
        return
    end
    local account_id = AccountId(player)
    local recipient_guid = RecipientGuid(player)
    account_id_argument = Integer(account_id_argument, 1, 4294967295)
    event_token = StrictKey(event_token, 191)
    result = Integer(result, RESULT_LOSS, RESULT_WIN)
    is_bot = Boolean(is_bot)
    same_account_opponent = Boolean(same_account_opponent)
    local same_ip = Boolean(same_ip_opponent)
    inactive = Boolean(inactive)
    deserter = Boolean(deserter)
    local real_opponents = Integer(real_opponent_count, 0, 1000)
    local bot_opponents = Integer(bot_opponent_count, 0, 1000)
    if not account_id_argument or account_id_argument ~= account_id
            or not event_token or not result or is_bot == nil or is_bot
            or same_account_opponent == nil or same_account_opponent
            or same_ip == nil or inactive == nil or deserter == nil
            or not real_opponents or not bot_opponents
            or real_opponents + bot_opponents <= 0
            or not M.IsMatchActive(duration_seconds, active_seconds, presence_buckets,
                active_buckets, inactive, deserter) then
        return
    end
    objective_major = Integer(objective_major, 0, 100000) or 0
    objective_standard = Integer(objective_standard, 0, 100000) or 0
    objective_assist = Integer(objective_assist, 0, 100000) or 0
    local base_xp = M.ComputeBattlegroundBase(active_buckets, result,
        objective_major, objective_standard, objective_assist,
        M.MatchMinuteCap(battlefield_type_id, map_id, true))
    battlefield_type_id = Integer(battlefield_type_id, 0, 4294967295) or 0
    zone_id = Integer(zone_id, 0, 4294967295) or 0
    map_id = Integer(map_id, 0, 4294967295) or 0
    local _, weekly_period = CurrentPeriods()
    if not weekly_period then
        return
    end
    local claims = { {
        player = player,
        claim = {
            account_id = account_id,
            recipient_guid = recipient_guid,
            event_token = event_token,
            component = COMPONENT.WINTERGRASP,
            source_type = SOURCE.PVP_WINTERGRASP,
            source_entry = zone_id > 0 and zone_id or map_id,
            base_xp = base_xp,
            same_ip_risk = same_ip,
        },
    } }
    local breadth = BreadthClaim(account_id, recipient_guid, event_token, "wintergrasp",
        zone_id > 0 and zone_id or battlefield_type_id, weekly_period, same_ip)
    if breadth then
        claims[#claims + 1] = { player = player, claim = breadth }
    end
    ReserveAndPay(claims)
end

function ParagonPvPXP.OnOutdoorObjective(event, player, outdoor_pvp_type_id, objective_id,
        objective_entry, objective_tier, map_id, zone_id, team,
        participant_count, event_token)
    if not PreparePlayer(player) then
        return
    end
    event_token = StrictKey(event_token, 191)
    outdoor_pvp_type_id = Integer(outdoor_pvp_type_id, 0, 4294967295)
    objective_id = Integer(objective_id, 0, 4294967295)
    objective_entry = Integer(objective_entry, 0, 4294967295)
    objective_tier = Integer(objective_tier, OUTDOOR_STANDARD, OUTDOOR_MAJOR)
    map_id = Integer(map_id, 0, 4294967295)
    zone_id = Integer(zone_id, 0, 4294967295)
    participant_count = Integer(participant_count, 1, 1000)
    if not event_token or not outdoor_pvp_type_id or not objective_id
            or not objective_entry or not objective_tier or not map_id
            or not zone_id or not participant_count then
        return
    end
    local base_xp = objective_tier == OUTDOOR_MAJOR
        and ConfigInteger("PARAGON_PVP_OUTDOOR_MAJOR_XP", 30000, 0, 1000000000)
        or ConfigInteger("PARAGON_PVP_OUTDOOR_STANDARD_XP", 15000, 0, 1000000000)
    local account_id = AccountId(player)
    local recipient_guid = RecipientGuid(player)
    local _, weekly_period = CurrentPeriods()
    if not weekly_period then
        return
    end
    local source_entry = objective_entry > 0 and objective_entry or objective_id
    local claims = { {
        player = player,
        claim = {
            account_id = account_id,
            recipient_guid = recipient_guid,
            event_token = event_token,
            component = COMPONENT.OUTDOOR,
            source_type = SOURCE.PVP_OBJECTIVE,
            source_entry = source_entry,
            base_xp = base_xp,
        },
    } }
    local breadth = BreadthClaim(account_id, recipient_guid, event_token,
        string.format("outdoor:%d:%d", outdoor_pvp_type_id, zone_id),
        source_entry, weekly_period, false)
    if breadth then
        claims[#claims + 1] = { player = player, claim = breadth }
    end
    ReserveAndPay(claims)
end

function ParagonPvPXP.OnDuelComplete(event, winner, loser, duel_type, duration_seconds,
        same_account, same_ip, winner_is_bot, loser_is_bot, event_token)
    event_token = StrictKey(event_token, 191)
    duel_type = Integer(duel_type, 0, 255)
    duration_seconds = Integer(duration_seconds, 0, 86400)
    same_account = Boolean(same_account)
    same_ip = Boolean(same_ip)
    winner_is_bot = Boolean(winner_is_bot)
    loser_is_bot = Boolean(loser_is_bot)
    local winner_account = AccountId(winner)
    local loser_account = AccountId(loser)
    if not event_token or duel_type ~= 1 or not duration_seconds
            or same_account == nil or same_ip == nil
            or winner_is_bot == nil or loser_is_bot == nil
            or same_account or not winner_account or not loser_account
            or winner_account == loser_account
            or winner:GetLevel() < MinimumLevel()
            or loser:GetLevel() < MinimumLevel() then
        return
    end

    -- A playerbot can be the opponent, but never the recipient. Evaluate the
    -- two account-scoped duel claims independently so the real side still
    -- receives its full win/loss value.
    local winner_eligible = not winner_is_bot and not IsBot(winner)
        and PreparePlayer(winner)
    local loser_eligible = not loser_is_bot and not IsBot(loser)
        and PreparePlayer(loser)
    if not winner_eligible and not loser_eligible then
        return
    end

    local daily_period = CurrentPeriods()
    if not daily_period then
        return
    end
    local cap = ConfigInteger("PARAGON_PVP_DUEL_DISTINCT_OPPONENTS_PER_DAY", 3, 0, 100)
    local claims = {}
    local database_ok = true
    local function AddDuelClaim(player, account_id, opponent_id, component, amount)
        amount = Integer(amount, 0, 1000000000) or 0
        local entitlement = StrictKey(string.format("duel:%s:%d",
            daily_period, opponent_id), 191)
        if not entitlement then
            return
        end
        local used = ScalarOrNil(string.format([[
            SELECT COUNT(*) FROM %s
            WHERE account_id = %d AND period_key = '%s'
              AND entitlement_key IS NOT NULL
              AND component IN ('%s','%s');]], CLAIM_TABLE, account_id,
            daily_period, COMPONENT.DUEL_WIN, COMPONENT.DUEL_LOSS))
        local already_rewarded_count = ScalarOrNil(string.format([[
            SELECT COUNT(*) FROM %s
            WHERE account_id = %d AND entitlement_key = '%s';]],
            CLAIM_TABLE, account_id, entitlement))
        if used == nil or already_rewarded_count == nil then
            database_ok = false
            return
        end
        local already_rewarded = already_rewarded_count > 0
        if used >= cap or already_rewarded or amount <= 0 then
            amount = 0
            entitlement = nil
        end
        claims[#claims + 1] = {
            player = player,
            claim = {
                account_id = account_id,
                recipient_guid = RecipientGuid(player),
                event_token = event_token,
                component = component,
                source_type = SOURCE.PVP_DUEL,
                source_entry = opponent_id,
                base_xp = amount,
                counterpart_account_id = opponent_id,
                period_key = daily_period,
                entitlement_key = entitlement,
                same_ip_risk = same_ip,
            },
        }
    end
    if winner_eligible then
        AddDuelClaim(winner, winner_account, loser_account, COMPONENT.DUEL_WIN,
            ConfigInteger("PARAGON_PVP_DUEL_WIN_XP", 5000, 0, 1000000000))
    end
    if loser_eligible then
        AddDuelClaim(loser, loser_account, winner_account, COMPONENT.DUEL_LOSS,
            ConfigInteger("PARAGON_PVP_DUEL_LOSS_XP", 2000, 0, 1000000000))
    end
    if not database_ok then
        return
    end
    ReserveAndPay(claims)
end

M.COMPONENT = COMPONENT
M.RESULT = { LOSS = RESULT_LOSS, WIN = RESULT_WIN, DRAW = RESULT_DRAW }
M.MATCH_KIND = { BATTLEGROUND = MATCH_BATTLEGROUND, ARENA = MATCH_ARENA }
M.HONOR_CONTEXT = {
    OUTDOOR_PVP = HONOR_CONTEXT_OUTDOOR_PVP,
    BATTLEFIELD = HONOR_CONTEXT_BATTLEFIELD,
}
M.SOURCE = {
    HONOR = SOURCE.PVP_HONOR,
    BATTLEGROUND = SOURCE.PVP_BATTLEGROUND,
    ARENA = SOURCE.PVP_ARENA,
    OBJECTIVE = SOURCE.PVP_OBJECTIVE,
    DUEL = SOURCE.PVP_DUEL,
    BREADTH = SOURCE.PVP_BREADTH,
    WINTERGRASP = SOURCE.PVP_WINTERGRASP,
}

RegisterPlayerEvent(77, ParagonPvPXP.OnHonor)
RegisterPlayerEvent(78, ParagonPvPXP.OnMatchComplete)
RegisterPlayerEvent(79, ParagonPvPXP.OnBattlefieldComplete)
RegisterPlayerEvent(80, ParagonPvPXP.OnOutdoorObjective)
RegisterPlayerEvent(81, ParagonPvPXP.OnDuelComplete)

RegisterMediatorEvent("OnAfterPlayerStatReady", function(player, paragon)
    if player and not IsBot(player) then
        MaybeCleanup()
        M.PayPendingClaims(player)
    end
end)

package.loaded["paragon.modules.paragon_pvp_xp"] = M

print("[Paragon] PvP Merit XP module loaded")

return M
