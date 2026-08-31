--[[
    Durable, account-wide one-time collection rewards.

    The generated value tables are strict whitelists. Unknown appearance IDs
    never receive a fallback reward. The generator seeds every collection
    present at deployment into the claim tables with pending_xp=0, so this
    module does not back-pay existing collections.

    New mount, companion, and appearance claims use a write-ahead protocol:

      1. synchronously INSERT the account claim with its exact pending XP;
      2. project the exact flat-award result and atomically persist that state
         plus clear those pending rows in one InnoDB multi-table UPDATE;
      3. replay the committed award through the live COLLECTIBLE pipeline.

    A crash before step 2 leaves the claim payable. A crash after step 2 sees
    durable progression with no pending duplicate even if live replay never
    happened. Each claim kind is settled separately,
    avoiding a mount x appearance cross-product in SQL.
]]

local Constant = require("paragon_constant")
local Config = require("paragon_config")
local Hook = require("paragon_hook")

local DB = Constant.DB_NAME
local SOURCE_COLLECTIBLE = Hook.ExperienceSource.COLLECTIBLE
local SPELL_CLAIM_TABLE = DB .. ".paragon_rewarded_collectible_spell"
local ITEM_CLAIM_TABLE = DB .. ".paragon_rewarded_appearance"
local ACCOUNT_ITEM_CLAIM_TABLE = DB .. ".paragon_rewarded_account_item"
local NOTABLE_XP = 10000
local TICK_MS = 10000

local SCOPE_CACHE_KEY = "ParagonCollectionRewardScopeV2"
local UNKNOWN_ITEM_KEY = "ParagonCollectUnknownItems"
local UNKNOWN_ACCOUNT_ITEM_KEY = "ParagonCollectUnknownAccountItems"
local DIRTY_KEY = "ParagonCollectRewardDirty"

-- Strong process-wide account scopes keep simultaneous characters from the
-- same account from racing stale per-player mirrors. They are rebuilt from
-- durable rows on every Lua-state restart.
local SCOPES = {}

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

-- Repeatable XP modifiers may leave the live Paragon object with a fractional
-- remainder (for example, the high-level 0.8 multiplier). Repository saves use
-- string.format("%d") and therefore persist the nonnegative value truncated.
-- Settlement must checkpoint that same representation instead of rejecting a
-- perfectly valid live state and leaving every pending claim stuck forever.
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

local function Comma(value)
    local text, count = tostring(value), nil
    repeat
        text, count = text:gsub("^(-?%d+)(%d%d%d)", "%1,%2")
    until count == 0
    return text
end

-- ALE documents CharDBQuery as its synchronous character-database primitive.
-- DML returns no result set, but completion is ordered before Lua continues.
local function ExecuteSync(sql)
    CharDBQuery(sql)
end

-- ============================================================================
-- AUTHORITATIVE VALUES (boot snapshot; restart after regenerating)
-- ============================================================================

local SPELL_XP = {}
local ITEM_XP = {}
local ACCOUNT_ITEM_XP = {}

local function AccountItemKey(kind, item_id)
    return tostring(kind) .. ":" .. tostring(item_id)
end

do
    local result = CharDBQuery(string.format(
        "SELECT spell_id, kind, name, xp FROM %s.paragon_collectible_spell_xp;", DB))
    if result then
        repeat
            SPELL_XP[result:GetUInt32(0)] = {
                kind = result:GetString(1),
                name = result:GetString(2),
                xp = result:GetInt32(3),
            }
        until not result:NextRow()
    end

    result = CharDBQuery(string.format(
        "SELECT item_id, name, xp FROM %s.paragon_collectible_item_xp;", DB))
    if result then
        repeat
            ITEM_XP[result:GetUInt32(0)] = {
                name = result:GetString(1),
                xp = result:GetInt32(2),
            }
        until not result:NextRow()
    end

    result = CharDBQuery(string.format(
        "SELECT kind, item_id, name, xp "
            .. "FROM %s.paragon_collectible_account_item_xp;", DB))
    if result then
        repeat
            local kind = result:GetString(0)
            local item_id = result:GetUInt32(1)
            ACCOUNT_ITEM_XP[AccountItemKey(kind, item_id)] = {
                kind = kind,
                item_id = item_id,
                name = result:GetString(2),
                xp = result:GetInt32(3),
            }
        until not result:NextRow()
    end
