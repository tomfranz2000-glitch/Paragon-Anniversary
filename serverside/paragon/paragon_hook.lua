--[[
    Paragon Hook System

    Manages all server-side event hooks and client-server communication for the
    paragon system. Handles player login/logout, experience gains, and statistic
    updates through event handlers and addon communication.

    Responsibilities:
    - Player login/logout lifecycle management
    - Experience gain distribution from various sources
    - Statistic point allocation and reallocation
    - Client-server addon communication
    - Event registration for ALE/Eluna

    Architecture:
    - Event-driven design with Mediator pattern
    - Server events trigger paragon state updates
    - Client packets processed through addon functions
    - Statistics applied/removed atomically

    @module paragon_hook
    @author iThorgrim
    @license AGL v3
]]

local Paragon = require("paragon_class")
local Config = require("paragon_config")
local Repository = require("paragon_repository")
local Constant = require("paragon_constant")

-- ============================================================================
-- CONFIGURATION
-- ============================================================================

local Hook = {
    Addon = {
        Prefix = "ParagonAnniversary",
        Functions = {
            [1] = "OnParagonClientLoadRequest",
            [2] = "OnParagonClientSendStatistics"
        }
    }
}

-- Experience source type enumeration
local EXPERIENCE_SOURCE = {
    CREATURE = 1,
    ACHIEVEMENT = 2,
    SKILL = 3,
    QUEST = 4
}

-- ============================================================================
-- PRIVATE FUNCTIONS
-- ============================================================================

---
--- Retrieves a player object from their GUID low value.
---
--- @param guid_low The low part of the player's GUID
--- @return The player object, or false if not found
---
local function GetPlayerIfExist(guid_low)
    local guid = GetPlayerGUID(guid_low)
    if not guid then
        return false
    end

    local player = GetPlayerByGUID(guid)
    if not player then
        return false
    end

    return player
end

---
--- Applies or removes all paragon statistic modifiers to a player.
---
--- Iterates through all invested statistics and applies/removes bonuses based on
--- the statistic type (UNIT_MODS, COMBAT_RATING, or AURA).
---
--- Mediator Events:
--- - OnBeforeUpdatePlayerStatistics: (player, paragon, apply) - allows modification before applying
--- - OnAfterUpdatePlayerStatistics: (player, paragon, apply) - allows cleanup after applying
---
--- @param player The player object to update
--- @param paragon The paragon instance containing stat data
--- @param apply Boolean indicating whether to apply (true) or remove (false) the bonuses
---
local function UpdatePlayerStatistics(player, paragon, apply)
    if not apply then
        apply = false
    end

    -- Allow modules to hook before statistics are applied/removed
    player, paragon, apply = Mediator.On("OnBeforeUpdatePlayerStatistics", {
        arguments = { player, paragon, apply },
        defaults = { player, paragon, apply },
    })

    local statistics = paragon:GetStatistics()
    if not statistics then
        return
    end

    for stat_id, stat_value in pairs(statistics) do
        if not stat_value or stat_value <= 0 then
            goto continue
        end

        local stat_data = Config:GetByStatId(stat_id)
        if not stat_data then
            goto continue
        end

        local constant_stat_type = Constant.STATISTICS[stat_data.type]
        if not constant_stat_type then
            goto continue
        end

        -- Apply bonus based on statistic type
        if stat_data.type == "UNIT_MODS" then
            player:HandleStatFlatModifier(constant_stat_type[stat_data.value], stat_data.application, stat_value, apply)
        elseif stat_data.type == "COMBAT_RATING" then
            player:ApplyRatingMod(constant_stat_type[stat_data.value], stat_value, apply)
        elseif stat_data.type == "AURA" then
            if apply then
                for _ = 1, stat_value do
                    player:AddAura(constant_stat_type[stat_data.value], player)
                end
            else
                player:RemoveAura(constant_stat_type[stat_data.value])
            end
        end

        ::continue::
    end

    -- Allow modules to hook after statistics are applied/removed
    Mediator.On("OnAfterUpdatePlayerStatistics", {
        arguments = { player, paragon, apply },
    })
end

-- ============================================================================
-- PLAYER EXPERIENCE MANAGEMENT
-- ============================================================================

