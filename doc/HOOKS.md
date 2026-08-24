# 🔌 Paragon System Hooks Reference

## 📋 Overview

This document lists all available **Mediator hooks** in the Paragon system. Hooks are event points where modules can register listeners to extend functionality without modifying core files.

### Hook Categories

1. **Experience Management** - Experience gain and calculation
2. **Level Progression** - Level changes and level-up events
3. **Statistics Management** - Stat allocation and application
4. **Client Communication** - Addon requests and responses
5. **Player Lifecycle** - Login/logout and data persistence
6. **Server Events** - Server-wide initialization and shutdown

---

## 🎯 Experience Management Hooks

### OnBeforeUpdatePlayerExperience

**Phase:** Validation / Pre-Processing
**Source:** `paragon_hook.lua` → `UpdatePlayerExperience()`

**Parameters:**
```lua
player      -- The player object
paragon     -- The paragon instance
source_type -- CREATURE=1, ACHIEVEMENT=2, SKILLUP=3, QUEST=4,
            -- CRAFT=5, GATHER=6, PROCESS=7, COLLECTIBLE=8
entry       -- The source entry/context ID
```

**Return Value:**
```lua
paragon, source_type, entry  -- Modified values or originals
```

**Description:**
Triggered before experience reward is calculated. Allows modules to modify the experience source or pre-validate conditions before processing.

**Example:**
```lua
RegisterMediatorEvent("OnBeforeUpdatePlayerExperience", function(player, paragon, source_type, entry)
    -- Deny experience from certain creatures
    if source_type == 1 and entry == 999 then
        return paragon, 0, 0  -- Cancel by returning invalid values
    end
    return paragon, source_type, entry
end)
```

---

### OnExperienceCalculated

**Phase:** Modification
**Source:** `paragon_hook.lua` → `UpdatePlayerExperience()`

**Parameters:**
```lua
player              -- The player object
paragon             -- The paragon instance
source_type         -- The experience source type
specific_experience -- The calculated experience value
```

**Return Value:**
```lua
experience  -- Modified experience value
```

**Description:**
Triggered after repeatable experience is calculated but before level-up
processing. Allows modification of `CREATURE=1`, `CRAFT=5`, `GATHER=6`, and
`PROCESS=7` XP based on conditions. Flat `ACHIEVEMENT=2`, `SKILLUP=3`,
`QUEST=4`, and `COLLECTIBLE=8` awards always bypass this hook at the common
award boundary. Their authoritative config/generator values are already the
final amounts; no runtime one-time scaling stage exists.

Subscribers run in registration order. Each subscriber receives the XP returned
by the previous subscriber; returning `nil` leaves that value unchanged. This
makes independent modifiers compose deterministically.

**Example:**
```lua
RegisterMediatorEvent("OnExperienceCalculated", function(player, paragon, source_type, exp)
    local current_level = paragon:GetLevel()

    -- Boost for low levels
    if current_level <= 10 then
        return exp * 1.5
    end

    -- Penalty for high levels
    if current_level >= 100 then
        return exp * 0.8
    end

    return exp
end)
```

---

### OnUpdatePlayerExperience

**Phase:** Core Processing
**Source:** `paragon_hook.lua` → `UpdatePlayerExperience()`
**Requirement:** **REQUIRED** - At least one handler must register (paragon_anniversary.lua)

**Parameters:**
```lua
player              -- The player object
paragon             -- The paragon instance
specific_experience -- The experience value to process
```

**Return Value:**
```lua
paragon  -- Updated paragon instance
```

**Description:**
Core experience processing handler. Handles cascading level-ups, point allocation, and state management. This is the main business logic handler for experience gains.

**Note:** Provided by `paragon_anniversary.lua`. Do not override unless you understand the implications.

**Handler Location:** `modules/paragon_anniversary.lua`

---

### OnParagonStateSync

**Phase:** State Synchronization
**Source:** `paragon_hook.lua` → `UpdatePlayerExperience()`

**Parameters:**
```lua
player  -- The player object
paragon -- The paragon instance
```

**Return Value:**
None

**Description:**
Triggered after experience is processed but before client sync. Allows custom state transformations or cleanup before sending data to client.

