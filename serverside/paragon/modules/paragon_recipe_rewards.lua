--[[
    Account-wide, one-time profession recipe XP.

    The generated whitelist contains final craft-spell IDs only.  Event 44 is
    therefore both strict and path-independent: a trainer, pattern, discovery,
    quest wrapper, specialization switch, or alternate teaching item all land
    on the same durable (account_id, spell_id) entitlement.

    Existing known recipes never pay retroactively.  Each character is seeded
    once per generated catalogue version on the Paragon-ready login event;
    known whitelist spells are inserted with zero pending XP before the session
    accepts new learn events.  Future learns are write-ahead claims.  Below the
    configured minimum level their XP remains pending and is paid as one flat,
    unmodified amount when an eligible character reaches the threshold.
]]

local Config = require("paragon_config")
local Constant = require("paragon_constant")
local Hook = require("paragon_hook")
local RecipeData = require("paragon.modules.paragon_recipe_data")

local DB = Constant.DB_NAME
local CLAIM_TABLE = DB .. ".paragon_recipe_reward_claim"
local SEED_TABLE = DB .. ".paragon_recipe_reward_seed"
local SOURCE = Hook.ExperienceSource.COLLECTIBLE
local SESSION_SEEDED_KEY = "ParagonRecipeRewardSeededV1"
local SCOPE_CACHE_KEY = "ParagonRecipeRewardScopeV1"
local OWNER_CHARACTER = 0
local OWNER_ACCOUNT = 1
local SCOPES = setmetatable({}, { __mode = "v" })

local function Integer(value)
    value = tonumber(value)
    if not value or value ~= value then
        return nil
    end
    local integer = math.floor(value)
    if integer ~= value then
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

local function MinLevel()
    return tonumber(Config:GetByField("MINIMUM_LEVEL_FOR_PARAGON_XP")) or 80
end

local function Comma(value)
    local text, count = tostring(value), nil
    repeat
        text, count = text:gsub("^(-?%d+)(%d%d%d)", "%1,%2")
    until count == 0
    return text
end

-- ALE's synchronous DML primitive returns no result set, but completion is
-- ordered before the callback continues.
local function ExecuteSync(sql)
    CharDBQuery(sql)
end

local function Scope(player)
    local account_id = Integer(player:GetAccountId())
    if not account_id or account_id <= 0 then
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
        "SELECT spell_id, pending_xp FROM %s WHERE account_id = %d;",
        CLAIM_TABLE, scope.account_id))
    if result then
        repeat
            local spell_id = result:GetUInt32(0)
            local pending = tonumber(result:GetString(1)) or 0
            scope.known[spell_id] = true
            scope.pending = scope.pending + pending
        until not result:NextRow()
    end
    scope.loaded = true
    return scope
end

local function ProgressionTarget(player)
    if tonumber(Config:GetByField("LEVEL_LINKED_TO_ACCOUNT")) == 1 then
        return DB .. ".account_paragon", "account_id", Integer(player:GetAccountId()), OWNER_ACCOUNT
    end
    return DB .. ".character_paragon", "guid", Integer(player:GetGUIDLow()), OWNER_CHARACTER
end

local function Scalar(sql)
    local result = CharDBQuery(sql)
    if not result then
        return nil
    end
    -- ALE's GetUInt64 returns userdata; its decimal SQL representation is the
    -- portable numeric boundary for Lua settlement arithmetic.
    return tonumber(result:GetString(0))
end

local function CurrentProgression(player)
    local paragon = player and player:GetData("Paragon")
    local level = paragon and Integer(paragon:GetLevel())
    local experience = paragon and PersistedExperience(paragon:GetExperience())
    local table_name, id_column, owner_id = ProgressionTarget(player)
    if not paragon or not level or level <= 0 or experience == nil
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

local function RefreshPending(scope)
    local pending = Scalar(string.format(
        "SELECT COALESCE(SUM(pending_xp), 0) FROM %s "
            .. "WHERE account_id = %d;",
        CLAIM_TABLE, scope.account_id))
    if pending == nil then
        return nil
    end
    scope.pending = pending
    return pending
end

-- Normal/repeatable Paragon XP is live until logout and can be fractional.
-- Checkpoint its persistable floor as the settlement CAS baseline without
-- mutating live memory until the durable commit succeeds.
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