---
--- Updates player paragon experience based on activity source.
---
--- Calculates experience reward for the given source type and entry, then
--- processes experience gain through the Mediator system which handles
--- level-ups and point allocation.
---
--- Mediator Events:
--- - OnBeforeUpdatePlayerExperience: (player, paragon, source_type, entry) - allows modification before award
--- - OnExperienceCalculated: (player, paragon, source_type, specific_experience) - after calculation
--- - OnUpdatePlayerExperience: (player, paragon, specific_experience) - delegates level-up handling
--- - OnAfterUpdatePlayerExperience: (player, paragon) - cleanup after processing
--- - OnParagonStateSync: (player, paragon) - allows custom sync logic before sending to client
---
--- @param player The player object
--- @param paragon The paragon instance to update
--- @param source_type The source type (EXPERIENCE_SOURCE enum)
--- @param entry The source entry ID (creature ID, achievement ID, skill ID, or quest ID)
--- @return boolean True if experience was awarded, false otherwise
---
local function UpdatePlayerExperience(player, paragon, source_type, entry)
    if not player or not paragon or not source_type or not entry then
        return false
    end

    -- Check minimum level requirement
    local min_level = tonumber(Config:GetByField("MINIMUM_LEVEL_FOR_PARAGON_XP")) or 80
    if player:GetLevel() < min_level then
        return false
    end

    -- local patch (account-wide paragon): bots never collect paragon experience
    if player.IsPlayerBot and player:IsPlayerBot() then
        return false
    end

    paragon, source_type, entry = Mediator.On("OnBeforeUpdatePlayerExperience", {
        arguments = { player, paragon, source_type, entry },
        defaults = { paragon, source_type, entry },
    })

    -- Map source type to config key
    local source_config_map = {
        [EXPERIENCE_SOURCE.CREATURE] = "UNIVERSAL_CREATURE_EXPERIENCE",
        [EXPERIENCE_SOURCE.ACHIEVEMENT] = "UNIVERSAL_ACHIEVEVEMENT_EXPERIENCE",
        [EXPERIENCE_SOURCE.SKILL] = "UNIVERSAL_SKILL_EXPERIENCE",
        [EXPERIENCE_SOURCE.QUEST] = "UNIVERSAL_QUEST_EXPERIENCE"
    }

    local config_key = source_config_map[source_type] or "UNIVERSAL_CREATURE_EXPERIENCE"
    local universal_value = tonumber(Config:GetByField(config_key)) or 0

    if universal_value <= 0 then
        return false
    end

    -- Get source-specific experience value (falls back to universal value)
    local source_experience_map = {
        ["UNIVERSAL_CREATURE_EXPERIENCE"] = Config:GetCreatureExperience(entry),
        ["UNIVERSAL_ACHIEVEVEMENT_EXPERIENCE"] = Config:GetAchievementExperience(entry),
        ["UNIVERSAL_SKILL_EXPERIENCE"] = Config:GetSkillExperience(entry),
        ["UNIVERSAL_QUEST_EXPERIENCE"] = Config:GetQuestExperience(entry)
    }

    local specific_experience = source_experience_map[config_key] or universal_value
    if not specific_experience then
        return false
    end

    -- Allow modules to see the calculated experience before processing
    specific_experience = Mediator.On("OnExperienceCalculated", {
        arguments = { player, paragon, source_type, specific_experience },
        defaults = { specific_experience },
    })

    specific_experience = tonumber(specific_experience)
    if not specific_experience or specific_experience <= 0 then
        return false
    end

    -- Process experience gain through Mediator (triggers level-ups)
    paragon = Mediator.On("OnUpdatePlayerExperience", {
        arguments = { player, paragon, specific_experience },
        defaults = { paragon }
    })

    -- Allow modules to customize how paragon state is synced to client
    Mediator.On("OnParagonStateSync", {
        arguments = { player, paragon },
    })

    -- Update client with new paragon state
    player:SendServerResponse(Hook.Addon.Prefix, 1, paragon:GetLevel())
    player:SendServerResponse(Hook.Addon.Prefix, 4, paragon:GetPoints())
    player:SendServerResponse(Hook.Addon.Prefix, 2, paragon:GetExperience(), paragon:GetExperienceForNextLevel())

    player:SetData("Paragon", paragon)

    Mediator.On("OnAfterUpdatePlayerExperience", {
        arguments = { player, paragon },
    })

    return true
