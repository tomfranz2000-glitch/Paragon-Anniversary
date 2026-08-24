--[[
    Paragon_ExpToast.lua

    Paragon XP drops are rendered NATIVELY: the server sends the real
    SMSG_LOG_XPGAIN packet (paragon_exp_drops.lua), so the engine draws
    the float over the slain mob and emits the stock chat line. Victimless
    packets only emit chat, so server response 8 uses Blizzard's standard
    floating-combat-text API for quests and one-time/repeatable non-kill gains.

    At max level every XP line is a paragon gain (the core never sends
    the packet at cap), so this file only rewords the native chat line
    from "experience" to "paragon experience".
]]

ChatFrame_AddMessageEventFilter("CHAT_MSG_COMBAT_XP_GAIN", function(self, event, msg, ...)
    if type(msg) == "string" and msg:find("experience") and not msg:find("paragon experience") then
        return false, (msg:gsub("experience", "paragon experience", 1)), ...
    end
end)

local function FormatXP(value)
    if BreakUpLargeNumbers then
        return BreakUpLargeNumbers(value)
    end
    return tostring(value)
end

--- Draw the exact applied amount immediately for a non-creature Paragon gain.
--- Kill awards remain native and victim-anchored, so they never call this.
function UIParagon_OnReceiveExperienceDrop(player, arg_table)
    local gained = arg_table and tonumber(arg_table[1])
    if not gained or gained ~= gained or gained <= 0 then
        return
    end
    gained = math.floor(gained)
    if CombatText_AddMessage and CombatText_StandardScroll then
        CombatText_AddMessage(
            "+" .. FormatXP(gained) .. " Paragon XP",
            CombatText_StandardScroll, 1.0, 0.82, 0.0)
    end
end
