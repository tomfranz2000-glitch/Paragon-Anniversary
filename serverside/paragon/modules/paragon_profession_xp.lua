--[[
    Paragon profession experience

    Two deliberately different reward lanes live here:

      * genuine profession skill points are one-time completion progress. The
        highest rewarded value is durable at account scope when Paragon is
        account-linked (character scope otherwise), and the resulting XP is
        flat: it never crosses OnExperienceCalculated;
      * successful craft/gather/process actions are repeatable. ALE event 76
        supplies a server action token and authoritative context, the generated
        profession data resolves the base amount, and the award crosses
        OnExperienceCalculated exactly once.

    Event 76 contract:
      (event, player, actionKind, skillId, contextId, quantity, actionToken)

    The generated resolver is the only valuation authority. Unknown or
    mismatched rows fail closed.
]]

local Config = require("paragon_config")
local Constant = require("paragon_constant")
local Hook = require("paragon_hook")
local ProfessionData = require("paragon.modules.paragon_profession_data")

local SOURCE = Hook.ExperienceSource
local ACTION = ProfessionData.ACTION
local DB = Constant.DB_NAME
local PROGRESS_TABLE = DB .. ".paragon_profession_progress"

local OWNER_CHARACTER = 0
local OWNER_ACCOUNT = 1
local TOKEN_CACHE_LIMIT = 4096
local PROGRESS_CACHE_KEY = "ParagonProfessionProgressV1"
local TOKEN_CACHE_KEY = "ParagonProfessionTokensV1"
-- Active players retain their scope through SetData; weak values let an
-- offline account/character scope be collected instead of growing forever.
local PROGRESS_SCOPES = setmetatable({}, { __mode = "v" })

-- Exact AzerothCore WotLK IsProfessionSkill set. Weapon, defense, riding and
-- lockpicking skill lines are intentionally absent.
local PROFESSION_SKILLS = {
    [129] = true, -- First Aid
    [164] = true, -- Blacksmithing
    [165] = true, -- Leatherworking
    [171] = true, -- Alchemy
    [182] = true, -- Herbalism
    [185] = true, -- Cooking
    [186] = true, -- Mining
    [197] = true, -- Tailoring
    [202] = true, -- Engineering
    [333] = true, -- Enchanting
    [356] = true, -- Fishing
    [393] = true, -- Skinning
    [755] = true, -- Jewelcrafting
    [773] = true, -- Inscription
}

-- This is the only bridge from ALE action kinds to Paragon source types.
-- Keep it local and explicit so a core enum change is a one-table adjustment.
local ACTION_SOURCE = {
    [ACTION.CRAFT] = SOURCE.CRAFT,
    [ACTION.GATHER_GAMEOBJECT] = SOURCE.GATHER,
    [ACTION.GATHER_CREATURE] = SOURCE.GATHER,
    [ACTION.FISHING_AREA] = SOURCE.GATHER,
    [ACTION.FISHING_HOLE] = SOURCE.GATHER,
    [ACTION.PROSPECT] = SOURCE.PROCESS,
    [ACTION.MILL] = SOURCE.PROCESS,
    [ACTION.DISENCHANT] = SOURCE.PROCESS,
}

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

local function IsBot(player)
    return player and player.IsPlayerBot and player:IsPlayerBot()
end

local function MinLevel()
    return tonumber(Config:GetByField("MINIMUM_LEVEL_FOR_PARAGON_XP")) or 80
end

local function SystemEnabled()
    return (tonumber(Config:GetByField("ENABLE_PARAGON_SYSTEM")) or 1) ~= 0
end

local function SkillPointXP()
    local value = Integer(Config:GetByField("UNIVERSAL_SKILL_EXPERIENCE")) or 1000
    return math.max(0, value)
end

local function Scope(player)
    if tonumber(Config:GetByField("LEVEL_LINKED_TO_ACCOUNT")) == 1 then
        return OWNER_ACCOUNT, Integer(player:GetAccountId())
    end
    return OWNER_CHARACTER, Integer(player:GetGUIDLow())
end

local function ScopeCache(player)
    local owner_type, owner_id = Scope(player)
    if owner_id == nil or owner_id <= 0 then
        return nil
    end

    -- One world process can have multiple characters from the same account
    -- online. Share their account-scoped high-water cache so serialized ALE
    -- callbacks cannot both claim the same point while async SQL catches up.
    local scope_key = tostring(owner_type) .. ":" .. tostring(owner_id)
    local cache = PROGRESS_SCOPES[scope_key]
    if not cache then
        cache = { owner_type = owner_type, owner_id = owner_id, skills = {}, loaded_all = false }
        PROGRESS_SCOPES[scope_key] = cache
    end
    player:SetData(PROGRESS_CACHE_KEY, cache)
    return cache