**Example:**
```lua
RegisterMediatorEvent("OnParagonStateSync", function(player, paragon)
    -- Store metadata for tracking
    paragon._last_update_timestamp = os.time()

    -- Log experience gain
    -- LogParagonUpdate(player:GetGUID(), paragon:GetState())
end)
```

---

### OnAfterUpdatePlayerExperience

**Phase:** Cleanup / Logging
**Source:** `paragon_hook.lua` → `UpdatePlayerExperience()`

**Parameters:**
```lua
player  -- The player object
paragon -- The paragon instance (with _last_exp_gained and _last_levels_gained metadata)
awarded_experience -- Exact amount applied after source resolution/modifiers
source_type        -- CREATURE=1 ... COLLECTIBLE=8
entry              -- Source entry/context ID supplied at the award boundary
```

**Return Value:**
None

**Description:**
Triggered after experience processing is complete and client has been updated.
The XP-drop module uses the exact amount/source to show non-kill rewards
immediately and to anchor creature rewards to their victim without stale-state
diffing.

**Example:**
```lua
RegisterMediatorEvent("OnAfterUpdatePlayerExperience", function(player, paragon, awarded_experience, source_type, entry)
    local last_exp = awarded_experience or 0
    local last_levels = paragon._last_levels_gained or 0

    if last_levels > 0 then
        print(player:GetName() .. " leveled up " .. last_levels .. " times!")
    end
end)
```

---

## 📊 Level Progression Hooks

### OnParagonLevelChanged

**Phase:** State Change Notification
**Source:** `paragon_class.lua` → `SetLevel()`

**Parameters:**
```lua
paragon    -- The paragon instance
old_level  -- The previous level
new_level  -- The new level
```

**Return Value:**
None

**Description:**
Triggered when paragon level changes (via SetLevel or AddLevel). Allows modules to react to level-ups with notifications, effects, or logging.

**Example:**
```lua
RegisterMediatorEvent("OnParagonLevelChanged", function(paragon, old_level, new_level)
    if old_level < new_level then
        local levels_gained = new_level - old_level
        print("Level up! Gained " .. levels_gained .. " levels")

        -- Play level-up effect
        -- player:PlaySound(857)  -- Level up sound
    end
end)
```

---

### OnParagonExperienceChanged

**Phase:** State Change Notification
**Source:** `paragon_class.lua` → `SetExperience()`

**Parameters:**
```lua
paragon      -- The paragon instance
old_exp      -- The previous experience
new_exp      -- The new experience
```

**Return Value:**
None

**Description:**
Triggered when experience changes. Useful for UI updates or progress tracking.

---

### OnParagonPointsChanged

**Phase:** State Change Notification
**Source:** `paragon_class.lua` → `SetPoints()`

**Parameters:**
```lua
paragon      -- The paragon instance
old_points   -- The previous point count
new_points   -- The new point count
```

**Return Value:**
None

**Description:**
Triggered when available paragon points change. Used for notifications when points are earned or spent.

---

### OnParagonStatChanged

**Phase:** State Change Notification
**Source:** `paragon_class.lua` → `SetStatValue()`

**Parameters:**
```lua
paragon      -- The paragon instance
stat_id      -- The statistic ID that changed
old_value    -- The previous invested value
new_value    -- The new invested value
```

**Return Value:**
None

**Description:**
Triggered when a character invests or reallocates statistic points. Used for logging and validation.

---

## ⚙️ Statistics Management Hooks

### OnBeforeUpdatePlayerStatistics

**Phase:** Pre-Processing
**Source:** `paragon_hook.lua` → `UpdatePlayerStatistics()`

**Parameters:**
```lua
player  -- The player object
paragon -- The paragon instance
apply   -- Boolean: true to apply, false to remove stats
```

**Return Value:**
```lua
player, paragon, apply  -- Modified or original values
```

**Description:**
Triggered before statistic bonuses are applied or removed. Allows pre-validation of stat application.

---

### OnAfterUpdatePlayerStatistics

**Phase:** Post-Processing / Side Effects
**Source:** `paragon_hook.lua` → `UpdatePlayerStatistics()`

**Parameters:**
```lua
player  -- The player object
paragon -- The paragon instance
apply   -- Boolean: true if stats were applied, false if removed
```

**Return Value:**
None

