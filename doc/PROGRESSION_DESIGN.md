# Paragon Progression Design

Design for reworking the Paragon-Anniversary experience system on the test server.
Gameplay spec first, implementation notes at the end. Values marked **[tunable]**
remain configurable. Status: implemented test-realm economy, 2026-08-31.

---

## Vision

Paragon is an **endgame-onward, effectively unbounded progression track** that
rewards playing the whole game, not just repeating the most efficient activity.
Two lanes deliver that:

- **Repeatable sources** (mobs, quests, crafting, gathering, and processing) pay what the content is worth by
  Blizzard's own difficulty math — endgame pays best per hour, but nothing is
  worthless.
- **One-time sources** (achievements, collections, reputation progress, skill
  mastery, and profession recipes) are where completionism
  concentrates: they pay once, so chasing breadth beats repeating one farm.

The curved cost to reach Paragon 2000 is **1,845,119,090 XP**. The eight
bounded catalog pools total **1,004,442,000 XP**, or about **54.44%** of that
progression target. Reputation is additional and intentionally variable with
the factions available to the account:

| One-time pool | Complete-catalog XP |
|---|---:|
| Achievements (12,720 eligible points × 10,000) | 127,200,000 |
| Appearance item IDs | 354,984,000 |
| Mount spells | 187,933,000 |
| Companion spells | 83,525,000 |
| Toys (78 researched item IDs) | 43,500,000 |
| Heirlooms (38 current item IDs × 100,000) | 3,800,000 |
| Skill mastery (12,700 points × 5,000) | 63,500,000 |
| Profession recipes | 140,000,000 |
| **Bounded-catalog total** | **1,004,442,000** |

---

## Experience sources

### Creature kills — at-level formula

A mob grants the XP it would be worth to a player *of the mob's own level*,
regardless of the recipient's level. Mobs zero through nine levels below the
recipient pay that full value; mobs ten or more levels below pay a flat 50%.