end

local function LoadState(player, skill_id)
    local cache = ScopeCache(player)
    if not cache then
        return nil
    end
    if cache.skills[skill_id] then
        return cache.skills[skill_id], cache
    end

    local state = { high_water = 0, pending_xp = 0 }
    local result = CharDBQuery(string.format(
        "SELECT high_water, pending_xp FROM %s "
            .. "WHERE owner_type = %d AND owner_id = %d AND skill_id = %d;",
        PROGRESS_TABLE, cache.owner_type, cache.owner_id, skill_id))
    if result then
        state.high_water = result:GetUInt32(0)
        state.pending_xp = result:GetUInt64(1)
    end
    cache.skills[skill_id] = state
    return state, cache
end

local function LoadAllStates(player)
    local cache = ScopeCache(player)
    if not cache then
        return nil
    end
    if cache.loaded_all then
        return cache
    end

    local result = CharDBQuery(string.format(
        "SELECT skill_id, high_water, pending_xp FROM %s "
            .. "WHERE owner_type = %d AND owner_id = %d;",
        PROGRESS_TABLE, cache.owner_type, cache.owner_id))
    if result then
        repeat
            local skill_id = result:GetUInt32(0)
            if PROFESSION_SKILLS[skill_id] then
                local existing = cache.skills[skill_id]
                local db_high_water = result:GetUInt32(1)
                local db_pending = result:GetUInt64(2)
                if existing then
                    -- Async writes may still be reaching the DB. Never let a
                    -- temporarily older row move the live session backward.
                    existing.high_water = math.max(existing.high_water or 0, db_high_water)
                    existing.pending_xp = math.max(existing.pending_xp or 0, db_pending)
                else
                    cache.skills[skill_id] = {
                        high_water = db_high_water,
                        pending_xp = db_pending,
                    }
                end
            end
        until not result:NextRow()
    end
    cache.loaded_all = true
    return cache
end

-- ALE documents CharDBQuery as its synchronous character-database primitive.
-- DML intentionally returns nil (there is no result set), but the statement has
-- completed before control returns. Mastery awards use it for write ordering;
-- repeatable profession actions never touch this path.
local function ExecuteSync(sql)
    CharDBQuery(sql)
end

-- Durable monotonic high-water update plus an optional newly banked amount.
-- VALUES(pending_xp) is an increment, never a replacement; repeated Lua
-- callbacks are suppressed by the cached high-water before this statement.
local function PersistProgress(cache, skill_id, high_water, pending_delta)
    ExecuteSync(string.format([[
        INSERT INTO %s
            (owner_type, owner_id, skill_id, high_water, pending_xp)
        VALUES (%d, %d, %d, %d, %d)
        ON DUPLICATE KEY UPDATE
            high_water = GREATEST(high_water, VALUES(high_water)),
            pending_xp = pending_xp + VALUES(pending_xp);]],
        PROGRESS_TABLE, cache.owner_type, cache.owner_id, skill_id,
        high_water, pending_delta or 0))
end

local function ProgressionTarget(cache)
    if cache.owner_type == OWNER_ACCOUNT then
        return DB .. ".account_paragon", "account_id"
    end
    return DB .. ".character_paragon", "guid"
end

-- Ensure the progression side of the later multi-table UPDATE exists. This is
-- a no-op for established players and records only the already-loaded state for
-- a first-time player; it never acknowledges pending profession XP.
local function EnsureProgressionRow(player, cache)
    local paragon = player:GetData("Paragon")
    local level = paragon and Integer(paragon:GetLevel())
    local experience = paragon and Integer(paragon:GetExperience())
    if not level or level <= 0 or not experience or experience < 0 then
        return false
    end

    local table_name, id_column = ProgressionTarget(cache)
    ExecuteSync(string.format(
        "INSERT IGNORE INTO %s (%s, level, experience) VALUES (%d, %d, %d);",
        table_name, id_column, cache.owner_id, level, experience))
    return true
end