**Description:**
Triggered after statistic bonuses are applied or removed. Used for visual effects, notifications, and logging.

**Example:**
```lua
RegisterMediatorEvent("OnAfterUpdatePlayerStatistics", function(player, paragon, apply)
    if apply then
        -- Add visual effect when stats are applied
        -- player:AddAura(PARAGON_BUFF_AURA, player)
        -- player:SendBroadcastMessage("Paragon bonuses activated!")
    end
end)
```

---

### OnBeforeStatisticChange

**Phase:** Validation
**Source:** `paragon_hook.lua` → `OnParagonClientSendStatistics()`

**Parameters:**
```lua
player       -- The player object
paragon      -- The paragon instance
stat_id      -- The statistic ID being changed
stat_value   -- The new value for the statistic
```

**Return Value:**
```lua
paragon, stat_id, stat_value  -- Modified or original values
```

**Description:**
Triggered for each statistic change before it's applied. Allows per-stat validation and modification (PvP limits, point availability, etc.).

**Example:**
```lua
RegisterMediatorEvent("OnBeforeStatisticChange", function(player, paragon, stat_id, stat_value)
    -- Limit stats in PvP
    if player:IsInPvP() and stat_value > 50 then
        stat_value = 50
    end

    return paragon, stat_id, stat_value
end)
```

---

### OnAfterStatisticChange

**Phase:** Cleanup / Side Effects
**Source:** `paragon_hook.lua` → `OnParagonClientSendStatistics()`

**Parameters:**
```lua
player    -- The player object
paragon   -- The paragon instance
stat_id   -- The statistic ID that changed
stat_value -- The new value applied
```

**Return Value:**
None

**Description:**
Triggered after each statistic change is applied. Used for logging, validation, and side effects.

---

### OnBeforeClientStatisticsUpdate

**Phase:** Validation
**Source:** `paragon_hook.lua` → `OnParagonClientSendStatistics()`

**Parameters:**
```lua
player -- The player object
paragon -- The paragon instance
data   -- Table of statistic changes {categoryId, statId, value}
```

**Return Value:**
```lua
paragon, data  -- Modified or original values
```

**Description:**
Triggered before any statistic updates are processed. Allows batch validation before individual stat changes.

---

### OnAfterClientStatisticsUpdate

**Phase:** Cleanup / Notifications
**Source:** `paragon_hook.lua` → `OnParagonClientSendStatistics()`

**Parameters:**
```lua
player  -- The player object
paragon -- The paragon instance
```

**Return Value:**
None

**Description:**
Triggered after all statistic updates are complete. Used for final notifications and cleanup.

---

## 👤 Client Communication Hooks

### OnBeforeClientLoadRequest

**Phase:** Pre-Processing
**Source:** `paragon_hook.lua` → `OnParagonClientLoadRequest()`

**Parameters:**
```lua
player  -- The player object
paragon -- The paragon instance
```

**Return Value:**
```lua
paragon  -- Modified or original paragon instance
```

**Description:**
Triggered before paragon data is loaded for client. Allows modification of paragon state before sending.

---

### OnAfterClientLoadRequest

**Phase:** Post-Processing
**Source:** `paragon_hook.lua` → `OnParagonClientLoadRequest()`

**Parameters:**
```lua
player      -- The player object
paragon     -- The paragon instance
categories  -- Table of category/statistic data to send
```

**Return Value:**
```lua
categories  -- Modified or original categories data
```

**Description:**
Triggered after categories are prepared but before sending to client. Allows filtering or modification of displayed data.

---

## 🔄 Player Lifecycle Hooks

### OnBeforePlayerStatLoad

**Phase:** Pre-Processing
**Source:** `paragon_hook.lua` → `Hook.OnPlayerLogin()`

**Parameters:**
```lua
player  -- The player object
paragon -- The paragon instance (before loading from DB)
```

**Return Value:**
```lua
paragon, callback  -- Modified paragon and optional custom callback
```

**Description:**
Triggered before paragon data is loaded from database. Allows pre-loading initialization.

---

### OnAfterPlayerStatLoad

**Phase:** Post-Processing
**Source:** `paragon_hook.lua` → `Hook.OnPlayerLogin()`

