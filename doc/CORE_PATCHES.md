# Paragon Core Patches

Everything the Paragon reward track needs that could **not** be done from Lua.
Re-apply these after any AzerothCore update that overwrites core files or
rebuilds the world database.

Companion docs: `Paragon Progression Design.md` (XP economy), and the module
itself at `env/dist/etc/lua_scripts/paragon/modules/paragon_rework_track.lua`.

---

## 1. C++ patch — faster mount casting (paragon levels 100 + 750)

**File:** `src/server/game/Entities/Unit/Unit.cpp`, end of `Unit::ModSpellCastTime`

**Why a core patch was unavoidable:** mount summons are damage class
`SPELL_DAMAGE_CLASS_NONE`, and that branch of `ModSpellCastTime` ignores spell
haste entirely, so no haste aura can affect them. The flat cast-time modifier
auras (`SPELLMOD_CASTING_TIME`) match spells by class-family mask, and mounts
carry no family flags to match against. Eluna exposes no hook before the cast
time is computed. Precedent for this style of override sits a few lines above:
the stock core hardcodes a 0.5s cast for cooking while wearing the Chef's Hat.

Add after the `switch (spellInfo->DmgClass)` block, before the closing brace:

```cpp
    if (spellInfo->HasAura(SPELL_AURA_MOUNTED)) // paragon reward track: faster mounting
    {
        if (HasAura(1900005)) // level 100
            castTime = std::max(castTime - 1000, 0);
        if (HasAura(1900071)) // level 750 (stacks: standard 1500ms mounts become instant)
            castTime = std::max(castTime - 500, 0);
    }
```

Notes:
- The cheap check is first: `spellInfo->HasAura(...)` is a 3-element scan and
  is false for virtually every cast, so the aura lookups only run on mounts.
- Instant mount spells (67 of the 396 in 3.3.5) stay instant thanks to the
  floor; the 324 standard 1500ms mounts become 500ms at level 100 and **0ms
  with both markers** (level 750). 0 is an engine-native state: those 67
  stock instants plus GM `.cheat casttime` already run it daily.
- At 0ms the cast-while-moving gate self-disables — `Spell::prepare` only
  rejects `SPELL_FAILED_MOVING` when `m_casttime` is nonzero — so mounting
  works at a full run, and there is no cast window left to interrupt. That
  is the milestone's intent, not an accident; combat/indoor/area checks are
  cast-time-independent and unaffected.
- **Mounting while MOVING needs a second half: the client-side mount
  move-cast pass** (in `Tools/paragon_client_patch.py`). The moving block
  is the CLIENT's pre-send check — a CMSG_CAST_SPELL packet probe proved
  moving attempts never reach the server, and no server producer of cast
  result 51 is reachable for a 0ms mount cast (prepare's gate needs
  nonzero cast time; CheckCast's moving branch only hits auto-repeat or
  `AURA_INTERRUPT_FLAG_NOT_SEATED` spells, and mounts carry neither). The
  client check keys on `INTERRUPT_FLAG_MOVEMENT` (0x1) in the record's
  InterruptFlags — cast time is irrelevant to it — so the pass strips that
  one bit from all client mount records (aura 78), leaving cast times
  stock. Verified in-game. The server reads its own DBC files, so IT keeps
  stock flags and stays sole authority: below 750 prepare still rejects
  moving casts (nonzero cast time) and Spell::update still interrupts
  mid-cast movement; at 750 the 0ms cast makes moving casts land.
- **War story (2026-08-18), so nobody re-litigates it**: the move-cast
  pass was briefly reverted on a false charge of stance-bar pollution.
  The paladin's aura bar had filled with Aquatic Form, warrior stances and
  NPC-variant auras — actually caused by the milestone-600 test grant of
  2100 sequential quest ids: those included OTHER classes' teaching quests,
  and the stock core re-casts every rewarded quest's `RewardSpell` at
  EVERY login with no class check (`Player::learnQuestRewardedSpells`,
  called per-quest from `_LoadQuestStatusRewarded`; loading-time learns
  never persist to character_spell, which is why the spellbook looked
  clean in the DB). Fixed by re-granting 2100 quests filtered to
  `RewardSpell=0 AND RewardDisplaySpell=0 AND AllowableClasses=0` (7794
  candidates exist). **Any future quest-credit grant must use that
  filter.** No mount record ever appeared on the stance bar; the client's
  `GetShapeshiftFormInfo` dump (macro in-game) is the definitive
  instrument for identifying stance-bar entries.
- 1900071 ("Paragon Swift Mount") is a SERVER_SPELLS generator row —
  server-only, bp unused, applied via `SPECIAL_AURAS.SWIFT_MOUNT`.
- Requires a **rebuild**, not just a restart:
  `docker compose -p azerothcore-test build ac-worldserver` then recreate the
  container. The Dockerfile mounts a BuildKit ccache, so a one-file change
  recompiles only that file plus the link step.

## 1b. C++ patch — extended talent ranks (paragon level 125, Paladins)

Retail talents use at most 5 of the **9** rank slots Talent.dbc reserves. Three
edits open slots 6–9 up, and a fourth gates them behind the paragon milestone.

**`src/server/shared/DataStores/DBCStructure.h`**

```cpp
#define MAX_TALENT_RANK 9                                   // was 5
#define TALENT_POINTS_PER_TIER 5                            // new constant
```
and widen the struct comment: `RankID` covers fields 4–12, so delete the old
`// uint32 spellRank [4] // 9-12 not used` line.

**`src/server/shared/DataStores/DBCfmt.h`** — read the four previously skipped
fields:

```cpp
char constexpr TalentEntryfmt[] = "niiiiiiiiiiiiixxixxixxx";   // was "niiiiiiiixxxxixxixxixxx"
```

**`src/server/game/Entities/Player/Player.cpp`** — one change in
`Player::LearnTalent`:

```cpp
// tier gating means "points per tree row", NOT "rank slots" - it must stay 5
if (spentPoints < (talentInfo->Row * TALENT_POINTS_PER_TIER))
    return;
```

Why each piece matters:
- `MAX_TALENT_RANK` was doing double duty. Raising it *without* splitting out
  `TALENT_POINTS_PER_TIER` would demand 9 points per tier for **every class**,
  silently breaking all 30 talent trees.
- The format-size assert compares against `sizeof(TalentEntry)`; both sides grow
  48 → 64 bytes together, so they stay consistent.
- All ~24 other `MAX_TALENT_RANK` sites are loops already guarded with
  `if (talentInfo->RankID[rank])`, so the 891 five-rank talents are unaffected.

**Who may learn extended ranks is NOT in the core.** Enforcement lives in Lua
via a mod-ale hook (§1c), so raising another talent's cap needs no core change.

## 1c. mod-ale hook — `PLAYER_EVENT_ON_CAN_LEARN_TALENT` (74)

The stock core already calls `sScriptMgr->OnPlayerCanLearnTalent(player,
talent, rank)` inside `LearnTalent`; mod-ale just didn't forward it to Lua.
Four small edits (all inside `modules/mod-ale/src/`, following the
`OnCanGroupInvite` pattern):

- `LuaEngine/Hooks.h` — `PLAYER_EVENT_ON_CAN_LEARN_TALENT = 74` before
  `PLAYER_EVENT_COUNT`
- `LuaEngine/LuaEngine.h` — `bool OnCanLearnTalent(Player*, uint32 talentId, uint32 rank);`
- `LuaEngine/hooks/PlayerHooks.cpp` — `START_HOOK_WITH_RETVAL(..., true)` +
  push player/talentId/rank + `CallAllFunctionsBool(PlayerEventBindings, key, true)`
- `ALE_SC.cpp` — `OnPlayerCanLearnTalent` override forwarding
  `talent->TalentID`, **AND** `PLAYERHOOK_CAN_LEARN_TALENT` added to the
  `ALE_PlayerScript` constructor's hook list
- (doc comment in `LuaEngine/methods/GlobalMethods.h`)

Two traps found the hard way (2026-08-17, shipped broken once):
1. **The constructor hook list is load-bearing.** AzerothCore only dispatches
   a `PlayerScript` override if its `PLAYERHOOK_*` enum is registered in the
   script's constructor. An override method alone compiles fine and is
   silently never called — the gate simply doesn't exist.
2. **`CallAllFunctionsBool` needs `default_value = true` for CAN_-style
   hooks.** Its default is `false`, under which a Lua handler returning
   *nothing* refuses the action — that would have blocked every talent learn
   server-wide once trap 1 was fixed. With `true`, handlers only need to
   return `false` to refuse. (Several upstream ALE CAN_ hooks share this
   quirk; ours passes `true` explicitly.)

Lua handlers receive `(event, player, talentId, rank)` with **0-based rank**
(5 = first extended rank) and return `false` to refuse. The gate policy is the
`EXTENDED_TALENTS` table in `paragon_rework_track.lua` — one line per extended
talent with its paragon milestone. Unknown extended talents and players whose
paragon data is still loading are refused by default. A dedicated marker aura
is no longer involved (1900006 existed briefly and was retired).

**Client patch required** (unlike everything else here). The talent frame reads
the *client's* DBCs, so ranks 6–9 need `Client/Data/patch-5.MPQ` and
`Client/Data/enUS/patch-enUS-5.MPQ` (patched `Talent.dbc` + `Spell.dbc`, built
by `scratchpad/patch_client_dbc.py` + `build_mpq.py`). Because DBCs are static,
**every** Paladin sees the 9-rank talent; those below paragon 125 simply have
their clicks refused by the server.

## 1d. C++ patch — dual class auras (paragon level 200, Paladins)

**File:** `src/server/game/Spells/Auras/SpellAuras.cpp` — a file-static helper
`ParagonAllowDualAura()` above `Aura::CanStackWith`, plus a wrap of the
exclusivity early-return inside it.

