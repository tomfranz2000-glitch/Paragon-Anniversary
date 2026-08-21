--[[
    mediator.lua
    
    A lightweight mediator pattern implementation with:
    - Singleton pattern for global access
    - Named parameters for improved readability
    - Multiple return values with default fallbacks
    
    @module Mediator
    @author iThorgrim
    @license AGL v3
    @version 2.0
]]

local Object = Object or require("classic")

-- Lua 5.2+: unpack moved to table.unpack
local unpack = unpack or table.unpack

---@class Mediator
---@field private events table<string, table> Registered callbacks by event name
local Mediator = Object:extend()

-- Singleton instance
local Instance = nil

---
--- Initializes a new Mediator instance.
---
--- @return Mediator
---
function Mediator:new()
    self.events = {}
end

---
--- Gets the singleton instance of the Mediator.
--- Creates the instance on first call.
---
--- @return Mediator The singleton instance
---
function Mediator.GetInstance()
    if not Instance then
        Instance = Mediator()
    end
    return Instance
end

---
--- Registers a callback function for a specific event.
---
--- @param eventName string The event name
--- @param callback function The callback function to execute
--- @return void
---
function Mediator:Register(eventName, callback)
    if not self.events[eventName] then
        self.events[eventName] = {}
    end
    table.insert(self.events[eventName], callback)
end

---
--- Triggers an event and collects return values from registered callbacks.
---
--- @param eventName string The event name to trigger
--- @param params table Named parameters with optional: arguments, defaults
--- @return ... Merged return values
---
function Mediator:On(eventName, params)
    params = params or {}
    
    local args = params.arguments or {}
    local defaults = params.defaults or {}
    
    -- No callbacks registered
    if not self.events[eventName] then
        return self:_UnpackDefaults(defaults)
    end
    
    local allReturns = {}
    
    -- Execute all callbacks
    for _, callback in ipairs(self.events[eventName]) do
        local success, result = pcall(callback, unpack(args))
        if success then
            table.insert(allReturns, type(result) == "table" and result or {result})
        else
            error("Mediator: Error in callback for '" .. eventName .. "': " .. tostring(result))
        end
    end
    
    -- Return merged results
    if #allReturns > 0 then
        return self:_MergeReturns(allReturns, defaults)
    end
    
    return self:_UnpackDefaults(defaults)
end

---
--- Merges return values from multiple callbacks.
--- For each position, uses the first non-nil value found.
---
--- @param allReturns table Array of return value arrays
--- @param defaults table Array of default values
--- @return ... Merged return values
--- @private
---
function Mediator:_MergeReturns(allReturns, defaults)
    local maxReturnCount = #defaults
    
    -- Find maximum return count
    for _, returns in ipairs(allReturns) do
        if #returns > maxReturnCount then
            maxReturnCount = #returns
        end
    end
    
    if maxReturnCount == 0 then
        return nil
    end
    
    local mergedReturns = {}
    
    for i = 1, maxReturnCount do
        local value = nil
        
        -- Find first non-nil value at this position
        for _, returns in ipairs(allReturns) do
            if returns[i] ~= nil then
                value = returns[i]
                break
            end
        end
        
        -- Fallback to default if still nil
        if value == nil and defaults[i] ~= nil then
            value = defaults[i]
        end
        
        mergedReturns[i] = value
    end
    
    return unpack(mergedReturns)
end

---
--- Unpacks default values.
---
--- @param defaults table Array of default values
--- @return ... Unpacked values or nil
--- @private
---
function Mediator:_UnpackDefaults(defaults)
    if #defaults == 0 then
        return nil
    end
    return unpack(defaults)
end

---
--- Clears callbacks for a specific event or all events.
---
--- @param eventName string|nil Event name to clear, or nil to clear all
--- @return void
---
function Mediator:Clear(eventName)
    if eventName then
        self.events[eventName] = nil
    else
        self.events = {}
    end
end

---
--- Gets the count of registered callbacks for an event.
---
--- @param eventName string|nil Event name, or nil for total count
--- @return number Callback count
---
function Mediator:GetCallbackCount(eventName)
    if eventName then
        return self.events[eventName] and #self.events[eventName] or 0
    else
        local total = 0
        for _, callbacks in pairs(self.events) do
            total = total + #callbacks
        end
        return total
    end
end

-- =============================================================================
-- GLOBAL API
-- =============================================================================

local mediatorInstance = Mediator.GetInstance()

---
--- Global Mediator API for convenient access.
---
_G.Mediator = {
    ---
    --- Triggers an event with named parameters.
    ---
    --- @param eventName string The event name
    --- @param params table Named parameters with optional: arguments, defaults
    --- @return ... Return values
    ---
    On = function(eventName, params)
        return mediatorInstance:On(eventName, params)
    end,
    
    ---
    --- Registers a callback.
    ---
    --- @param eventName string The event name
    --- @param callback function The callback function
    --- @return void
    ---
    Register = function(eventName, callback)
        return mediatorInstance:Register(eventName, callback)
    end,
    
    ---
    --- Clears callbacks for an event.
    ---
    --- @param eventName string|nil Event to clear, or nil for all
    --- @return void
    ---
    Clear = function(eventName)
        return mediatorInstance:Clear(eventName)
    end,
    
    ---
    --- Gets callback count for an event.
    ---
    --- @param eventName string|nil Event name, or nil for total
    --- @return number Callback count
    ---
    GetCallbackCount = function(eventName)
        return mediatorInstance:GetCallbackCount(eventName)
    end
}

-- =============================================================================
-- HELPER FUNCTIONS
-- =============================================================================

---
--- Registers a mediator callback.
---
--- @param eventName string The event name
--- @param callback function|nil The callback function (optional)
--- @return function|void Registration function or void
---
function RegisterMediatorEvent(eventName, callback)
    if type(callback) == "function" then
        mediatorInstance:Register(eventName, callback)
    else
        return function(cb)
            mediatorInstance:Register(eventName, cb)
        end
    end
end

return Mediator