end

-- ============================================================================
-- PLAYER POINTS MANAGEMENT
-- ============================================================================

---
--- Updates a character's paragon statistic investment and available points.
---
--- Validates point availability and stat limits before applying changes.
--- Recursively handles point reallocation if negative points result from change.
---
--- @param player The player object
--- @param paragon The paragon instance to update
--- @param stat_id The statistic ID to modify
--- @param stat_value The new value to set for the statistic
--- @return boolean True if points were updated, false otherwise
---
local function UpdateParagonPoints(player, paragon, stat_id, stat_value)
    if not player or not paragon or not stat_id or not stat_value then
        return false
    end

    paragon, stat_id, stat_value = Mediator.On("OnBeforeUpdateParagonPoints", {
        arguments = { player, paragon, stat_id, stat_value },
        defaults = { paragon, stat_id, stat_value },
    })

    local actual_stat_value = paragon:GetStatValue(stat_id)
    local available_points = paragon:GetPoints()

    -- Recalculate available points based on change in stat value
    if actual_stat_value > stat_value then
        available_points = available_points + (actual_stat_value - stat_value)
    elseif actual_stat_value < stat_value then
        available_points = available_points - (stat_value - actual_stat_value)
    end

    paragon, stat_id, actual_stat_value, available_points = Mediator.On("OnUpdateParagonPoints", {
        arguments = { player, paragon, stat_id, actual_stat_value, available_points },
        defaults = { paragon, stat_id, actual_stat_value, available_points },
    })

    -- If negative points, recursively deallocate and revert
    if available_points < 0 then
        UpdateParagonPoints(player, paragon, stat_id, actual_stat_value)
        return false
    end

    -- Apply the stat change
    paragon:SetPoints(available_points)
    paragon:SetStatValue(stat_id, stat_value)

    -- Send updated stat to client
    player:SendServerResponse(Hook.Addon.Prefix, 5, {
        id = stat_id,
        value = stat_value,
        category = Config:GetCategoryByStatId(stat_id)
    })

    Mediator.On("OnAfterUpdateParagonPoints", {
        arguments = { player, paragon, stat_id, stat_value }
    })

    return true
end

-- ============================================================================
-- ADDON COMMAND HANDLERS
-- ============================================================================

---
--- Handles client request to load and display all paragon data.
---
--- Sends the player's level, experience, categories, and statistics to the
--- client addon for UI display and interaction.
---
--- Mediator Events:
--- - OnBeforeClientLoadRequest: (player, paragon) - allows modification before loading
--- - OnAfterClientLoadRequest: (player, paragon, categories) - allows modification of sent data
---
--- @param player The player object making the request
--- @param _ Unused parameter (always nil for addon requests)
---
function OnParagonClientLoadRequest(player, _)
    if not player then
        return false
    end

    -- Bots have no client, so every opcode this function sends is discarded
    -- after being built and serialized -- including the whole reward track
    -- (opcode 7), which is 60 milestones with per-class labels and talent
    -- coordinates. Bots also never send the addon request that is this
    -- function's OTHER caller, so the only way they get here is the login
    -- path in Hook.OnPlayerStatLoad.
    --
    -- Skipping it also stops OnAfterClientLoadRequest firing for them, which
    -- is what was registering three 10s tickers per bot. Nothing is lost:
    -- every subscriber to that event either only pushes client state, or
    -- reconciles something that self-gates on IsPlayerBot anyway
    -- (paragon_gem_double, paragon_rare_hunter, paragon_solo_dungeon).
    --
    -- !! DO NOT REASON FROM "BOTS ARE ALWAYS AT PARAGON 0" HERE !!
    -- That is true of every RNDBOT and false of the one case that matters.
    -- `.bot add` on the owner's OWN character builds a fresh WorldSession
    -- with isBot = true and logs it in from the DB, so IsPlayerBot() is true
    -- while the paragon object still carries that ACCOUNT's real level --
    -- 1750, not 0. Every gate in this system has to hold on its own terms
    -- for a bot-flagged session at a high paragon level; an argument that
    -- leans on the level being 0 is not an argument. (paragon_dual_enchant
    -- shipped a bot bug for exactly this class of reasoning.)
    if player.IsPlayerBot and player:IsPlayerBot() then
        return false
    end

    local paragon = player:GetData("Paragon")
    if not paragon then
        Hook.OnPlayerLogin(3, player)
        return false
    end

    -- Trigger Mediator event before loading
    paragon = Mediator.On("OnBeforeClientLoadRequest", {
        arguments = { player, paragon },
        defaults = { paragon },
    })

    -- Build category/statistic data with player's current assignments
    local categories = Config:GetCategories()
    if not categories then
        return false
    end

    for _, category_data in pairs(categories) do
        local statistics = category_data.statistics
        if statistics then
            for stat_id, stat_data in pairs(statistics) do
                stat_data.assigned = paragon:GetStatValue(stat_id)
            end
        end
    end

    -- Allow modules to modify the data sent to client
    categories = Mediator.On("OnAfterClientLoadRequest", {
        arguments = { player, paragon, categories },
        defaults = { categories },
    })

    -- Send complete paragon state to client
    player:SendServerResponse(Hook.Addon.Prefix, 1, paragon:GetLevel())
    player:SendServerResponse(Hook.Addon.Prefix, 2, paragon:GetExperience(), paragon:GetExperienceForNextLevel())
    player:SendServerResponse(Hook.Addon.Prefix, 3, categories)
    player:SendServerResponse(Hook.Addon.Prefix, 4, paragon:GetPoints())

    return true