**Parameters:**
```lua
player  -- The player object
paragon -- The paragon instance (after loading from DB)
```

**Return Value:**
None

**Description:**
Triggered after paragon data is loaded and applied. Used for post-login initialization.

---

### OnPlayerStatLoad

**Phase:** Callback
**Source:** `paragon_class.lua` → `Load()`

**Parameters:**
```lua
guid_low -- The player's GUID low value
paragon  -- The loaded paragon instance
```

**Return Value:**
Boolean

**Description:**
Internal callback triggered after async database load completes. Used by paragon_hook.lua to finalize player setup.

---

### OnBeforePlayerStatSave

**Phase:** Pre-Processing
**Source:** `paragon_hook.lua` → `Hook.OnPlayerLogout()`

**Parameters:**
```lua
player  -- The player object
paragon -- The paragon instance
```

**Return Value:**
```lua
paragon  -- Modified or original paragon instance
```

**Description:**
Triggered before paragon data is saved on logout. Allows final modifications before persistence.

---

### OnAfterPlayerStatSave

**Phase:** Cleanup
**Source:** `paragon_hook.lua` → `Hook.OnPlayerLogout()`

**Parameters:**
```lua
player  -- The player object
paragon -- The paragon instance
```

**Return Value:**
None

**Description:**
Triggered after paragon data is saved. Used for cleanup and logging.

---

## 🖥️ Server Events

### OnLuaStateOpen

**Phase:** Server Initialization
**Source:** `paragon_hook.lua` - Registered to `SERVER_EVENT_ON_LUA_STATE_OPEN (33)`

**Parameters:**
```lua
-- Mediator hooks available, but no specific parameters
```

**Description:**
Triggered when Lua scripts load. Reloads paragon data for all online players.

---

### OnLuaStateClose

**Phase:** Server Shutdown
**Source:** `paragon_hook.lua` - Registered to `SERVER_EVENT_ON_LUA_STATE_CLOSE (16)`

**Parameters:**
```lua
-- Mediator hooks available, but no specific parameters
```

**Description:**
Triggered when Lua scripts unload. Saves paragon data for all online players.

---

## 📌 Experience Source Hooks

These hooks are triggered for specific experience sources:

### OnBeforeCreatureExperience

**Phase:** Pre-Processing
**Source:** `paragon_hook.lua` → `Hook.OnPlayerKillReward()`

**Parameters:**
```lua
player    -- The player object
creature  -- The creature object that was killed
paragon   -- The paragon instance
participantCount -- Alive, in-range group members counted before Paragon eligibility
isRaid    -- Whether the standard raid sharing rule applies
```

**Return Value:**
```lua
paragon  -- Modified or original paragon instance
```

**Description:**
Triggered once per recipient credited by AzerothCore's `KillRewarder`, before
creature experience is awarded. Allows modification based on creature and
native group-credit properties.

---

### OnAfterCreatureExperienceAwarded

**Phase:** Post-Processing
**Source:** `paragon_hook.lua` → `Hook.OnPlayerKillReward()`

**Parameters:**
```lua
player    -- The credited player
creature  -- The creature whose positive Paragon XP award completed
```

**Description:**
Triggered only after a positive creature award has completed the full
experience/state-sync pipeline. The XP-drop module uses it to anchor the native
XP packet to the slain creature without relying on player-event handler order.

---

### OnBeforeAchievementExperience

**Phase:** Pre-Processing
**Source:** `paragon_hook.lua` → `Hook.OnPlayerAchievementComplete()`

**Parameters:**
```lua
player        -- The player object
achievement   -- The achievement object
paragon       -- The paragon instance
```

**Return Value:**
```lua
paragon  -- Modified or original paragon instance
```

**Description:**
Triggered before achievement experience is awarded.

---

### OnBeforeQuestExperience

**Phase:** Pre-Processing
**Source:** `paragon_hook.lua` → `Hook.OnPlayerQuestComplete()`

**Parameters:**
```lua
player  -- The player object
quest   -- The quest object
paragon -- The paragon instance
```

**Return Value:**
```lua
paragon  -- Modified or original paragon instance
```

**Description:**
Triggered before quest experience is awarded.

---

### OnBeforeSkillExperience