end

local function CountEntries(values)
    local count = 0
    for _ in pairs(values) do
        count = count + 1
    end
    return count
end

-- ============================================================================
-- DURABLE ACCOUNT CLAIM SCOPE
-- ============================================================================

local function Scope(player)
    local account_id = player and Integer(player:GetAccountId())
    if not account_id or account_id <= 0 then
        return nil
    end

    local scope = SCOPES[account_id]
    if not scope then
        scope = {
            account_id = account_id,
            spells = {},
            items = {},
            account_items = {},
            loaded = false,
            settling = {},
        }
        SCOPES[account_id] = scope
    end
    player:SetData(SCOPE_CACHE_KEY, scope)
    return scope
end

local function LoadClaims(scope, table_name, id_column, target)
    local result = CharDBQuery(string.format(
        "SELECT %s FROM %s WHERE account_id = %d;",
        id_column, table_name, scope.account_id))
    if result then
        repeat
            target[result:GetUInt32(0)] = true
        until not result:NextRow()
    end
end

local function LoadScope(player)
    local scope = Scope(player)
    if not scope or scope.loaded then
        return scope
    end

    LoadClaims(scope, SPELL_CLAIM_TABLE, "spell_id", scope.spells)
    LoadClaims(scope, ITEM_CLAIM_TABLE, "item_id", scope.items)
    local result = CharDBQuery(string.format(
        "SELECT kind, item_id FROM %s WHERE account_id = %d;",
        ACCOUNT_ITEM_CLAIM_TABLE, scope.account_id))
    if result then
        repeat
            local kind = result:GetString(0)
            local item_id = result:GetUInt32(1)
            scope.account_items[AccountItemKey(kind, item_id)] = true
        until not result:NextRow()
    end
    scope.loaded = true
    return scope
end

local function ClaimExists(table_name, id_column, account_id, entry)
    return CharDBQuery(string.format(
        "SELECT pending_xp FROM %s WHERE account_id = %d AND %s = %d;",
        table_name, account_id, id_column, entry)) ~= nil
end

local function ClaimSpell(player, spell_id, amount)
    local scope = LoadScope(player)
    if not scope or scope.spells[spell_id] then
        return false
    end

    ExecuteSync(string.format(
        "INSERT IGNORE INTO %s (account_id, spell_id, pending_xp) "
            .. "VALUES (%d, %d, %d);",
        SPELL_CLAIM_TABLE, scope.account_id, spell_id, amount))

    -- Only a row visible to a synchronous read becomes known/payable in this
    -- process. A failed DML statement therefore remains retryable.
    if not ClaimExists(
            SPELL_CLAIM_TABLE, "spell_id", scope.account_id, spell_id) then
        return false
    end
    scope.spells[spell_id] = true
    return true
end