end

---
--- Handles client request to update paragon statistics.
---
--- Validates all statistic changes, recalculates available points, and
--- updates statistic bonuses. Processes changes atomically to prevent
--- invalid state transitions.
---
--- Mediator Events:
--- - OnBeforeClientStatisticsUpdate: (player, paragon, data) - allows modification before processing
--- - OnBeforeStatisticChange: (player, paragon, stat_id, value) - per-stat hook before validation
--- - OnAfterStatisticChange: (player, paragon, stat_id, value) - per-stat hook after application
--- - OnAfterClientStatisticsUpdate: (player, paragon) - allows cleanup after all updates
---
--- @param player The player object making the request
--- @param arg_table Table containing {statistics_array} with categoryId, statId, and value
--- @return boolean True if all updates succeeded, false if validation failed
---
function OnParagonClientSendStatistics(player, arg_table)
    if not player or not arg_table then
        return false
    end

    local data = arg_table[1]
    if not data then
        player:SendNotification("ERROR.")
        return false
    end

    local paragon = player:GetData("Paragon")
    if not paragon then
        return false
    end

    -- Trigger Mediator event before processing any statistics
    paragon, data = Mediator.On("OnBeforeClientStatisticsUpdate", {
        arguments = { player, paragon, data },
        defaults = { paragon, data },
    })

    -- Temporarily remove all stat bonuses during processing
    UpdatePlayerStatistics(player, paragon, false)

    -- Process each statistic update
    for _, updated_data in pairs(data) do
        -- Validate category
        local category_id = updated_data.categoryId
        if not category_id then
            UpdatePlayerStatistics(player, paragon, true)
            return false
        end

        local categories = Config:GetCategories()
        local category_data = categories[category_id]
        if not category_data then
            UpdatePlayerStatistics(player, paragon, true)
            return false
        end

        -- Validate statistic
        local statistic_id = updated_data.statId
        if not statistic_id then
            UpdatePlayerStatistics(player, paragon, true)
            return false
        end

        local statistic_data = category_data.statistics[statistic_id]
        if not statistic_data then
            UpdatePlayerStatistics(player, paragon, true)
            return false
        end

        -- Validate value and limit
        local statistic_value = updated_data.value
        if not statistic_value or statistic_value < 0 then
            UpdatePlayerStatistics(player, paragon, true)
            return false
        end

        if statistic_data.limit > 0 and statistic_value > statistic_data.limit then
            UpdatePlayerStatistics(player, paragon, true)
            return false
        end

        -- Allow modules to intercept before stat change (for additional validation/modification)
        paragon, statistic_id, statistic_value = Mediator.On("OnBeforeStatisticChange", {
            arguments = { player, paragon, statistic_id, statistic_value },
            defaults = { paragon, statistic_id, statistic_value },
        })

        -- Apply the stat change
        UpdateParagonPoints(player, paragon, statistic_id, statistic_value)

        -- Allow modules to hook after stat change (for side effects, logging, etc.)
        Mediator.On("OnAfterStatisticChange", {
            arguments = { player, paragon, statistic_id, statistic_value },
        })
    end

    player:SetData("Paragon", paragon)

    -- Reapply all stat bonuses after processing
    UpdatePlayerStatistics(player, paragon, true)
    player:SendServerResponse(Hook.Addon.Prefix, 4, paragon:GetPoints())

    -- Trigger Mediator event after all statistics have been updated
    Mediator.On("OnAfterClientStatisticsUpdate", {
        arguments = { player, paragon },
    })

    return true
