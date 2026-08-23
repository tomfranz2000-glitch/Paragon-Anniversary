--[[
    Paragon Rework: native XP drops

    Sends the real SMSG_LOG_XPGAIN (0x1D0) packet for paragon experience
    gains, so the CLIENT ENGINE renders them exactly like stock XP: the
    yellow float over the slain mob (kill gains) and the native chat line.
    At max level the core never sends this packet, so the channel is
    exclusively ours. The client side only rewords the chat line
    ("experience" -> "paragon experience", Paragon_ExpToast.lua).

    Packet layout (Player::SendLogXPGain, Player.cpp): uint64 victim guid
    (0 = none), uint32 total xp, uint8 type (0 kill / 1 non-kill), kill
    only: uint32 raw xp + float group rate, uint8 RAF flag.

    The gain handler records a pending drop. Creature awards then raise the
    OnAfterCreatureExperienceAwarded mediator event, which anchors the packet
    to the victim without depending on Lua file or player-handler load order.
    A 50ms fallback timer flushes non-kill gains (quests, achievements, skills,
    banking) as the victimless packet shape.
]]

local OPCODE_LOG_XPGAIN = 0x1D0

-- guidLow -> { gained = n }  (consumed by the kill handler or the flush timer)
local pending = {}

local function SendDrop(player, gained, victimGuid)
    local pkt = CreatePacket(OPCODE_LOG_XPGAIN, 22)
    if victimGuid then
        pkt:WriteGUID(victimGuid)
        pkt:WriteULong(gained)
        pkt:WriteUByte(0)          -- kill xp: engine floats it over the victim
        pkt:WriteULong(gained)
        pkt:WriteFloat(1)
    else
        pkt:WriteULong(0)
        pkt:WriteULong(0)          -- empty uint64 victim guid
        pkt:WriteULong(gained)
        pkt:WriteUByte(1)          -- non-kill xp: chat line only
    end
    pkt:WriteUByte(0)              -- no recruit-a-friend bonus
    player:SendPacket(pkt)
end

local function Snapshot(paragon)
    return {
        level = paragon:GetLevel(),
        xp = paragon:GetExperience(),
        max = paragon:GetExperienceForNextLevel(),
    }
end

-- baseline so the first gain after login diffs correctly
RegisterMediatorEvent("OnAfterUpdatePlayerStatistics", function(player, paragon, apply)
    pcall(function()
        if player and paragon and apply and not player:GetData("ParagonXpDrop") then
            player:SetData("ParagonXpDrop", Snapshot(paragon))
        end
    end)
end)

RegisterMediatorEvent("OnAfterUpdatePlayerExperience", function(player, paragon)
    local ok, err = pcall(function()
        if not player or not paragon then
            return
        end
        local now = Snapshot(paragon)
        local last = player:GetData("ParagonXpDrop")
        player:SetData("ParagonXpDrop", now)
        if not last then
            return
        end

        local gained = 0
        if now.level == last.level then
            gained = now.xp - last.xp
        elseif now.level > last.level then
            -- multi-level jumps understate (intermediate maxes unknown here)
            gained = (last.max - last.xp) + now.xp
        end
        if gained <= 0 then
            return
        end

        local guidLow = player:GetGUIDLow()
        pending[guidLow] = { gained = gained }
        CreateLuaEvent(function()
            local pend = pending[guidLow]
            if not pend then
                return
            end
            pending[guidLow] = nil
            local p = GetPlayerByGUID(guidLow)
            if p then
                SendDrop(p, pend.gained, nil)
            end
        end, 50, 1)
    end)
    if not ok then
        print("[Paragon] xp drop error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnAfterCreatureExperienceAwarded", function(player, creature)
    pcall(function()
        if not player or not creature then
            return
        end
        local pend = pending[player:GetGUIDLow()]
        if not pend then
            return
        end
        pending[player:GetGUIDLow()] = nil
        SendDrop(player, pend.gained, creature:GetGUID())
    end)
end)

print("[Paragon] Rework: native xp drops module loaded")