local function ProjectProgression(current, amount)
    local curve_cost = ParagonRework_CurveCost
    if type(curve_cost) ~= "function" then
        return nil
    end
    local cap = Integer(Config:GetByField("PARAGON_LEVEL_CAP")) or 0
    local level = current.level
    local experience = current.experience + amount
    local cost = Integer(curve_cost(level))
    if not cost or cost <= 0 then
        return nil
    end
    while experience >= cost do
        experience = experience - cost
        if cap <= 0 or level < cap then
            level = level + 1
        end
        cost = Integer(curve_cost(level))
        if not cost or cost <= 0 then
            return nil
        end
    end
    return level, experience
end

-- Commit the projected state and acknowledge every pending recipe row in one
-- InnoDB statement. The old state predicates make this a compare-and-swap.
local function CommitPending(scope, current, level, experience)
    ExecuteSync(string.format([[
        UPDATE %s progression
        JOIN %s recipe
          ON recipe.account_id = %d
         AND recipe.pending_xp > 0
        SET progression.level = %d,
            progression.experience = %d,
            recipe.pending_xp = 0
        WHERE progression.%s = %d
          AND progression.level = %d
          AND progression.experience = %d;]],
        current.table_name, CLAIM_TABLE, scope.account_id, level, experience,
        current.id_column, current.owner_id,
        current.level, current.experience))

    local remaining = RefreshPending(scope)
    local persisted = Scalar(string.format(
        "SELECT COUNT(*) FROM %s WHERE %s = %d "
            .. "AND level = %d AND experience = %d;",
        current.table_name, current.id_column, current.owner_id,
        level, experience))
    return remaining == 0 and persisted == 1
end

local function CanPayNow(player)
    return SystemEnabled() and player and not IsBot(player)
        and player:GetLevel() >= MinLevel()
        and player:GetData("Paragon") ~= nil
end

local function PayPending(player, learned_name)
    if not CanPayNow(player) then
        return false
    end
    local scope = LoadScope(player)
    if not scope or scope.settling then
        return false
    end

    scope.settling = true
    local ok, paid, paid_amount = pcall(function()
        local pending = RefreshPending(scope)
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

        -- The durable state is authoritative. Replay supplies normal level-up
        -- effects, client sync, and XP-drop notifications. If a future
        -- mediator rejects/changes the replay, force memory back to the
        -- already-committed projection so logout cannot undo it.
        local replay_ok, awarded, awarded_xp = pcall(
            Hook.AwardFlatExperience, player, SOURCE, 0, pending)
        if not replay_ok or not awarded
                or current.paragon:GetLevel() ~= level
                or current.paragon:GetExperience() ~= experience then
            current.paragon:SetLevel(level)
            current.paragon:SetExperience(experience)
        end
        return true, awarded_xp or pending
    end)
    scope.settling = false
    if not ok then
        print("[Paragon] recipe settlement error: " .. tostring(paid))
        return false
    end
    if not paid then
        return false
    end
    if learned_name then
        player:SendBroadcastMessage(string.format(
            "|cff00ff00[Paragon]|r New profession recipe learned: %s \226\128\148 +%s Paragon XP!",
            learned_name, Comma(paid_amount)))
    else
        player:SendBroadcastMessage(string.format(
            "|cff00ff00[Paragon]|r Banked profession-recipe rewards paid out +%s Paragon XP!",
            Comma(paid_amount)))
    end
    return true
end