end

-- ============================================================================
-- PLAYER LIFECYCLE MANAGEMENT
-- ============================================================================

---
--- Callback executed after paragon data has been loaded from the database.
---
--- Applies all stat bonuses and syncs UI with loaded paragon state.
---
--- @param guid_low The low part of the player's GUID
--- @param paragon The loaded paragon instance
--- @return boolean True if successful, false if player not found
---
function Hook.OnPlayerStatLoad(guid_low, paragon)
    if not guid_low or not paragon then
        return false
    end

    local player = GetPlayerIfExist(guid_low)
    if not player then
        return false
    end

    -- Trigger Mediator event for post-load processing
    paragon = Mediator.On("OnPlayerStatLoad", {
        arguments = { player, paragon },
        defaults = { paragon }
    })

    player:SetData("Paragon", paragon)

    -- Apply all loaded statistics bonuses to the character
    UpdatePlayerStatistics(player, paragon, true)

    -- Sync UI with loaded paragon state
    OnParagonClientLoadRequest(player)

    return true
end

---
--- Handles player login event.
---
--- Creates new paragon instance and asynchronously loads character-specific
--- data from the database. Handles player initialization on first login.
---
--- @param event The event ID (3 = PLAYER_EVENT_ON_LOGIN)
--- @param player The player object that logged in
--- @return boolean Always returns nil for event handlers
---
function Hook.OnPlayerLogin(event, player)
    if not player then
        return
    end

    -- Check if paragon system is enabled
    local system_enabled = tonumber(Config:GetByField("ENABLE_PARAGON_SYSTEM")) or 1
    if system_enabled == 0 then
        return
    end

    -- Get paragon configuration and player info
    local account_id = player:GetAccountId()
    local character_guid = player:GetGUIDLow()

    -- Create new paragon instance with account_id
    local paragon = Paragon(character_guid, account_id)

    -- Trigger Mediator event before loading
    paragon, callback = Mediator.On("OnBeforePlayerStatLoad", {
        arguments = { player, paragon },
        defaults = { paragon, Hook.OnPlayerStatLoad }
    })

    -- Asynchronously load paragon data from database
    paragon:Load(callback)

    Mediator.On("OnAfterPlayerStatLoad", {
        arguments = { player, paragon },
    })
end

---
--- Handles player logout event.
---
--- Removes all stat bonuses and saves paragon progress to the database.
--- Called when a player disconnects or logs out.
---
--- @param event The event ID (4 = PLAYER_EVENT_ON_LOGOUT)
--- @param player The player object that logged out
--- @return boolean Always returns nil for event handlers
---
function Hook.OnPlayerLogout(event, player)
    if not player then
        return
    end

    -- local patch (account-wide paragon): a bot's logout must never save —
    -- with LEVEL_LINKED_TO_ACCOUNT its stale level/xp copy would overwrite
    -- the account row the real player has been advancing
    if player.IsPlayerBot and player:IsPlayerBot() then
        return
    end

    local paragon = player:GetData("Paragon")
    if not paragon then
        return
    end

    -- Trigger Mediator event before saving
    paragon = Mediator.On("OnBeforePlayerStatSave", {
        arguments = { player, paragon },
        defaults = { paragon }
    })

    -- Remove all stat bonuses from character
    UpdatePlayerStatistics(player, paragon, false)

    -- Save paragon progress to database
    paragon:Save()

    Mediator.On("OnAfterPlayerStatSave", {
        arguments = { player, paragon },
    })