local function ClaimAppearanceBatch(player, entries)
    local scope = LoadScope(player)
    if not scope or #entries == 0 then
        return {}
    end

    local values, ids = {}, {}
    for _, entry in ipairs(entries) do
        if not scope.items[entry.item_id] then
            values[#values + 1] = string.format(
                "(%d,%d,%d)", scope.account_id, entry.item_id, entry.xp)
            ids[#ids + 1] = tostring(entry.item_id)
        end
    end
    if #values == 0 then
        return {}
    end

    ExecuteSync(string.format(
        "INSERT IGNORE INTO %s (account_id, item_id, pending_xp) VALUES %s;",
        ITEM_CLAIM_TABLE, table.concat(values, ",")))

    local confirmed = {}
    local result = CharDBQuery(string.format(
        "SELECT item_id FROM %s WHERE account_id = %d AND item_id IN (%s);",
        ITEM_CLAIM_TABLE, scope.account_id, table.concat(ids, ",")))
    if result then
        repeat
            local item_id = result:GetUInt32(0)
            scope.items[item_id] = true
            confirmed[item_id] = true
        until not result:NextRow()
    end
    return confirmed
end

local function ClaimAccountItemBatch(player, entries)
    local scope = LoadScope(player)
    if not scope or #entries == 0 then
        return {}
    end

    local values, predicates = {}, {}
    for _, entry in ipairs(entries) do
        local key = AccountItemKey(entry.kind, entry.item_id)
        if not scope.account_items[key] then
            values[#values + 1] = string.format(
                "(%d,'%s',%d,%d)", scope.account_id,
                entry.kind, entry.item_id, entry.xp)
            predicates[#predicates + 1] = string.format(
                "(kind='%s' AND item_id=%d)", entry.kind, entry.item_id)
        end
    end
    if #values == 0 then
        return {}
    end

    ExecuteSync(string.format(
        "INSERT IGNORE INTO %s "
            .. "(account_id, kind, item_id, pending_xp) VALUES %s;",
        ACCOUNT_ITEM_CLAIM_TABLE, table.concat(values, ",")))

    local confirmed = {}
    local result = CharDBQuery(string.format(
        "SELECT kind, item_id FROM %s WHERE account_id = %d AND (%s);",
        ACCOUNT_ITEM_CLAIM_TABLE, scope.account_id,
        table.concat(predicates, " OR ")))
    if result then
        repeat
            local kind = result:GetString(0)
            local item_id = result:GetUInt32(1)
            local key = AccountItemKey(kind, item_id)
            scope.account_items[key] = true
            confirmed[key] = true
        until not result:NextRow()
    end
    return confirmed
end

-- ============================================================================
-- PENDING SETTLEMENT
-- ============================================================================

local function ProgressionTarget(player)
    if tonumber(Config:GetByField("LEVEL_LINKED_TO_ACCOUNT")) == 1 then
        return DB .. ".account_paragon", "account_id", Integer(player:GetAccountId())
    end
    return DB .. ".character_paragon", "guid", Integer(player:GetGUIDLow())
end

local function CurrentProgression(player)
    local paragon = player:GetData("Paragon")
    local level = paragon and Integer(paragon:GetLevel())
    local experience = paragon and PersistedExperience(paragon:GetExperience())
    local table_name, id_column, owner_id = ProgressionTarget(player)
    if not level or level <= 0 or not experience or experience < 0
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

local function Scalar(sql)
    local result = CharDBQuery(sql)
    if not result then
        return nil
    end
    -- ALE exposes SQL uint64 columns as an `unsigned long long` userdata,
    -- not as a Lua number. `tonumber(result:GetUInt64(...))` therefore
    -- returns nil and used to make every pending settlement stop silently.
    -- Decimal SQL text is exact for all values in our supported Lua range.
    return tonumber(result:GetString(0))
end

-- Normal/repeatable XP lives in the Paragon object until logout. Persist the
-- exact live starting point before the compare-and-swap so collection
-- settlement never discards that unsaved progress or waits forever on a stale
-- progression row.
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

local function RefreshPending(table_name, account_id)
    return Scalar(string.format(
        "SELECT COALESCE(SUM(pending_xp), 0) FROM %s "
            .. "WHERE account_id = %d AND pending_xp > 0;",
        table_name, account_id))
end

local function ProjectProgression(current, amount)
    local curve_cost = ParagonRework_CurveCost
    if type(curve_cost) ~= "function" then
        return nil
    end
    local cap = Integer(Config:GetByField("PARAGON_LEVEL_CAP")) or 0
    local level = current.level
    local experience = current.experience + amount

    -- Use the same generated decaying-growth curve that self-heals the live
    -- Paragon object. Falling back to upstream's linear base*level rule here
    -- would commit a different state than live replay.
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

local function CommitPending(table_name, account_id, current, level, experience)
    ExecuteSync(string.format([[
        UPDATE %s progression
        JOIN %s claim
          ON claim.account_id = %d
         AND claim.pending_xp > 0
        SET progression.level = %d,
            progression.experience = %d,
            claim.pending_xp = 0
        WHERE progression.%s = %d
          AND progression.level = %d
          AND progression.experience = %d;]],
        current.table_name, table_name, account_id, level, experience,
        current.id_column, current.owner_id,
        current.level, current.experience))

    local remaining = RefreshPending(table_name, account_id)
    local persisted = Scalar(string.format(
        "SELECT COUNT(*) FROM %s WHERE %s = %d "
            .. "AND level = %d AND experience = %d;",
        current.table_name, current.id_column, current.owner_id,
        level, experience))
    return remaining == 0 and persisted == 1
end

local function CanClaim(player)
    return SystemEnabled() and player and not IsBot(player)
        and player:GetData("Paragon") ~= nil
end

local function CanPayNow(player)
    return CanClaim(player) and player:GetLevel() >= MinLevel()
end

local function PayPending(player, table_name, entry)
    if not CanPayNow(player) then
        return false, 0
    end
    local scope = LoadScope(player)
    if not scope or scope.settling[table_name] then
        return false, 0
    end

    scope.settling[table_name] = true
    local ok, paid, paid_amount = pcall(function()
        local pending = RefreshPending(table_name, scope.account_id)
        local current = CurrentProgression(player)
        if not pending or pending <= 0 or not current
                or not SyncCurrentProgression(current) then
            return false, 0
        end

        local level, experience = ProjectProgression(current, pending)
        if not level or not CommitPending(
                table_name, scope.account_id, current, level, experience) then
            return false, 0
        end

        -- The durable commit is authoritative. Replay the flat award for live
        -- level-up effects, client sync, and XP-drop notifications. A future
        -- mediator is not allowed to make logout overwrite the committed state.
        local awarded, awarded_xp = Hook.AwardFlatExperience(
            player, SOURCE_COLLECTIBLE, entry or 0, pending)
        if not awarded or current.paragon:GetLevel() ~= level
                or current.paragon:GetExperience() ~= experience then
            current.paragon:SetLevel(level)
            current.paragon:SetExperience(experience)
        end
        return true, awarded_xp or pending
    end)
    scope.settling[table_name] = nil
    if not ok then
        print("[Paragon] collection settlement error: " .. tostring(paid))
        return false, 0
    end
    return paid, paid_amount
end

local function PaySpellPending(player, entry, definition)
    local paid, amount = PayPending(player, SPELL_CLAIM_TABLE, entry)
    if paid then
        if definition and amount == definition.xp then
            player:SendBroadcastMessage(string.format(
                "|cff00ff00[Paragon]|r New %s collected: %s \226\128\148 +%s Paragon XP!",
                definition.kind, definition.name, Comma(amount)))
        else
            player:SendBroadcastMessage(string.format(
                "|cff00ff00[Paragon]|r Banked mount/companion rewards paid \226\128\148 +%s Paragon XP!",
                Comma(amount)))
        end
    end
    return paid, amount
end

local function PayAppearancePending(player, confirmed, notable)
    local paid, amount = PayPending(player, ITEM_CLAIM_TABLE, 0)
    if not paid then
        return false, 0
    end

    local count = 0
    for _ in pairs(confirmed or {}) do
        count = count + 1
    end
    if count > 0 then
        player:SendBroadcastMessage(string.format(
            "|cff00ff00[Paragon]|r %d new appearance%s collected \226\128\148 +%s Paragon XP!",
            count, count == 1 and "" or "s", Comma(amount)))
        for _, line in ipairs(notable or {}) do
            player:SendBroadcastMessage("|cff00ff00[Paragon]|r     " .. line)
        end
    else
        player:SendBroadcastMessage(string.format(
            "|cff00ff00[Paragon]|r Banked appearance rewards paid \226\128\148 +%s Paragon XP!",
            Comma(amount)))
    end
    return true, amount
end

local function PayAccountItemPending(player, confirmed, notable)
    local paid, amount = PayPending(player, ACCOUNT_ITEM_CLAIM_TABLE, 0)
    if not paid then
        return false, 0
    end

    local toy_count, heirloom_count = 0, 0
    for key in pairs(confirmed or {}) do
        if key:sub(1, 4) == "toy:" then
            toy_count = toy_count + 1
        elseif key:sub(1, 9) == "heirloom:" then
            heirloom_count = heirloom_count + 1
        end
    end
    local count = toy_count + heirloom_count
    if count > 0 then
        local collected = {}
        if toy_count > 0 then
            collected[#collected + 1] = string.format(
                "%d toy%s", toy_count, toy_count == 1 and "" or "s")
        end
        if heirloom_count > 0 then
            collected[#collected + 1] = string.format(
                "%d heirloom%s", heirloom_count,
                heirloom_count == 1 and "" or "s")
        end
        player:SendBroadcastMessage(string.format(
            "|cff00ff00[Paragon]|r New %s collected \226\128\148 +%s Paragon XP!",
            table.concat(collected, " and "), Comma(amount)))
        for _, line in ipairs(notable or {}) do
            player:SendBroadcastMessage("|cff00ff00[Paragon]|r     " .. line)
        end
    else
        player:SendBroadcastMessage(string.format(
            "|cff00ff00[Paragon]|r Banked toy/heirloom rewards paid \226\128\148 +%s Paragon XP!",
            Comma(amount)))
    end
    return true, amount
end

-- ============================================================================
-- MOUNTS + COMPANIONS
-- ============================================================================

local function OnLearnSpell(event, player, spell_id)
    local definition = SPELL_XP[spell_id]
    if not definition or not CanClaim(player) then
        return
    end

    local ok, err = pcall(function()
        if ClaimSpell(player, spell_id, definition.xp) then
            PaySpellPending(player, spell_id, definition)
        end
    end)
    if not ok then
        print("[Paragon] collection reward learn-event error: " .. tostring(err))
    end
end

RegisterPlayerEvent(44, OnLearnSpell)

-- ============================================================================
-- APPEARANCES (mod-transmog has no unlock hook)
-- ============================================================================

for _, event_id in ipairs({ 53, 29 }) do
    RegisterPlayerEvent(event_id, function(event, player)
        if player and not IsBot(player) then
            player:SetData(DIRTY_KEY, true)
        end
    end)
end

local function SettleAppearances(player)
    if not player:GetData(DIRTY_KEY) or not CanClaim(player) then
        return
    end
    player:SetData(DIRTY_KEY, nil)

    local scope = LoadScope(player)
    local result = CharDBQuery(string.format(
        "SELECT item_template_id FROM custom_unlocked_appearances "
            .. "WHERE account_id = %d;",
        scope.account_id))
    if not result then
        return
    end

    local entries, notable = {}, {}
    local unknown = player:GetData(UNKNOWN_ITEM_KEY)
    if unknown == nil then
        unknown = {}
        player:SetData(UNKNOWN_ITEM_KEY, unknown)
    end

    repeat
        local item_id = result:GetUInt32(0)
        if not scope.items[item_id] then
            local definition = ITEM_XP[item_id]
            if definition then
                entries[#entries + 1] = {
                    item_id = item_id,
                    xp = definition.xp,
                }
                if definition.xp >= NOTABLE_XP then
                    notable[#notable + 1] = string.format(
                        "%s (+%s)", definition.name, Comma(definition.xp))
                end
            elseif not unknown[item_id] then
                unknown[item_id] = true
                print(string.format(
                    "[Paragon] unlocked appearance item %d has no authoritative XP value; ignored",
                    item_id))
            end
        end
    until not result:NextRow()

    local confirmed = ClaimAppearanceBatch(player, entries)
    PayAppearancePending(player, confirmed, notable)
end

-- EZCollections writes these account-wide rows exactly once. Toys become
-- owned on their first successful use; heirlooms become owned when first
-- stored. Polling the two small ledgers keeps Paragon decoupled from the
-- collection module while preserving its authoritative collection semantics.
local function SettleAccountItems(player)
    if not CanClaim(player) then
        return
    end

    local scope = LoadScope(player)
    if not scope then
        return
    end
    local result = CharDBQuery(string.format([[
        SELECT 'toy', item_id
        FROM acore_characters.account_collection_toy
        WHERE account_id = %d
        UNION ALL
        SELECT 'heirloom', item_id
        FROM acore_characters.account_collection_heirloom
        WHERE account_id = %d;]], scope.account_id, scope.account_id))
    if not result then
        return
    end

    local entries, notable = {}, {}
    local unknown = player:GetData(UNKNOWN_ACCOUNT_ITEM_KEY)
    if unknown == nil then
        unknown = {}
        player:SetData(UNKNOWN_ACCOUNT_ITEM_KEY, unknown)
    end

    repeat
        local kind = result:GetString(0)
        local item_id = result:GetUInt32(1)
        local key = AccountItemKey(kind, item_id)
        if not scope.account_items[key] then
            local definition = ACCOUNT_ITEM_XP[key]
            if definition then
                entries[#entries + 1] = definition
                if definition.xp >= NOTABLE_XP then
                    notable[#notable + 1] = string.format(
                        "%s (%s, +%s)", definition.name,
                        definition.kind, Comma(definition.xp))
                end
            elseif not unknown[key] then
                unknown[key] = true
                print(string.format(
                    "[Paragon] collected %s item %d has no authoritative XP value; ignored",
                    kind, item_id))
            end
        end
    until not result:NextRow()

    local confirmed = ClaimAccountItemBatch(player, entries)
    PayAccountItemPending(player, confirmed, notable)
end

local tickers = {}

local function OnTick(event_id, delay, repeats, player)
    local ok, err = pcall(function()
        -- Retry crash-left write-ahead rows even when no appearance is dirty.
        PaySpellPending(player)
        PayAppearancePending(player)
        PayAccountItemPending(player)
        SettleAppearances(player)
        SettleAccountItems(player)
    end)
    if not ok then
        print("[Paragon] collection reward tick error: " .. tostring(err))
    end
end

local function EnsureTicker(player)
    local guid = player:GetGUIDLow()
    if not tickers[guid] then
        -- Deployment seeding has already claimed all pre-existing rows with
        -- pending_xp=0. This first sweep only detects post-deploy unlocks.
        player:SetData(DIRTY_KEY, true)
        tickers[guid] = player:RegisterEvent(OnTick, TICK_MS, 0)
    end
end

RegisterPlayerEvent(4, function(event, player)
    tickers[player:GetGUIDLow()] = nil
end)

RegisterPlayerEvent(13, function(event, player, old_level)
    old_level = Integer(old_level)
    local minimum = MinLevel()
    if player and old_level and not IsBot(player) and old_level < minimum
            and player:GetLevel() >= minimum then
        PaySpellPending(player)
        PayAppearancePending(player)
        PayAccountItemPending(player)
    end
end)

RegisterMediatorEvent("OnAfterPlayerStatReady", function(player, paragon)
    local ok, err = pcall(function()
        if player and paragon and not IsBot(player) then
            LoadScope(player)
            PaySpellPending(player)
            PayAppearancePending(player)
            PayAccountItemPending(player)
            SettleAccountItems(player)
        end
    end)
    if not ok then
        print("[Paragon] collection pending-drain error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnAfterUpdatePlayerStatistics", function(player, paragon, apply)
    local ok, err = pcall(function()
        if apply and player and paragon and not IsBot(player) then
            EnsureTicker(player)
        end
    end)
    if not ok then
        print("[Paragon] collection reward ticker error: " .. tostring(err))
    end
end)

print(string.format(
    "[Paragon] Rework: durable collection rewards loaded "
        .. "(%d spells, %d appearance values, %d account-item values)",
    CountEntries(SPELL_XP), CountEntries(ITEM_XP), CountEntries(ACCOUNT_ITEM_XP)))

return {
    OnLearnSpell = OnLearnSpell,
    SettleAppearances = SettleAppearances,
    PaySpellPending = PaySpellPending,
    PayAppearancePending = PayAppearancePending,
    PayAccountItemPending = PayAccountItemPending,
    SettleAccountItems = SettleAccountItems,
}