-- Persist the awarded Paragon state and acknowledge every pending profession
-- row in one InnoDB multi-table UPDATE. A crash before this statement leaves
-- the pending write-ahead rows payable; a crash after it sees both effects.
local function PersistAwardAndClear(player, cache)
    local paragon = player:GetData("Paragon")
    local level = paragon and Integer(paragon:GetLevel())
    local experience = paragon and Integer(paragon:GetExperience())
    if not level or level <= 0 or not experience or experience < 0 then
        return false
    end

    local table_name, id_column = ProgressionTarget(cache)
    ExecuteSync(string.format([[
        UPDATE %s progression
        JOIN %s profession
          ON profession.owner_type = %d
         AND profession.owner_id = %d
         AND profession.pending_xp > 0
        SET progression.level = %d,
            progression.experience = %d,
            profession.pending_xp = 0
        WHERE progression.%s = %d;]],
        table_name, PROGRESS_TABLE, cache.owner_type, cache.owner_id,
        level, experience, id_column, cache.owner_id))
    return true
end

local function CanPayNow(player)
    return SystemEnabled() and player and not IsBot(player)
        and player:GetLevel() >= MinLevel()
        and player:GetData("Paragon") ~= nil
end

-- Pays every pending profession point for the active progression scope as one
-- silent lump. A failed award leaves the durable rows and cache untouched.
local function PayPending(player)
    if not CanPayNow(player) then
        return false
    end

    local cache = LoadAllStates(player)
    if not cache then
        return false
    end

    local pending = 0
    for _, state in pairs(cache.skills) do
        pending = pending + (Integer(state.pending_xp) or 0)
    end
    if pending <= 0 then
        return false
    end

    if not EnsureProgressionRow(player, cache) then
        return false
    end

    if not Hook.AwardFlatExperience(player, SOURCE.SKILLUP, 0, pending) then
        return false
    end

    if not PersistAwardAndClear(player, cache) then
        return false
    end

    -- Event callbacks are serialized on the world thread. The durable state
    -- and live cache are acknowledged in the same callback.
    for _, state in pairs(cache.skills) do
        state.pending_xp = 0
    end
    return true
end

local function SeedPlayer(player)
    if not player or IsBot(player) then
        return
    end
    local cache = ScopeCache(player)
    if not cache then
        return
    end

    for skill_id in pairs(PROFESSION_SKILLS) do
        local current = 0
        if player.GetPureSkillValue then
            current = Integer(player:GetPureSkillValue(skill_id)) or 0
        elseif player.GetSkillValue then
            current = Integer(player:GetSkillValue(skill_id)) or 0
        end
        if current > 0 then
            CharDBExecute(string.format([[
                INSERT INTO %s
                    (owner_type, owner_id, skill_id, high_water, pending_xp)
                VALUES (%d, %d, %d, %d, 0)
                ON DUPLICATE KEY UPDATE
                    high_water = GREATEST(high_water, VALUES(high_water));]],
                PROGRESS_TABLE, cache.owner_type, cache.owner_id, skill_id, current))
            local state = cache.skills[skill_id]
            if state then
                state.high_water = math.max(state.high_water or 0, current)
            else
                cache.skills[skill_id] = { high_water = current, pending_xp = 0 }
            end
        end
    end
end

local function OnSkillUpdate(event, player, skill_id, old_value, max_value, step, new_value)
    -- Do not turn progress earned while the entire Paragon system is disabled
    -- into a deferred windfall. Existing pending rows are deliberately left
    -- untouched and become payable again after re-enable.
    if not SystemEnabled() or not player or IsBot(player) then
        return
    end

    skill_id = Integer(skill_id)
    if not skill_id or not PROFESSION_SKILLS[skill_id] then
        return
    end

    local previous = Integer(old_value)
    local current = Integer(new_value)
    if not previous or not current or current <= previous then
        return
    end

    local state, cache = LoadState(player, skill_id)
    if not state then
        return
    end

    -- The event's previous value is also authoritative. Using the greater of
    -- it and durable high-water prevents a newly installed script from paying
    -- historical points even if its migration seed was missed.
    local baseline = math.max(previous, state.high_water or 0)
    local points = math.max(0, current - baseline)
    state.high_water = math.max(current, state.high_water or 0)
    if points <= 0 then
        PersistProgress(cache, skill_id, state.high_water, 0)
        return
    end

    local amount = points * SkillPointXP()
    if amount <= 0 then
        PersistProgress(cache, skill_id, state.high_water, 0)
        return
    end

    -- First make the new claim part of the durable pending ledger. This is a
    -- write-ahead record: an interrupted/not-ready award remains payable, while
    -- the shared high-water cache prevents same-process account-alt races.
    state.pending_xp = (state.pending_xp or 0) + amount
    PersistProgress(cache, skill_id, state.high_water, amount)

    if CanPayNow(player) then
        local paragon = player:GetData("Paragon")
        paragon = Mediator.On("OnBeforeSkillExperience", {
            arguments = { player, skill_id, paragon },
            defaults = { paragon },
        })
        player:SetData("Paragon", paragon)
        PayPending(player)
    end