Formula (matches the core's own base-XP math):

| Map/zone content tier | Base XP at mob level L | Example |
|---|---|---|
| Classic | `5·L + 45` | Level 1 boar = **50** |
| The Burning Crusade | `5·L + 235` | Level 70 mob = 585 |
| Wrath of the Lich King | `5·L + 580` | Level 80 mob = 980 |

- The native calculation is authoritative: elite rank, `ExperienceModifier`,
  low-health `HealthModifier`, no-XP flags, partial player-damage scaling, map
  content tier, and the realm kill-XP rate all apply exactly as they do in the
  core.
- Creature override rows do not replace this value. This prevents low-health
  trash creatures from receiving a generic, inflated elite reward.

#### Instance difficulty and era scaling

The native at-level monster pool receives one content-context factor:

| Content | Factor |
|---|---:|
| World content and normal five-player dungeons | 1.00× |
| TBC heroic dungeon | 1.25× |
| WotLK heroic dungeon | 1.50× |
| TBC raid | 2.00× |
| WotLK normal raid | 2.50× |
| WotLK heroic raid | 4.00× |

TBC raids have a single factor because the expansion has no heroic raid mode.
For WotLK, heroic means an actual heroic map difficulty (10H/25H); a 25-player
normal raid is still normal, and Ulduar encounter hard modes remain under the
2.50× normal-raid rule because they do not change map difficulty.
The only era override is map 249, Onyxia's Lair: 3.3.5 contains its level-80
WotLK raid but retains expansion 0 on the reused Map.dbc row, so it is treated
as a WotLK normal raid and receives 2.50×.

The complete ordering is:

```text
native at-level monster XP
× instance content factor
× gray factor
× group share
→ truncate once
× personal Paragon XP modifiers
```

The factor cannot create XP for a creature whose native reward is zero.

### Quests — at-level value

A quest grants its own at-level XP value (from `quest_template` reward XP at the
quest's level), regardless of the player's level. A Northrend quest ≈ 20k, a
classic zone quest ≈ hundreds — old-world questing stays meaningful without
competing with endgame.

- **Repeatable/daily quests: included for now.** ⚠ Flagged for observation —
  full-value infinite turn-ins are the most likely degenerate farm in this
  design. Revisit with data (see Watchlist).

### Achievements — one-time, points-scaled

Kept as the completionist backbone. Replace the flat 100 with
**achievement points × 10,000 XP** [tunable]: a 10-pointer = 100,000, and a
50-point meta = 500,000. The configured per-point value is the final award;
there is no runtime one-time multiplier. Inherently one-time, inherently
breadth-rewarding. A durable account claim mirror prevents alts and
Alliance/Horde faction counterparts from paying the same achievement twice.
The migration seeds achievements already earned on any account without XP,
backpay, or reconciliation; future sub-80 completions claim once and retain the
existing bank-until-eligible behavior.

### Skill mastery — 5,000 flat XP per new point

Each genuinely new eligible high-water point grants **exactly 5,000 XP**
(`UNIVERSAL_SKILL_EXPERIENCE = 5000`). That is the stored and awarded value,
and it bypasses every personal XP modifier. In account-linked mode the
high-water mark is account-wide, so an alt, an unlearn/relearn cycle, or a
replayed callback cannot farm it; in character-linked mode it follows that
character instead.

The bounded mastery catalog contains **12,700 points worth 63,500,000 XP**:

- 14 professions at 450 points each: 6,300 points;
- 15 canonical use-leveled weapon tracks at 400 points each: 6,000 points; and
- lockpicking at 400 points.

Fist Weapons (473) canonicalizes to Unarmed (162), because AzerothCore advances
both from one attack. The eligible weapon tracks are Swords, Axes, Bows, Guns,
Maces, Two-Handed Swords, Staves, Two-Handed Maces, Unarmed/Fist Weapons,
Two-Handed Axes, Daggers, Thrown, Crossbows, Wands, and Polearms. Defense (95),
Dual Wield (118), Feral Combat (134), Shield (433), Riding (762), and
Runeforging (776) are excluded, as are direct auto-max grants: only genuine
point-by-point skill updates can advance the ledger. Existing values are seeded
without retroactive payment, while genuine future points earned below level 80
are durably banked and paid once the progression scope becomes eligible.

### Profession recipes — source-ranked, one-time

The first account claim of each generated final craft-spell ID pays a flat
source-ranked value. The **3,481 rewardable recipes total 140,000,000 XP**, with
a 5,000 XP floor and 1,000,000 XP cap. Trainer and automatic recipes sit at the
floor; quests, limited/expensive vendors, loot, reputation, discoveries, and
time-gated sources scale upward from their easiest legitimate acquisition path.
Alternate teaching items and wrappers collapse onto the same final craft spell.
The 77 unresolved or invalid candidates are quarantined rather than guessed.

Claims are account-wide. Existing spellbooks are version-seeded per character
without XP; there is no reconciliation or backpay. A genuine recipe learned
below level 80 is durably banked and paid unchanged when an eligible character
logs in or reaches the threshold.

### Collection unlocks — difficulty-scaled, one-time

The first account unlock of a transmog appearance, companion, mount, toy, or
heirloom pays the final value written by the collection generator:

| Catalog | IDs | Budget | Floor | Cap |
|---|---:|---:|---:|---:|
| Appearance item IDs | 23,185 | 354,984,000 | 5,000 | 3,000,000 |
| Mount spells | 311 | 187,933,000 | 250,000 | 10,000,000 |
| Companion spells | 205 | 83,525,000 | 100,000 | 4,000,000 |
| Toy item IDs | 78 | 43,500,000 | 50,000 | 3,000,000 |
| Heirloom item IDs | 38 | 3,800,000 | 100,000 | 100,000 |

The easiest legitimate acquisition path considers trainers, quests,
achievements, vendors (price, stock, reputation and currency), direct and
reference loot, encounter access, rarity, and time gates. Source-less
player-facing entries deliberately receive a rare future-content score because
the realm plans to make them obtainable later. The generator excludes only
verified NPC-equipment, QA/test, and placeholder records. Rewards stay keyed
per appearance item ID, including visual duplicates, as an intentional policy.
Unknown IDs outside the generated whitelist pay nothing and create no claim.
Reward mirrors prevent relearning, relogging, or another character from paying
the same unlock again; current collections are seeded without backpay.
Toy ownership is the EZCollections account-row created on first use, not mere
inventory possession. Heirloom ownership is its corresponding account-row on
first storage. Infinite replacement copies from the journal therefore cannot
pay twice. Toy values are an explicit per-ID 78-item acquisition audit with a
rarity-informed 50,000 XP floor, so TCG, promotion, removed-event, nested-loot,
and time-gated sources are not misclassified by a bare drop-rate query. Both
account-item tables key by `kind` and item ID, preventing a shared numeric toy
and heirloom ID from colliding.

### Reputation progress — 50 flat XP per new point

Every committed reputation point above the account's previous per-faction
high-water pays **exactly 50 XP**. Post-commit ALE event 82 observes final
absolute standing after rates, caps, racial effects, and spillover. Reputation
loss never lowers the high-water, so loss/regain cannot be farmed. The 15
Alliance/Horde faction-change pairs share their lower numeric faction ID,
preventing conversion from paying the same progress twice.

Existing standings for every character on the account are converted from
stored offsets to race/class-specific absolute standings and seeded without
backpay at login. Gains below level 80 bank their already-final XP. Disabling
the source advances the high-water without creating later backpay, and
playerbots cannot generate pending rewards.

### Profession actions — resource-valued repeatable XP

Successful crafting, gathering, fishing, prospecting, milling, and
disenchanting use server-authoritative action context and a generated valuation
table. Craft values derive from consumed materials; gather values derive from
expected primary yield; processing values derive from consumed inputs. Content
tier, bounded scarcity/cooldown adjustments, fixed-per-action semantics, and a
hard per-unit quantity clamp keep output-count recipes, AoE loot, and malformed
payloads from inflating awards. These repeatable awards cross the normal
`OnExperienceCalculated` multiplier boundary exactly once. Unknown or
skill-mismatched actions grant nothing. Repeatable profession actions are not
banked below level 80; they simply begin awarding Paragon XP at eligibility.

### PvP Merit — activity, objectives, and breadth

PvP pays repeatable base XP for final honor, credited active battleground and
Wintergrasp minutes, match outcomes, capped objectives, rated arenas,
skirmishes, OutdoorPvP objectives, and the first three distinct duel opponents
per reset-day. A separate weekly award promotes breadth across distinct
battleground maps, rated brackets, Wintergrasp, and legacy OutdoorPvP zones.

Participation gates, victim/roster diminishing returns, account-wide reset
caps, same-account rejection, and durable bridge tokens make cooperative PvP
valuable without turning arranged replays into the best farm. Real recipients
receive full values against playerbot opponents; playerbots themselves never
receive account-wide Paragon XP. All values and the exact rules are documented
in [`PVP_MERIT.md`](PVP_MERIT.md).

### Future one-time sources (deferred, not in scope)

Bestiary first-kills per creature entry · first clear per dungeon/raid boss ·
rare spawns.
Basic XP behavior ships first; these come as add-on modules afterward.

---

## Eligibility: starts at level 80

Paragon XP accrues **only at level 80** (`MINIMUM_LEVEL_FOR_PARAGON_XP = 80`).

**Pre-80 banking (placeholder solution, decided).** One-time XP rewards earned
before level 80 are **banked** and paid out as a single lump sum on reaching 80.
This covers achievements, skill-mastery and reputation high-water points,
profession recipe claims, and every collection claim. Mount/companion teaching
and the appearance/toy/heirloom reconciliation ticker settle eligible unlocks
after the account reaches level 80.

Regular-lane sources (mobs, quests, and repeatable profession actions) are
deliberately **not** banked. Mob and quest rewards continue feeding normal
character leveling where applicable; repeatable profession Paragon pay simply
begins at level 80.

Forward-only policy is deliberate: existing achievements, collections, skills,
recipes, and reputation standings are seeded without retroactive XP.

## Party and raid credit — ships with this, not after

Creature XP follows AzerothCore's native per-recipient group kill-credit path,
so tanks, healers, and other participating members are credited independently
of who lands the killing blow.

- AzerothCore's `KillRewarder` decides who receives group kill credit. This is
  independent of whether the final blow came from a player, altbot, pet,
  guardian, or totem.
- The standard group bonus is divided by the number of participating members
  *before* Paragon eligibility is checked: 1 = 100%, 2 = 50%, 3 = 38.87%,
  4 = 32.5%, 5 = 28%. If only one real level-80 player in a five-member party
  is eligible, that player receives 28%; the four rejected shares are not
  redistributed.
- Dead players do not receive Paragon XP. Playerbots count as participating
  group members but never receive it, because progression is account-wide for
  real players.

---

## The curve

**Goal: level 1000+ is genuinely reachable for a dedicated player** (a long
season of committed play, not a lifetime), with no hard wall — growth slows its
own growth instead.

- First level: **100,000 XP**.
- Per-level growth with decaying rate:

  ```
  cost(L) = cost(L−1) × (1 + r(L))      r(L) = r₀ / (1 + L/k)
  ```

  Realm constants: **r₀ = 0.029552484, k = 20** [tunable]. Early levels compound
  noticeably; by the deep hundreds growth is nearly flat — an endless but
  honest tail. This keeps the level-2000 cost at **1,454,342 XP**, only 3 XP
  above the previous curve's **1,454,339 XP** endpoint. With `k = 20`, integer
  rounding makes that the closest attainable endpoint.

Projected shape (base = 100,000, r₀ = 0.029552484, k = 20; illustrative income
assumption ~400k XP/hour of dedicated endgame play). This is a curve example,
not a post-instance-multiplier income forecast; live dungeon and raid
XP-per-hour should be measured before retuning it:

| Paragon level | Cost of that level | Cumulative XP through level | ≈ Hours |
|---|---|---|---|
| 1 | 100,000 | 100k | <1 |
| 10 | 122,665 | 1.12M | ~3 |
| 50 | 200,629 | 7.71M | ~19 |
| 100 | 275,134 | 19.71M | ~49 |
| 250 | 443,357 | 74.36M | ~186 |
| 500 | 652,566 | 212.61M | ~532 |
| 1000 | 971,341 | 622.37M | ~1,556 |
| 2000 | 1,454,342 | 1.847B | ~4,616 |

Tuning method: pick three anchors and solve the base/r₀/k combination to fit —
don't tune constants in the abstract. The current 100k/0.029552484/20 realm preset
is substantially slower than the original draft and should be evaluated from
live XP-per-hour measurements before further adjustment.

Related config changes:

- `PARAGON_LEVEL_CAP`: 999 → **10000** (or effectively uncapped) — the curve is
  the limiter, not a wall.
- Early-level ×3 booster (`LOW_LEVEL_THRESHOLD` / multipliers): **removed** —
  the cheap first levels already are the on-ramp; two overlapping accelerators
  muddy tuning.
- Points: unchanged — **1 point per level**, spend/respec as today. (Milestone
  bonus points every N levels: possible later, out of scope.)

---

## Watchlist (revisit with live data)

1. **Repeatable quests** — the deliberate open risk. Watch XP-per-hour of
   repeatable turn-in loops vs heroic clears; if a loop wins, discount
   repeatables to ~10% or exclude them.
2. **AoE trash farming** — at-level formula caps the damage (trash ≈ 1–2k), but
   watch mass-pull farms vs boss play.
3. **Daily-quest stacking** — 25 dailies ≈ 500k on quest values above; fine as
   a strong daily ritual, but it anchors the income assumption. If it dwarfs
   dungeon play, tune quest values, not the curve.
4. **Profession economy loops** — a full new 1-450 profession high-water track
   is 2.25M exact XP once per progression scope; a 1-400 weapon or lockpicking
   track is 2M. Track repeatable action XP/hour by profession and tier; tune
   generated resource weights rather than the curve.
5. Bot accrual noise in the paragon tables.

## Implementation map (brief)

The progression logic remains under `paragon/modules/`. Exact creature values
and reward attribution use the ALE additions carried by
`patches/05-mod-ale.patch`; profession action attribution additionally requires
`patches/02-core-profession-xp.patch` and `patches/07-mod-ale-profession-xp.patch`.
PvP Merit additionally requires `patches/08-core-pvp-merit.patch` and
`patches/09-mod-ale-pvp-merit.patch`. Reputation additionally requires
`patches/10-core-reputation-xp.patch` and
`patches/11-mod-ale-reputation-xp.patch`:

| Piece | Mechanism |
|---|---|
| At-level creature/quest values | ALE `Creature:GetAtLevelXPReward()` plus `Map:GetExpansion()` for instance-scaled kills; generated QuestXP data for quests |
| Achievement points scaling | `OnBeforeUpdatePlayerExperience` for achievement source |
| Level-80 gate | existing `MINIMUM_LEVEL_FOR_PARAGON_XP` config |
| Achievement banking | `banked_experience` column on the paragon character table; achievement hook accrues to it below 80; level-80 event pays it out through the normal XP pipeline |
| Party credit | ALE event 75, forwarded from `OnPlayerRewardKillRewarder`, once per core-credited recipient |
| Profession actions | ALE event 76; generated `paragon_profession_data.Resolve()` valuation; per-session action-token dedupe |
| Skill mastery | ALE event 62; explicit profession/weapon/lockpicking allowlist; Fist Weapons 473 → Unarmed 162; `paragon_profession_progress` high-water/pending ledger; flat award path |
| PvP Merit | ALE events 77-81; `paragon_pvp_reward_claim` account ledger; `paragon_pvp_xp.lua` policy |
| Reputation progress | Post-commit ALE event 82; `paragon_reputation_progress` account/faction high-water and pending ledger; flat 50-XP award path |
| Toys and heirlooms | EZCollections account ownership; `(kind, item_id)` value and `(account_id, kind, item_id)` pending-claim keys |
| Curve | recompute next-level cost on `OnParagonLevelChanged` via `SetExperienceForNextLevel` |

## Open decisions

1. ~~Level-80 start~~ — resolved: pre-80 banking of one-time rewards, with
   existing achievements, collections, skills, recipes, and reputation
   standings seeded forward-only without backpay.
2. ~~Bot exclusion~~ — resolved: bots count in the group divisor but do not receive account-wide Paragon XP.
3. ~~Party share~~ — resolved: use the standard party/raid group-rate schedule,
   counting participants before Paragon eligibility and splitting equally.
4. Curve constants after anchor discussion (r₀, k).