end

---
--- Handles character deletion event.
---
--- Cleans up paragon data when a character is deleted from the account.
--- Only deletes data if LEVEL_LINKED_TO_ACCOUNT is disabled (character-level paragon).
--- When account-level paragon is enabled, data persists for other characters on the account.
---
--- @param event The event ID (2 = PLAYER_EVENT_ON_CHARACTER_DELETE)
--- @param player_guid The GUID of the character being deleted
---
function Hook.OnCharacterDelete(event, player_guid)
    -- Delete paragon data (method will check account_linked and handle appropriately)
    -- If account-linked: preserves data (other characters on account still use it)
    -- If character-linked: deletes data for this character
    if player_guid then
        Repository:DeleteParagonData(player_guid)
    end
end

-- ============================================================================
-- PLAYER EXPERIENCE EVENTS
-- ============================================================================

---
--- Handles creature kill event.
---
--- Awards paragon experience when a player kills a creature that has been
--- configured with experience rewards.
---
--- Mediator Events:
--- - OnBeforeCreatureExperience: (player, creature, paragon, participantCount, isRaid)
---
--- @param event The event ID (75 = PLAYER_EVENT_ON_KILL_REWARD)
--- @param player A player credited by AzerothCore's KillRewarder
--- @param creature The creature object that was killed
--- @param isDungeon Whether the kill occurred in a dungeon
--- @param participantCount Alive group participants counted before Paragon eligibility
--- @param isRaid Whether the standard raid group rate applies
---
function Hook.OnPlayerKillReward(event, player, creature, isDungeon, participantCount, isRaid)
    if not player or not creature then
        return
    end

    local paragon = player:GetData("Paragon")
    if not paragon then
        return
    end

    -- Allow modules to intercept creature experience gain
    paragon = Mediator.On("OnBeforeCreatureExperience", {
        arguments = { player, creature, paragon, participantCount, isRaid },
        defaults = { paragon },
    })

    if UpdatePlayerExperience(player, paragon, EXPERIENCE_SOURCE.CREATURE, creature:GetEntry()) then
        -- Lets the native-drop module anchor this recipient's completed gain
        -- without relying on player-event handler registration order.
        Mediator.On("OnAfterCreatureExperienceAwarded", {
            arguments = { player, creature },
        })
    end
end

---
--- Handles achievement complete event.
---
--- Awards paragon experience when a player completes an achievement that has
--- been configured with experience rewards.
---
--- Mediator Events:
--- - OnBeforeAchievementExperience: (player, achievement, paragon) - allows modification before award
---
--- @param event The event ID (45 = PLAYER_EVENT_ON_ACHIEVEMENT_COMPLETE)
--- @param player The player object that completed the achievement
--- @param achievement The achievement object that was completed
---
function Hook.OnPlayerAchievementComplete(event, player, achievement)
    if not player or not achievement then
        return
    end

    local paragon = player:GetData("Paragon")
    if not paragon then
        return
    end

    -- Allow modules to intercept achievement experience gain
    paragon = Mediator.On("OnBeforeAchievementExperience", {
        arguments = { player, achievement, paragon },
        defaults = { paragon },
    })

    UpdatePlayerExperience(player, paragon, EXPERIENCE_SOURCE.ACHIEVEMENT, achievement:GetId())
end

---
--- Handles quest complete event.
---
--- Awards paragon experience when a player completes a quest that has been
--- configured with experience rewards.
---
--- Mediator Events:
--- - OnBeforeQuestExperience: (player, quest, paragon) - allows modification before award
---
--- @param event The event ID (54 = PLAYER_EVENT_ON_QUEST_COMPLETE)
--- @param player The player object that completed the quest
--- @param quest The quest object that was completed
---
function Hook.OnPlayerQuestComplete(event, player, quest)
    if not player or not quest then
        return
    end

    local paragon = player:GetData("Paragon")
    if not paragon then
        return
    end

    -- Allow modules to intercept quest experience gain
    paragon = Mediator.On("OnBeforeQuestExperience", {
        arguments = { player, quest, paragon },
        defaults = { paragon },
    })

    UpdatePlayerExperience(player, paragon, EXPERIENCE_SOURCE.QUEST, quest:GetId())