end

local function TokenKey(action_token)
    if action_token == nil then
        return nil
    end

    local token_type = type(action_token)
    if token_type ~= "number" and token_type ~= "string" then
        return nil
    end
    if token_type == "number" then
        if action_token ~= action_token or action_token <= 0
                or action_token ~= math.floor(action_token) then
            return nil
        end
    end

    local token = tostring(action_token):match("^%s*(.-)%s*$")
    if not token or token == "" or #token > 128 or token:find("%z") then
        return nil
    end
    local numeric_token = tonumber(token)
    if numeric_token and numeric_token <= 0 then
        return nil
    end
    return token
end

local function IsDuplicateAction(player, action_token, remember)
    local key = TokenKey(action_token)
    if not key then
        return true
    end

    local cache = player:GetData(TOKEN_CACHE_KEY)
    if not cache then
        cache = { seen = {}, ring = {}, cursor = 0 }
        player:SetData(TOKEN_CACHE_KEY, cache)
    end
    if cache.seen[key] then
        return true
    end
    if not remember then
        return false
    end

    cache.cursor = cache.cursor % TOKEN_CACHE_LIMIT + 1
    local oldest = cache.ring[cache.cursor]
    if oldest then
        cache.seen[oldest] = nil
    end
    cache.ring[cache.cursor] = key
    cache.seen[key] = true
    return false
end

local function OnProfessionAction(event, player, action_kind, skill_id,
        context_id, quantity, action_token)
    if not SystemEnabled() or not player or IsBot(player)
            or player:GetLevel() < MinLevel()
            or not player:GetData("Paragon") then
        return
    end

    action_kind = Integer(action_kind)
    skill_id = Integer(skill_id)
    context_id = Integer(context_id)
    quantity = Integer(quantity)
    local source_type = action_kind and ACTION_SOURCE[action_kind]
    if not source_type or not skill_id or not PROFESSION_SKILLS[skill_id]
            or not context_id or context_id <= 0 or not quantity or quantity <= 0
            or IsDuplicateAction(player, action_token, false) then
        return
    end

    local base_xp = ProfessionData.Resolve(action_kind, skill_id, context_id, quantity)
    base_xp = Integer(base_xp)
    if not base_xp or base_xp <= 0 then
        return
    end

    -- Remember only a valid resolved action, immediately before its single
    -- award attempt. A replay cannot earn XP even if a modifier later vetoes it.
    if IsDuplicateAction(player, action_token, true) then
        return
    end
    Hook.AwardExperience(player, source_type, context_id, base_xp, true)
end

-- paragon_hook owns event 62 for backward compatibility and delegates here.
Hook.ProfessionSkillUpdateHandler = OnSkillUpdate
RegisterPlayerEvent(76, OnProfessionAction)

-- The ready event fires after player:SetData("Paragon", paragon), unlike the
-- earlier load hooks. It is therefore safe for a pending payout.
RegisterMediatorEvent("OnAfterPlayerStatReady", function(player, paragon)
    if player and not IsBot(player) then
        LoadAllStates(player)
        SeedPlayer(player)
        PayPending(player)
    end
end)

-- Reaching the eligibility threshold in the same session is the fast path for
-- pre-80 skill-point banks. Login/Lua-reload remains covered by the ready hook.
RegisterPlayerEvent(13, function(event, player, old_level)
    local min_level = MinLevel()
    old_level = Integer(old_level)
    if player and old_level and not IsBot(player) and old_level < min_level
            and player:GetLevel() >= min_level then
        PayPending(player)
    end
end)

print("[Paragon] Profession XP module loaded")

return {
    ACTION_SOURCE = ACTION_SOURCE,
    PROFESSION_SKILLS = PROFESSION_SKILLS,
    OnProfessionAction = OnProfessionAction,
    OnSkillUpdate = OnSkillUpdate,
    PayPending = PayPending,
    SeedPlayer = SeedPlayer,
}
