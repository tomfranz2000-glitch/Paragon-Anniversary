--[[
    Eluna Timer Adapter for Mediator Pattern

    Provides Eluna/ALE-specific timer functionality for the Mediator pattern library.
    This adapter keeps the core Mediator library platform-agnostic while allowing
    deferred event execution with automatic callback queuing.

    Features:
    - Wraps CreateLuaEvent for use with Mediator's deferred events
    - Provides timer cancellation support via RemoveEventById
    - Queues Mediator events and executes them after all callbacks are registered
    - Maintains separation between universal library code and platform-specific code

    Usage:
    ```lua
    -- Publish an event with deferred execution
    Mediator.Publish("OnAfterMigrationExecute", {
        arguments = { player, data },
        defaults = { data },
        deferred = true,         -- Enable deferred execution
        flushDelay = 250         -- Optional: custom delay (default: 250ms)
    })
    ```

    @module eluna_adapter
    @author Paragon Team
    @license AGL v3
]]

-- ============================================================================
-- ELUNA TIMER ADAPTER IMPLEMENTATION
-- ============================================================================

---
--- Global adapter for Mediator timer functionality using Eluna's CreateLuaEvent.
--- Provides deferred event execution with automatic queuing.
---
--- @class MediatorTimerAdapter
---
_G.MediatorTimerAdapter = {
    pendingCalls = {},  -- Queue for deferred Mediator calls
    flushTimer = nil,   -- Timer handle for flushing the queue

    ---
    --- Creates a timed event using Eluna's CreateLuaEvent.
    ---
    --- Registers a global timed event that will execute the callback function
    --- after the specified delay. When the callback is called, it receives
    --- (eventId, delay, repeats) as parameters from Eluna.
    ---
    --- @param callback function The function to execute after the delay
    --- @param delay number Delay in milliseconds before execution
    --- @param repeats number Number of times to repeat (1 = execute once, 0 = infinite)
    --- @return number|nil The event ID for cancellation, or nil on failure
    ---
    CreateTimer = function(callback, delay, repeats)
        if type(callback) ~= "function" then
            error("MediatorTimerAdapter: callback must be a function")
            return nil
        end

        delay = delay or 250
        repeats = repeats or 1

        -- Wrap callback to ignore Eluna's automatic parameters (eventId, delay, repeats)
        local wrappedCallback = function(eventId, eventDelay, eventRepeats)
            callback()
        end

        -- CreateLuaEvent returns the eventId or nil
        -- Synopsis: eventId = CreateLuaEvent(function, delay, repeats)
        local eventId = CreateLuaEvent(wrappedCallback, delay, repeats)
        return eventId
    end,

    ---
    --- Cancels a previously created timer.
    ---
    --- Removes the timed event from Eluna's event system, preventing
    --- any future executions of the callback.
    ---
    --- @param eventId number The event ID returned by CreateTimer
    ---
    CancelTimer = function(eventId)
        if not eventId then
            return
        end

        -- RemoveEventById cancels the timed event
        RemoveEventById(eventId)
    end,

    ---
    --- Publishes a Mediator event with optional deferred execution.
    ---
    --- If deferred=true, the event is queued and executed after a delay,
    --- allowing callbacks to be registered after the event is published.
    ---
    --- @param eventName string The event name to trigger
    --- @param params table Named parameters with optional: arguments, defaults, deferred, flushDelay
    --- @return ... Merged return values (nil if deferred)
    ---
    Publish = function(eventName, params)
        params = params or {}

        -- Check if this call should be deferred
        if params.deferred then
            -- Add to queue
            table.insert(_G.MediatorTimerAdapter.pendingCalls, {
                eventName = eventName,
                params = params
            })

            -- Schedule flush if not already scheduled
            _G.MediatorTimerAdapter:_ScheduleFlush(params.flushDelay)
            return nil
        end

        -- Execute immediately
        return Mediator.On(eventName, params)
    end,

    ---
    --- Schedules a flush of pending calls after a delay.
    --- If already scheduled, does nothing.
    ---
    --- @param delay number Delay in milliseconds (default: 250)
    --- @private
    ---
    _ScheduleFlush = function(self, delay)
        delay = delay or 250

        if self.flushTimer then
            return  -- Already scheduled
        end

        self.flushTimer = self.CreateTimer(function()
            _G.MediatorTimerAdapter:_FlushPendingCalls()
        end, delay, 1)
    end,

    ---
    --- Flushes all pending event calls from the queue.
    --- Called automatically after the scheduled delay.
    ---
    --- @private
    ---
    _FlushPendingCalls = function(self)
        if #self.pendingCalls == 0 then
            return
        end

        local callsToExecute = self.pendingCalls
        self.pendingCalls = {}
        self.flushTimer = nil

        -- Execute all queued calls through Mediator
        for _, call in ipairs(callsToExecute) do
            Mediator.On(call.eventName, call.params)
        end
    end
}

return _G.MediatorTimerAdapter