**Why a core patch was unavoidable:** "one aura at a time" is neither client
UI nor DB data — it is `Aura::CanStackWith`: any two same-caster spells whose
`GetSpellSpecific()` is `SPELL_SPECIFIC_AURA` are mutually exclusive
(`IsAuraExclusiveBySpecificPerCasterWith`, SpellInfo.cpp). The family-flag
test behind that spec (`SpellFamilyFlags[2] & 0x20`) matches paladin auras
and nothing else (the core's own comment says so), so an exception scoped to
that spec cannot leak onto seals, hands, aspects or curses sharing the same
switch. No module hook fires there, and DB `spell_group` rules never touch
these spells (only Retribution Aura r1 is in a group — one with no stack
rule). The client needs nothing: paladins have no stance bar, both auras just
render as two buffs, and party members receive both through the same
server-side check.

The wrapped early-return:

```cpp
    // check spell specific stack rules
    if (m_spellInfo->IsAuraExclusiveBySpecificWith(existingSpellInfo)
            || (sameCaster && m_spellInfo->IsAuraExclusiveBySpecificPerCasterWith(existingSpellInfo)))
    {
        // paragon reward track: dual-aura milestone
        if (!(sameCaster && ParagonAllowDualAura(this, existingAura)))
            return false;
    }
```

Design constraints baked into the helper (the non-obvious parts):

- **Marker is `Player::HasSpell(1900008)`, not `HasAura`.** At login
  `_LoadSpells` runs before `_LoadAuras` (PlayerStorage.cpp:5529 vs 5534), so
  a *learned* marker is already present when saved auras reload — a dual pair
  active at logout survives the relog. An aura marker would lose that race
  and silently drop one aura every login. The Lua side teaches/unteaches
  1900008 through `LEARNED_SPELL_SPECIALS` like the trainer gate.
- **Pairwise contract.** `CanStackWith` returning false removes the
  *existing* aura. Below the cap of two the helper allows everything; at the
  cap it returns false for every existing class aura except the newest — so
  casting a third silently swaps out the oldest, no error spam.
- **Cap counted on the caster, incoming aura excluded by object identity.**
  Raid-area ticks re-apply an already-active aura to group members; that
  incoming aura IS one of the caster's two counted aura objects. Identity
  exclusion turns the count into 1 → stack allowed. Counting by spell id
  instead would make members flicker-lose an aura on every area tick.
- The marker's own hidden passive never pollutes the count: it is cloned
  generic (`SpellClassSet 0`), so its `GetSpellSpecific()` is not
  `SPELL_SPECIFIC_AURA`.

Rebuild is the cheap kind (one .cpp, ~3 min). Reverting = delete the helper
and unwrap the early-return; the Lua reward then grants a marker nothing
reads, which is harmless.

## 1e. C++ patch — extra permanent enchant slots (paragon level 275, universal)

**File:** `src/server/game/Spells/SpellEffects.cpp` — file-static
`ParagonShiftPermEnchant()` + `ParagonSlotHoldsSocketEnchant()` above
`Spell::EffectEnchantItemPerm`, plus one call inside it (right after the
`item_owner` resolution, before the stock remove/set/apply of
`PERM_ENCHANTMENT_SLOT`).

**Deliberately a generic mechanism, not a milestone patch** (user requirement:
future limit raises / other item types must not need rebuilds):

- **Capacity = markers.** Marker spells from the explicit list
  `{1900009, 1900033, 1900034, 1900035}`; each one the item **owner** knows
  (trade-window enchants respect the owner's milestone, not the enchanter's)
  adds one extra permanent enchant slot. Explicit ids, NOT a base+count
  range: the original `1900009 + i` design collided with the Divine
  Strength rank spells 1900010-13, whose `EquippedItemClass -1` passes the
  item-fit check — a paladin with rank 6 learned silently had capacity 2
  (masked in testing only by Cryptmaker's real gem sockets).
- **Item eligibility = the marker's own spell_dbc data.** Each marker's
  `EquippedItemClass` / `EquippedItemSubclass` / `EquippedItemInvTypes`
  columns are matched against the target item via the stock
  `Item::IsFitToSpellRequirements`. 1900009 is class 2 / masks 0 = any
  weapon. "Two enchants on rings" later = a new marker row with armor masks.
- **Expansion recipe (no rebuild):** clone a new marker row `1900010+` with
  the wanted item masks (generator `MARKERS` supports per-entry `overrides`),
  add a `LEARNED_SPELL_SPECIALS` line + track milestone in Lua, restart.

**Slot mechanics.** Extra enchants live in enchantment slots that never carry
a permanent enchant natively, in shift order `PRISMATIC(6) → SOCK_3(4) →
SOCK_2(3) → SOCK_1(2)`, where a sock slot is usable only if the item template
has no real socket there (`Socket[slot-2].Color == 0`) and any slot holding a
socket-granting enchant (`ITEM_ENCHANTMENT_TYPE_PRISMATIC_SOCKET`, i.e. belt
buckles once armor is eligible) is skipped — gems and buckles are never
displaced. On enchant: previous PERM enchant moves to the first free chain
slot; when the chain is full it cascades and the oldest drops. Re-applying
the enchant already in PERM is a no-op; an enchant re-applied from an extra
slot is cleared there first (no duplicate stat stacking). Every slot write is
bracketed with `ApplyEnchantment(item, slot, false/true)` so equipped items
stay stat-correct.

**Why this works with zero other changes:** the stock code is slot-agnostic
everywhere that matters — `Player::ApplyEnchantment(Item*, bool)` loops all
12 slots (PlayerStorage.cpp:4400) so login/equip applies extras; the combat
proc scan loops all 12 (Player.cpp:7497) so Mongoose-style enchants proc from
extra slots; `item_instance.enchantments` persists all 12. The enchant
travels **on the item** (mail/trade/sub-80 alts keep it — same "gates the
act, not ownership" stance as trainer ranks).

**Every component and its gate** (audited 2026-08-18; the milestone gates the
ACT of dual-enchanting — never the item, whose extra enchant is item-bound
like purchased trainer ranks):

| Component | File | Gate |
|---|---|---|
| The shift (mechanics) | `SpellEffects.cpp` §1e helpers | item OWNER `HasSpell(1900009)` + marker's item masks |
| Marker teach/unteach | `paragon_rework_track.lua` `LEARNED_SPELL_SPECIALS.DUAL_ENCHANT` | TRACK milestone 275 reached, apply pass only (sub-80s never get one → never taught) |
| Tooltip data push | `paragon/modules/paragon_dual_enchant.lua` (CSMH "ParagonEnch" fn1, `{[clientInvSlot]=enchantId}`) | **none, deliberately** — reports item truth for whoever holds the weapon; only addon clients ever register the 10s tick (bots send no client load request) |
| Tooltip render | addon `Paragon_DualEnchant.lua` + generated `Paragon_EnchantText.lua` | pushed state non-empty |
| Replace-popup skip | addon `Paragon_DualEnchant.lua` `StaticPopup_Show` hook | DUAL_ENCHANT milestone unlocked (live track data) AND new enchant name ∈ `ParagonWeaponEnchantNames` |

The popup skip is **friction removal only, never a mechanics gate**: an
ambiguous-name enchant shows the stock popup, and clicking Yes still shifts.
A character *without* the milestone enchanting over a dual-enchanted weapon
replaces only the primary — the extra slot is untouched (only a
milestone-holder's cast manages it).

**Client display internals** (all findings verified in-game):
- The 3.3.5 client APPLIES prismatic-slot enchants but renders no tooltip
  line for them, and its link builder maps the 4th gem field to the BONUS
  slot — links carry nothing (chat-linked weapons can never show the second
  line). Hence the CSMH push.
- The addon merges the second line into the PRIMARY enchant line's own
  FontString (`SetText(perm .. "\n" .. extra)`). Never shift text across
  tooltip lines: socket icons and the money frame anchor to specific line
  FontStrings and desync (shipped broken once). The `\n` grow reflows the
  whole anchor chain (`GameTooltipTemplate.xml`: every `TextLeft(i)` hangs
  from `TextLeft(i-1)` BOTTOMLEFT).
- The replace dialog is FrameXML: UIParent event `REPLACE_ENCHANT` /
  `TRADE_REPLACE_ENCHANT` → `StaticPopup_Show(which, oldName, newName)`
  (names = SpellItemEnchantment enUS names), OnAccept = global
  `ReplaceEnchant()` / `ReplaceTradeEnchant()` — both callable from addons.

**Change recipes** (what to touch for future enchant work):

1. *Raise the weapon limit to 3+*: new marker `1900010` (generator `MARKERS`
   entry, same weapon overrides) + `LEARNED_SPELL_SPECIALS` line + new TRACK
   milestone → restart. **Known follow-up work**: the display push reads only
   the prismatic slot — extend `paragon_dual_enchant.lua` to also read the
   sock-slot chain (mirror the core's slot picker: `{6,4,3,2}` minus real
   template sockets) and the addon to render a list per weapon slot.
2. *Extend to other item types* (e.g. rings): new marker with
   `EquippedItemClass=4` + invtype mask overrides + Lua wiring → restart.
   (Done at scale by milestone 725: `ENCHANT_SLOTS` in the push module now
   covers all eligible slots, and the popup needs no addon handling — the
   eager-shift addendum above suppresses it server-side. Belt buckles stay
   safe: the core skips socket-granting enchants.)
3. *Enchant data changed* (new custom enchants, DBC edits): rerun
   `Tools/gen_enchant_text.py` (rebuilds both the id→text table and the
   weapon-safe name set from Spell.dbc effect-53 spells) → user does
   `/reload`. No restart.
4. *Full content regen*: `Tools/paragon_client_patch.py` MARKERS carries
   1900009 with its `overrides` — a regen reproduces the marker row and adds
   its client Spell.dbc entry (MPQ write needs the game closed).

Rebuild is the cheap kind (one .cpp). Reverting = delete the helpers and the
one call; markers then gate nothing, harmless.

**Eager-shift addendum (2026-08-18)**: `ParagonEagerShiftNewEnchant`, called
at the end of `EffectEnchantItemPerm`, immediately relocates a fresh
permanent enchant from the primary slot into a free extra slot while
capacity remains — the primary slot stays EMPTY as the landing pad for the
next cast. Reason: the client fires its REPLACE_ENCHANT confirmation
whenever the primary slot is occupied, and that popup **cannot be accepted
by addon code** — `ReplaceEnchant()` is protected (ADDON_ACTION_FORBIDDEN;
the old addon auto-accept worked only by taint-state accident and has been
deleted from `Paragon_DualEnchant.lua`). With the pad kept empty the popup
only ever appears when capacity is physically full, i.e. when accepting
truly overwrites the primary enchant — correct friction. Consequences:
enchants predominantly live in the EXTRA slots (the display push renders
them; the native perm tooltip line is usually absent), and items enchanted
BEFORE this addendum show one legacy popup on their next re-enchant, after
which the eager shift normalizes them (verified in-game 2026-08-18: fresh
double-enchants are popup-free). Cleanup done: the popup auto-accept code,
its `ParagonUnlockedEnchantSlots` mirror, and the generated
`ParagonWeaponEnchantNames`/`ParagonArmorEnchantNames` sets are all
REMOVED (gen_enchant_text.py emits only the id→text table now). For any
future need to manipulate item enchant slots from Lua: ALE exposes
`Item:SetEnchantment(enchantId, slot)` and `Item:ClearEnchantment(slot)`
(LuaFunctions.cpp:1051-52) with stat apply handled.

## 1f. C++ patch — slow attenuation (paragon level 425, universal)

**File**: `src/server/game/Entities/Unit/Unit.cpp`, `Unit::UpdateSpeed`,
directly between the strongest-slow lookup and its `AddPct` application
(before the `SPELL_AURA_MOD_MINIMUM_SPEED` floor, which stays authoritative).

Every slow in the game — chills, Hamstring, daze, Crippling Poison — funnels
through this single point as the strongest `SPELL_AURA_MOD_DECREASE_SPEED`
modifier (strongest wins, slows never stack), for run, swim and flight alike.
Stock 3.3.5 has **no aura type that reduces slow magnitude** (only immunity,
duration mods and the min-speed floor), so the mechanic is a patch:

```cpp
if (slow)
    if (AuraEffect const* attenuation = GetAuraEffect(1900037, EFFECT_0))
    {
        int32 reduction = std::max<int32>(0, std::min<int32>(attenuation->GetAmount(), 100));
        slow = slow * (100 - reduction) / 100;
    }
```

- **The percent lives in the marker's own basepoints** (1900037 "Paragon
  Slow Attenuation", bp 24 + die 1 = 25): retuning 25%→50%, or a future tier
  swap, is a `spell_dbc` edit + restart — **no rebuild**. The clamp keeps a
  bad row from flipping slows into hastes.
- 1900037 is generated by `Tools/paragon_client_patch.py` **`SERVER_SPELLS`**
  (new family: SQL only, never staged into the MPQs — the client has no
  entry; its `DO_NOT_DISPLAY` attribute keeps the aura invisible). Applied via
  `SPECIAL_AURAS.SLOW_ATTENUATION` in `paragon_rework_track.lua`.
- Integer math truncates toward zero = in the player's favor (-50% slow at
  25% → -37%).
- Edge: granted while already snared, the dummy-aura apply does not itself
  trigger `UpdateSpeed`; the reduction bites on the next speed update (slow
  refresh/expiry — effectively instant for chills).

Reverting = delete the one `if` block; the marker aura then does nothing.

## 1g. C++ patch — stat-scaling level (paragon levels 500 + 800, universal)

**Files**: `src/server/game/Entities/Player/Player.h` (one decl) and
`Player.cpp`: new helper `Player::GetStatScalingLevel()` + the identical
`level = GetLevel()` + GT-clamp prologue of **six** conversion functions
replaced with it: `GetMeleeCritFromAgility`, `GetDodgeFromAgility`,
`GetSpellCritFromIntellect`, `GetRatingMultiplier`, `OCTRegenHPPerSpirit`,
`OCTRegenMPPerSpirit`.

The helper returns the GT-clamped level minus the **SUM** of the EFFECT_0
basepoints of markers **1900039** (milestone 500) and **1900072**
(milestone 800) — a second milestone needs a second marker because an aura
cannot stack with itself. Clamped ≥ 1; without markers it is bit-identical
to the old prologue. Those six functions are the COMPLETE gt-table stat
conversion surface — nothing else (combat level, hit vs targets, XP, spell
requirements, skill caps) is touched.

Measured effect at level 80 (gt tables, extracted from the client; every
combat rating shares ONE curve, which is why one addon factor per tier
suffices):

| reduction | ratings | agi→crit Δ/pt (pala) | int→crit Δ/pt (pala) | spirit→mana | spirit→health |
|---|---|---|---|---|---|
| −2 (500) | ×1.157674 | +0.0030 | +0.0010 | ×1.1085 | flat |
| −4 (500+800) | ×1.340208 | +0.0066 | +0.0021 | ×1.2287 | flat |

At −10 every rating is ×2.08 — the knob is steep.

Support cast:
- Both markers ride the `SERVER_SPELLS` generator family (bp = level
  reduction; retune = row edit + restart, **no rebuild** — but adding a
  NEW marker id needs the §1g sum-loop extended = rebuild), applied via
  `SPECIAL_AURAS.SCALING_LEVEL` / `SCALING_LEVEL_2`.
- The auras are passive-attribute AddAuras → **not saved** to
  character_aura → each login re-adds them AFTER the core computed stats:
  `paragon_scaling_level.lua` pokes a full refresh whenever the two-marker
  presence STATE changes (RecalculateRating idiom + ±1 AGI/INT/SPI flat
  mods).
- Character-sheet percent values are server fields (correct on their own);
  the client-computed "X Rating equals Y%" lines are corrected by the
  addon's `Paragon_ScalingLevel.lua` — `ParagonScalingUnlocked()` returns
  the SUMMED reduction of all unlocked `SCALING_LEVEL*` rewards (or
  false), and every factor lives in a table keyed by that sum; the
  spirit-mana fold in `Paragon_SpiritTooltip.lua` uses the same key.
  (Tooltip text is REWRITTEN post-hoc — never wrap/replace the Blizzard
  `GetCombatRatingBonus` global, that was the taint lesson.) **Keep the
  factor tables in sync with the markers' bp.**

Reverting = restore the six prologues and drop the helper.

## 1h. C++ patch — dual blessings (paragon level 700, Paladins)

**File**: `src/server/game/Spells/Auras/SpellAuras.cpp`, directly below the
§1d dual-aura helpers: `PARAGON_DUAL_BLESSING_MARKER` (1900056) +
`ParagonIsBlessingGroupMember` + `ParagonAllowDualBlessing`, and a one-line
wrap of the `SPELL_GROUP_STACK_RULE_EXCLUSIVE_FROM_SAME_CASTER` branch in
`Aura::CanStackWith`.

**The mechanism it patches** (found by live-probe + code hunt — the
static-search lesson: spell_group umbrella rows are NEGATIVE subgroup
references and invisible to positive-id queries): blessing cross-kind
exclusivity is **spell_group 1010** ({-1002 Might, -1005 Wisdom, -1006
Kings, -1007 Sanctuary, -1008 Protection, -1009 Light}, stack rule 2 =
EXCLUSIVE_FROM_SAME_CASTER). The loader expands subgroups recursively, so
every blessing is a member at runtime; the removal fires in
`_RemoveNoStackAurasDueToAura` during aura application (why triggered casts
were equally affected).

Patch semantics (the §1d pairwise contract):
- Marker owner (`HasSpell` 1900056) keeps **two** same-caster blessings per
  TARGET; a third swaps out the oldest (newest-survives, GetApplyTime).
- Membership is checked against **group 1010 itself** — data-driven, no
  hardcoded blessing masks.
- Same-kind pairs (Might vs Greater Might) share a SUBGROUP, get the
  umbrella pruned in `CheckSpellGroupStackRules`, and resolve through the
  EXCLUSIVE/EXCLUSIVE_HIGHEST branches ABOVE the patched one — still
  exclusive, zero double-dipping, no extra code.
- Per-target enforcement means the milestone works on every target the
  paladin blesses, not just self.
- Other rule-2 spell groups (non-blessings) keep stock behavior — the
  helper requires BOTH spells in group 1010.

Reverting = delete the helpers and the one-line wrap.

## 1i. C++ patch — ghost sprint + no spirit-healer sickness (paragon level 825, universal)

**Files**: two one-condition edits reading marker aura **1900073** ("Paragon
Ghost Sprint", SERVER_SPELLS family — server-only, bp 59 + die 1 = 60):

1. `Player.cpp`, `Player::ResurrectPlayer` — the `if (!applySickness)`
   early-return becomes `if (!applySickness || HasAura(1900073))`. The ONLY
   caller passing `applySickness = true` is the spirit-healer handler
   (`NPCHandler.cpp` `HandleSpiritHealerActivate`), so this waives exactly
   spirit-healer resurrection sickness. The 25% durability hit stays (it
   lives in the handler, untouched — deliberate).
2. `Unit.cpp`, `Unit::UpdateSpeed`, MOVE_RUN non-mounted branch — after the
   stock modifiers: `if (!IsAlive())` add the marker's EFFECT_0 amount to
   `main_speed_mod`. Ghost form's own +50% (spell 8326 EFFECT_1 is a plain
   MOD_INCREASE_SPEED aura) wins the max-pick as usual and the marker ADDS
   on top: **+110% total while dead** (night elf Wisp Spirit's 75 replaces
   the 50 the same way → 135). bp = spell_dbc data: retune = row edit +
   restart, no rebuild.

Why no extra plumbing is needed:
- The marker is a hidden PASSIVE aura and `RemoveAllAurasOnDeath` spares
  passives — it persists into (and through) death, so both reads see it
  while dead / at the spirit healer.
- Ghost form's own speed aura (8326) applies at repop and is removed at
  resurrect, and each of those triggers the same `UpdateSpeed` that
  evaluates our branch — no new call sites.
- Applied via `SPECIAL_AURAS.GHOST_SPRINT`; no client data, no addon work
  (the ghost speed needs no tooltip and sickness simply never appears).

Reverting = drop the two conditions.

## 1j. C++ patch — durability guard (paragon level 850, universal)

**File**: `Player.cpp`, `Player::DurabilityPointsLoss` — the SINGLE funnel
for every durability loss on the server: combat point ticks
(`DurabilityPointLossForEquipSlot`), death (`DurabilityLossAll` 10%) and
spirit-healer resurrect (25%) all convert to points and land here
(`DurabilityLoss` computes points then delegates — Player.cpp:4837).

Directly under the stock `HasPreventDurabilityLossAura()` check (the 100%
prevention aura type — it still wins outright), scale positive point
losses by marker **1900074**'s EFFECT_0 bp (74 + die 1 = 75 = percent
REDUCED), with probabilistic rounding: `points*keep/100` plus one more
point with probability `(points*keep)%100`. The common 1-point combat tick
at 75% becomes a 25% chance of a single point — statistically exact, and a
25%-of-max death hit scales cleanly. `points <= 0` casts (repair-style
negative deltas) are untouched.

- Marker rides SERVER_SPELLS (bp = reduction percent; retune = row edit +
  restart, no rebuild), applied via `SPECIAL_AURAS.DURABILITY_GUARD`;
  passive → present through death and at the spirit healer.
- No client data, no addon work.

**Milestone 1275 top-up:** the last 25% is NOT a second percentage marker — this patch reads only 1900074's
amount and does not sum markers (unlike §1g). "100% reduced" is exactly the stock aura
`SPELL_AURA_PREVENT_DURABILITY_LOSS` (289), which the stock `HasPreventDurabilityLossAura()` check above this
block already honours, so spell 1900123 carries it and the 875 marker simply stops mattering. Exact (no
probabilistic tick can leak a point) and no rebuild.

Reverting = drop the one block.

## 1k. C++ patch — soft landing (paragon level 875, universal)

**File**: `Player.cpp`, `Player::HandleFall` — the single site computing
player fall damage. Beside the existing special cases (Gust of Wind cap,
the fork's Divine Protection `damage /= 2`), scale `damage` down by marker
**1900075**'s EFFECT_0 bp (49 + die 1 = 50 = percent removed), before the
`EnvironmentalDamage(DAMAGE_FALL, ...)` call.

- Order: Safe Fall yard-reduction and Feather Fall/Hover immunity apply
  first (untouched); Divine Protection stacks multiplicatively (both →
  25% damage). A lethal fall can still kill at 50%.
- Marker rides SERVER_SPELLS (bp = reduction percent; retune = row edit +
  restart, no rebuild), applied via `SPECIAL_AURAS.SOFT_LANDING`. No
  client data, no addon work.

Reverting = drop the one condition.

## 1l. C++ patch — trainer list: known higher rank ⇒ row Known (Beyond Mastery support)

**File**: `src/server/game/Entities/Creature/Trainer.cpp`,
`Trainer::GetSpellState` — directly after the `HasSpell(trainerSpell)` Known
check, walk `GetNextSpellInChain` upward: if the player knows ANY higher
rank, return `SpellState::Known` instead of falling through to the
requirement checks.

Why: rank chains that are NOT "stackable with ranks" (the aura spells —
pure APPLY_AURA effects) get their lower ranks REMOVED by the core when a
higher rank is learned. After buying a custom top rank (milestones
900/925/950; Devotion/Retribution Aura), the stripped lower rows reappeared
in the trainer list as red "Unavailable" entries — rank 1 even as buyable
green (no prerequisite). With the walk, superseded rows report Known (the
semantically true state) and the client's default filter hides them. Also
cleans the same latent display wart for the milestone-175 six on
mass-taught characters. Chain walks only run while building trainer lists —
no hot-path cost.

Reverting = drop the loop.

## 1m. C++ patch — Provocation aggro radius (paragon level 1050, universal)

**File**: `src/server/game/Entities/Creature/Creature.cpp`, TWICE — the
identical clamp sits in `Creature::GetAggroRange` (~line 3413, THE live
aggro path: `CanStartAttack` line ~1936 uses it, and the aura-handler
table names it for auras 91/152) and in `Creature::GetAttackDistance`
(~line 3625, which only feeds the stealth-alert distance check in
Object.cpp). First deploy patched only GetAttackDistance and did nothing
in play — the two near-identical functions are the trap. Both insert
directly after the levelDiff computation, before the -25 clamp. (In
GetAggroRange the level locals are misnamed/swapped; the sign of
levelDiff is the same in both.)

**The mechanism it patches**: aggro radius = `20 - levelDiff` yards
(1 yd/level, floor 5 yd), so a level 80 shrinks every low-level creature's
radius to the floor (level-10 mob: 20-70). The stock
`SPELL_AURA_MOD_DETECTED_RANGE` hook lower in the function adds a player
aura's basepoints to the radius, but only AFTER the level term — a flat
bonus can never restore deep-gray radii without absurdly inflating
mid-level ones (found live: level-10 Defias ignored the +30 yd version at
~8 yd).

Patch semantics: while the player carries marker aura **1900104**
(Provocation, milestone 1050 "Magnetic Presence"), `levelDiff > 0` is
clamped to 0 — creatures never shrink their radius for a level advantage,
so everything at-or-below your level uses its full same-level 20 yd
radius; the marker's own MOD_DETECTED_RANGE effect (+5 yd, bp 4 + die 1)
then widens that to a uniform ~25 yd ("as if ~5 levels below them").
Creatures above the player are untouched (levelDiff <= 0 path). The stock
`creatureLevel + 5 <= MAX_PLAYER_LEVEL` gate still limits the +5 yd to
creatures <= 75. Neutral factions and already-engaged creatures are
unaffected (faction/threat rules run elsewhere). Retuning = spell_dbc
basepoints edit + restart, no rebuild; the levelDiff clamp itself is
behavior, not magnitude.

Spell data: 1900104 in Tools/paragon_client_patch.py CUSTOM_SPELLS (clone
588 Inner Fire — instant, free, infinite, cancelable self-buff; aura 152
bp 4). Taught via LEARNED_SPELL_SPECIALS.PROVOCATION.

## 1n. RETIRED — Leap of Devotion is now a spell RANK (paragon level 1075, Paladins)

The §1n core patch (conditional +5000ms in Player::AddSpellAndCategoryCooldowns),
marker 1900105, and the addon tooltip rewrite were ALL removed 2026-08-19 in
favor of the native ranks pattern (user call, correct one): milestone 1075
teaches **Faithful Leap Rank 2 (1900106)** — identical spell data to rank 1
(1900030) except RecoveryTime 10000. Both ranks carry honest static data, so
tooltips are truthful on both sides of the gate with zero conditional logic.

Wiring: SPELL_RANKS entry in Tools/paragon_client_patch.py ("hidden": no
trainer/SLA rows) + an emitted chain-root spell_ranks row (1900030 rank 1 —
the root is milestone-taught, not stock, so the tool must emit it) +
LEARNED_SPELL_SPECIALS.LEAP_COOLDOWN teaching 1900106. The spell_ranks chain
makes the server send SMSG_SUPERCEDED_SPELL on learn (client swaps book/bar
buttons like any trained rank); the reconcile's RemoveSpell reverts to rank 1
below the milestone. The relay module accepts both rank ids.

Two debug lessons preserved from the dead ends: (a) the 3.3.5 client's local
cooldown model ignores a bare SMSG_CLEAR_COOLDOWN mid-swirl — cooldown-shape
changes belong in spell data; (b) tool set_subtext originally wrote only
already-populated locale slots, so a subtext-less clone (6544) silently
dropped "Rank 2" — enUS is now force-written.

## 1o. mod-ale patch — full enchantment-slot range in Item Lua methods (paragon level 1100, universal)

**Files:** `modules/mod-ale/src/LuaEngine/methods/ItemMethods.h` —
`GetEnchantmentId`, `SetEnchantment`, `ClearEnchantment` slot validation lifted
from `MAX_INSPECTED_ENCHANTMENT_SLOT` (7) to `MAX_ENCHANTMENT_SLOT` (12),
opening the PROP slots (7-11) to Lua — **plus** the three core unique-gem
scans widened from slots 2-6 to 2-11 (BONUS still skipped):
`Item::GetGemCountWithID`, `Item::GetGemCountWithLimitCategory` (Item.cpp
~1015) and the gem loop in `Player::CanEquipUniqueItem` (Player.cpp ~14138).
Safe because random-property enchants in slots 7-10 carry SpellItemEnchantment
GemID 0 and never match a real gem. Rebuild required.

**Why:** milestone 1100 (Twice-Girded) lets a second Eternal Belt Buckle open a
second prismatic socket. The socket lives in **PROP_ENCHANTMENT_SLOT_4 (11)**
on EVERY belt — chosen over the spare SOCK slots because the client renders
PROP-slot enchant names as a tooltip text line directly under the socket rows
(the random-suffix display mechanism), which the addon restyles into a real
socket row; SOCK slots render nothing at all. `Player::ApplyEnchantment`
applies PROP-slot enchants with no socket-shape check (login/equip blindly
apply all 12 slots), and `HandleSocketOpcode` never writes slots it received
no gem for, so native re-socketing leaves the overflow gem alone.

**The slot-11 constraint:** `Item::SetItemRandomProperties` writes ALL FIVE
prop slots (7-11) from the roll (Item.cpp:684 — `Enchantment[i - PROP_0]`,
loop to MAX), so slot 11 is only safe on belts with template RandomProperty =
RandomSuffix = 0. The module refuses random-stat belts (level-80 gear is all
fixed-stat; only leveling greens are excluded). The widened unique-gem scans
(above) make stock socketing and equip checks see the slot-11 gem, so
unique-equipped and jeweler's-cap rules hold in the stock direction; the
module's own Lua path mirrors them (cross-item unique scan + ParagonGemLimitMax
cap from ItemLimitCategory.dbc).

**Everything else is Lua/addon:** `modules/paragon_double_buckle.lua`,
`modules/paragon_gem_data.lua` (Tools/gen_gem_data.py — GemProperties.dbc id →
enchant + color mask), marker 1900107 (LEARNED_SPELL_SPECIALS.DOUBLE_BUCKLE),
addon `Paragon_DoubleBuckle.lua` (prefix "ParagonBuckle": tooltip line +
drop-target overlay on the waist slot — the client socket UI hardcodes
template+1 prismatic and can never render the second socket). Empty open
socket = enchant 3729 (stat-less) parked in the overflow slot; a gem replaces
it. Milestone gates the act, not ownership (§1e stance): opened sockets/gems
survive a strip.

**Second-buckle interception is ITEM_EVENT_ON_USE, NOT the cast hook** (found
by adversarial review, first design was dead code): `Spell::CheckItems`
rejects a prismatic enchant whenever the target's PRISMATIC slot is occupied
(`SPELL_FAILED_MAX_SOCKETS`, Spell.cpp:7544) and it runs in `prepare()` —
PLAYER_EVENT_ON_SPELL_CAST (`Spell::_cast`) never fires for a second buckle.
ITEM_EVENT_ON_USE fires from the use-item packet BEFORE the spell exists
(ALE::OnUse, ItemHooks.cpp:81; Lua returning false suppresses the cast
silently), so the module consumes a buckle itself and opens the socket there.
The addon's overlay also accepts a dropped buckle (same server path) as a
belt-and-suspenders road. The stock "maximum sockets" error still fires for
non-milestone / already-doubled attempts — buckle NOT consumed in those cases.

**Tooltip display:** the parked enchant renders its raw DBC name as a white
line under the socket rows ("Socket Belt" while empty, the gem text once
filled). `Paragon_DoubleBuckle.lua` restyles that line IN PLACE — one SetText
with an inline `|T...|t` texture escape, no lines added/moved (the §1e
redistribution lesson) — into "[socket icon] Prismatic Socket" (grey) or
"[gem icon] +30 Stamina". 3.3.5 has NO prismatic socket texture: the generic
`Interface\ItemSocketingFrame\UI-EmptySocket` IS what native prismatic rows
use (verified against the client MPQ listfiles; the colored variants live in
locale-enUS.MPQ). The gem icon comes from GetItemIcon on a representative gem
entry the server includes in the state push.

**Addon taint rule:** never SetScript-wrap the stock paperdoll slot button —
its right-click branch is `UseInventoryItem`, PROTECTED in 3.3.5 (belt
tinkers), and a wrapped handler taints the whole path. The drop target is an
overlay button shown only while the cursor holds a gem/buckle
(CURSOR_UPDATE), stock scripts untouched.

## 1p. mod-ale patch — dungeon-completion server event (paragon level 1200, universal)

**Files:** `modules/mod-ale/src/LuaEngine/Hooks.h` (new
`MAP_EVENT_ON_ENCOUNTER_COMPLETE = 36` in ServerEvents),
`LuaEngine.h` + `hooks/ServerHooks.cpp` (`ALE::OnEncounterComplete` pushing
`(map, creditType, creditEntry, source-or-nil, dungeonId)`), and `ALE_SC.cpp`
(new `ALE_GlobalScript : GlobalScript` overriding
`OnAfterUpdateEncounterState`, registered alongside the other ALE script
objects). Rebuild required.

**Why:** milestone 1200 (Lone Conqueror) needs "final encounter of this
dungeon just completed" — the core's own matcher is
`Map::UpdateEncounterState` (Map.cpp:2933), which fires
`GlobalScript::OnAfterUpdateEncounterState` (Map.cpp:2980) for BOTH credit
types (boss kill via KillRewarder, scripted spell credit via
`Spell::finish`) with `dungeonCompleted` = `lastEncounterDungeon` of the
matched final encounter, 0 otherwise. Nothing Lua-visible was equivalent:
`PLAYER_EVENT_ON_KILL_CREATURE` fires only for literal player killing blows
(guardian kills and post-departure credits missed), and
`RegisterCreatureEvent` on a scripted boss REPLACES its boss AI (ALE's
AllCreatureScript wins the GetCreatureAI race) — never bind creature events
to scripted bosses.

**Deliberately NOT gated on `updated`:** that flag requires the credited
source to carry a C++ InstanceScript — classic dungeons without one never
set it, and Karazhan chess passes `source = nullptr`. The hook forwards
everything; consumers gate on `dungeonId != 0` and dedupe themselves
(`paragon_solo_dungeon.lua` keys first-clears on (guid, dungeonId) in
`acore_ale.paragon_solo_clears`).

**Solo semantics (module):** solo = `#map:GetPlayers() == 1` at credit time
— playerbots are real Player objects in the map list, so a bot inside the
instance breaks solo; group members OUTSIDE the instance don't. The 92-entry
allowlist in the module doubles as the LFG-id → name map (the DB cannot
resolve names: `lfgdungeons_dbc` is an empty override table) and excludes
raids/UBRS/holiday finals. Crit damage rides server-only spell 1900118
(aura 163, misc 127 = every school: melee whites, specials + Auto Shot, all
spell crits, NOT healing crits — stock Chaotic-meta behavior); integer
percent only, so +0.25%/clear applies as floor(clears / 4) whole percent
(accepted design).

## 1q. C++ patch — lockpicking before the Death Knight branch (Codex node 56, universal)

`Player::LearnDefaultSkill`, `SKILL_RANGE_LEVEL` case
(`src/server/game/Entities/Player/Player.cpp` ~12165). One branch **reordered**,
no logic changed: `skillId == SKILL_LOCKPICKING` now precedes
`IsClass(CLASS_DEATH_KNIGHT, …)`.

The ladder is `ALWAYS_MAXSKILL` → `SKILL_FLAG_ALWAYS_MAX_VALUE` → **DK** →
`FIST_WEAPONS` → **LOCKPICKING**. The DK branch grants `(level - 1) * 5`
(395 at 80), so a Death Knight never reached the lockpicking branch's
`max(1, GetSkillValue(633))`.

**Why it suddenly mattered:** §2h opens skill 633 to every class, making this
ladder reachable for a DK for the first time. Two consequences, both
unrecoverable on a non-refundable node — a DK buying the node would be gifted
395/400 instead of the intended 1/400, and would then be **re-pinned to 395 on
every login**, unable to ever hold 400. The re-pin is the nastier half:
`Player::addSpell` (~3378) re-runs `LearnDefaultSkill` for lockpicking
*unconditionally* —

```cpp
if ((AcquireMethod == LEARNED_ON_SKILL_LEARN && !HasSkill(pSkill->id))
    || ((pSkill->id == SKILL_LOCKPICKING || …) && TrivialSkillLineRankHigh == 0))
```

— the `!HasSkill` guard binds only to the **first** leg of that `||`, and
SkillLineAbility 8439 has `TrivialSkillLineRankHigh == 0`, so the second leg
fires on every `addSpell(1804)` including `_LoadSpells` at login. This is also
exactly why a Paladin is safe: `max(1, GetSkillValue(633))` re-reads and
preserves the value that `_LoadSkills` restored moments earlier.

**Stock-safe:** with stock data a DK can never reach this code for skill 633 —
`GetSkillRaceClassInfo` returns null and `LearnDefaultSkill` early-returns. Only
the §2h override makes the branch reachable, so the reorder is a no-op on
unmodified realms. `FIST_WEAPONS` deliberately stays below the DK branch.

Found by adversarial review, not by testing — the only DK on this realm is
level 55 and the Codex gates at 80, so it would have sat latent.

## 1r. C++ patch - flight over the vanilla continents (Codex node 59, universal)

`src/server/game/Spells/SpellInfo.cpp`, in `SpellInfo::CheckLocation` (~line 1521),
plus one added `#include "AreaDefines.h"`.

### The primary site: the attribute has exactly one consumer

`SPELL_ATTR4_ONLY_FLYING_AREAS` (0x04000000, "Only in Outland/Northrend",
`SharedDefines.h:544`) is carried by **118 of 49839** Spell.dbc rows - every
flying mount plus Flight Form 33943 / Swift Flight Form 40120 - and it is
tested in **exactly one place in the entire core**, `SpellInfo.cpp:1522`. The
continent test itself is trivial:

```cpp
bool IsFlyable() const { return flags & AREA_FLAG_OUTLAND; }   // DBCStructure.h:540
```

`AREA_FLAG_OUTLAND` is 0x400 and is carried by **0 of 499 map-0 areas and 0 of
473 map-1 areas** (vs 342/497 Outland, 543/552 Northrend). That single missing
flag is the whole of the vanilla-continent flight ban.

That one call site serves **both** halves of the restriction (a second, independent gate exists in `spell_gen_mount` — see below — but it does not read the attribute at all):

| Caller | Purpose | `player` | `strict` |
|---|---|---|---|
| `Spell.cpp:6012` | cast-time check | `ToPlayer()` or **nullptr** | true |
| `PlayerUpdates.cpp:1883` | area-update aura strip | `this` | **false** |
| `PlayerUpdates.cpp:1904` | controlled-unit aura strip | **nullptr** | true |

So one waiver covers both taking off and *staying* airborne across subzone
boundaries. A cast-time-only patch would be useless - you would mount and then
be dropped at the first area change.

### The patch

```cpp
uint32 const paragonVMap = GetVirtualMapForMapAndZone(map_id, zone_id);
bool const paragonSkies = player
    && (paragonVMap == MAP_EASTERN_KINGDOMS || paragonVMap == MAP_KALIMDOR)
    && player->HasSpell(1900130);

if (!areaEntry
    || (!areaEntry->IsFlyable() && !paragonSkies)
    || (strict && (areaEntry->flags & AREA_FLAG_NO_FLY_ZONE) != 0)
    || !player->canFlyInZone(map_id, zone_id, this))
```

`NO_FLY_ZONE` and the Northrend Cold Weather Flying rule are left fully intact.

**Keyed on the VIRTUAL continent, not the raw map id.** Seven playable zones
physically live on map 530 while being conceptually Azeroth, and
`WorldMapArea.dbc` says so through `virtual_map_id`:

| virtual 0 (Eastern Kingdoms) | virtual 1 (Kalimdor) |
|---|---|
| Eversong Woods, Ghostlands, Silvermoon City, Isle of Quel'Danas | Azuremyst Isle, Bloodmyst Isle, The Exodar |

Real Outland zones carry `virtual_map_id -1` and fall back to 530, so they are
untouched; **no row on map 571 remaps to 0 or 1** (verified against the DBC:
exactly 7 rows resolve to 0/1 and they are precisely the zones above), so Cold
Weather Flying cannot be bypassed this way. Testing the raw `map_id` instead
would either miss those seven zones, or - if 530 were added wholesale - open
the whole of Outland.

### Two rules this patch encodes

**1. The `player &&` term MUST stay first.** The trailing `canFlyInZone` term
dereferences `player`. On maps 0/1 it is currently unreachable *only* because
`!IsFlyable()` short-circuits ahead of it - and two of the three callers can
pass `nullptr` (see the table). Making Azeroth pass `IsFlyable()`
unconditionally would arm a real null deref. Ordering the guard this way means
the marker check fails first for every nullptr caller, so the null path is
never widened.

**2. Never do this by flagging the areas or editing `IsFlyable()`.**
`sAreaTableStore` is one process-wide store, so either route is global - and
mod-playerbots reads the **exact same predicate**:

```cpp
// modules/mod-playerbots/.../CheckMountStateAction.cpp - BotCanUseFlyingMount
AreaTableEntry const* area = sAreaTableStore.LookupEntry(bot->GetAreaId());
if (!area || !area->IsFlyable()) return false;
```

with `AiPlayerbot.UseFlyMountAtMinLevel = 60` and 5500 RNDBOT characters. The
data route hands flying mounts to every one of them. The marker route cannot,
because bots never learn 1900130.

### The SECOND waiver site - `spell_gen_mount`

`spell_gen_mount` (`src/server/scripts/Spells/spell_generic.cpp:4129`) computes
its own `canFly` and **never calls `CheckLocation`**, so the waiver has to be
repeated there or the nine scaling "hybrid" wrappers stay grounded for holders.
`map` there is already the virtual continent, so the same two constants apply.

Shipping only the first waiver was not merely an omission - it **actively broke
three of the nine**. Winged Steed (`:6292`), Blazing Hippogryph (`:6295`) and
X-53 Touring Rocket (`:6297`) are registered with
`mount0 = mount60 = mount100 = 0`: they are flying-only and have no ground
variant. With `canFly` false, `mount` resolved to `_mount100 == 0`, the
`if (mount)` block was skipped, `PreventHitAura()` never ran, and the wrapper's
own bare `SPELL_AURA_MOUNTED` applied - mount model, walking speed, GCD spent,
no error. **That state was unreachable before 1r**, because `CheckLocation`
rejected those three outright on maps 0/1; letting the cast through is what
exposed it. Found by the post-ship audit, not in testing.

Rule of thumb: **change one waiver, change the other.**

### RESOLVED: the client DOES block, and the fix is client-side (2026-08-19)

The server patch alone is **not sufficient**. The 3.3.5a client refuses to *send* a
flying-mount cast on maps 0/1, and it was proven by packet probe rather than argued.

**The probe.** A temporary `LOG_ERROR` was added inside the `ONLY_FLYING_AREAS`
branch (so it could only fire for player casts of those 118 spells - no flood),
printing map/zone/area/`IsFlyable`/`NO_FLY`/marker/`strict` and the decision. With
the marker held:

```
map 530 zone 3483 ... flyable 1 marker 1 strict 1 -> not-waived   (Hellfire, CAST)
map 0   zone 12 area 87 flyable 0 marker 1 strict 0 -> WAIVED      (Elwynn, AREA UPDATE)
```

`strict 1` is the cast path, `strict 0` the area-update path. Casting in Outland
produced `strict 1` lines; **casting in Elwynn produced none at all**. The packet
never arrived. The Outland leg is what makes this conclusive - without a positive
control, a silent log is ambiguous between "client blocked" and "probe broken".

**The tell that pointed the right way.** A gryphon mounted in Outland and ridden
into Elwynn *keeps flying* - that is the `strict 0 -> WAIVED` line, i.e. the core
patch correctly declining to strip the aura. But the same mount cannot be **recast**
there. Server permits, client refuses: cast blocked, aura preserved.

### The client fix - strip the ATTRIBUTE, not the area table

Implemented as the "azeroth flight pass" in `Tools/paragon_client_patch.py`:
strip `SPELL_ATTR4_ONLY_FLYING_AREAS` from **all 118 client `Spell.dbc` records**
carrying it, staged into `patch-enUS-5.MPQ`. Selected by the attribute, not by an id
list, so the set needs no maintenance; guarded by a `>= 100` sanity floor so a layout
change fails loudly instead of silently shipping a no-op.

**This is the same shape as the milestone-750 "mount move-cast pass"** already in that
tool: the 3.3.5 client pre-checks the spell RECORD before sending, so clearing the bit
client-side lifts the block while the server keeps stock data and stays the authority.
That precedent is what identified the spell record as the target.

**Deliberately NOT done via `AreaTable.dbc`.** Flagging ~972 Azeroth areas
`AREA_FLAG_OUTLAND` has unmeasured client-side blast radius (world map, music,
flight-path UI) for no extra benefit. It is also worth recording *why the area table
was the wrong theory*: `SpellInfoCorrections.cpp:5327` notes the client blocks
flying-mount casts in area 3479 (The Veiled Sea) - an **Outland** area that already
carries `AREA_FLAG_OUTLAND` - so the client's rule was never keyed on that flag.

### The resulting split, and why gating still holds

| | client | server |
|---|---|---|
| `Spell.dbc` ONLY_FLYING_AREAS | **stripped** (MPQ) | **stock** (`env/dist/data/dbc`) |
| effect | stops pre-judging, always sends | `CheckLocation` runs, demands marker 1900130 |

Client permissive, server authoritative. Consequences worth being explicit about:

- A **non-holder** who tries now gets the SERVER's refusal - `SPELL_FAILED_INCORRECT_AREA`,
  which the client renders as **"You are in the wrong zone."** (not a client-side refusal).
  Nothing about the client change grants flight; it only moves who says no.
- **Playerbots are unaffected** - they have no client at all, and `BotCanUseFlyingMount`
  reads the SERVER's `IsFlyable()`, which stays false on maps 0/1.
- The strip is global for anyone using this client install, but that is harmless precisely
  because the server never stopped checking.

### Error-string reference (this cost real time - write it down)

| code | client string |
|---|---|
| `SPELL_FAILED_INCORRECT_AREA` | **"You are in the wrong zone."** |
| `SPELL_FAILED_NOT_HERE` | "You can't use that here." |
| `SPELL_FAILED_NO_MOUNTS_ALLOWED` | "You can't mount here." |
| `SPELL_FAILED_ONLY_OUTDOORS` | "Can only use outside" |

Extracted from `Interface/FrameXML/GlobalStrings.lua` in `patch-enUS-3.MPQ`. The gate
patched here produces the FIRST one; any other wording means a different code path, so
match the exact string before assuming this patch is at fault.

## 2. Custom spell data (`acore_world.spell_dbc` + client `Spell.dbc`)

These rows are emitted by `Tools/paragon_client_patch.py` into both generated
SQL and the client Spell.dbc. A world-DB re-import or reset still requires
reapplying the generated SQL; changing client-visible metadata requires a full
client patch rebuild and restart.

`spell_dbc` merges into the spell store at **worldserver startup only**, so
adding or editing a row needs a restart.

| ID | Name | Effect |
|----|------|--------|
| 1900003 | Paragon Swiftness | `REWARD_AURAS`: `EffectAura_1 = 31` (MOD_INCREASE_SPEED), +50% run speed, with client name/icon/tooltip |
| 1900004 | Paragon Aquatic Grace | `REWARD_AURAS`: `EffectAura_1 = 58` (MOD_INCREASE_SWIM_SPEED), +100% swim speed, with client name/icon/tooltip |
| 1900005 | Paragon Quick Mount | `REWARD_AURAS`: `EffectAura_1 = 4` (DUMMY) — pure marker read by the patch in §1, with client name/icon/tooltip |
| 1900008 | Paragon Dual Aura | hidden passive marker (clone of 1900007), read via `HasSpell` by the patch in §1d; in the generator's `MARKERS`, so the next full regen also gives it a client entry |
| 1900009 | Paragon Dual Enchant | hidden passive marker (clone of 1900008), `EquippedItemClass=2` masks 0 — the §1e patch reads both `HasSpell` (capacity) AND these equipped-item columns (eligibility); in the generator's `MARKERS` with `overrides` |
| 1900030 | Faithful Leap | milestone 350 paladin ability, the CLICK HALF: pure AoE-reticle dummy (Blizzard-profile attributes, `Effect_1 = 3` dummy, target 28, 0–40yd, 15s plain CD, **SpellMissileID zeroed — see §2a**) whose only job is delivering the clicked point to the server. Taught via `LEARNED_SPELL_SPECIALS.FAITHFUL_LEAP`. Tooltip shows live damage via the `$1900031s1` cross-spell variable |
| 1900031 | Faithful Leap Impact | clone of Consecration R10 (48819): eff1 = one-time instant Holy burst on dest-area enemies (target 16, bp 799 + die 1 = 800), eff2 = visual-only persistent dynobj (dummy aura, no ticks, duration idx 65 = 1.5s, Consecration ground visual 5600, 8yd) re-targeted 18→28 (dest-caster would anchor at the TAKEOFF point; 28 is the Blizzard/Flare dynobj-dest pattern). `spell_bonus_data`: 0.15 SP + 0.15 AP direct. Client entry REQUIRED for the ground visual (dynobj renders the spell's visual from the CLIENT's DBC) and the tooltip variable |
| 1900032 | Faithful Leap Jump | the SERVER HALF: keeps the prototype's `SPELL_EFFECT_JUMP_DEST` (arc: min/max height 1.0/7.5, 4× run speed) with target 87 (use supplied dest) + eff2 triggering 1900031 at that dest. Never learned, never client-cast — `paragon_faithful_leap.lua` relays the clicked point into it via `CastSpellAoF` (player self-jump engine path proven by Feral Charge: `EffectJumpDest` handles player casters with SetCanTeleport + anticheat) |
| 1900010–13 | Divine Strength ranks 6–9 | `EffectAura_1 = 137`, +18/21/24/27% Strength |
| 1900014 | Paragon Consecration Burst | `Effect_1 = 2` (SCHOOL_DAMAGE), instant holy PBAoE, 8yd — damage overridden per cast |
| 1900040 | Living Symbol | milestone 525 paladin: visible passive with **aura 256 (NO_REAGENT_USE)**, effect mask A `0x11010002` = union of all 12 Greater Blessing ranks (each verified to carry Symbol of Kings 21177). The handler unions effect masks into the client-visible `PLAYER_NO_REAGENT_COST` fields; `Player::CanNoReagentCast` matches cast family flags against them (pure mask overlap, no family-name check). Client greys the reagent line from the same fields — zero addon work. `LEARNED_SPELL_SPECIALS.LIVING_SYMBOL` |
| 1900038 | Avenger's Reach | milestone 450 paladin: visible passive carrying **aura 107 (ADD_FLAT_MODIFIER), misc 17 (SPELLMOD_JUMP_TARGETS), +2** with effect masks A/B 0x4000 — the exact inverse of Blizzard's Glyph of Avenger's Shield (54930, −2). Server applies it in `Spell::SelectImplicitChainTargets` (both chaining effects: damage + daze); the client applies it to the "$x1 total targets" tooltip. Flat mods sum (glyph + milestone = stock 3). **SpellClassSet deliberately kept 10** — a spellmod only affects its own family; the §2a zero-the-family rule is inverted for modifier passives (the spell's own SpellClassMask stays 0) |
| 1900037 | Paragon Slow Attenuation | milestone 425 universal: invisible server-only dummy aura (SERVER_SPELLS generator family — never in the client MPQs); its **bp (24 + die 1 = 25) IS the "slows weakened by N%" knob** read by the §1f `Unit::UpdateSpeed` patch. Applied via `SPECIAL_AURAS.SLOW_ATTENUATION` |
| 1900071 | Paragon Swift Mount | milestone 750 universal: invisible server-only dummy marker (SERVER_SPELLS family, bp unused) — the §1 mount patch takes another 500ms off mount casts when present, flooring at 0: stacked with 1900005 the standard 1500ms mounts are INSTANT (and castable while moving — the `Spell::prepare` moving gate only applies to nonzero cast times). Applied via `SPECIAL_AURAS.SWIFT_MOUNT` |
| 1900036 | Empowered Spirit | milestone 375 universal: **3× regen from the same Spirit with NO formula edit** — the core multiplies exactly the spirit-derived regen terms (and nothing else: MP5/food/drinks are added after) by two percent auras: eff1 aura 110 (MOD_POWER_REGEN_PERCENT, misc 0 = mana) at `StatSystem.cpp` `UpdateManaRegen`, eff2 aura 88 (MOD_HEALTH_REGEN_PERCENT) at `Player.cpp` `RegenerateHealth`. Both bp 199 + die 1 = +200%. Visible spellbook passive (Attributes 0x40, Divine Spirit icon 1879, `$s1%` live tooltip), taught via `LEARNED_SPELL_SPECIALS.SPIRIT_REGEN`. Client-computed spirit-stat tooltip corrected by `Paragon_SpiritTooltip.lua` (keep its `MULT` in sync). Casting-regen talents (Meditation-style) and spirit-stat buffs compound by design; pets/creatures and spirit→SP/crit conversions untouched |

1900014 (`CUSTOM_SPELLS` in `Tools/paragon_client_patch.py`, cloned from Holy Nova's
damage sub-spell 48078 with priest family + mana cost zeroed) also owns a
`spell_bonus_data` row: direct 0.32 SP + 0.32 AP = 8× Consecration's per-tick
coefficients, so the burst scales with gear like the DoT total. The cast-side
logic (which Consecration ranks trigger it, damage totals, the paragon-125
gate) lives in `paragon_rework_track.lua`'s SPELL MODIFICATIONS section — the
established pattern for per-player spell behavior: DBC data cannot be
conditional, so a Lua cast-event handler triggers a custom spell only for
qualifying players. The gate is milestone 125.

(1900006 "Paragon Talent Mastery" existed briefly as a talent-gate marker and
was retired when the §1c hook replaced it.)

Ranks 6–9 are full clones of spell 20266 (rank 5) with only the ID, base points
and rank subtext changed. They — and the `talent_dbc` override row giving
talent 2185 nine ranks, and the client MPQs — are all produced by
**`Tools/extended_talents.py`** (config table at the top; `--apply` pipes the
SQL in). DB rows override DBC file rows by ID, and `DBCDatabaseLoader` maps
columns **positionally against the format string**, so `talent_dbc`'s column
order must keep matching `TalentEntryfmt`.

### The unified generator

**`Tools/paragon_client_patch.py`** is the single source of truth for all
custom spell/talent data (it superseded `extended_talents.py` and
`patch_client_dbc.py` — don't run those, they'd rebuild the MPQs with only
their own slice). Three config tables — `TALENT_RANKS`, `SPELL_RANKS`,
`MARKERS` — produce `generated/paragon_content.sql` (`--apply` pipes it in)
and both patch-X MPQs. Notes learned the hard way: `spell_dbc` mask columns
are unsigned (Holy Light's family mask has the high bit set — emit unsigned),
and every content family must live in ONE Spell.dbc build or the last
generator to run silently drops the others' records.

- Raising a talent cap: `TALENT_RANKS` entry + `EXTENDED_TALENTS` line in
  `paragon_rework_track.lua`.
- New trainable spell rank: `SPELL_RANKS` entry (chain root, clone, values,
  cost) + if it's a new spell family, a `MARKERS` entry and a
  `LEARNED_SPELL_SPECIALS` line in the Lua.
- Then restart worldserver + client. No C++, no hand-written SQL.

### Milestone 175 — trainable spell ranks (Paladin)

Six new ranks purchasable at any paladin trainer, gated by marker spell
**1900007 "Paragon Level 175"** (hidden passive, taught by the reward track's
`ReconcileLearnedSpells`).

### Adding a new trainable spell rank — THE RECIPE

One entry in `SPELL_RANKS` in `Tools/paragon_client_patch.py` provides almost
everything. Fields: chain root (`first`), the rank to clone, new spell id,
rank number, value overrides, gold cost. Then:

1. `python paragon_client_patch.py --apply` — **with the game client CLOSED**
   (a running client holds the MPQs locked and the build fails with
   PermissionError). Emits ALL of:
   - server `spell_dbc` row (the spell itself)
   - server `spell_ranks` row (rank chain → spellbook superseding + the
     spell's SP coefficients via `GetSpellBonusData`'s first-in-chain
     fallback, SpellMgr.cpp:954)
   - server `trainer_spell` rows for every trainer list that carries the
     clone spell, gated by `ReqAbility1` = the milestone marker spell
   - client `Spell.dbc` records (name/tooltip/icon)
   - client `SkillLineAbility.dbc` records — **without these the trainer
     window renders EMPTY**, see hard-won lesson 3 below
2. If it's a new milestone: `MARKERS` entry + `LEARNED_SPELL_SPECIALS` line
   and a `class_rewards` entry in `paragon_rework_track.lua`.
3. If the spell feeds another system (Consecration → the milestone-125
   burst), register the new rank there (`CONSECRATION_BURST.totals`).
4. Restart worldserver (all three server tables load at startup) and do a
   **full client restart** — process exit, not logout; MPQs load once at
   launch.

**Hard-won lessons (each one shipped broken first):**

1. **This core uses the MODERN trainer system** — `ObjectMgr::LoadTrainers`
   reads `trainer` + `trainer_spell` + `creature_default_trainer`. The legacy
   `npc_trainer` table still exists in the DB but is **dead data**; rows
   there never reach any trainer window. Paladin lists are TrainerIds
   **3, 4, 5**.
2. A logged-out client is NOT a restarted client. The trainer UI silently
   drops entries whose spell ids its (stale) Spell.dbc cannot resolve.
3. **The client only displays trainer entries for spells present in its
   `SkillLineAbility.dbc`** — that DBC drives the trainer window's category
   grouping; a spell without a row falls out of every category and the list
   shows an empty, greyed-out "All" header even though the packet carries the
   entry and the spell tooltip resolves fine. The generator clones each
   rank's row from its predecessor (skill lines: Holy 594, Protection 267,
   Retribution 184 for paladins). Client-side only — the server needs no SLA
   data.
4. Visibility semantics from `Trainer::GetSpellState` (enum matches the
   3.3.5 wire: 0 available/green, 1 unavailable/red, 2 known/grey): unmet
   `ReqAbility`/level/prev-rank → row still SENT, shown greyed with
   "Requires <marker name>"; only class/race-unfit rows are omitted, and
   `IsSpellFitByClassAndRace` passes spells with no SkillLineAbility data.
   A chained rank goes green only once the player knows the previous rank.

**Debugging tool for next time:** ALE packet events work —
`RegisterPacketEvent(0x1B1, 7, handler)` intercepts SMSG_TRAINER_LIST
server-side and can decode count + spell ids (entry stride 38 bytes; wrap
reads in pcall, `tostring()` everything printed — some packet methods return
boxed userdata). That probe is what proved the server sent all 183 entries
and pinned the fault client-side. Values extrapolate each spell's own last rank-to-rank
growth step; costs 2,500–10,000g:

| ID | Spell | Value | Cost |
|----|-------|-------|------|
| 1900020 | Holy Light R14 | 5690–6337 heal | 10,000g |
| 1900021 | Flash of Light R10 | 903–1011 heal | 4,000g |
| 1900022 | Consecration R9 | 147/tick (1176 total) | 7,500g |
| 1900023 | Shield of Righteousness R3 | 692 | 2,500g |
| 1900024 | Hammer of Wrath R7 | 1477–1628 | 5,000g |
| 1900025 | Exorcism R10 | 1343–1497 | 6,000g |

`spell_ranks` chains them onto their spells, which also makes
`GetSpellBonusData`'s first-in-chain fallback (SpellMgr.cpp:954) hand every
new rank its spell's coefficients automatically. Consecration R9 is also
registered in `CONSECRATION_BURST.totals` (1176) so the milestone-125 burst
fires for it — that coupling must be maintained for any future Consecration
rank.

Their authoritative definitions are `REWARD_AURAS` in
`Tools/paragon_client_patch.py`; do not reintroduce handwritten SQL copies.
The generator's custom-spell coverage audit compares every database ID in the
1900000–1999999 range with its staged client IDs and the explicit
`SERVER_SPELLS` allowlist, and aborts with every unexplained ID listed.

## 2a. Adding custom spells — the recipe (distilled from the Faithful Leap saga)

Everything goes through **`Tools/paragon_client_patch.py`**, the single source
for all custom DBC content. `CUSTOM_SPELLS` entries are
`{id, clone, name, subtext?, description?, overrides, bonus?}`: a full clone
of an existing Spell.dbc row with integer column overrides, emitted to BOTH
`acore_world.spell_dbc` (server) and the client patch-X MPQs. Optional
`bonus` adds a `spell_bonus_data` row (SP/AP coefficients). Deploy =
`python paragon_client_patch.py --apply` → worldserver restart → **full
client restart** (MPQs load at process start; this client does NOT
write-lock its MPQs, so a successful write proves nothing about whether the
game is running — but a running game never sees the new file either way).

**Step 0 — pick the architecture:**

- *Server-only behavior* (hidden auras, markers, triggered damage):
  server row only; invisible to the client by design. EXCEPTION: anything
  with a ground/dynobj visual needs a client entry (the client renders a
  dynamic object's visual from ITS OWN Spell.dbc).
- *Player-castable, self/unit-targeted*: one spell, client entry mandatory
  (spellbook, cast UI, tooltip, cooldown display).
- *Player-castable, GROUND-targeted*: use the **two-spell pattern** —
  a click dummy shaped exactly like a proven click spell (Blizzard 10 /
  Flare 1543 profile: `Attributes 0x10000`, `Targets 0x40`, effect target
  28, plain range) that only delivers the clicked point, plus a server-side
  action spell fed by a Lua relay
  (`RegisterPlayerEvent(5)` → `Spell:GetTargetDest()` →
  `player:CastSpellAoF(x, y, z, ACTION_SPELL, true)`). The client's cast
  pipeline has hidden data hooks (see the pitfalls); a vanilla dummy is the
  only fully predictable client interaction.

**Step 1 — pick a clone base close to what you want** (its VISUALS, cast
style and school come along free), then normalize the known traps:

| Column | Trap |
|---|---|
| `SpellMissileID` | **THE trajectory trap.** Nonzero = the client casts in trajectory mode (castFlags 0x2, aim-forward dest, reticle cosmetic — the vehicle-catapult mechanic). Immune to every targeting column. Zero it unless you want catapult aiming. Cost us four deploy rounds. |
| `PowerType` / `ManaCost` | Clone keeps them (the Heroic Leap prototype was RAGE). Always set explicitly. |
| `Category` / `CategoryRecoveryTime` | Shared cross-spell cooldowns. For customs: `Category 0`, plain `RecoveryTime`. |
| `RangeIndex` | Semantics live in SpellRange.dbc — some have MIN ranges (95 "Charge" = 8–25!). 5 = 0–40yd, 13 = anywhere, 4 = 30yd, 1 = self. |
| `DurationIndex` | SpellDuration.dbc: 21 = infinite, 31 = 8s, 65 = 1.5s, 36 = 1s, 39 = 2s, 327 = 0.5s. |
| `Targets` | 0x40 = dest-location → client shows the AoE reticle. Pairs with implicit target 28 on real click spells. |
| Implicit targets | 28 = click-dest (dynobj pattern), 87 = "use supplied dest" (server passthrough — right for triggered/relayed spells), 89 = trajectory, 16 = enemies around dest, 18 = dest-caster (**anchors at the caster AT RESOLUTION TIME** — for anything triggered mid-movement that's the takeoff point, not the landing), 22/15 = PBAoE. |
| `EffectDieSides` | Displayed/rolled value = bp + roll(1..die). die 1 → value = bp+1 (Blizzard stores value−1). die 0 → exact bp (required for `CastCustomSpell` overrides). |
| `SpellClassSet` / masks | Zero for customs — otherwise class talents and procs latch onto the spell (learned with the Consecration burst). |
| `EquippedItemClass` /subclass/invtypes | Clone keeps weapon requirements; also double as data-driven eligibility for the §1e enchant patch. |

**Step 2 — damage scaling**: `spell_bonus_data` (direct/dot + ap variants).
Boot-log proof it loaded: the "Loaded N Extra Spell Bonus Data" count goes
up by one — AC logs errors for bonus rows on unknown spells, so a clean
count-up also proves the custom spell entered the spell store.

**Step 3 — tooltips**: `description` supports the client's `$` variables:
`$s1` = own effect-1 value, **`$<spellid>s<n>` = another spell's value**
(e.g. Faithful Leap shows `$1900031s1` = the impact spell's 800). The
referenced spell must be in the client MPQ. Numbers shown are BASE values —
same convention as default skill tooltips (coefficients are invisible).

**Step 4 — teaching it**: add a `LEARNED_SPELL_SPECIALS` line + a TRACK
milestone entry in `paragon_rework_track.lua` — the reconcile
teaches/unteaches on milestone state, handles sub-80 gating, and the
spellbook entry appears with icon and tooltip from the client MPQ.

**Debugging order when a cast misbehaves** (do #1 FIRST — it found in one
look what four hypothesis-driven data revisions missed):

1. **Full-column diff vs a PROVEN similar spell**: dump every spell_dbc
   column of yours next to Blizzard/Flare (or whatever works like you
   want), print only columns differing from both. The culprit column is
   usually staring at you.
2. Server-side dest/state probe: `RegisterPlayerEvent(5)` +
   `Spell:GetTargetDest()` → broadcast to chat.
3. Raw packet probe: `RegisterPacketEvent(0x12E, 5)` on CMSG_CAST_SPELL —
   read castCount(u8), spellId(u32), castFlags(u8), targetFlags(u32); if
   flags & 0x40: packed transport guid (mask byte + one byte per set bit)
   then dest xyz floats. ALE hands Lua a copy; the real handler is
   unaffected. castFlags 0x2 = the client is trajectory-casting.
4. When N deploys behave IDENTICALLY, stop suspecting staleness and start
   suspecting an UNCHANGED column.

**ID ledger** (check this BEFORE assigning an id — the §1e patch originally
reserved a range that collided with the talent ranks):

| Range | Holder |
|---|---|
| 1900000–02 | upstream-reserved (the broken AURA-stat spells, never created) |
| 1900003–05 | aura specials (swiftness / swim / quick mount) |
| 1900007–09 | markers (trainer gate / dual aura / dual enchant) |
| 1900010–13 | Divine Strength ranks 6–9 (talent family) |
| 1900014 | Consecration burst |
| 1900020–25 | trainer spell ranks |
| 1900030–32 | Faithful Leap trio |
| 1900033–35 | **reserved**: future extra-enchant markers (§1e explicit list) |
| 1900036 | Empowered Spirit (milestone 375: 3× spirit regen via aura 110 + aura 88) |
| 1900037 | Paragon Slow Attenuation (milestone 425: server-only marker, bp = reduction %, §1f) |
| 1900038 | Avenger's Reach (milestone 450: +2 Avenger's Shield chain targets via SPELLMOD_JUMP_TARGETS; SpellClassSet kept 10 — modifier passives keep their target's family) |
| 1900039 | Paragon Stat Scaling (milestone 500: server-only marker, bp = levels reduced, §1g; addon factors must track the bp) |
| 1900040 | Living Symbol (milestone 525: aura 256 reagent-free Greater Blessings, mask 0x11010002) |
| 1900041–44 | Benediction ranks 6–9 (milestone 550, −12/14/16/18%) |
| 1900045–48 | Divinity ranks 6–9 (milestone 575, +6/7/8/9% dual-field) |
| 1900049–51 | Anticipation ranks 6–8 (milestone 625, +6/7/8% dodge) |
| 1900052–53 | Seals of the Pure ranks 6–7 (milestone 650, +18/21% dual-field) |
| 1900054–55 | Conviction ranks 6–7 (milestone 675, +6/7% crit dual-field) |
| 1900056 | Paragon Dual Blessing (milestone 700: hidden marker read by §1h) |
| 1900033, 1900057–66 | Milestone 725 enchant-slot ladder markers (§1e capacity; 1900033 = third weapon slot, 57–66 = chest/legs/hands/feet/wrist/back/head/shoulder/rings/shield; taught by avg-ilvl thresholds in paragon_enchant_slots.lua) |
| 1900034–35, 1900067–70 | **reserved**: future §1e enchant markers (already compiled into the core array) |
| 1900071 | Paragon Swift Mount (milestone 750: server-only marker, §1 takes another 0.5s off mount casts — instant with milestone 100) |
| 1900072 | Paragon Stat Scaling II (milestone 800: server-only marker, clone of 1900039 — §1g sums both to −4 effective levels) |
| 1900073 | Paragon Ghost Sprint (milestone 825: server-only marker, §1i — bp 60 added to run speed while dead + waives spirit-healer resurrection sickness) |
| 1900074 | Paragon Durability Guard (milestone 850: server-only marker, §1j — bp = percent of durability loss removed in the DurabilityPointsLoss funnel) |
| 1900075 | Paragon Soft Landing (milestone 875: server-only marker, §1k — bp = percent of fall damage removed in HandleFall) |
| 1900076–78 | Beyond Mastery trainer gates (milestones 900/925/950; MARKERS family — trainer_spell.ReqAbility1 per wave, prev rank in ReqAbility2) |
| 1900079–83 | Milestone 900 trainer ranks: Hammer of Wrath R8, Holy Shock R8 (main), Holy Shield R7, Lay on Hands R6, Blessing of Might R11 |
| 1900084–85 | Holy Shock R8 hidden sub-spells (damage / heal; spell_ranks chains on 25912/25914 — the core's spell_pal_holy_shock resolves them by rank) |
| 1900086–90 | Milestone 925 trainer ranks: Flash of Light R11, Avenger's Shield R6, Holy Wrath R6, Blessing of Wisdom R10, Retribution Aura R8 |
| 1900091–95 | Milestone 950 trainer ranks: Holy Light R15, Exorcism R11, Consecration R10 (burst totals entry 1528 in CONSECRATION_BURST), Devotion Aura R11, Shield of Righteousness R4 |
| 1900096–98 | Timeless Body I–III (codex node 51: §1g scaling markers — Player.cpp sums all five) |
| 1900099 | Petting Zoo mp5 carrier (codex regen aura — carries the SUMMED mp5 with Meditation via CastCustomSpell bp) |
| 1900100–03 | Toughness ranks 6–9 (milestone 1025, talent family) |
| 1900104 | Provocation (milestone 1050: aura 152 detected-range buff + §1m levelDiff clamp) |
| 1900105 | ~~retired~~ (Leap of Devotion marker — minted then deleted when 1075 became a spell rank; do not reuse without checking character_spell leftovers) |
| 1900106 | Faithful Leap Rank 2 (milestone 1075, spell_ranks chain on 1900030) |
| 1900107 | Paragon Double Buckle (milestone 1100: hidden marker read by paragon_double_buckle.lua, §1o) |
| 1900108–09 | Stoicism ranks 4–5 (milestone 1125, talent family — first 3-rank talent extended: EXTENDED_TALENTS gate carries base = 3) |
| 1900110 | Paragon Gem Doubling Regen (milestone 1150: mp5 carrier aura, clone of 1900099 — each reconcile-owning module needs its own aura id) |
| 1900111 | Paragon Level 1175 (Beyond Mastery V trainer gate — MARKERS family) |
| 1900112–16 | Milestone 1175 trainer ranks: GBoW R6, GBoM R6, Redemption R8 (SpellClassSet set directly — stock rows rely on a hardcoded SpellInfoCorrections list), Holy Shield R8, Hammer of Wrath R9 |
| 1900117 | Paragon Solo Conqueror (milestone 1200: hidden marker, LEARNED_SPELL_SPECIALS) |
| 1900118 | Paragon Solo Conqueror Crit (milestone 1200: aura 163 carrier, misc 127, bp = whole crit-damage percent via CastCustomSpell — clone of the 1900110 profile) |
| 1900119–20 | Swift Retribution ranks 4–5 (milestone 1225, talent family — core-scripted talent: `spell_pal_swift_retribution` is bound as `-53379` = all ranks, and LoadSpellTalentRanks builds the chain from the Talent.dbc rank slots, so new ranks inherit the script with no core change) |
| 1900121–22 | Improved Blessing of Might ranks 3–4 (milestone 1225, talent family — second TWO-rank talent extended: EXTENDED_TALENTS gate carries base = 2) |
| 1900123 | Paragon Durability Immunity (milestone 1275: carries the STOCK aura 289 SPELL_AURA_PREVENT_DURABILITY_LOSS — honoured by `HasPreventDurabilityLossAura()` on the FIRST line of `Player::DurabilityPointsLoss`, before the §1j scaling, so it needs no core change and leaks no rounding) |
| 1900124–28 | Sudden Light ranks 1–5 (milestone 1325: BRAND-NEW talent 2286, passive proc-trigger, ProcChance 2/4/6/8/10) |
| 1900129 | Sudden Light buff (instant next Holy Light: aura 108 misc 10 SPELLMOD_CASTING_TIME −100%, 1 charge, 15s) |
| 1900130 | Paragon Skies of Azeroth (codex node 59 flight marker, read by core patch 1r) |
| 1900131 | Paragon Stat Scaling III (milestone 1350: bp 0 + die 1 = **1** effective level, vs 2 for 1900039/1900072) |
| 1900132–33 | One-Handed Weapon Specialization ranks 4–5 (milestone 1375, 13%/16%) |
| 1900134–35 | Two-Handed Weapon Specialization ranks 4–5 (milestone 1375, 8%/10%) |
| 1900136–37 | Combat Expertise ranks 4–5 (milestone 1375, 8/10 — all three effects moved together) |
| 1900138 | Ward of Ages (codex node 60) — aura 87 misc 127, DYNAMIC amount via CastCustomSpell(-rank), die 0 |
| 1900139 | Bulwark of Ages (milestone 1450) — aura 87 misc 127, FIXED bp -5 + die 0 |
| 1900140 | Fleet of Ages (codex node 61) — THREE effects: aura 129 / 130 / 209, all die 0, amounts via CastCustomSpell(bp0,bp1,bp2) |
| 1900141–45 | Beyond Mastery VI trainer ranks (milestone 1425) — Avenger’s Shield R7, Holy Wrath R7, Exorcism R12, Consecration R11, Holy Light R16 |
| 1900146 | "Paragon Level 1425" gate marker (GATE_1425) |
| 1900147–50 | Holy Guidance ranks 6–9 (milestone 1475, 24/28/32/36) |
| 1900151 | Touched by the Light rank 4 (milestone 1475, 80/40/80) |
| **1900152+** | free |

**Talent ids** (separate range — see §2g for why they must NOT be 1900xxx): stock max 2285; **2286** = Sudden Light (milestone 1325); **2287+** free.

## 2b. Custom glyph slot — milestone 225 (no core patch)

A seventh, major-only glyph socket in the center of the glyph flower, for all
classes. The 3.3.5 client caps real glyph slots at six (fixed update-field
block compiled into the client), so this slot never touches the stock glyph
protocol — a glyph's actual effect is just a passive aura, and we cast it
ourselves:

- **Server** `paragon/modules/paragon_glyph_slot.lua`: own CSMH prefix
  `ParagonGlyph` (client fn 1 = apply request `{item}`, server fn 1 = state
  `{aura, error?}` — both CSMH registries are multi-prefix, zero upstream
  edits). Validates milestone/major/class/possession, checks duplicates
  against the six stock sockets by reading update fields
  `PLAYER_FIELD_GLYPHS_1` = index **1318**+0..5 via `GetUInt32Value`,
  consumes the item, casts the glyph's passive aura, persists to
  `acore_ale.paragon_custom_glyph` (guid PK; created at module load),
  reconciles on the apply pass + level change. Overwrite-to-replace, old
  glyph lost — stock semantics. v1 limitation: shared across both dual-spec
  sets (stock slots swap per spec; ours doesn't).
- **Client** `Paragon\Paragon_GlyphSlot.lua` (+ toc line): socket built from
  the stock virtual `GlyphTemplate` + styled by the stock
  `GlyphFrameGlyph_SetGlyphType(socket, GLYPHTYPE_MAJOR)` helper, so the art
  is native. Positioned at the flower center `("CENTER", -13, 17)` — the
  stock sparkle animations' shared origin. The template's own scripts MUST be
  overridden after CreateFrame (they call `GetGlyphSocketInfo` with a fake id
  and error — OnUpdate also indexes `slotAnimations[id]`). Hidden until
  `ParagonRewardTrackData.currentLevel >= 225`; refresh rides a
  `hooksecurefunc` on `GlyphFrame_Update`. Drag-only application (use-item
  spell-targeting mode is not supported).
- **Data** `paragon/modules/paragon_glyph_data.lua` generated by
  `Tools/gen_glyph_data.py`: item_template class-16 rows joined through the
  client Spell.dbc (effect 74 → EffectMiscValue = property) to
  GlyphProperties.dbc (property → passive aura SpellId, TypeFlags bit 1 =
  minor). Spell.dbc column indices come from the spell_dbc SQL schema's
  ordinal positions (same positional trick as the unified generator). 348
  usable glyphs (283 major / 65 minor — WotLK really does have ~4× more
  majors; verified against known minors). Regenerate after glyph item
  changes: `python Tools/gen_glyph_data.py` + worldserver restart.

## 2c. AccountBound module (account-wide achievements/titles/reputations)

Third-party module (github.com/AlsoNotMehh/AccountBound) installed 2026-08-17
at `modules/AccountBound` (its own nested git repo — `git pull` there for
module updates; it does NOT match the `modules/mod-*` exclusion pattern of the
core snapshot, so a future `git add -A` would record it as a gitlink: fine).
Config: `env/dist/etc/modules/AccountBound.conf` — enabled categories:
**Achievements** (SyncRealmFirst=1 per owner request), **Titles**
(SyncRealmFirst=1), **Reputations**. Mounts/Pets/Professions/Friends are OFF
until requested.

**Verified working 2026-08-17**: startup backfill unioned the owner account to
17/17/17/17 achievements, added 3,426 titles and 126,893 reputation rows
realm-wide (1.16M achievement inserts total — bot accounts hold many
characters). NOTE: the backfill runs on EVERY boot and blocks the world loop
~3 minutes on this dataset (it logs its summary only when FINISHED, minutes
after "World Initialized" — don't diagnose it as dead before then). All three
`StartupBackfill` keys are therefore now **0** (migration done; live sync +
on-create seeding cover steady state). To force a re-union: set them to 1,
restart once, set back to 0. The deployed .conf must carry the FULL key set —
the loader does not fall back to the .dist once a .conf exists (harmless
"Missing property" spam + code defaults otherwise).

Why it passed review (assessed before install):

- **Silent SQL materialization**: on completion it `INSERT IGNORE`s rows into
  the OTHER characters' `character_achievement` (and title blob / reputation
  rows) — never the live API. So no reward/title/mail duplication, no login
  toasts, and — critical for us — **no ALE achievement event fires for synced
  copies → no paragon XP flood, and achievement paragon XP is automatically
  once-per-account** (a synced row pre-completes the achievement on the alt,
  so the alt can never re-earn it).
- AC's `AchievementMgr::SaveToDB` is INCREMENTAL (only `changed` rows), so
  silent inserts survive even for online characters; they become visible at
  the target's next login. Startup backfill re-unions everything each boot.
- All hooks it uses exist in the playerbot fork (verified), constructor hook
  lists done correctly, startup backfill + on-create seeding built in,
  faction-pair conversion via the core's FactionChangeAchievements table.

**Local patch in `modules/AccountBound/src/Titles.cpp`** (2026-08-17, found
via a title that refused to propagate): the stock module pushes titles
outward only on the EARNER's save, gated one-shot against a login snapshot
(`SyncTitlesFromPlayerToAccount`, snapshot-equality early return). A target
online during that single push has the DB write overwritten by its own next
full character save (knownTitles is part of the characters row, unlike the
incremental achievement save), and there is NO retry or inbound path — the
title becomes unreachable for that character (`.save` re-runs are no-ops).
Patch: `MergeAccountTitlesIntoPlayer` + `PLAYERHOOK_ON_LOGIN` — on every
login the account's title union is applied to the LIVE player object
(SetTitle), making sync order-independent; the player's own save persists
it. Worth offering upstream. Module updates (`git pull` in the module dir)
will conflict here — re-apply from this note.

Known sharp edges (accepted for now):

- **Alt-bots earn achievements too**: a bot-earned achievement syncs to the
  main, which then can never earn it itself — with per-character paragon that
  achievement's XP is effectively burned for the main. Fix when playerbot
  work resumes: a socket-null guard in the module's
  `OnPlayerAchievementComplete` (bots never write the account layer).
- A union of achievements from several characters can complete a META for
  real on an alt at login (criteria engine sees the synced sub-achievements)
  — reward + paragon XP fire once for it. Treated as intended semantics: the
  account collectively earned the meta.

## 2d. Account-wide paragon (2026-08-17)

The upstream system ships a complete account mode: `LEVEL_LINKED_TO_ACCOUNT`
in `acore_ale.paragon_config` (now **1**) routes level/XP load+save to
`acore_ale.account_paragon` (account_id PK) while **statistics always stay
per-character** — exactly the chosen policy (shared level, per-character
allocations; points are derived at load as level − spent, so per-character
spending needed zero work). Migration seeded each account with its BEST
character's row (owner account = 258 from Ggfsreg; other characters'
progress absorbed). Bots turned out to have no paragon rows at all.

Local additions that make the mode safe (all tagged "account-wide paragon"
in-source):

- **mod-ale `Player:IsPlayerBot()`** (PlayerMethods.h + LuaFunctions.cpp
  registration): `GetSession()->IsBot()` — the playerbot fork's own session
  flag. Detects alt-bots AND world bots, no mod-playerbots coupling.
- **paragon_hook.lua**: (1) bot gate in `UpdatePlayerExperience` — bots never
  collect paragon XP (accepted side effect: a bot completing an achievement
  first voids that achievement's XP account-wide); (2) bot gate in
  `OnPlayerLogout` — a bot's logout must never `Save()`: with account mode
  its stale level/XP copy would OVERWRITE the row the real player advanced.
  This is the data-loss trap in the stock account mode.
- **paragon_rework_party.lua**: bot gate in `GrantShare` (that path raises
  the mediator directly, bypassing the hook's gates).
- **paragon_account_gate.lua** (new module): sub-80 characters get NO
  benefits — forces the statistics apply-pass to a remove-pass and blanks
  client point-spend requests via the two OnBefore mediator events. The
  paragon object keeps the true account level (never mutated) so saves stay
  correct; on reaching 80 the normal refresh activates everything.
- **Inline level-80 gates** in handlers outside the apply chain: extended
  talents (event 74) + Consecration burst (event 5) in the track module,
  and `Owed()` in the glyph module.

Known cosmetic gap: sub-80 characters still SEE the account level and point
total in the paragon window (spending is server-refused). Client-side hiding
below 80 is an optional polish pass.

## 2e. Custom achievements — solo dungeon clears (2026-08-19, no core patch)

96 real in-game achievements in their own **"Solo Clears"** subtab under
Dungeons & Raids (category 15200, parent **168**): one per dungeon
(**19000 + LFG dungeon id**, so 19001-19276), three era metas (19301
Classic / 19302 Outland / 19303 Northrend) and the capstone **19304
"Pinnacle"**, which grants the `%s the Pinnacle` title (CharTitles id 201,
mask 144) through `achievement_reward`. Generated by
`Tools/paragon_client_patch.py` (SOLO_ACH_* constants + SOLO_DUNGEONS).

**No rebuild — both sides are data.** The client gets patched
Achievement.dbc / Achievement_Criteria.dbc / Achievement_Category.dbc in
the locale MPQ; the server merges the SAME rows from the
`achievement_dbc`, `achievement_criteria_dbc` and
`achievement_category_dbc` override tables (`DBCStores.cpp` LOAD_DBC),
which is why the server side is pure SQL.

**Metas complete natively.** Each meta carries one **type 8**
(`ACHIEVEMENT_CRITERIA_TYPE_COMPLETE_ACHIEVEMENT`) criterion per member
achievement and the capstone one per meta:
`AchievementMgr::CompletedAchievement` fires
`UpdateAchievementCriteria(COMPLETE_ACHIEVEMENT, id)` (AchievementMgr.cpp:2341)
and `IsCompletedAchievement` requires ALL criteria when `Minimum_Criteria`
is 0 (AchievementMgr.cpp:2078-2096), so the chain resolves itself.
`Player::CheckAllAchievementCriteria` at login re-evaluates it.

**Granting** is Lua: `paragon_solo_dungeon.lua ReconcileAchievements`
calls `Player:SetAchievement` (ALE PlayerMethods.h:2202 →
`CompletedAchievement`, HasAchieved-guarded, so it is idempotent) for
every recorded clear, both at clear time and on every reconcile — the
latter is the retro-grant path for clears banked before the rows existed.
Achievements follow the REGISTRY, not milestone 1200, so they accrue below
the milestone. The core **refuses all grants while in GM mode**
(AchievementMgr.cpp:2287), so the module skips GM sessions entirely and
catches up on the next GM-off reconcile.

**Icons** are harvested at generation time from the stock achievement whose
name matches the dungeon (`Heroic: <name>` first for heroics), with a
lookup-override table for the names that differ (`The Stockade` →
`Stormwind Stockade`, Dire Maul wings → `King of Dire Maul`, Old Hillsbrad
→ `The Escape From Durnholde`, Black Morass → `Opening of the Dark
Portal`, `Magisters' Terrace` → `Magister's Terrace`, Pit of Saron / Halls
of Reflection → `The …`). The generator WARNS on any unmatched name rather
than silently shipping a wrong icon.

**The two-"Dungeons & Raids" trap (cost one deploy round):** the category
DBC has **two** rows with that exact name — **168** (parent −1, the
ACHIEVEMENTS tab, children 14808 Classic / 14805 TBC / 14806 Lich King
Dungeon / …) and **14807** (parent 1 = *Statistics*, children 14821
Classic / 14963 Secrets of Ulduar / …). Matching by name alone picks
14807 and silently parents the subtab under Statistics: the rows load
into the client fine, but the achievement UI never renders them and their
points count toward nothing — the exact symptom of "the client acts like
they don't exist". Only a **parent == −1** row is a valid achievement-tab
parent, because `Blizzard_AchievementUI.lua`'s
`AchievementFrameCategories_GetCategoryList` seeds the tree with the
parent −1 rows and then attaches each remaining category to a row already
in the tree (so a direct child of a top-level category always resolves,
regardless of file order). The generator now asserts `parent == -1`.
Useful trick: `Blizzard_AchievementUI.lua` extracts straight out of
`patch-enUS.MPQ` — read the client's own UI code instead of guessing.

**Deploy gotcha (2026-08-19):** the worldserver's `data/dbc` directory is a
**READ-ONLY docker volume** — the `docker cp CharTitles.dbc` path used for
the milestone-1000 Paragon title no longer works (that file is baked in at
143 records, including title 200). New titles go through the
`chartitles_dbc` override table instead; only ids NOT already in the baked
file may be listed there. Same applies to any future DBC the server needs.

## 2f. Big Game Hunter — milestone 1300 (no core patch, no spell id)

Each unique rare creature killed for the first time grants +10 armor,
+0.25 resilience and +0.25 haste. Registry, hooks and the live tooltip all
live in `modules/paragon_rare_hunter.lua`; the only new persistent state is
`acore_ale.paragon_rare_kills` (guid, entry).

**"Rare" is the core's own concept:** `creature_template.rank` **2**
(`CREATURE_ELITE_RAREELITE`) and **4** (`CREATURE_ELITE_RARE`) —
SharedDefines.h:2966-2968. Calibrated against Time-Lost Proto Drake (32491,
rank 2) and Bjarn / Ravasaur Matriarch / Bro'Gaz (rank 4); quest NPCs like
Knot Thimblejack are rank 0, world bosses rank 3 (excluded). ALE exposes
`Creature:GetRank()` (CreatureMethods.h:816 -> template rank). **426**
rares actually spawn (129 rare elite + 297 rare; 391 open world, 35 in
instances) -> a full sweep is +4,260 armor, +106 resilience, +106 haste.
The module reads that denominator live from the world DB at load rather
than hardcoding it.

**Rating granularity:** combat ratings are int32 end to end —
`Player::ApplyRatingMod` takes `int32`, and the ALE wrapper's `float`
argument is truncated at that call (PlayerMethods.h:5112-5121). 0.25 per
kill is therefore impossible to apply directly; resilience and haste bank
into **+1 every 4th kill** (the milestone-1200 crit-damage rounding again).
Armor at 10 apiece is exact. Both ratings apply to all three sub-ratings
per the codex convention.

**Hot-path note:** `PLAYER_EVENT_ON_KILL_CREATURE` fires for every kill by
every player, and this realm runs ~2500 bots — so the handler is ordered
cheapest-first, with the rank check rejecting ~99.9% of kills before any
bot test or DB touch. The event was already consumed by
`paragon_exp_drops.lua` and `paragon_rework_party.lua`, so this adds a
handler to an already-live dispatch rather than a new cost class.

## 2g. Brand-new talent — "Sudden Light", milestone 1325 (no core patch)

The first talent this project ADDS to a tree rather than extending. Talent
**2286** in paladin Retribution: critical strikes have a 2/4/6/8/10% chance
to make the next Holy Light instant. Generated by
`Tools/paragon_client_patch.py` NEW_TALENTS (client Talent.dbc row +
`talent_dbc` row + 5 rank spells + 1 buff + 1 `spell_proc` row). Entirely
data — no rebuild, worldserver restart only.

**Placement is dictated by mod-playerbots, not by aesthetics.**
`PlayerbotAIConfig::ParseTempTalentsOrder` sorts each tab by (Row, Col) and
maps the Nth CHARACTER of the premade spec-link string to the Nth sorted
talent. `playerbots.conf`'s paladin Retribution segments are exactly 26
characters for exactly 26 talents, so a new talent MUST SORT LAST or every
paladin bot's build shifts by one. Divine Storm sits at (10,1), so only
(10,2) and (10,3) are usable — this one takes **(10,2)**. The generator now
asserts both "cell free" and "sorts last".

**ROW ORDER IN Talent.dbc IS LOAD-BEARING — a new row cannot be appended.**
The file is strictly **grouped by TabID** (33 contiguous runs for 33 tabs)
and sorted by **(Row, Col)** within each run; paladin tabs occupy file
indices 663-688 (381), 689-714 (382), 715-740 (383). The client builds its
per-tab talent list from that grouping, so a row parked at EOF makes that
tab's ENTIRE TREE render empty — background art draws, zero buttons. This
happened live: the file validated perfectly (header, record bytes, string
block all correct) and the fault was purely positional. The generator now
SPLICES each new row directly after the last row of its own tab (2286 ->
index 689, right after Divine Storm), which conveniently also satisfies the
mod-playerbots sort-last rule. The server is unaffected either way — it
looks talents up by id through the DBC index table.

**The talent id must stay near the existing max (2285).**
`sTalentStore.GetNumRows()` returns highestId+1 and `Player::GetSpec` loops
it three times per call, so a 1900xxx talent id would allocate a ~15 MB
index table and inflate those loops ~830× on a 2500-bot realm. Spell ids
stay in the 1900xxx range; only the TALENT id is constrained.

**NEVER delete the talent row once anyone has learned it.**
`Player::_LoadTalents` asserts on the talent position and
ActivateSpec/resetTalents dereference it unguarded — removal means crashes
and failed logins, not graceful degradation. Strip with
`player:ResetTalents()`.

**The proc chain** (no C++ anywhere — Art of War and Surge of Light have no
scripts and no `spell_script_names` rows): five passive rank spells cloned
from Surge of Light rank 1 (33150) carry aura 42 PROC_TRIGGER_SPELL with
per-rank `ProcChance`; ONE negative `spell_proc` row (`SpellId = -1900124`,
`Chance = 0`) is copied per rank by `LoadSpellProcs`, which re-reads each
rank's own chance — so one row yields 2/4/6/8/10%. `HitMask = 2` is the
ONLY encoding of crit-only; leaving it 0 procs on every hit.
`ProcTypeMask = 0x11114` covers melee autos, dmg-class MELEE (Crusader
Strike, Divine Storm, Judgement's 54158), dmg-class **RANGED** (Hammer of
Wrath, Avenger's Shield — omitting 0x100 silently excluded a core execute),
dmg-class NONE, and dmg-class MAGIC. `Cooldown = 6000`: unlike Art of War
this mask includes AoE magic and procs are evaluated PER TARGET, so without
an ICD the real rate far exceeds the advertised 10%.

**The buff (1900129)** clones Art of War rank 2's buff (59578) — the
structural twin. Two corrections are mandatory when cloning it:
- `EffectSpellClassMaskA_2` must be zeroed. The column naming is
  letter = EFFECT index, number = DWORD index, so 59578's effect-1 mask is
  the triple (0x40000000, 0x2, 0) and that second bit is **Exorcism** —
  which is exactly why its tooltip reads "Flash of Light or Exorcism".
  Left in place, the clone also makes Exorcism instant.
- `ProcTypeMask` must be **0**. AzerothCore auto-generates a proc entry for
  any spell with proc flags, and `Player::RemoveSpellMods` explicitly SKIPS
  spells that have one — the charge would never drop and the buff would be
  permanent.
Kept from 59578: `AttributesEx6 = 0x40` (green floating combat text on proc
— without it the player sees NOTHING, because the aura-text cvar defaults
off) and `SpellVisualID_1 = 11955` (holy chest flash + HolyProtection.wav).

**Hiding it below the milestone is client-side only.** The server never
transmits the tree — only learned (talentId, rank) pairs — so it can make
the talent unlearnable but not invisible. `RequiredSpellID` (Talent.dbc
field 20) is a DEAD field: the DBC format string skips it, it is commented
out of `TalentEntry`, and all 892 stock rows are zero. Unmet prerequisites
are GREYED, never hidden. So `Paragon_TalentMask.lua` hides the button when
the reward's `talent` table carries `hidden = true`, and only when the
talent has **zero points** (hiding one with points spent would make an
inspected paladin's tab total stop adding up). Server refusal rides the
existing event-74 gate with `base = 0`, which now also messages the player
so a refusal without the addon is not mistaken for a bug.

**Player-facing feedback — note that WotLK has NO spell-activation
overlay.** The action-button glow and screen flare are a Cataclysm feature:
`SpellActivationOverlay.dbc` exists in no 3.3.5 archive and `Wow.exe`
contains none of the `SPELL_ACTIVATION_OVERLAY_*` event names, so FrameXML
could not register them even if the DBC were shipped. What the game gives
free is the buff icon + countdown, the floating text, the visual/sound, and
a Holy Light that fires with **no cast bar** (server-authoritative:
`CalcCastTime` clamps to 0 and `SendSpellStart` writes that timer).
`Paragon_HolyLightProc.lua` rebuilds the glow in Lua from art the client
already ships (`Interface\Cooldown\star4`,
`Interface\Buttons\IconBorder-GlowRing`), driven by UNIT_AURA. Action
buttons ARE protected, so it only parents textures and reads GetActionInfo
— whose 2nd return is a SPELLBOOK INDEX, not a spell id; the 4th return is
the real id.

### Recipe — adding the NEXT brand-new talent

1. **Pick the cell.** Dump the target tab from `Tools/cache/Talent.dbc` and
   list occupied (Row, Col). The cell must be free AND sort last by
   (Row, Col) within the tab — re-check the tab's segment length in
   `env/dist/etc/modules/playerbots.conf` against the tab's talent count
   first. The generator asserts both, so a bad choice fails the run rather
   than the realm.
2. **Pick ids.** Talent id = current max + 1 (NOT 1900xxx). Spell ids come
   from the 1900xxx ledger: N ranks + 1 buff if it is a proc talent.
3. **Add a `NEW_TALENTS` entry** in `Tools/paragon_client_patch.py`. For a
   proc talent, clone 33150 for the ranks and 59578 for the buff and honour
   both clone corrections in §2g (zero `EffectSpellClassMaskA_2/A_3`, zero
   the buff's `ProcTypeMask`). Use `$h` once in the shared description so
   every rank renders its own chance. Set `proc_type_mask` from the target
   spells' DBC **DmgClass**, not from intuition — check whether any of them
   are dmg-class RANGED (0x100).
4. **Gate it.** Add `EXTENDED_TALENTS[<id>] = { milestone = N, base = 0 }`
   and a TRACK row in `modules/paragon_rework_track.lua`, whose reward
   carries `talent = { tab, tier+1, column+1, base = 0, hidden = true }`
   (payload coords are 1-based; tab 382→1, 383→2, 381→3).
5. **Deploy.** CLOSE THE CLIENT (MPQ writes fail against a running client
   and the run aborts before the SQL apply), then
   `python Tools/paragon_client_patch.py --apply`, then
   `docker restart ac-worldserver`. Full client restart afterwards.
6. **Verify before playing:** the MPQ's Talent.dbc still has one contiguous
   run per tab, the new row sits inside its tab's run, and the tab's rows
   are sorted by (Row, Col). A blank tree in-game means this check was
   skipped.

## 2h. Teaching a class-locked SKILL — Codex node 56 "Sticky Fingers" (2026-08-19)

First **permanent** Codex node, and the first grant of a *skill* rather than a
stat. 25 points, cap 1, Mastery family. Teaches Lockpicking (633) + Pick Lock
(1804) to any class, starting at value 1 / max `level × 5` and ground up by
picking locks.

### The one thing that makes it work

Skill 633's class restriction is **pure data**: `SkillRaceClassInfo.dbc` row
**601** `(601, 633, RaceMask -1, ClassMask 8)` — the only row for the skill.
Four separate core paths would otherwise strip it, and **all four funnel through
`GetSkillRaceClassInfo`** (`DBCStores.cpp:943`):

| Path | What it does without the override |
|---|---|
| `Player::_LoadSkills` | deletes the `character_skills` row at login |
| `Player::CheckSkillLearnedBySpell` | drops spell 1804 at login |
| `Player::LearnDefaultSkill` | early-returns, grant never happens |
| `Player::UpdateSkillsForLevel` | skips the max-value update |

That function skips a mask test entirely when the mask is **zero**
(`if (mask && !(mask & bit)) continue`), so one override row with
`ClassMask 0` opens all four. DB rows override the DBC **by ID**
(`DBCDatabaseLoader.cpp:135-140`), and `SkillRaceClassInfoBySkill` is built
*after* the load — so row 601 is *replaced*, not duplicated. **Worldserver
restart required**; `.reload ale` does nothing for DBC stores.

### PATCH BOTH COPIES — the trap that cost a debugging round

The client ships its **own** `SkillRaceClassInfo.dbc` and filters its skill list
through it. Patching only the server produced: picking locks **worked**, but no
Skills-pane bar and **no skill-up messages** — because nothing in the core sends
`CHAT_MSG_SKILL` (it exists only as an enum); the client generates that line by
diffing its own list. Meanwhile `Spell::CanOpenLock` reads the *server's* value,
so the ability itself was fine.

> **Server-side success + client-side invisibility = the two DBC copies
> disagree.** Any gate change must be mirrored into `patch-enUS-5.MPQ`.

`Tools/paragon_client_patch.py` now emits both halves side by side.

### What stays locked

Trainers still refuse Pick Lock to non-rogues, via **two independent gates**:
`Trainer::IsTrainerValidForPlayer` (`Trainer.cpp:226`, `player->getClass() ==
Requirement`; trainer 9 requires class 4) and, inside
`IsSpellFitByClassAndRace`, the SkillLineAbility ClassMask test that runs
**before** the `GetSkillRaceClassInfo` test — SkillLineAbility 8439 keeps
`ClassMask 8`. Verified in-game. Playerbots are unaffected:
`SetRandomSkill(SKILL_LOCKPICKING)` sits inside `case CLASS_ROGUE:`.

### The `permanent` node concept

`permanent = true` must be honoured in **both** `HandleRefund` *and* the
`respec` branch — guarding one leaves the other as a free rebate (respec was
`SetData(RANKS_KEY, {})`, which would hand back the points while the skill
stayed learned). Non-refundability is not flavour: ALE exposes **no**
skill-removal method, and the core's only removal idiom (`SetSkill` with
currVal 0) cascades through every `SkillLineAbility` on the line calling
`removeSpell`.

`kind = "skill"` is deliberately a **no-op in `ComputeMods`** — this is one-way
persistent character state, not a reversible modset. It is applied by
`ReconcileGrants`.

### Review-caught, worth keeping in mind for the next one

- **Refuse the sale to anyone who already owns the grant.** A rogue has skill
  633 + spell 1804 already, so both `ReconcileGrants` guards no-op while the 25
  points stay spent. `HandleBuy` checks before charging.
- **Never paper over a failed grant.** A `SetSkill` fallback that fires when the
  gate is closed fabricates a row `_LoadSkills` deletes at the next login —
  turning a loud failure into a silent one. A missing skill right after a
  *fresh* `LearnSpell` is a precise "override row is not live" probe; log it.
- **Confirm irreversible purchases client-side.** Every other node is
  right-click refundable, so a misclick was always free.
- **The self-heal restores existence, not value.** After a deletion,
  `max(1, GetSkillValue(633))` reads 0 and re-seeds at 1 — losing the override
  row costs the player the whole grind.
- See **§1q** for the Death Knight ordering bug this node exposed.

### Lock values (for calibrating any future skill grant)

Max skill = `level × 5` = 400 at 80. Of 388 `Lock.dbc` records, 50 have a
picklock case; highest *reachable* requirement is **400** (Titanium Lockbox),
highest on a spawned object **385**. Two locks require 5000 = deliberate
"never pickable" sentinels. Only **4** locks accept skill 1 (ids 5, 202, 203,
319 — Practice Lock, Battered Junkbox, Small Locked Chest, Ornate Bronze
Lockbox), which is the bootstrap set. `SkillChance.Orange = 100` on this realm
means a **guaranteed** +1 below skill 26; then 75% to 50, 25% to 100, and 0
from 101.

## 2i. Teaching class-locked SPELLS — Codex node 57 "Ley Line Atlas" (2026-08-19)

Second permanent node and the first **multi-rank** one: 6 ranks, 10 pts each
(60 total), each rank teaching the next stock mage city teleport **for the
player's own faction**.

| Rank | Alliance | Horde |
|---|---|---|
| 1 | Stormwind 3561 | Orgrimmar 3567 |
| 2 | Ironforge 3562 | Undercity 3563 |
| 3 | Exodar 32271 | Silvermoon 32272 |
| 4 | Darnassus 3565 | Thunder Bluff 3566 |
| 5 | Theramore 49359 | Stonard 49358 |
| 6 | Shattrath 33690 | Shattrath 35715 |

Rank order is the `spellLevel` ladder (20/20/20/30/35/60) **and** matches
`acore_world.player_factionchange_spells` index-for-index, so a paid faction
change swaps each granted teleport to its counterpart and the rank stays
coherent. Resolve the list live from `GetTeam()`; **never persist a faction**
on the grant.

### The gate

All twelve sit on **SkillLine 237 "Arcane"**, `SkillLineAbility ClassMask 128`,
`AcquireMethod 0`. `Player::CheckSkillLearnedBySpell` would refuse them for any
non-mage at every login, so one override row opens it:

```sql
DELETE FROM acore_world.skillraceclassinfo_dbc WHERE ID = 55;
INSERT INTO acore_world.skillraceclassinfo_dbc
  (ID, SkillID, RaceMask, ClassMask, Flags, MinLevel, SkillTierID, SkillCostIndex)
  VALUES (55, 237, 0, 0, 1040, 0, 0, 0);
```

> **`Flags` MUST stay 1040** (`0x400 MONO_VALUE | 0x10 ALWAYS_MAX_VALUE`).
> Mages hold skill 237 as a *real* skill, and `LearnDefaultSkill` /
> `UpdateSkillsForLevel` both branch on `ALWAYS_MAX_VALUE`. Copying row 601's
> `Flags = 128` would silently break mage Arcane skill maxing.

Three of the four `GetSkillRaceClassInfo` consumers are **inert** here, because
only mages ever hold skill 237 as a character skill (`playercreateinfo_skills`
gates it by classmask): `_LoadSkills`, `LearnDefaultSkill` and
`UpdateSkillsForLevel` all iterate skills the player *has*. Only
`CheckSkillLearnedBySpell` matters. Trainers stay shut — `IsSpellFitByClassAndRace`
tests the SLA `ClassMask 128` first.

### NO CLIENT HALF — and do not "fix" that

§2h's *patch both DBC copies* rule **does not extend here**, and this is the
most likely thing for a future reader to get wrong. That rule exists because a
granted **skill** is filtered through the client's own SRCI in the Skills pane.
This node grants only **spells** (`AcquireMethod 0`, so no skill is ever
created), and the client's spellbook-tab routine tests the SkillLineAbility's
own `ClassMask` **first** and short-circuits before SRCI — so the teleports land
in the **General** tab and a mirrored client row would be a no-op.
Field-corroborated: Pick Lock sits in General today despite its client SRCI row
already being opened.

**Rule of thumb:** mirror the client copy when granting a **skill**; don't
bother when granting only **spells**.

### The failure mode is worse than §2h's

Without row 55, `_LoadSpells` refuses the spell but the follow-up `removeSpell`
is a no-op, leaving an **orphaned `character_spell` row**. `ReconcileGrants`
re-learns it as `PLAYERSPELL_NEW`, and `_SaveSpells` emits a bare `INSERT` →
**duplicate key on PK (guid, spell)** → `ExecuteTransaction` rolls back the
character's **entire save transaction**, every save, forever. Hence the
load-time alarm in `paragon_codex.lua` that checks both rows 55 and 601 and
shouts. It was verified by deliberately breaking the row and watching it fire —
**an untested alarm is worthless**.

### Multi-rank permanent nodes — the trap

> **Never `break` the purchase loop because the NEXT rung is already known.**

Codex ranks are strictly sequential, so refusing one mid-ladder rung locks the
character out of every **higher** rank — and on a permanent node there is no
refund or respec to unwind it. The guard must block only when **nothing in the
rest of the ladder** remains to teach. The rank *is* the price (`SpendOn` sums
`RankCost` over `1..rank`), so a "free skip" cannot be expressed; paying for a
rung you already own is the far smaller harm.

This was caught by adversarial review while a planted test spell had already put
the live character into the triggering state. The test spell was swapped to
**Teleport: Dalaran 53140** — same skill line, same gate, *not* in the node's
list, so it exercises the identical code path without touching the ladder.

### Other things worth keeping

- **Faction:** ALE `Player:GetTeam()` returns `GetTeamId()` — `TEAM_ALLIANCE = 0`,
  `TEAM_HORDE = 1`. The adjacent `PVP_TEAM_*` enum is **inverted**
  (`PVP_TEAM_HORDE = 0`); confusing them silently swaps every faction gate.
- **A list node inverts the §2h scalar guard.** With neither `node.spell` nor
  `node.skill` set, `(not nil) and (not nil)` = true, which would make the node
  permanently unbuyable. Branch on `node.spells` first.
- **Declare a class refusal as node data** (`deniedClass` / `deniedText`) and
  push it, so the client can grey the plaque and skip the irreversible-purchase
  popup instead of the server denying *after* the player confirmed. The old
  message ("You already know the mage teleports") was also a lie for a mage who
  never trained Shattrath — the real reason is the class.
- **Usability (stock, deliberately not overridden):** each cast consumes a
  **Rune of Teleportation** (17031), takes **10 s**, and is blocked in combat and
  while moving. No cooldown. Overriding twelve stock spells to remove the
  reagent would change them for mages too and drag the client Spell.dbc back
  into scope.

## 2j. A LEVEL-DERIVED node — Codex node 58 "Paragon Ascendance" (2026-08-19)

1 rank, 10 pts, **refundable**. Grants the player a share of the stat gains
their **own class** earns across levels 1-80, scaled by paragon level:
`floor(gain x paragonLevel / 1000)`. 1000 paragon levels = exactly one
lifetime of level-ups. **No core patch, no DBC, no MPQ** - Lua + addon only.

### Where the numbers come from

`acore_world.player_class_stats` (Level 80 row minus Level 1 row). **Race is
deliberately absent**: `player_race_stats` is a flat per-race offset that
never varies with level, so the level-derived gain is purely class-based.

| Class | STR | AGI | STA | INT | SPI | HP | Mana |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 Warrior | 151 | 93 | 137 | 16 | 39 | 8101 | 0 |
| 2 Paladin | 129 | 70 | 121 | 78 | 84 | 6906 | 4334 |
| 3 Hunter | 54 | 158 | 107 | 70 | 76 | 7278 | 4981 |
| 4 Rogue | 92 | 166 | 84 | 23 | 47 | 7579 | 0 |
| 5 Priest | 23 | 31 | 47 | 152 | 158 | 6908 | 3790 |
| 6 Death Knight | 152 | 92 | 138 | 15 | 39 | 8101 | 0 |
| 7 Shaman | 99 | 54 | 115 | 107 | 121 | 6899 | 4311 |
| 8 Mage | 16 | 23 | 39 | 158 | 152 | 6931 | 3168 |
| 9 Warlock | 39 | 47 | 68 | 137 | 144 | 7113 | 3766 |
| 11 Druid | 68 | 62 | 78 | 121 | 137 | 7373 | 3436 |

**Death Knight has no level-1 row** - `player_class_stats` carries only levels
55-80 for class 6, because DKs start at 55. Its curve is however identical to
the warrior's everywhere the two overlap (level 55: 108/73/99/29/42 vs
109/73/100/29/42, `BaseHP` 1359 for both; level 80 `BaseHP` 8121 for both), so
the row above is the DK's own level-80 stats minus the **warrior's** level-1
baseline (23/20/22/20/20, `BaseHP` 20). Its own 55->80 gain (67/39/61/6/17)
would have made the DK roughly a third of every other class.

Zero mana for warrior/rogue/DK is **correct, not a hole**: those classes gain
no mana from levelling at all. They draw the three largest health numbers.

**Scale.** `PARAGON_LEVEL_CAP` is **10000** in both the Anniversary preset and
the live `acore_ale.paragon_config` row, so the node tops out at **ten**
lifetimes, not one. Owner-approved: "anything above 2000 is not realistically
achievable anyway."

### Mechanics

New `kind = "classlevel"` in `paragon_codex.lua`. `ComputeMods` gained a
`paragon` parameter (the level lives on that object, not the player).

- **`CLASS_LEVEL_KEYS` / `CLASS_LEVEL_GAIN` are ARRAYS, not hashes.**
  `SameMods` diffs the applied modset **positionally**, so emission order must
  be deterministic; a `pairs()` walk would churn the entire set - and with it
  the health/mana-preserving strip-and-reapply - on every single reconcile.
- Every key is emitted **unconditionally, including zeros**. Constant list
  length keeps `SameMods` diffing on amounts alone, and a 0 flat modifier is a
  no-op in `HandleStatFlatModifier`.
- `HEALTH` = UNIT_MOD 5, `MANA` = 6. `MANA` had **no prior precedent** in this
  codebase; verified at the source: `Unit.cpp:12101-12108` groups
  `UNIT_MOD_MANA` with the other powers and calls
  `UpdateMaxPower(GetPowerTypeByAuraGroup(unitMod))`, exactly parallel to
  `UNIT_MOD_HEALTH` -> `UpdateMaxHealth()`.
- Integer-exact by construction: `math.floor(gain x plevel / 1000)`. Worst
  case `8101 x 10000 = 81,010,000`, far inside a double's exact range.
- Icon `Spell_Holy_EmpowerChampion`, **verified present** by parsing
  `SpellIcon.dbc` out of `locale-enUS.MPQ` (2281 records). Both first guesses
  (`Achievement_Level_80`, `Spell_Holy_ProclaimChampion_02`) were **absent** -
  see the trap below.

### Three traps this node walked into

**1. `OnParagonLevelChanged` must reconcile - and must carry its own gate.**
The magnitude moves with paragon level, so the codex's level handler now calls
`Reconcile` and not just `PushState`; without it the bonus lags until the next
full statistics pass. **But that handler fires OUTSIDE the statistics apply
chain**, where `paragon_account_gate.lua`'s `OnBeforeUpdatePlayerStatistics`
apply=false flip never runs. Ungated, a paragon level-up would have applied
**the entire codex modset - all 38 nodes, not just 58** - to a sub-80
character on the account, defeating the whole point of the gate. The module's
own contract spells this out ("Handlers that fire OUTSIDE the apply chain
carry their own inline player-level gates"), and the cited precedent
(`paragon_ilvl_bonus.lua`'s `Owed()`) carries `player:GetLevel() >= 80`.
Fixed with the in-file event-44 gate: `if not (IsBot(player) or
player:GetLevel() < 80)`. `PushState` stays **ungated**, exactly as before.

`IsBot` had to be **hoisted** above the level handler: Lua resolves locals at
parse time, so a call from the earlier handler would have hit a nil *global*
and thrown inside a pcall that only prints.

**2. Mana leaked on every level-up.** `Reconcile` already preserved **health**
across the strip-and-reapply (lowering max health clamps current health).
**Mana has the identical problem and had no rescue** - it simply never
mattered before, because no modset changed often enough to notice. Node 58
changes on *every paragon level*, so a caster would have lost a level's worth
of mana each time. Added alongside the health rescue. **Mind the argument
order, it differs between the two calls**: `GetPower(type)` but
`SetPower(amount, type)` (`mod-ale .../methods/UnitMethods.h:810` and `:1543`).
POWER_MANA = 0; the `> 0` guard skips rage/energy/runic-power classes.

**3. Do NOT guess icon paths.** `mpyq`'s `read_file` returns `None` for
`Interface\Icons\*.blp` and these MPQs have **no `(listfile)`**, so both
obvious probes report MISS for icons that demonstrably work (e.g.
`INV_Enchant_EssenceEternalLarge`, live on node 55). Neither is evidence.
Parse **`DBFilesClient\SpellIcon.dbc`** from `locale-enUS.MPQ` instead
(WDBC, 2 fields: id + string-ref, 8-byte records) and match against its string
block - that is authoritative for every icon a spell can use.

### Client

Addon-only, **no MPQ rebuild** (the addon is a plain folder under
`Interface/AddOns`). `BonusText` treats `classlevel` like `collection`: the
magnitude is server-computed and arrives in the state push, so it must never
fall through to the `per x rank` default. Mastery plaque row 4 sits at
y -454..-498 in the x 272+ column, clear of the Wards flower (x <= 230), so
**no band-2 shift was needed** this time.

## 2k. A CORE-CAPABILITY node - Codex node 59 "Skies of Azeroth" (2026-08-19)

1 rank, **100 points**, **permanent/non-refundable**. Teaches server-only
marker spell **1900130**; all behaviour lives in core patch **1r**.

### The node is almost nothing

```lua
{ id = 59, family = "mastery", name = "Skies of Azeroth",
  icon = "Interface/Icons/Ability_Mount_Gryphon_01",
  kind = "skill", per = 1, cap = 1, cost = 100, permanent = true,
  spell = 1900130, teaches = "flight over Azeroth" },
```

`kind = "skill"` with **`node.skill` deliberately unset**. That single omission
is what makes it free of new code:

- `ReconcileGrants`' scalar arm collapses to *learn the marker if missing*, and
  the entire skill branch (the fresh-learn probe, the `SetSkill` seed) is
  skipped - those exist only for node 56's `character_skills` row.
- `HandleBuy`'s already-owned guard `(not node.spell or HasSpell(node.spell))
  and (not node.skill or HasSkill(node.skill))` collapses to
  `HasSpell(1900130)`.
- No `skillraceclassinfo_dbc` override and no `REQUIRED_SRCI` entry, because
  there is no skill line.
- Client side is **comment-only**: the `kind == "skill"` `BonusText` cap-1
  branch already renders Learned/Not learned, and the whole permanent-purchase
  path (tooltip swap, right-click swallow, `PARAGON_CODEX_PERMANENT` popup)
  keys purely off `def.permanent`.

### Marker must be LEARNED, not an aura

`_LoadSpells` runs before `_LoadAuras` at login (`PlayerStorage.cpp`), so a
learned marker is already present when the core next runs `CheckLocation`. An
aura marker loses that race. This is the standing house rule and it is exactly
why the node is `kind = "skill"` + `LearnSpell` rather than
`kind = "scaling"` + `AddAura`.

### The alarm - and the blind spot it closes

Node 59's alarm probes the **running spell store**, not just the DB:

```lua
local live = GetSpellInfo and GetSpellInfo(1900130)
```

The ALE global goes straight to `sSpellMgr->GetSpellInfo`
(`mod-ale methods/GlobalMethods.h:3527`) and returns nil for an unknown id.
This matters because **`spell_dbc` merges into the store at STARTUP ONLY** - a
row present in the DB but added since the last boot is *not live*, and the
older `REQUIRED_SRCI`-style DB-only check would call that healthy. Both probes
are combined so the message names the actual remedy ("just RESTART" vs
"re-apply the row"). **Verified by pointing it at a bogus id 1999999,
restarting, watching it fire with the correct branch, and reverting.**

Without it: `LearnSpell` grants nothing, `HasSpell(1900130)` is never true, and
100 non-refundable points buy a silent no-op.

### Deployment

Core rebuild **and** a full worldserver restart (not a reload - `spell_dbc`
merges at startup). Marker created by surgical SQL clone of 1900107; the
matching `SERVER_SPELLS` entry in `Tools/paragon_client_patch.py` is SQL-only
and is never staged into the MPQs, exactly like 1900107/1900117.

**The MARKER needs no client work - but THE FEATURE DOES.** The client refuses
to send the cast on maps 0/1, so `Tools/paragon_client_patch.py`'s azeroth
flight pass must also have run and the rebuilt MPQs must be in place (client
fully closed for the rebuild, full client restart after). See 1r. Deploying the
core patch alone leaves the node bought, the marker held, and flight still
impossible to initiate - the only visible symptom being that a flying mount
ridden IN from Outland keeps working while it cannot be recast.

## 2l. Milestones 1350 + 1375 (2026-08-19)

Two stock-shape milestones, both following existing recipes. TRACK is now 55
entries, max level 1375.

### 1350 "Ageless Might" — third stat-scaling marker

Universal. Marker **1900131** (clone of 20266, `EffectBasePoints_1 = 0`, so
bp 0 + die 1 = **1** effective level, unlike 1900039/1900072 which carry bp 1
= 2 each). Milestone total is therefore **−5**, plus up to 3 more from the
codex's Timeless Body ranks.

**!! ONE MARKER, THREE REGISTRIES !!** A scaling marker is inert unless its id
appears in *all three*:

| where | why |
|---|---|
| `Player::GetStatScalingLevel` (`Player.cpp:5291`) | the core's loop names its markers **explicitly** — a spell row alone does nothing |
| `SPECIAL_AURAS` in `paragon_rework_track.lua` | grants the aura at the milestone |
| `SPELLS` in `paragon_scaling_level.lua` | **the easy one to miss.** `State()` builds a string from this list; omit the new id and the string never changes when the aura lands, so `Sync()` early-returns and **no stat recompute happens** — the milestone silently does nothing until some unrelated event pokes stats |

The core one means this milestone needs a **rebuild**, not just data.

**Client tooltip factors must be recomputed too.** `Paragon_ScalingLevel.lua`
`RATING_FACTOR` and `Paragon_SpiritTooltip.lua` `SCALING_MANA_FACTOR` are keyed
by the *total* reduction, so both needed a `[5]` entry:

- `RATING_FACTOR[5] = 1.441893` — `gtCombatRatings` `ratio(80) / ratio(80-n)`
- `SCALING_MANA_FACTOR[5] = 1.293572` — `gtRegenMPPerSpt`, same rows, *not* inverted

Method verified by reproducing the existing entries exactly (1.157674 at −2 and
1.340208 at −4; mana 1.1085 / 1.2287). Reachable totals are only {2, 4, 5}
because milestones unlock in order, so there is no gap at 3.

### 1375 "Master at Arms" — three talent caps +2

Paladin. All three are **3-rank** talents, so every gate carries `base = 3`.

| talent | id | payload coords | ranks 1–3 | new 4–5 |
|---|---|---|---|---|
| One-Handed Weapon Specialization | 1429 | tab 2, tier 6, col 3 | 4/7/10% | **13/16%** |
| Two-Handed Weapon Specialization | 1410 | tab 3, tier 5, col 1 | 2/4/6% | **8/10%** |
| Combat Expertise | 1753 | tab 2, tier 8, col 3 | 2/4/6 | **8/10** |

Combat Expertise uses the per-field dict form (like Divinity / Seals of the
Pure) because it has **three** effects — aura 240 `MOD_EXPERTISE`, 137
`MOD_TOTAL_STAT_PERCENTAGE` and 290. Bumping only `EffectBasePoints_1` would
raise the expertise and silently leave the stamina behind.

**!! THE CONFIG VALUE IS THE DISPLAYED NUMBER, NOT THE BASE POINT !!** The
generator stores `bp = value - 1` because every clone keeps `die = 1`. Verified
against Divine Strength: config `18` → stored bp `17`. Writing raw base points
ships every rank one short and breaks the talent's arithmetic step — this was
caught only by reading the rows back out of `spell_dbc` after the first
`--apply`, so **always verify `bp + die` against the intended display**.

No new `Talent.dbc` rows were added (only rank columns 4–5 on existing rows),
so the contiguity / tab-sort checks in the new-talent recipe do not apply here.

### Deploy used

Core rebuild (for the `GetStatScalingLevel` list) + `python
Tools/paragon_client_patch.py --apply` with the client closed + worldserver
restart + full client restart.

## 2m. Milestone 1400 "Racially Ambiguous" (2026-08-20)

One **active** racial ability of another race, freely swappable out of
combat. TRACK is now 56 entries, max level 1400. **No core patch, no
rebuild, no new spell ids** — the ledger still starts free at 1900138.

### The twelve picks

Classified from `SPELL_ATTR0_PASSIVE` (Spell.dbc **field 4**, pinned by
positive *and* negative controls), never from memory. **Twelve, not ten:**
Dwarf and Undead each have two actives, and Human **Perception (58985) is a
PASSIVE** in 3.3.5 so it does not qualify.

| race | pick(s) |
|---|---|
| Human | Every Man for Himself |
| Orc | Blood Fury (3 class variants) |
| Dwarf | Stoneform, Find Treasure |
| Night Elf | Shadowmeld |
| Undead | Will of the Forsaken, Cannibalize |
| Tauren | War Stomp |
| Gnome | Escape Artist |
| Troll | Berserking |
| Blood Elf | Arcane Torrent (3 resource variants) |
| Draenei | Gift of the Naaru (7 family variants) |

### !! GRANT THE ORIGINALS, DO NOT CLONE !!

Cloning is the obvious plan and it is **wrong here** — the actives are
precisely the racials the core hardcodes **by spell id**:

- **Every Man for Himself**: the CC break is not spell data at all.
  `IMMUNE_TO_MOVEMENT_IMPAIRMENT_AND_LOSS_CONTROL_MASK` is assigned by
  literal id in `Spell.cpp:6901` **and** `SpellInfo.cpp:2471`, plus a
  `SPELL_ATTR2_NO_SCHOOL_IMMUNITIES` fix in `SpellInfoCorrections.cpp:5189`.
  A clone breaks free of **nothing**.
- **Gift of the Naaru**: all seven rows are byte-identical
  (`eff 6 / bp 9 / aura 8`). The whole heal comes from
  `spell_gen_gift_of_naaru` switching on the **spell’s own
  `SpellFamilyName`** (Spell.dbc field 208) — WARRIOR/HUNTER/DEATHKNIGHT
  scale off AP, MAGE/WARLOCK/PRIEST off SP, PALADIN/SHAMAN off max(SP, AP).
  A clone tagged GENERIC hits the script’s `default: break` and heals **0**.
- **Blood Fury / Arcane Torrent** variants differ for real (AP+RAP vs
  AP+spellpower vs spellpower; 15 energy vs 6% mana vs 150 runic).

Granting originals resolves every variant natively. The three classes with
no stock row inherit the family that matches how they scale: Rogue → the
warrior GotN row, Warlock → the priest row, Druid → the paladin row.

### The gate: ten `skillraceclassinfo_dbc` override rows

`Player::CheckSkillLearnedBySpell` (`Player.cpp:3203`) runs from
`_LoadSpells` at **every login** and deletes any spell whose
`SkillLineAbility` skill line fails `GetSkillRaceClassInfo`. Every racial
hangs off its own race’s line, so without help a granted racial works
perfectly **until the next relog** and then vanishes — leaving an orphaned
`character_spell` row that the next save re-INSERTs, rolling the
character’s **entire save transaction** back, every save, forever. Same
failure mode as codex §2h/§2i, hence the load-time alarm (**tested by
deliberately breaking two rows and watching it name both**).

Rows are the stock rows with `RaceMask` widened to 0 and **nothing else
touched**. `ClassMask` is already 1535. **Flags differ per line and must be
copied verbatim** — 1170 for seven lines, **146** for Orc/Blood Elf/Draenei
(`GetSkillRangeType` branches on MONO_VALUE / ALWAYS_MAX_VALUE, and a
non-zero `SkillTierID` would flip it to `SKILL_RANGE_RANK`):

| ID | skill | race | Flags | | ID | skill | race | Flags |
|---|---|---|---|---|---|---|---|---|
| 68 | 101 | Dwarf | 1170 | | 841 | 733 | Troll | 1170 |
| 71 | 124 | Tauren | 1170 | | 861 | 753 | Gnome | 1170 |
| 70 | 125 | Orc | **146** | | 862 | 754 | Human | 1170 |
| 69 | 126 | Night Elf | 1170 | | 867 | 756 | Blood Elf | **146** |
| 72 | 220 | Undead | 1170 | | 877 | 760 | Draenei | **146** |

### !! NO CLIENT MIRROR — THIS DELIBERATELY INVERTS THE §2h RULE !!

§2h says to mirror a gate row into the MPQ when granting a **skill**,
because the Skills pane filters through the client’s own SRCI. These
racials carry **AcquireMethod 2** (`LEARNED_ON_SKILL_LEARN`), so `_addSpell`
really *does* create the foreign racial skill line server-side — and all
ten lines sit in **SkillLineCategory 9 "Secondary Skills"**, the same
category as First Aid and Riding, i.e. **displayed**. Leaving the client
copy race-locked is what keeps "Racial - Troll" **out of** a Draenei’s
Skills pane while the server keeps the row that makes the spell survive
login. Mirroring would *add* the clutter. **That is also why this milestone
needs no MPQ rebuild at all.**

### The sibling leak, and why the scrub is a single call

Opening a line is not surgical: `LearnSpell` → `_addSpell` →
`LearnDefaultSkill` → `SetSkill` → `learnSkillRewardedSpells` grants every
sibling on the line whose **own `RaceMask` is 0**. Checked all ten: eight
leak nothing (every ability is race-tagged). Only **756** leaks Magic
Resistance (822) + Arcane Affinity (28877) and **760** leaks Gemcutting
(28875).

Teardown uses **`SetSkill(line, 0, 0, 0)`**, whose removal branch walks
`GetSkillLineAbilitiesBySkillLine` and drops the skill *and every spell on
it* in one call — old pick and leaked siblings together. The player’s own
line is excluded from that loop, which is what keeps a real Blood Elf’s
Magic Resistance safe. Stripped leaks stay stripped: the only paths that
re-run `learnSkillRewardedSpells` are `SetSkill` (ours) and `UpdateSkillPro`
(use-based skill-ups, which racial lines never get).

### Swap guard

Unlearn/relearn **clears the spell’s cooldown**, so an unguarded swap is a
free cooldown reset. The request is refused while the **currently held**
pick is on cooldown (`HasSpellCooldown`) — which closes it completely,
since the player must wait the cooldown out either way — plus an
out-of-combat check.

### Files

- `modules/paragon_racial_pick.lua` (new, prefix `ParagonRacial`), table
  `acore_ale.paragon_racial_pick (guid, pick_key)`, self-creating.
- `modules/paragon_rework_track.lua`: TRACK row 1400. The `RACIAL_PICK`
  SPECIAL is **informational only** — `ApplyReward` skips SPECIAL and no
  registry entry exists, exactly like `GEM_DOUBLE`; the module gates on
  `paragon:GetLevel() >= 1400` like `paragon_solo_dungeon.lua`.
- `Paragon_RacialPick.lua` (new client file, added to `Paragon.toc`) +
  `Paragon_RewardTrack.lua` (click-vs-drag on the node, tooltip line, icon
  swap). **The server ships only ids** — every option is a real stock
  spell, so the client resolves name/icon via `GetSpellInfo` and the
  tooltip via `SetHyperlink`.
- `Tools/paragon_client_patch.py`: the ten SRCI rows (server half only).

**Node icon** `Interface/Icons/Ability_Racial_Avatar` (verified present in
`SpellIcon.dbc`, unused elsewhere). Deploy: patcher `--apply` + worldserver
restart + client `/reload` (addon Lua only; no MPQ change).

## 2n. Milestone 1500 "The Heavens Take Notice" — first ITEM payout (2026-08-20)

The track’s first reward that hands over a real item: a **Celestial Steed**
(item 54811), **mailed**. TRACK is now 57 entries, max level 1500. Pure Lua
+ data — no core patch, no MPQ, no new spell ids.

### Why mail rather than bags

`AddItem` fails on a full inventory and there is no second chance — the
crossing does not repeat. `SendMail` is a **global** taking a receiver GUID,
so it works regardless of bag space, and it carries flavour text. It also
builds the item through the core (`Item::CreateItem` inside ALE’s
`SendMail`), so the template’s own `spellcharges_1 = -1` is copied
automatically — **this is why the payout cannot repeat the "no charges
remain" trap** from the hand-written `mail_items` rows.

Stationery **61 = MAIL_STATIONERY_GM** (parchment envelope). Sender guid
stays **0**: ALE’s `SendMail` hardcodes `MAIL_NORMAL`, so a creature sender
is not expressible, and guid 0 renders with no sender name — which the GM
stationery makes read as official rather than broken. A named sender would
need a dedicated character to mail from.

### !! DELIBERATELY NOT LEDGERED (user decision) !!

Every other one-way reward here is reconciled from state; an item cannot be.
The choice was a per-account mirror (as in `paragon_collection_rewards.lua`)
or nothing, and **nothing wins on this server**:

- the crossing test `old < level <= new` matches **exactly once** per real
  progression, and a lump XP grant still produces one event whose old/new
  span the threshold — no cascade double-pays;
- only one character per account can be online, so the **account-wide**
  paragon level cannot be crossed twice at once;
- a real player never de-levels, and a ledger would actively **break** the
  re-test loop below.

If idempotency is ever wanted, the mirror-table pattern drops straight in.

### !! `.paragon setlevel` RAISES NO EVENT !!

`setlevel` writes the level straight onto the object
(`paragon_admin.lua`) and **never raises `OnParagonLevelChanged`**, so it can
neither pay nor un-pay. Only `addxp`/`addlevel` go through
`GrantExperience`, which does. Setting the level straight to 1500+ therefore
pays **nothing** — the single easiest way to believe this module is broken
when it is not.

Re-test loop:

```
.paragon setlevel 1499      -- silent, no crossing
.paragon addlevel 1         -- real crossing -> mail arrives
```

**Prefer testing at the CURRENT level instead** (temporarily point `PAYOUTS`
at `<current level + 1>` and `.paragon addlevel 1`): dropping to 1499 makes
codex spend exceed the level pool for as long as it lasts, and the admin
command itself warns a relog is needed to strip session milestones.

### Lua 5.2: `unpack` is nil

ALE builds against **lua52** (`mod-ale/CMakeLists.txt`), where the global
`unpack` moved to `table.unpack`. `SendMail(unpack(args))` shipped once and
**ate a live crossing** — the handler is pcall-wrapped, so the error reached
only the worldserver log while the player got no reward and no message.
Alias first, as `lib/Mediator/mediator.lua:18` does:

```lua
local unpack = unpack or table.unpack
```

The module now also messages the player when a payout errors, because a
swallowed failure on a non-repeating crossing is otherwise undetectable
in-game.

### Where the data lives

`PAYOUTS` in **`modules/paragon_milestone_items.lua`** is the single source
of truth (any level, up to 12 items — `MAX_MAIL_ITEMS`). The TRACK row at
1500 is **informational only**, exactly like `GEM_DOUBLE`, `SOLO_DUNGEON`
and `RACIAL_PICK`: the track module has **no DB or mail layer of its own**
(it is pure in-memory, with no `CharDBQuery`/`IsBot` anywhere), so payout
data lives with the payout code rather than being duplicated across both.

A load-time alarm verifies every payout entry exists in `item_template` —
a bad entry makes `SendMail` raise a Lua error *at the crossing*, which is
the worst possible moment since the crossing never repeats. **Tested by
pointing the payout at entry 9999999 and watching it fire.**

Icon `Interface/Icons/Ability_Mount_CelestialHorse`, resolved from
`Spell.dbc` field 133 → `SpellIcon.dbc` (never guessed).

## 2o. Damage reduction — codex node 60 + milestone 1450 (2026-08-20)

Two independent sources of flat percentage damage reduction. Codex is now
**40 nodes**; TRACK is **58 entries**, max level 1500. Lua + two spell rows;
no core patch, no MPQ, no rebuild.

- **Codex node 60 "Ward of Ages"** — mastery, **1% per rank, flat 25 points
  each** (no `step`), cap 10, refundable. Aura **1900138**.
- **Milestone 1450 "Bulwark of Ages"** — universal, flat **−5%**. Aura
  **1900139** via `SPECIAL_AURAS`.

### The mechanism

`SPELL_AURA_MOD_DAMAGE_PERCENT_TAKEN` (**aura 87**), `EffectMiscValue` =
school mask (**127** = all seven). The core reads it in exactly **two**
places:

| call site | covers |
|---|---|
| `Unit::SpellDamageBonusTaken` | spell hits **and periodic/DoT ticks** |
| `Unit::MeleeDamageBonusTaken` | melee and ranged |

So it covers all incoming combat damage. **Environmental damage (falling,
lava, drowning) bypasses both.**

### !! STACKING IS MULTIPLICATIVE !!

`Unit::GetTotalAuraMultiplier` runs `AddPct(multiplier, amount)` once per
aura, so every aura-87 effect multiplies:

- node rank 10 + milestone: `0.90 × 0.95 = 0.855` → **14.5%**, not 15%
- add Imp. Righteous Fury (−6%) and Shield of the Templar (−3%):
  `× 0.94 × 0.97 = 0.7796` → **22.04%**, not 24%

The useful framing: each source always removes its own percentage of
*whatever still gets through*, so its relative value never degrades — only
the headline sum looks smaller. Accepted by design (user: *"i dont care if
its additive or multiplicative"*), and keeping the two auras separate means
neither module has to know the other exists.

The one escape hatch: spells sharing a `spell_group` whose stack rule is
**3 = `EXCLUSIVE_SAME_EFFECT`** registered for that aura type collapse to the
highest in the group (`SpellMgr::AddSameEffectStackRuleSpellGroups`). Custom
spells belong to no group, so they always stack.

### Paladin sources, verified from Spell.dbc

| source | value | schools | mechanism |
|---|---|---|---|
| Improved Righteous Fury (20468–70) | −2/−4/−6% | all (127) | SpellMod (aura 107) filling Righteous Fury 25780 eff2, which ships at **base 0** — only while RF is up |
| Shield of the Templar (53709–11) | −1/−2/−3% | all (127) | plain aura 87 |
| Guarded by the Light (53583/85) | −3/−6% | **magic only** (126) | excludes physical |
| Divine Protection (498) | −50% | all | cooldown |
| Ardent Defender | — | — | **not aura 87** — `SCHOOL_ABSORB` + dummy, below 35% HP only |
| **Blessing of Sanctuary (20911/25899)** | −3% on paper | — | **NOT IMPLEMENTED** — see below |

**Blessing of Sanctuary does nothing on this server.** Its −3% is a
`SPELL_AURA_DUMMY`, and the only dummy the damage-taken path reads is
`Unit::processDummyAuras`, which handles exactly one case: `SpellIconID
2109` (Rogue Cheat Death). `spell_pal_blessing_of_sanctuary` only casts the
stat buff **67480** (which carries no aura 87 at all — just two
`MOD_TOTAL_STAT_PERCENTAGE` effects) and handles the mana proc. Stock AC gap,
not something this work introduced.

### !! ONE AURA CARRYING −rank, NEVER rank STACKS OF −1% !!

Because aura 87 multiplies, ten stacked −1% auras would come to
`0.99¹⁰ = −9.56%`, not −10%. Node 60 therefore casts a **single** aura
whose amount is the whole value.

### !! EffectDieSides MUST BE 0, AND LUA CANNOT VERIFY IT !!

The aura amount is `basePoints + dieSides`. A die of 1 makes every rank one
percent too strong — the same off-by-one that shipped six talent ranks short
at milestone 1375. **ALE exposes no way to read an aura effect amount**, so
unlike a talent this cannot be checked from Lua at all: the `spell_dbc` row
is the only place the value is verifiable. Both rows were read back after
`--apply` (1900138: bp 0 / die 0; 1900139: bp −5 / die 0).

### Implementation shape

Node 60's `kind = "mitigation"` is a no-op in the modset — there is no
UNIT_MOD, rating or binding for damage reduction — so it rides the codex's
dynamic-amount channel, a direct sibling of `ReconcileRegen`:
`ComputeMods` gained a **fourth** return, and `ReconcileMitigation` recasts
`CastCustomSpell(player, 1900138, true, -rank)` whenever the rank moves.
Amounts are session-tracked (`ParagonCodexMitigation`) for the same reason
the mp5 channel is: a relog-restored aura is always recast.

Profiles match the house templates exactly — 1900138 mirrors 1900099/1900110
(Attributes 0x80 non-passive so `CastCustomSpell` can cast it, `AttributesEx3`
ALLOW_AURA_WHILE_DEAD, `AttributesEx4` arena-safe); 1900139 mirrors 1900131
(Attributes 0xC0 = PASSIVE | DO_NOT_DISPLAY, granted with `AddAura`, which
**cannot pass custom basepoints** — hence the baked −5). Both inherit
`DurationIndex 21` = **−1, infinite**, and clone base 20266's
`DispelType 0`, so the negative amount classifying the effect as a debuff
(there is no aura-87 case in `_isPositiveEffect`) is harmless: undispellable,
and `RemoveArenaAuras` only strips *positive* non-passive auras.

Plaque row 5 (array order, 2 per row) at y −506..−550, inside the 680-tall
content frame. Icons `Spell_Nature_SkinofEarth` / `Ability_Warrior_ShieldWall`,
both verified present in `SpellIcon.dbc` and unused elsewhere.

## 2p. Codex node 61 "Fleet of Ages" — movement speed (2026-08-20)

Mastery, **+1% movement speed per rank, flat 10 points each**, cap 25,
refundable. Aura **1900140**. Codex is now **41 nodes**. Lua + one spell row.

### !! THE *_ALWAYS AURA FAMILY IS THE WHOLE POINT !!

`Unit::UpdateSpeed` reads two different families and treats them completely
differently:

| family | read by | behaviour |
|---|---|---|
| `MOD_INCREASE_SPEED` (31) and friends | `GetMaxPositiveAuraModifier` | **only the single HIGHEST applies** |
| `MOD_*_SPEED_ALWAYS` (129/130/209) | `GetTotalAuraMultiplier` | **every one multiplies** |

A 1% aura on the **31** family would be permanently masked by Sprint,
Aspect of the Cheetah or milestone 50's own +50% and would do **nothing at
all**. The node therefore uses the ALWAYS family. The final combination is:

```cpp
float speed = std::max(non_stack_bonus, stack_bonus);
if (main_speed_mod)
    AddPct(speed, main_speed_mod);
```

so the ALWAYS multiplier is applied *and then* the max-picked sprint winner
multiplies on top — e.g. rank 25 (×1.25) under Sprint (+70%) gives
`1.25 × 1.70 = 2.125`. That is what makes this genuinely stack.

**Caveat from that `max()`:** an active `MOD_SPEED_NOT_STACK` (171) effect
larger than the node's multiplier **discards** it rather than combining.
Checked the whole DBC — aura 171 is used only by vehicle/quest toys (Rocket
Boost, Ram Speed Boost, "Mush!"), nothing a player carries in normal play.

### Three effects, one per movement mode

The modes do not cross over, so one aura carries all three:

| effect | aura | covers |
|---|---|---|
| 1 | 129 `MOD_SPEED_ALWAYS` | running, **unmounted** |
| 2 | 130 `MOD_MOUNTED_SPEED_ALWAYS` | running, **mounted** |
| 3 | 209 `MOD_MOUNTED_FLIGHT_SPEED_ALWAYS` | **flying**, mounted |

`CastCustomSpell` takes **bp0/bp1/bp2**, so one cast sets all three from the
rank. Effects 2 and 3 need `Effect_N = 6` and `ImplicitTargetA_N = 1`
(caster) set explicitly — the `SERVER_SPELLS` default override blanks
`Effect_2/3` and clone base 20266 carries only one effect.

**Swim is deliberately absent.** `MOVE_SWIM` has no `*_ALWAYS` variant at all
(it max-picks `MOD_INCREASE_SWIM_SPEED`), so a 1% swim effect could never
beat milestone 150's +100% and would be dead weight.

### Third user of the dynamic-amount channel

`ReconcileMovement` is the third copy of the `ReconcileRegen` pattern
(after `ReconcileMitigation`); `ComputeMods` now returns **five** channels.
They differ only in spell id, sign, and basepoint count — **at a fourth,
fold them into one table-driven reconcile.**

Die sides 0 on all three effects so each amount is exactly the rank; row
read back after `--apply`. Icon `Spell_Nature_Swiftness` (verified in
`SpellIcon.dbc`, unused elsewhere). Plaque row 5 beside node 60.

## 2q. Milestones 1425 + 1475 (2026-08-20)

TRACK is now **60 entries**, max level 1500 (1425 and 1475 slot between
existing rows — **TRACK must stay ascending**).

### 1425 "Beyond Mastery VI" — fifth gated trainer wave

The Beyond Mastery numerals run **I=175, II=525, III=775, IV=900, V=1175**,
so VI lands at 1425. Gate marker **1900146** ("Paragon Level 1425"), wired
through `LEARNED_SPELL_SPECIALS.TRAINER_RANKS_1425` and referenced by each
row’s `trainer_spell.ReqAbility1`.

**THE COST FORMULA, derived from the existing waves rather than invented.**
Each wave’s BASE band starts **8k above the previous wave’s** and steps
**+1/+2/+2/+2**:

| wave | base band (gold) |
|---|---|
| III (775) | 10 / 11 / 13 / 15 / 17 |
| IV (900) | 18 / 19 / 21 / 23 / 25 |
| V (1175) | 26 / 27 / 29 / 31 / 33 |
| **VI (1425)** | **34 / 35 / 37 / 39 / 41** |

On top of that, the BM V premium rule: **+2k per prior custom rank already
in that spell’s chain**. Final costs, verified in `trainer_spell`:

| spell | rank | priors | cost |
|---|---|---|---|
| Avenger’s Shield | 7 | 1 | 34+2 = **36k** |
| Holy Wrath | 7 | 1 | 35+2 = **37k** |
| Exorcism | 12 | 2 | 37+4 = **41k** |
| Consecration | 11 | 2 | 39+4 = **43k** |
| Holy Light | 16 | 2 | 41+4 = **45k** |

**Values continue each chain’s own measured growth ratio**, which every one
of these holds to within 0.1% across its whole ladder: Holy Light 1.1640,
Exorcism 1.3070, Consecration 1.2993, Avenger’s Shield 1.2046, Holy Wrath
1.2259.

**Shield of Righteousness was deliberately excluded.** Its R3→R4 step in the
milestone-900 wave was **1.1431**, breaking its own 1.333 stock ratio — so
projecting R5 would have been a guess whichever ratio was chosen.

**!! `SPELL_RANKS` values are RAW column values; `TALENT_RANKS` values are
DISPLAYED numbers !!** The two tables in the same file take opposite
conventions — displayed = `bp + die` either way, but only `TALENT_RANKS`
subtracts for you (`overrides[field] = v - 1`).

**Consecration R11 also needed a `CONSECRATION_BURST.totals` entry**
(`[1900144] = 1984`, i.e. 248/tick × 8). Without it the new top rank would
have been the only Consecration that does not detonate under milestone 125 —
a silent, rank-specific hole.

### 1475 "Vessel of Light" — two talent cap raises

Same recipe as 1375. Both use the **per-field dict** form.

| talent | id | payload coords | ranks | new |
|---|---|---|---|---|
| Touched by the Light | 2195 | tab 2, tier 9, col 1 | 3 → 4 | **80 / 40 / 80** |
| Holy Guidance | 1746 | tab 1, tier 8, col 3 | 5 → 9 | **24 / 28 / 32 / 36** |

Touched by the Light has **three effects on different steps** — aura 174
spell power from strength (+20), aura 50 crit (+10), aura 175 healing power
(+20) — so `value_field` would have moved one and silently left the other
two behind.

**Holy Guidance now has NINE ranks, exactly filling `MAX_RANK_SLOTS`
(Talent.dbc has 9 rank columns). It can never be extended again** — a sixth
rank would trip the generator’s `assert used + len(new_ranks) <= 9`.

Stock talent rows live in **Talent.dbc**, not `talent_dbc` — that table holds
only rows the patcher has already overridden, so a not-yet-extended talent
returns nothing there.

Both milestones verified by reading rows back after `--apply`: talent values
`bp + die` match the intended displays exactly, and the trainer rows carry
the right gate, prev-rank chain and gold.

## 2r. The nine non-Paladin classes (2026-08-20)

Every class-specific milestone now pays a real reward to all ten classes.
**135 talent extensions and 272 new trainer ranks**, spell ids 1901000-1901870.

### The three kinds of class milestone

The 26 class-specific milestones were sorted once and the sort drives
everything below:

| kind | milestones | count |
|---|---|---|
| talent cap raise | 75, 275, 475, 625, 725, 825, 1025, 1125, 1225, 1375, 1475 | 11 |
| new trainer ranks | 175, 525, 775, 900, 1175, 1425 | 6 |
| bespoke mechanic | 125, 225, 325, 425, 575, 675, 1075, 1325 | 8 |
| went universal | 375 | 1 |

The first two generalise: pick a different talent, pick a different spell. The
third does not — a custom spell, a core patch, a brand-new talent. Those get a
label that says so and **grant nothing**, which needs no code because
`ApplyReward` only acts on `UNIT_MODS` and `COMBAT_RATING` and every `SPECIAL`
handler matches its own value, so an unknown value is inert everywhere.

`PLACEHOLDER_STAT` and its nineteen loops are **gone**.

### Parity is the contract

Where a Paladin milestone raises one talent by four ranks, so does every other
class's. Where it touches three talents, so do theirs. The *base* rank count is
free (Talent.dbc caps at 9 total, so +4 needs base <= 5), which is why the
picks mix 5-, 3- and 2-rank talents exactly as the Paladin's own do.

**The one deliberate break is the Death Knight's trainer waves: four spells
per wave, 24 ranks, not 31.** It has eight trainer-taught rank chains where
every other class has fourteen to forty, because its spells all start at level
55. Holding it to 31 would drive single chains five ranks deep against the
Paladin's maximum of three. Eight chains x three ranks = 24 lands exactly on
that maximum.

### Generated, not written

`Tools/gen_class_talents.py` and `Tools/gen_class_trainers.py` read the DBCs,
extrapolate, print a full review table, and write
`Tools/generated/class_*.py`, which `paragon_client_patch.py` appends to
`TALENT_RANKS` / `SPELL_RANKS`. `Tools/gen_class_track_lua.py` turns the same
two files into the Lua reward entries **and** their `EXTENDED_TALENTS` gate
rows, so the labels and the enforcement can never drift apart.

Rerun order after any pick change:

```
python gen_class_talents.py --emit
python gen_class_trainers.py --emit
python gen_class_track_lua.py > generated/class_track.lua
python paragon_client_patch.py --apply      # client CLOSED
```

### What the generators REFUSE, and why each one matters

Every one of these was a pick that looked fine and would have shipped broken:

- **`EffectTriggerSpell` moves between ranks.** The rank's payload lives in the
  spell it fires, so a clone of the top rank fires the *old* rank's payload and
  the milestone grants nothing. Killed Flurry, Inspiration, Icy Talons,
  Predatory Strikes, Savage Combat, Natural Perfection.
- **The ladder is a curve, not a line.** Armored to the Teeth runs 108/54/36 —
  an armor *divisor*, i.e. 108/n. A straight line through it lands on 0 and
  then goes negative. Caught by demanding the projection keep the retail sign;
  killed Bladed Armor the same way.
- **The "percentage" is already at its ceiling.** Arcane Stability, Furor and
  Improved Mind Blast all read 100% at rank 5 — extra ranks grant literally
  nothing. Rogue Quick Recovery was worse: 40%/80% energy refund extended to
  120% is a free-energy loop.
- **Basepoints are not always a magnitude.** Living Bomb stores the id of the
  spell to detonate in `EffectBasePoints_2` (44460/55360/55361) — a rising,
  same-sign, non-zero ladder that sails through every other test, and
  "continuing" it points rank 4 at a spell that does not exist. Caught by
  refusing a ladder whose every rung is itself a high spell id.
- **Basepoints on a dead effect slot.** Blizzard leaves stale numbers in slots
  with `Effect_N = 0`; Rogue Master Poisoner carries 33/66/100 in one. Reading
  that as scaling refuses a perfectly good talent.

### !! PROJECT IN EFFECTIVE VALUES, NOT RAW BASEPOINTS !!

With die 1 the game reads basepoints+1, so a talent whose tooltip says 25%/50%
stores 24/49. Fitting a line to 24/49 gives a step of 24.5 and a next rank of
73 instead of 75. Projecting in **effective** space and converting back on
emission reproduces every value the Paladin milestones shipped by hand,
including Improved Blessing of Might's 12/25 -> 37/50. The emitter writes
`value - 1`, so emit `effective - offset + 1` and the raw column lands right
for die 0 and die 1 alike.

### !! COEFFICIENT INHERITANCE FALLS BACK TO RANK 1, NOT TO THE CLONE !!

`SpellMgr::GetSpellBonusData` looks up the spell, and on a miss falls back to
`GetFirstSpellInChain` — **rank 1**, whose coefficient is often deliberately
downscaled because it is castable at level 1.
`SpellMgr::GetSpellThreatEntry` does exactly the same.

This had already shipped as a live bug: **Holy Light ranks 14/15/16 ran at
direct_bonus 0.481 instead of 1.66** (stock ranks 4-13 all carry 1.66; rank 1
carries 0.481), so the three custom ranks had roughly 29% of the intended
spellpower scaling and rank 13 out-healed all of them. Nothing errored and
nothing logged — the spell just got quietly worse as you ranked it up.

Fixed at the source: `paragon_client_patch.py` now copies the **clone's**
`spell_bonus_data` and `spell_threat` rows onto every new rank, which removes
the fallback from the picture entirely. An audit of all 34 pre-existing custom
ranks found Holy Light was the only casualty.

### Milestone 375 went universal

Every class has an average item level, so `ILVL_ATTUNEMENT` never had cause to
be class-specific. `paragon_ilvl_bonus.lua` `CLASS_WEIGHTS` now carries all ten
classes on one shape — primary 1.0 / Stamina 0.75 / secondary 0.5 — so no class
is ahead on budget, only on split. The TRACK row is a plain universal reward
and `MILESTONE_375` is deleted; the label names no stats because the client
appends the live per-stat numbers from the `ParagonIlvl` payload underneath it.

### The placeholder text

`If you want this to have a cool effect, hmu <3 - Tom`

Plain ASCII `<3` and not a heart: the 3.3.5 UI font (`FRIZQT__.TTF` in
`locale-enUS.MPQ`) has **no glyph for U+2764**, and none for the plain card-suit
U+2665 either — both draw as an empty box. Verified by parsing the font cmap,
with U+00A9 and U+0041 as positive controls. (If a heart is ever wanted,
`|TInterface/Icons/Achievement_WorldEvent_Valentine:14|t` is a real texture in
the client.)

### Spell id blocks

One clean 100-id block per class, because id RANGES are landmines — the enchant
markers once overlapped Divine Strength's extended ranks and silently gave
paladins a extra enchant slot.

| Warrior 1901000 | Hunter 1901100 | Rogue 1901200 | Priest 1901300 | DK 1901400 |
|---|---|---|---|---|
| **Shaman 1901500** | **Mage 1901600** | **Warlock 1901700** | **Druid 1901800** | |

Within a block: talents 00-39, trainer ranks 40-79, 80-99 spare.
**1900152-1900999 stays free for future universal work.**

### Verified after `--apply`

764 custom `spell_dbc` rows, 151 `talent_dbc` rows, 272 `trainer_spell` and
272 `spell_ranks` rows; Warrior Deflection 130 carries ranks 6-9 in both the DB
and `patch-5.MPQ`; Heroic Strike chains 47450 -> R14 -> R15 -> R16 with the
right gate marker and prev-rank on each; every Death Knight wave has exactly
four spells; Frostbolt R17/R18 continue the stock ladder's own ratio. Clean
worldserver boot, no Lua errors, no alarms.

**Needs a FULL CLIENT RESTART** — Talent.dbc and Spell.dbc both changed.

## 2s. Playerbot interaction audit (2026-08-20)

Full static audit of the Paragon system against mod-playerbots. **Nothing
breaks functionally** -- bots are structurally locked out -- but the audit
found one real performance defect and one wasteful path, both fixed below.

### Why bots are inert: they can never leave paragon 0

Gated at all three experience paths: `paragon_hook.lua` XP grant, the same
file's logout save (a bot's stale copy would otherwise overwrite the account
row), and `paragon_rework_party.lua` party shares. At paragon 0 every
milestone, aura, spell and talent check evaluates false.

Confirmed against the live DB rather than assumed:

| check | result |
|---|---|
| bot characters knowing any Paragon spell (1900000+) | **0 of 5529** |
| bot characters with `extraBonusTalentCount > 0` | **0 of 5528** |
| non-rogue bots with the globally-opened lockpicking skill | **0** |
| `account_paragon` rows for bot accounts | **0** (only the owner's) |

### !! THE SPEC-LINK SORT IS THE TALENT LANDMINE !!

`PlayerbotAIConfig::ParseTempTalentsOrder` sorts a tab's talents by (Row, Col)
and maps **the i-th character of the link string to the i-th sorted talent**.
Inserting a talent anywhere but the end shifts every bot build in that tab.

Sudden Light (2286) is the only talent the Paragon work ever added, and it
sorts at **index 26 of 26** in Paladin Retribution -- dead last. The longest
Retribution link segment is **26 characters**, so no bot link even reaches it.
No cell was ever moved. Extra RANKS on existing talents (all 150 of them)
cannot shift the sort at all, since Row/Col and the talent count are untouched.

Checked every rank request too, not just the shape: **all 3053 spec-link rank
digits across every class, spec and level are within their talent's retail
cap**, so bots never address an extended rank. The `PLAYER_EVENT_ON_CAN_LEARN_TALENT`
gate is defence-in-depth here, not the only barrier -- which matters, because
ALE's `CallAllFunctionsBool` runs those hooks with default_value=true and would
fail OPEN if the Lua state ever failed to load.

`MAX_TALENT_RANK` 5 -> 9 is safe for bots because tier gating uses its own
`TALENT_POINTS_PER_TIER = 5`. The bot pet-talent loops now iterate nine slots
instead of five, but pet talents hold zeros there, so behaviour is unchanged.

### Why bots cannot learn the 272 new trainer ranks

`PlayerbotFactory::InitAvailableSpells` walks every trainer and defers to the
core's `Trainer::CanTeachSpell` -> `GetSpellState`, which checks `ReqAbility`.
Our ranks carry the milestone marker in ReqAbility1 and bots do not have it, so
they resolve `Unavailable`. Bot spell SELECTION (`SpellIdValue::Calculate`)
also only reads `bot->GetSpellMap()`, so an unknown rank cannot be picked even
hypothetically, and no bot code touches the rank-chain APIs
(`GetLastSpellInChain` and friends), so extending 272 chains cannot confuse
targeting.

Every hardcoded `19xxxxx` id in the core was swept: 28 of them, all <= 1900130,
none inside the new 1901000-1901870 block.

### THE DEFECT: paragon_dual_enchant had no bot gate at all

Its header claimed *"Bots cost nothing here: tickers are only ever registered
from the addon's client load request, which bots never send."* **That premise
was false.** `OnAfterClientLoadRequest` fires from
`OnParagonClientLoadRequest`, which `Hook.OnPlayerStatLoad` calls on **every
login** -- it is not only the addon handler.

So every bot registered a 10s ticker whose body walked **13 equipment slots x
4 enchantment slots** with nothing to gate it. The send was delta-suppressed;
the scan was not. At the configured 2500 concurrent bots that is roughly
**19,500 ALE calls per second** plus a fresh Lua table per bot per tick, all
discarded. The same `Push` also runs on `RegisterPlayerEvent(29)` -- **every
equip** -- so bot gear randomization paid the 13-slot scan per item.

Fixed with the gate its sibling modules already had
(`paragon_double_buckle:286`, `paragon_gem_double`'s `eligible` fast path):
`Eligible()` in front of `CurrentState`, and `EnsureTicker` refusing bots.

**EnsureTicker is gated on IsBot ONLY, never on level** -- it is reached just
once per session, at login, so a level test there would leave a character that
dings 80 mid-session without a ticker for the rest of the session. A bot never
stops being a bot, so that test is safe to make permanent; the level test
belongs in `Push`, which runs repeatedly.

### The waste: the login client push ran for bots

`OnParagonClientLoadRequest` had no bot gate, so every bot login built and
serialized the full reward track (opcode 7 -- 60 milestones with per-class
labels and talent coordinates) for a session with no addon. The §2r class work
grew that payload by ~2.6 KB per class, making an existing waste worse.

Gated at the top of the function. Skipping it also stops
`OnAfterClientLoadRequest` firing for bots, which is what registered **three**
10s tickers per bot (dual enchant, double buckle, gem double). Nothing is lost:
every subscriber either only pushes client state, or reconciles something that
is already a no-op at paragon 0.

### Known, accepted, not bot-related

`Player::GetStatScalingLevel` does six `GetAuraEffect` lookups per call and is
reached from every gt-table stat conversion; `Creature::GetAttackDistance` and
`GetAggroRange` each do a `HasAura(1900104)` behind a `levelDiff > 0` guard.
Both are multimap lookups on hot paths, measured as acceptable.

Sub-80 characters never reconcile bonus talent points DOWN: the account gate
flips apply to false and `ReconcileBonusTalents` sits inside `if apply`. The
owner's level-55 Death Knight holds 5 stale points. Harmless on this server.

## 2t. Patch archive names are load order (2026-08-20)

Our client archives moved off digits and onto letters, and the general patch
became a superset of the locale one. Nothing about the content changed; this
is purely about surviving a third-party patch dropped into the Data folder.

### How the client actually resolves patch priority

Read out of `Wow.exe`, not out of folklore. The archive table is built at
`.text 0x405AB0` from exactly four search patterns:

```
Data\patch-?.MPQ            Data\<locale>\patch-<locale>-?.MPQ
Data\patch.MPQ              Data\<locale>\patch-<locale>.MPQ
```

- **`?` is a raw Win32 `FindFirstFile` wildcard — exactly one character.**
  Probed against the real filesystem: `patch-A.MPQ` and `patch-z.MPQ` match,
  **`patch-10.MPQ` does not** and would silently never load.
- The wildcard hits are sorted at `0x405D2F` by comparator `0x401200`, which
  is `neg(SStrCmpI(a, b))` — **descending**.
- `SStrCmpI` (`0x41B9E0`) folds `A`-`Z` by `+0x20`, so the compare is
  **case-insensitive** and digits (`0x30`-`0x39`) rank **below** letters
  (`0x61`-`0x7A`).
- The mount loop at `0x405E90` walks that array **backwards**, calling
  `SFileOpenArchive(name, priority, 0xC00, &handle)` with `priority` counting
  **up** from 0. Highest name mounted last, **and last wins**.
- `patch.MPQ` and `patch-<locale>.MPQ` are appended AFTER the sort (they are
  the second pass of the same loop), so they end up at the very bottom.

The resulting ladder, reproduced against the live Data folder:

| prio | archive |
|---|---|
| 0 | `Data\enUS\patch-enUS.MPQ` |
| 1 | `Data\patch.MPQ` |
| 2-4 | `Data\enUS\patch-enUS-2 / -3 / -X.MPQ` |
| 5-8 | `Data\patch-2 / -3 / -W / -X.MPQ` |

### !! EVERY GENERAL PATCH OUTRANKS EVERY LOCALE PATCH !!

The descending sort compares the **full path**, and `Data\p...` > `Data\enUS\...`
at position 5 (`p` = 0x70 vs `e` = 0x65). So the entire general block sits
above the entire locale block — **even stock `patch-2.MPQ` mounts above our
locale archive.**

`Spell.dbc` used to live *only* in the locale patch. Any third-party general
patch carrying `DBFilesClient\Spell.dbc` would have shadowed it from any
letter, and it would have failed **silently** — the same shape of bug as the
Holy Light coefficient fallback in §2r. Fixed by mirroring the whole locale
stage into the general stage before the build, by directory walk rather than
a hardcoded list, so a DBC added later is covered without anyone remembering.
The locale archive is still written and still correct; it is now a redundant
lower-priority fallback.

### The default names

| was | now | holds |
|---|---|---|
| `patch-4.MPQ` | **`patch-W.MPQ`** | 14 Paragon/custom UI `.blp` |
| `patch-5.MPQ` | **`patch-X.MPQ`** | all 8 DBCs (was 3) |
| `patch-enUS-5.MPQ` | **`patch-enUS-X.MPQ`** | the same 8 DBCs, as fallback |

`W` and `X` clear the letters real HD packs use (the Warmane set is
`patch-C` / `patch-D` / `patch-F`) while **leaving Y and Z free on purpose**:
those are what a mod author reaches for when told "use a late letter", and a
same-name drop is an *overwrite*, which is worse than losing a priority
contest. The names are configurable with `--general-name` and `--locale-name`
when another patch already occupies either default.

Every generated archive carries `ParagonAnniversary\owner.txt`. The generator
checks that exact marker before replacing an existing target and aborts before
touching stages or SQL if ownership cannot be proven. It also writes to a
temporary file and atomically replaces a known-owned archive only after the new
MPQ verifies. Legacy `patch-5` / `patch-enUS-5` files are reported but never
deleted automatically because the old archives predate the marker.

### !! patch-W.MPQ HAS NO GENERATOR !!

Its source BLPs are not anywhere in the tree — `grep` across the whole project
finds no reference to it at all. It was built once, by hand. **The only other
copy is `Tools/mpq-backup/`**, made at the same time as this rename. If it is
ever overwritten by a third-party patch and that backup is gone, the Paragon
UI art is gone with it.

### Do HD patches actually conflict?

No, on content. Our whole footprint is 17 files, diffed against all seven
stock general archives (~210k entries): **zero overlap** — even the
retail-sounding `Interface\Common\portrait-ring-withbg.blp` and `help-i.blp`,
which are modern retail paths that do not exist in 3.3.5a. The 2025 HD
Characters/Creatures/Mounts pack ships `CharHairGeosets`, `CharSections`,
`CharacterFacialHairStyles`, `CreatureDisplayInfo`, `CreatureDisplayInfoExtra`,
`CreatureFamily`, `CreatureModelData`, `EmotesTextSound`, `HelmetGeosetVisData`
and `SpellVisualKitModelAttach` — none of ours. Warmane's `patch-C/D/F` are
models and textures only. Stock `patch-2.MPQ` (11,811 files) and
`patch-3.MPQ` (2,994 files) contain **zero** DBCs; every stock DBC is in
`patch-enUS-3.MPQ`.

The residual risk is a **kitchen-sink pack that dumps a whole DBFilesClient
folder into a general MPQ**. Those exist. After this change it would have to
outrank `patch-X.MPQ` to hurt us, i.e. be named `patch-Y` or `patch-Z`.

### The ChromieCraft patch set, checked

The set ChromieCraft points at uses `patch-A.mpq` (classic login screen,
character creation, water/liquid), `patch-B.mpq` (tileset textures) and
`patch-C.mpq` (cloth textures + character models). **A, B and C all mount
BELOW `patch-W` and `patch-X`, so whatever they contain, our files win** --
this needs no inspection of the archives at all, which is the point of moving
onto letters.

The one name to avoid is **`patch-Z.mpq`** -- ChromieCraft's *Challenge Modes*
patch (an AIO thing, not HD). That outranks `patch-X`. It is server-specific
and there is no reason to install it here, but it is the single name that
would win against us.

The local ChromieCraft client at `Desktop\WowWotlk\ChromieCraft_3.3.5a` is
currently **stock** -- its Data folder holds only the untouched 2021 archives,
no custom patch of any kind.

### Tools/check_patch_collisions.py

Rather than reasoning about it again next time, run the checker. It rebuilds
the real mount ladder with the same Win32 wildcard the client uses, then
reports which archive actually wins for every file we ship.

```
python Tools/check_patch_collisions.py
python Tools/check_patch_collisions.py --general-name patch-Y.MPQ \
    --locale-name patch-enUS-Y.MPQ
```

It **probes the MPQ hash table directly instead of reading `(listfile)`**,
because a listfile is an ordinary file inside the archive that plenty of patch
authors omit -- that makes an archive unenumerable but never unqueryable, and
we know exactly which names we care about. It also flags archives present but
never mountable (a two-character suffix like `patch-10.MPQ` is invisible to
the client), refuses to treat an unparseable archive as empty, and verifies the
ownership marker instead of assuming that a configured filename is ours.

Verified by injecting a fake `patch-Z.MPQ` carrying `DBFilesClient\Spell.dbc`
and `Interface\Paragon\ParagonUI.blp`: both were reported, with the winning
archive and our own lower copies named, exit code 1. A checker that has only
ever printed OK is not a checker.

## 2u. The in-game tutorial, rebuilt (2026-08-20)

The "?" button on the Paragon panel ran a six-step tour written against a UI
that no longer exists. It now runs ten steps against the one that ships, and
the reward track finally gets explained.

### !! HALF THE OLD TOUR POINTED AT HIDDEN FRAMES !!

`Paragon_Codex.lua` calls `HideLegacySpender()`, which hides
`Body.TopSpacer`, `Body.StatisticsList` and `ApplyButton`, and re-hides them
through a `hooksecurefunc` on every rebuild. The point-spender they belonged
to was replaced by the Codex.

Old steps 4, 5 and 6 targeted exactly those three frames. The only guard was
`if not targetFrame then skip end` -- and a hidden frame is **not nil**, so it
never fired. The tour highlighted empty space and explained left-click/+1,
right-click/-1, scroll/+-5 interactions that no longer exist anywhere in the
addon.

Fixed by building the step list **once, at start, filtered on `IsVisible()`**
(`BuildSteps`), so an absent or hidden element is dropped before the tour
begins rather than skipped while running. That also fixes two things the old
skip-at-display-time approach got wrong:

- it only skipped **forward**, so `Previous` would land on the unavailable
  step and be bounced forward again -- Back silently stopped working. Latent
  while every step targeted an XML frame that always exists; live the moment
  the list includes the track and Codex, which the server builds on demand.
- the counter read `#steps` including ones that would be skipped, so the tour
  could say "Step 3/6" and then end.

### Three smaller defects fixed in passing

- **Alpha restore clobbered a designed value.** `RestoreUIParagonAlpha` set
  every touched frame to alpha 1, but `UIParagon.HelpButton` ships at
  **0.5** (`UIParagon.xml:464`). Running the tour permanently brightened the
  "?" for the rest of the session. The original alpha is now captured at
  start and restored.
- **Edge clamping only worked on one axis.** The overflow fix did the
  horizontal and vertical corrections as two separate
  `ClearAllPoints`/`SetPoint` pairs, and the second discarded the first's
  offset, so a box overflowing a corner only ever got pushed back one way.
  Both deltas now go into a single re-anchor.
- **The box height was hardcoded per step index** (`if stepIndex == 6 then
  260`). It is now derived from `Description:GetStringHeight()`.

`Paragon_RemoveActivateTutorial` also assigned an undeclared global `bool`.
Harmless -- nil is falsy and `TutorialEnd` had already run -- but it read as a
deliberate flag, so it now says `false`.

### The ten steps

Top to bottom, matching the panel's own layout: Paragon level -> experience ->
the on-screen bar toggle -> **the reward track** (60 milestones, 1 to 1500,
granted automatically) -> **reading it** (drag/wheel, gold vs grey, hover for
the exact reward) -> **the one clickable node** -> **class milestones** -> the
Codex -> spending points -> the "?" itself.

Three of those carry information that existed nowhere in the client:

- **The reward track is the centre of the feature** and the old tour never
  mentioned it once.
- **Milestone 1400 is the only node that responds to a click** (the racial
  picker -- see `UIParagonTrackNode_OnMouseUp`), and nothing on screen says
  so. The level is formatted in from `ParagonRacialData.milestone` rather
  than hardcoded, and the step drops itself if that payload has not arrived.
- **Class milestones pay out somewhere else entirely** -- extra talent ranks
  past the normal cap, and extra trainer ranks. Neither is visible on this
  panel, so the tour now sends you to your talents and your trainer.

There is exactly **one** help button (`UIParagon.xml:464`, texture
`Interface\Common\help-i`), anchored under the close button -- not one per
section.

### Locale handling

The Reward Track, the Codex and the racial picker all shipped English-only,
and the tutorial gains keys faster than ten locale blocks get translated.
`Paragon_Locales.lua` now gives every non-English table an `__index`
metatable pointing at `enUS`, so a missing key quietly reads English while a
translated one still wins. Verified: `frFR` keeps its own `TUTORIAL_TITLE`
and `EXPERIENCE_TEXT` and falls back only for the new keys; `enGB` is skipped
by identity, because it *is* the `enUS` table.

The three keys describing the removed spender (`TUTORIAL_POINTS`,
`TUTORIAL_CATEGORIES`, `TUTORIAL_STATS`) were deleted from all ten blocks --
27 lines, and nothing outside the tutorial ever read them.

### Verified headlessly, not by eye

`pip install lupa` provides a real **Lua 5.1** interpreter, the same version
3.3.5 runs. Two passes:

1. Every addon `.lua` (23 files) compiles clean.
2. A ~100-line mock of the 3.3.5 frame API (`CreateFrame`, alpha, visibility,
   fontstrings) runs the tour headlessly. 16 assertions: the counter reads
   10/10 with a full panel, 8 with no Codex, 9 with no racial payload and 4
   with only the banner; `Previous` walks all the way back from step 10;
   milestone 1400 is formatted into step 6; the help button ends at 0.5 both
   on Finish and on the panel being closed mid-tour.

Harness kept at `scratchpad/tutorial_harness.lua`. Writing it turned up a
Lua trap worth remembering: **`X and nil or Y` always evaluates to `Y`**,
because `X and nil` is falsy -- the option flags in the first draft silently
did nothing.

**These are loose addon files -- no MPQ rebuild. `/reload` is enough.**

### Follow-up: the description was truncating with an ellipsis

Reported from a screenshot: step 8 rendered ~3.5 lines and ended in a literal
"..." mid-sentence, with visible empty space below it.

**The root cause was never proven.** Nobody located the engine code that emits
that ellipsis, and the arithmetic rules out the tooltip's own frame rect as the
cap. What the client's own FrameXML *does* establish, from a full extraction of
all 293 files:

- **`GetStringHeight()` is called ZERO times in Blizzard's entire 3.3.5 UI.**
  The measurement call they actually use is `FontString:GetHeight()` --
  `StaticPopup.lua:2922` reads `32 + text:GetHeight() + 8 + button1:GetHeight()`,
  and `GossipFrame.lua:172` / `WatchFrame.lua:604` do the same. The addon was
  built on an API the client's own UI never touches.
- **Blizzard shows the frame, then measures it.** `StaticPopup_Show` anchors,
  calls `dialog:Show()` at `StaticPopup.lua:3271`, and only then calls
  `StaticPopup_Resize` at `:3273` -- under their own comment *"Finally size and
  show the dialog"*. `GossipFrame.lua:21` calls `ShowUIPanel` before
  `GossipFrameUpdate()` at `:27`; `LFDFrame.xml:683-685` drives its measuring
  update from `<OnShow>`. **The addon did the exact opposite**: it measured
  while the tooltip was still hidden and called `Show()` forty lines later.
- **Every wrapping body FontString in FrameXML is one anchor + an explicit
  width + height 0** (`QuestInfo.xml:279-283` x=285 y=0, `StaticPopup.xml:53-63`
  x=290 y=0). Where Blizzard *does* use two opposing horizontal anchors it
  pairs them with a fixed height and `maxLines` -- i.e. precisely where an
  ellipsis is wanted (`InterfaceOptionsPanels.xml:64-80`, 36 occurrences). The
  tutorial's description was the only wrapping text in the addon using the
  two-anchor form. **The addon's own non-truncating wrapped text already gets
  this right**: `Paragon_RacialPick.lua:197-199` sets one anchor and an
  explicit `SetWidth`.

So the fix is built to hold whether or not the diagnosis is right:

1. One `TOPLEFT` anchor + explicit `SetWidth(388)` + `SetHeight(0)`, matching
   FrameXML and the addon's own working precedent. `SetWordWrap(true)` and
   `SetNonSpaceWrap(false)` are called behind existence guards -- both are real
   FontString methods in 3.3.5, verified in `Wow.exe` where they sit in the same
   method-name table as `GetStringHeight` and `SetSpacing` (0x5e9254 / 0x5e926c).
   There is no `SetMaxLines` on FontString.
2. `SetHeight(0)` before every `SetText`, since a non-zero height is 3.3.5's
   truncation mechanism -- a no-op if nothing set one, insurance if something did.
3. Measure with `GetHeight()`, keeping `GetStringHeight()` as a floor via `max`.
4. **Anchor, `Show()`, then set the text and size** -- Blizzard's order.
5. **A grow-only second pass one frame later**, on the tooltip's own
   self-cancelling `OnUpdate`. This is the real insurance: it depends on nothing
   from a previous step, only on the frame having been rendered once, so it
   holds on step 1 of a cold `/reload`. Growing only (the same principle as
   StaticPopup's `maxHeightSoFar` clamp) means a bad reading can never collapse
   the box, and it breaks the self-locking failure the old code had, where one
   short measurement sized the box small and it never recovered.

The synthesis agent also proposed an invisible measuring FontString plus a
line-count estimator. **Deliberately not taken** -- it can under-count on greedy
word wrap, mishandles future `|T` and `|H` escapes, and insures against a case
the deferred grow pass already covers. Simpler is more likely to be right.

Cosmetic, and separable from the fix: `TOOLTIP_CHROME_BOTTOM` 78 -> 62 with the
literal `+ 12` becoming `TOOLTIP_TEXT_PAD = 10`. The tallest bottom element is
`stepIcon`, whose top edge sits at y=58, so the old numbers reserved a 32px dead
band under the text at every step -- that is the empty space in the screenshot,
and it is *not* evidence about the truncation.

**If it still truncates**, the anchoring was not the cause. Paste this in-game
to settle it in one line:

```
/run local d=ParagonTutorialTooltip.Description print(d:GetHeight(),d:GetStringHeight(),d:GetWidth(),ParagonTutorialTooltip:IsShown())
```

The fallback is Blizzard's other canonical answer: stop resizing entirely and
put a fixed-size description inside a ScrollFrame, the way `ItemTextFrame` does
(`ItemTextFrame.xml:145` ScrollFrame, `:203-206` a fixed 270x304 child, and not
one `SetHeight`/`GetHeight` call in `ItemTextFrame.lua`). A ScrollFrame's
scissor rect is the only thing in FrameXML that provably clips a region.

### Copy changes

The tour no longer quotes the milestone count or the level cap -- both were
promises the copy had no business making. Asserted in the test suite, not just
edited.

### Test suite

`scratchpad/test_tutorial.py` (22 assertions) now also covers the sizing:
`Show()` is logged before the description's `SetText` *and* before its first
measurement; the deferred pass grows 170 -> 360 on a realized measurement and
refuses to shrink on a bad one; a runaway measurement clamps at 600; the
`OnUpdate` is armed per step, disarms after one tick, and is cleared by
`Paragon_TutorialEnd`.

Two harness bugs found and fixed while writing it, both mine: a catch-all
`__index` that returned a *function* for unset `__`-prefixed state fields (so
`GetHeight` returned a function), and shared mock names for child FontStrings,
which made the ordering assertion match `Title:SetText` -- which legitimately
runs before `Show()`.

## 2v. The reward track caps the view ahead (2026-08-20)

**Every milestone you have earned stays on the track.** What is capped is the
view *forward*: only the next four unearned milestones are rendered, so there
is still something left to find out. A fresh level 80 has earned nothing and
sees exactly four; at max level nothing is upcoming and all sixty are on show.

`TRACK_UPCOMING_COUNT = 4` in `Paragon_RewardTrack.lua`; the selection is
`VisibleMilestones(all, currentLevel)`, applied inside
`UIParagon_RebuildRewardTrack`. The loop can `break` once the upcoming budget
is spent only because the list is sorted ascending -- everything after that
point is also upcoming.

### !! CLICKING THE TUTORIAL LEFT THE WHOLE TRACK LOOKING UNEARNED !!

Reported from the game, and it outlasted both closing the tour and a relog.

`UIParagon_RefreshRewardTrackLocks` says "locked" by desaturating the icon and
greying the border and the level label. The tutorial dimmed every non-current
step frame to alpha **0.5**, and four of its steps target the track. **Alpha
multiplies down the parent chain**, so one stale 0.5 on the section, the
clipper or the strip drags every node underneath with it -- landing on exactly
the same visual language the lock styling uses, and making earned, gold,
full-colour milestones read as unearned.

Fixed at both ends, because the second half is what makes it stick:

1. **Cause.** A `noDim` flag on the step definition, set on all four steps that
   target the track. Those frames keep their own alpha for the whole tour and
   rely on the pulsing highlight border instead. The general rule: *never dim a
   frame whose alpha already carries meaning.*
2. **Persistence.** `UIParagon_RebuildRewardTrack` now forces
   `SetAlpha(1)` on the section, the clipper, the strip and every node it
   places. A rebuild runs on login and on every level change, so **no dim from
   any source can outlive one** -- it does not matter what put it there. A
   sweep confirmed the tutorial is the only thing in the addon that has ever
   called `SetAlpha` on these frames, and no XML `alpha=` attribute touches
   them, so (1) removes the only known cause and (2) covers the unknown ones.

Recycled node frames get the same reset: `UIParagon_RebuildRewardTrack` reuses
`ParagonTrackNode_N` frames by name rather than recreating them, so a dim
applied to one would otherwise survive every future rebuild.

### The strip snaps to its far end on rebuild

With the earned tail always present the strip gets long again -- sixty nodes is
4480px against a 650px clipper -- and offset 0 shows the *oldest* milestones.
A rebuild happens on login and on a level change, and at both of those moments
what matters is the right-hand end: the milestone just earned and the few still
ahead. Left at 0, the entire point of capping the view would sit off-screen
behind a long tail. Manual drags are untouched, since dragging does not
rebuild.

Centring still applies, but only when the content fits: four nodes are 280px in
a 650px clipper, so `startX` centres them at 185; sixty nodes overflow, so it
falls back to the left padding and the strip scrolls as before.

### A level change rebuilds rather than repaints

The hook on `UIParagon_OnClientReceiveLevel` used to call
`UIParagon_RefreshRewardTrackLocks`. A level change can move the window, not
just the lock states, so repainting alone would leave the milestone you just
earned sitting there gold with the next one never appearing. It rebuilds when
the level actually moved and repaints otherwise.

### Copy

`TUTORIAL_TRACK_NODES` is back to explaining that the strip scrolls -- it does
again, once you have a tail -- with the addition that only the next few are
visible ahead of it.

### Tests

`scratchpad/test_track.py`, 25 assertions, driven through the real
`UIParagon_RebuildRewardTrack` rather than a copy of the selection logic:
the split at paragon 0 / 25 / 100 / 1375 / max, the racial node being visible
from the moment it is earned onward, a level-up extending the tail and
advancing the head, centring at x=185 for a short strip versus left-aligned and
scrolled-to-the-end for a long one, lock states, and -- the regression guard
for the bug above -- that a deliberately dimmed section, clipper and node all
come back at alpha 1 after a rebuild.

`test_tutorial.py` is at 27, including that the track and clipper sit at alpha 1
on every step while the banner still dims.

## 2w. The paladin aura bar vs the Paragon frames (2026-08-20)

Paladin auras live on **`ShapeshiftBarFrame`** in 3.3.5, not in the buff frame,
so a paladin always has a six-button bar just above the left end of the main
action bar. It overlapped both Paragon frames. One half is fixed; the other
half is **known, deliberate and left alone**.

### FIXED: the window was in the same stratum as the action bars

| frame | strata | source |
|---|---|---|
| `UIParent` | MEDIUM | `UIParent.xml` |
| `MainMenuBar` | MEDIUM (inherited, declares none) | `MainMenuBar.xml:4` |
| `ShapeshiftBarFrame` | MEDIUM (parented to MainMenuBar) | `BonusActionBarFrame.xml:201` |
| `UIParagon` | MEDIUM (inherited, declared none) | `UIParagon.xml` |

Same stratum means nothing but frame creation order decided who drew on top,
and the aura bar won. `UIParagon` is now `frameStrata="HIGH"` — Blizzard's own
answer for a frame that must clear the bottom bars, declared for exactly that
reason at `BonusActionBarFrame.xml:56`.

Checked rather than assumed, everything that must stay above the panel still
does: `StaticPopup` is DIALOG (`StaticPopup.xml:36`), so the Codex respec and
permanent-node confirms are unaffected; `GameTooltip` is TOOLTIP
(`GameTooltip.xml:9`); the tutorial's frames are FULLSCREEN_DIALOG.

### !! AN XML COMMENT MAY NOT CONTAIN A DOUBLE HYPHEN !!

The comment explaining the above was first written with `--` in it as prose
punctuation. That is not well-formed XML, and **an XML parse failure takes down
the whole addon**, not just that one frame. Caught by validating with
`xml.etree.ElementTree` before shipping. This codebase's comment style uses
`--` constantly, so XML validation is now part of the routine.

### NOT FIXED, DELIBERATELY: the XP bar still runs under the aura bar

`ParagonExpBar` is 1024 wide and anchored `BOTTOM` to `MainMenuBar`'s `TOP`
(`ParagonExpBar.xml:6-16`), i.e. across the entire band Blizzard stacks its
secondary bars into. On a paladin it therefore passes underneath
`ShapeshiftBarFrame`. Both ways out are worse than the overlap:

- **Lifting the XP bar above the cluster was built, shipped and reverted.** It
  measured the cluster's real extent and cleared it correctly — and looked
  considerably worse: the whole 1024px bar floats ~40px up, visually detached
  from the main bar it belongs to, with the aura buttons poking through it.
- **Moving the aura bar up cannot be done cleanly.**
  `UIParent_ManageFramePositions` (`UIParent.lua:1949`) does not position
  anything itself; it dispatches to secure code via
  `FramePositionDelegate:SetAttribute("uiparent-manage", true)`, so any
  `SetPoint` applied from here is overwritten the next time the secure handler
  runs. The two supported ways in are `frame.ignoreFramePositionManager = true`
  (`UIParent.lua:1229`), which means owning every layout case Blizzard handles
  — bars toggled, vehicle and possess bars, max level, reputation bar — or
  mutating `UIPARENT_MANAGED_FRAME_POSITIONS` (`UIParent.lua:1189`), which the
  secure handler reads and which risks taint and "action blocked" errors in
  combat. Neither is worth it for a cosmetic overlap on one class.

The reasoning is recorded in a block comment above
`ParagonExpBar_UpdatePosition` so the next person does not re-derive it and
re-ship the reverted version.

**If it is ever revisited, the cheap option is horizontal, not vertical**:
narrow the bar so it stops short of the aura bar (which occupies roughly the
leftmost 210px of MainMenuBar's 1024) instead of moving anything up.

## 3. Unrelated but easy to lose

`modules/mod-ale` must stay named exactly that — the core's
`modules/CMakeLists.txt` only links lualib for a directory named `mod-ale`.
The Lua 5.2 `unpack` shim in `paragon/lib/Mediator/mediator.lua` is also a local
fix that an upstream module update would revert.

## 4. Updating the core (established 2026-08-17)

**This core is the playerbot fork, not stock AzerothCore.** Playerbot hooks are
baked into `src/server/game` (Creature/LFGQueue/Item/Player/Unit.cpp), and
mod-playerbots does not compile without them. Never merge stock
`azerothcore/azerothcore-wotlk` master — it would strip the hooks. The fork
tracks stock closely (~days to a few weeks behind), so updating to the fork's
head *is* updating AzerothCore.

The repo is git-managed since 2026-08-17: branch **`paragon-live`** holds a
snapshot commit of the local tree (base was fork `ceeb3116e`, 2026-07-24) plus
the merge to fork head. Remotes: `fork` = mod-playerbots/azerothcore-wotlk
(branch **Playerbot** — the org that replaced liyunfan1223's repos), `origin` =
stock AC (comparison only, never merge).

Procedure:

1. `git fetch fork Playerbot && git merge fork/Playerbot` (on `paragon-live`).
2. Resolve conflicts if any (the 2026-08-17 update had none), then verify every
   local patch survived:
   - `grep 1900071 src/server/game/Entities/Unit/Unit.cpp` (mount block §1 —
     grep the NEWEST id: a stale conflict resolution keeping only the old
     1900005 line would still pass a 1900005 grep)
   - `grep -c ParagonAllowDualAura src/server/game/Spells/Auras/SpellAuras.cpp` (= 2)
   - `grep TALENT_POINTS_PER_TIER src/server/game/Entities/Player/Player.cpp` (tier gate)
   - `grep MAX_TALENT_RANK src/server/shared/DataStores/DBCStructure.h` (= 9)
   - `grep niiiiiiiiiiiiixxixxixxx src/server/shared/DataStores/DBCfmt.h`
   - `grep -c IsContinue src/server/game/DungeonFinding/LFGMgr.cpp` (LFG fix, see below)
   - Dockerfile still builds with `-j "$CBUILD_JOBS"` (owner tweak)
3. Rebuild BOTH images, then recreate (a plain `docker restart` keeps the old
   image): `docker compose -p azerothcore-test build ac-worldserver ac-authserver`
   then `docker compose -p azerothcore-test up -d --no-deps ac-worldserver ac-authserver`.
4. The worldserver's built-in updater applies new SQL migrations at boot (watch
   for the four "database is up-to-date!" lines). Custom rows live in the
   1900000+ ID range upstream never touches.
5. Boot-verify: `World Initialized`, nine `[Paragon]` module lines, no errors,
   `Loaded 1271 Extra Spell Bonus Data` (count proves spell 1900014 entered the
   store).

Known local divergence riding in the snapshot (beyond §1–§2): an
**undocumented LFG fix** — `LFGMgr.cpp`/`LFGScripts.cpp` `IsContinue` changes
with a full-file backup at `LFGMgr.cpp.pre-iscontinue` (owner-made, same
`.pre-*` naming as the compose-override backups); the owner's Dockerfile
`CBUILD_JOBS` pin; mod-collections' three `pending_db_characters` SQL files;
and the compose override backups. All must keep surviving future merges.

## Track reorder (2026-08-18)

The milestone track was reordered wholesale (design pass: recurring reward
types spread out, class/generic alternating, QoL roughly every 4th slot,
collection-scaling ladders at the century marks of the first half). Any
section ABOVE this one that names a milestone level refers to the
PRE-reorder track. Old -> new:

| old | new | reward | | old | new | reward |
|---|---|---|---|---|---|---|
| 25 | 25 | +5 talents | | 550 | 275 | Benediction +4 |
| 50 | 50 | OOC speed | | 575 | 475 | Divinity +4 |
| 75 | 150 | swim speed | | 600 | 400 | quest ladder |
| 100 | 250 | mount cast -1s | | 625 | 625 | Anticipation +3 |
| 125 | 75 | Divine Strength +4 | | 650 | 725 | Seals of the Pure +2 |
| 150 | 125 | Consecration burst | | 675 | 825 | Conviction +2 |
| 175 | 175 | trainer wave 1 | | 700 | 675 | dual blessings |
| 200 | 225 | dual auras | | 725 | 850 | enchant-slot ladder |
| 225 | 350 | glyph slot | | 750 | 750 | instant moving mount |
| 250 | 375 | ilvl attunement | | 775 | 550 | +5 talents |
| 275 | 650 | dual weapon enchant | | 800 | 925 | scaling -2 more |
| 300 | 100 | mount XP ladder | | 825 | 800 | ghost sprint |
| 325 | 200 | companion XP ladder | | 850 | 875 | durability -75% |
| 350 | 325 | Faithful Leap | | 875 | 950 | fall damage -50% |
| 375 | 600 | spirit regen 3x | | 900 | 525 | Beyond Mastery I |
| 400 | 450 | +1 talent /100 | | 925 | 775 | Beyond Mastery II |
| 425 | 975 | slow reduction | | 950 | 900 | Beyond Mastery III |
| 450 | 575 | Avenger's Shield x5 | | 975 | 500 | transmog ladder |
| 475 | 300 | achievement ladder | | 1000 | 1000 | title + 10 talents |
| 500 | 700 | scaling -2 | | | | |

Mechanical changes: MILESTONE_* Lua tables renamed to their new levels;
TRAINER_RANKS_900/925/950 SPECIAL keys -> _525/_775/_900; gate spells
1900076-78 RENAMED "Paragon Level 525/775/900" (spell ids and trainer_spell
rows unchanged - only Name_Lang server+client, hence a client patch);
EXTENDED_TALENTS + Consecration gate milestones remapped; module
MILESTONE_LEVEL constants now: achievement 300, quest 400, transmog 500,
glyph 350, ilvl 375, enchant slots 850, collection_xp 100/200. The
milestone-175 wave and its gate 1900007 are deliberately unchanged.