local function InsertSeedBatch(scope, values)
    if #values == 0 then
        return true
    end
    ExecuteSync(string.format(
        "INSERT IGNORE INTO %s (account_id, spell_id, pending_xp) VALUES %s;",
        CLAIM_TABLE, table.concat(values, ",")))

    local ids = {}
    for _, spell_id in ipairs(values.spells) do
        ids[#ids + 1] = tostring(spell_id)
    end
    local confirmed = Scalar(string.format(
        "SELECT COUNT(*) FROM %s WHERE account_id = %d "
            .. "AND spell_id IN (%s);",
        CLAIM_TABLE, scope.account_id, table.concat(ids, ",")))
    if confirmed ~= #values.spells then
        return false
    end
    for _, spell_id in ipairs(values.spells) do
        scope.known[spell_id] = true
    end
    return true
end

local function SeedCharacter(player)
    if not player or IsBot(player) then
        return false
    end
    local scope = LoadScope(player)
    local guid = Integer(player:GetGUIDLow())
    if not scope or not guid or guid <= 0 then
        return false
    end

    local result = CharDBQuery(string.format(
        "SELECT catalog_version FROM %s WHERE guid = %d;", SEED_TABLE, guid))
    if result and result:GetUInt32(0) == RecipeData.VERSION then
        player:SetData(SESSION_SEEDED_KEY, true)
        return true
    end

    local values = { spells = {} }
    local seed_ok = true
    local function Flush()
        if not InsertSeedBatch(scope, values) then
            seed_ok = false
        end
        values = { spells = {} }
    end
    for spell_id in RecipeData.Iterate() do
        if player:HasSpell(spell_id) and not scope.known[spell_id] then
            values[#values + 1] = string.format("(%d,%d,0)", scope.account_id, spell_id)
            values.spells[#values.spells + 1] = spell_id
            if #values >= 250 then
                Flush()
            end
        end
    end
    Flush()
    if not seed_ok then
        return false
    end
    ExecuteSync(string.format([[
        INSERT INTO %s (guid, account_id, catalog_version)
        VALUES (%d, %d, %d)
        ON DUPLICATE KEY UPDATE
            account_id = VALUES(account_id),
            catalog_version = VALUES(catalog_version),
            seeded_at = CURRENT_TIMESTAMP;]],
        SEED_TABLE, guid, scope.account_id, RecipeData.VERSION))
    local seeded = Scalar(string.format(
        "SELECT COUNT(*) FROM %s WHERE guid = %d AND account_id = %d "
            .. "AND catalog_version = %d;",
        SEED_TABLE, guid, scope.account_id, RecipeData.VERSION))
    if seeded ~= 1 then
        return false
    end
    player:SetData(SESSION_SEEDED_KEY, true)
    return true
end

local function ClaimLearn(player, definition)
    local scope = LoadScope(player)
    if not scope or scope.known[definition.spellId] then
        return false
    end

    -- Learning while the whole Paragon system is disabled consumes the
    -- one-time entitlement without creating a deferred windfall.
    local amount = SystemEnabled() and definition.xp or 0
    ExecuteSync(string.format(
        "INSERT IGNORE INTO %s (account_id, spell_id, pending_xp) "
            .. "VALUES (%d, %d, %d);",
        CLAIM_TABLE, scope.account_id, definition.spellId, amount))
    local stored = Scalar(string.format(
        "SELECT pending_xp FROM %s WHERE account_id = %d AND spell_id = %d;",
        CLAIM_TABLE, scope.account_id, definition.spellId))
    if stored == nil then
        return false
    end

    scope.known[definition.spellId] = true
    RefreshPending(scope)
    return amount > 0 and stored > 0
end

local function OnLearnSpell(event, player, spell_id)
    local definition = RecipeData.Get(spell_id)
    if not definition or not player or IsBot(player) then
        return
    end
    local ok, err = pcall(function()
        -- Ignore login-time/history events until versioned no-backpay seeding
        -- has completed.  A later seed records the known spell with zero XP.
        if not player:GetData(SESSION_SEEDED_KEY) then
            return
        end
        if ClaimLearn(player, definition) and CanPayNow(player) then
            PayPending(player, definition.name)
        end
    end)
    if not ok then
        print("[Paragon] recipe reward learn-event error: " .. tostring(err))
    end
end

RegisterPlayerEvent(44, OnLearnSpell)

RegisterMediatorEvent("OnAfterPlayerStatReady", function(player, paragon)
    local ok, err = pcall(function()
        if player and not IsBot(player) then
            SeedCharacter(player)
            PayPending(player)
        end
    end)
    if not ok then
        print("[Paragon] recipe reward seed error: " .. tostring(err))
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
    "[Paragon] Recipe rewards loaded (%d spells, %d XP, catalog v%d)",
    RecipeData.COUNT, RecipeData.BUDGET, RecipeData.VERSION))

return {
    OnLearnSpell = OnLearnSpell,
    PayPending = PayPending,
    SeedCharacter = SeedCharacter,
}