end

---
--- Handles skill update event.
---
--- Awards paragon experience when a player increases a skill that has been
--- configured with experience rewards.
---
--- Mediator Events:
--- - OnBeforeSkillExperience: (player, skill_id, paragon) - allows modification before award
---
--- @param event The event ID (62 = PLAYER_EVENT_ON_SKILL_UPDATE)
--- @param player The player object whose skill was updated
--- @param skill_id The skill ID that was updated
--- @param value Current skill value (unused)
--- @param max Maximum skill value (unused)
--- @param step Skill step increase (unused)
--- @param new_value New skill value (unused)
---
function Hook.OnPlayerSkillUpdate(event, player, skill_id, value, max, step, new_value)
    if not player or not skill_id then
        return
    end

    local paragon = player:GetData("Paragon")
    if not paragon then
        return
    end

    -- Allow modules to intercept skill experience gain
    paragon = Mediator.On("OnBeforeSkillExperience", {
        arguments = { player, skill_id, paragon },
        defaults = { paragon },
    })

    UpdatePlayerExperience(player, paragon, EXPERIENCE_SOURCE.SKILL, skill_id)
end

-- ============================================================================
-- SERVER EVENTS
-- ============================================================================

---
--- Handles Lua state open event.
---
--- Called when the server is initialized or Lua scripts are reloaded.
--- Reloads paragon data for all players currently in the world.
---
--- @param event The event ID (33 = SERVER_EVENT_ON_LUA_STATE_OPEN)
---
function Hook.OnLuaStateOpen(event)
    local players = GetPlayersInWorld()
    if not players then
        return
    end

    for _, player in pairs(players) do
        Hook.OnPlayerLogin(3, player)
    end
end

---
--- Handles Lua state close event.
---
--- Called when the Lua scripts are being unloaded or the server is shutting down.
--- Saves paragon data for all players currently in the world.
---
--- @param event The event ID (16 = SERVER_EVENT_ON_LUA_STATE_CLOSE)
---
function Hook.OnLuaStateClose(event)
    local players = GetPlayersInWorld()
    if not players then
        return
    end

    for _, player in pairs(players) do
        Hook.OnPlayerLogout(4, player)
    end
end

---
--- Handles player command event.
---
--- Currently supports a "test" command for debugging paragon system functionality.
--- Can be extended to support additional admin commands.
---
--- @param event The event ID (42 = PLAYER_EVENT_ON_COMMAND)
--- @param player The player object executing the command
--- @param command The command string entered by the player (without leading slash)
--- @return boolean False to allow other command handlers to process the command
---
function Hook.OnPlayerCommand(event, player, command)
    if not player or not command then
        return
    end

    if command == "test" then
        local paragon = player:GetData("Paragon")
        if not paragon then
            return false
        end

        -- Remove existing bonuses
        UpdatePlayerStatistics(player, paragon, false)

        -- Add test stat value
        paragon:AddStatValue(1, 150)
        player:SetData("Paragon", paragon)

        -- Reapply bonuses
        UpdatePlayerStatistics(player, paragon, true)

        return false
    end
end

-- ============================================================================
-- EVENT REGISTRATION
-- ============================================================================

-- Player Events
RegisterPlayerEvent(2, Hook.OnCharacterDelete)
RegisterPlayerEvent(3, Hook.OnPlayerLogin)
RegisterPlayerEvent(4, Hook.OnPlayerLogout)
RegisterPlayerEvent(42, Hook.OnPlayerCommand)
RegisterPlayerEvent(45, Hook.OnPlayerAchievementComplete)
RegisterPlayerEvent(54, Hook.OnPlayerQuestComplete)
RegisterPlayerEvent(62, Hook.OnPlayerSkillUpdate)

-- Server Events
RegisterServerEvent(16, Hook.OnLuaStateClose)
RegisterServerEvent(33, Hook.OnLuaStateOpen)

-- Addon Communication Events
RegisterClientRequests(Hook.Addon)

return Hook
