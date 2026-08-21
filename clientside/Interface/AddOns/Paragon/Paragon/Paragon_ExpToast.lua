--[[
    Paragon_ExpToast.lua

    Paragon XP drops are rendered NATIVELY: the server sends the real
    SMSG_LOG_XPGAIN packet (paragon_exp_drops.lua), so the engine draws
    the float over the slain mob and emits the stock chat line — exact
    stock positioning and animation, nothing custom drawn here.

    At max level every XP line is a paragon gain (the core never sends
    the packet at cap), so this file only rewords the native chat line
    from "experience" to "paragon experience".
]]

ChatFrame_AddMessageEventFilter("CHAT_MSG_COMBAT_XP_GAIN", function(self, event, msg, ...)
    if type(msg) == "string" and msg:find("experience") and not msg:find("paragon experience") then
        return false, (msg:gsub("experience", "paragon experience", 1)), ...
    end
end)
