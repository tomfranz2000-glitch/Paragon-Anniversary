--[[
    Paragon Rework: Faithful Leap relay (milestone 350, Paladin)

    The castable spell 1900030 is a plain AoE-click dummy (Blizzard-profile
    row — the only cast flow the 3.3.5 client reliably click-targets;
    packet-probing showed it trajectory-casts anything shaped like the
    Heroic Leap prototype, reticle notwithstanding). This module relays the
    clicked destination into the server-side leap spell 1900032, which
    carries the prototype's actual jump effect and triggers the impact
    (1900031: one-time Holy burst + 1.5s consecration visual) at that spot.

    Entitlement is the learned spell itself (LEARNED_SPELL_SPECIALS teaches
    1900030 at milestone 350); 1900032 is never learned and only ever cast
    from here.

    Milestone 1075 (Leap of Devotion) teaches Faithful Leap RANK 2
    (1900106): identical spell data except the 10s cooldown — both ranks
    relay through here identically. No per-cast cooldown logic anywhere.
]]

local LEAP_RANKS = { [1900030] = true, [1900106] = true }
local JUMP_SPELL = 1900032

-- Fires for every player cast server-wide: the first line must stay a
-- cheap rejection.
RegisterPlayerEvent(5, function(event, player, spell, skipCheck)
    if not LEAP_RANKS[spell:GetEntry()] then
        return
    end
    local ok, err = pcall(function()
        local x, y, z = spell:GetTargetDest()
        if not x then
            return
        end
        player:CastSpellAoF(x, y, z, JUMP_SPELL, true)
    end)
    if not ok then
        print("[Paragon] faithful leap relay error: " .. tostring(err))
    end
end)

print("[Paragon] Rework: faithful leap module loaded")