**Phase:** Pre-Processing
**Source:** `modules/paragon_profession_xp.lua` via `Hook.OnPlayerSkillUpdate()`

**Parameters:**
```lua
player    -- The player object
skill_id  -- The skill ID that was updated
paragon   -- The paragon instance
```

**Return Value:**
```lua
paragon  -- Modified or original paragon instance
```

**Description:**
Triggered before an eligible profession high-water award. The universal
`UNIVERSAL_SKILL_EXPERIENCE` is multiplied by the actual number of newly
mastered points and bypasses `OnExperienceCalculated`. With the shipped direct
value of 2000, each point pays exactly 2000 XP. Legacy per-skill override rows are not
consulted. Account-linked realms share one durable high-water mark across alts.
Weapon, defense, riding, and lockpicking updates do not qualify.

---

### OnAfterPlayerStatReady

**Phase:** Player State Ready
**Source:** `paragon_hook.lua` → `Hook.OnPlayerStatLoad()`

**Parameters:**
```lua
player  -- The player object
paragon -- The fully loaded Paragon instance already stored with SetData
```

**Description:**
Triggered only after the live Paragon object is ready. The profession module
uses this point to seed existing skill high-water values without retroactive XP
and to pay any durable pre-80 profession bank.

---

### ALE player event 76 — profession action

**Source:** required ALE/core profession-action hook

```lua
(event, player, actionKind, skillId, contextId, quantity, actionToken)
```

Action kinds are `CRAFT=1`, `GATHER_GAMEOBJECT=2`, `GATHER_CREATURE=3`,
`FISHING_AREA=4`, `FISHING_HOLE=5`, `PROSPECT=6`, `MILL=7`, and
`DISENCHANT=8`. The profession module maps them to Paragon sources 5–7, resolves
the authoritative base reward through generated profession data, rejects
unknown/mismatched rows, and deduplicates the server action token before the
normal multiplier pipeline.

---

## 📊 Hook Execution Order

Hooks execute in registration order. For guaranteed consistent behavior, follow this order:

```
1. VALIDATION PHASE
   - OnBeforeUpdatePlayerExperience
   - OnBeforeClientStatisticsUpdate
   - OnBeforeStatisticChange

2. CALCULATION/MODIFICATION PHASE
   - OnExperienceCalculated
   - OnBeforeUpdatePlayerStatistics

3. CORE PROCESSING PHASE
   - OnUpdatePlayerExperience (REQUIRED)
   - UpdatePlayerStatistics (core implementation)

4. STATE CHANGE PHASE
   - OnParagonLevelChanged
   - OnParagonExperienceChanged
   - OnParagonPointsChanged
   - OnParagonStatChanged

5. SYNC/NOTIFICATION PHASE
   - OnParagonStateSync
   - OnAfterUpdatePlayerStatistics
   - OnAfterStatisticChange
   - OnAfterClientStatisticsUpdate

6. CLEANUP/LOGGING PHASE
   - OnAfterUpdatePlayerExperience
   - OnAfterPlayerStatSave
```

---

## 🔗 Hook Dependencies

**Required Hooks (must have at least one handler):**
- `OnUpdatePlayerExperience` - Core experience processing

**Interdependent Hooks:**
- `OnBeforeStatisticChange` → `OnAfterStatisticChange`
- `OnBeforeUpdatePlayerStatistics` → `OnAfterUpdatePlayerStatistics`
- `OnBeforeClientStatisticsUpdate` → `OnAfterClientStatisticsUpdate`
- `OnBeforePlayerStatLoad` → `OnAfterPlayerStatLoad`
- `OnBeforePlayerStatSave` → `OnAfterPlayerStatSave`

**Event Chains:**
- Experience gain: OnBefore... → OnCalculated → OnUpdate → OnStateSync → OnAfter...
- Stat change: OnBeforeChange → (apply stats) → OnAfterChange

---

## 📚 Related Documentation

- **[MODULES.md](MODULES.md)** - Module development guide
- **[README.md](../README.md)** - Main Paragon system documentation
- **paragon_hook.lua** - Hook implementations
- **modules/paragon_anniversary.lua** - Example module using hooks

---

<div align="center">

### 🎯 **Use hooks to extend without modifying core files**

*Mediator pattern enables unlimited extensibility*

</div>
