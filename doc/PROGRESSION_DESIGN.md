# Paragon Progression Design

Design for reworking the Paragon-Anniversary experience system on the test server.
Gameplay spec first, implementation notes at the end. Values marked **[tunable]**
are starting points, not commitments. Status: draft for review, 2026-08-17.

---

## Vision

Paragon is an **endgame-onward, effectively unbounded progression track** that
rewards playing the whole game, not just repeating the most efficient activity.
Two lanes deliver that:

- **Repeatable sources** (mobs, quests, crafting, gathering, and processing) pay what the content is worth by
  Blizzard's own difficulty math — endgame pays best per hour, but nothing is
  worthless.
- **One-time sources** (achievements and profession mastery) are where completionism
  concentrates: they pay once, so chasing breadth beats repeating one farm.

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
**achievement points × 2000 XP** [tunable]: a 10-pointer = 20,000, and a
50-point meta = 100,000. The configured per-point value is the final award;
there is no runtime one-time multiplier. Inherently one-time, inherently
breadth-rewarding.

### Profession mastery — 2000 flat XP per new point

Each genuinely new profession high-water point grants **exactly 2,000 XP**
(`UNIVERSAL_SKILL_EXPERIENCE = 2000`). That is the stored and awarded value,
and it bypasses every personal XP modifier. In
account-linked mode the high-water mark is account-wide, so an alt, an
unlearn/relearn cycle, or a replayed callback cannot farm it; in
character-linked mode it follows that character instead. A complete 1–450
profession is therefore 900,000 XP. Weapon, defense, riding, and lockpicking
never qualify. Existing skill values are seeded without retroactive payment,
while genuine future points earned below level 80 are durably banked and paid
once the account becomes eligible.

### Collection unlocks — difficulty-scaled, one-time

The first account unlock of a transmog appearance, companion, or mount pays the
final value written by the collection generator. Ordinary appearances pay
2,000 XP, baseline companions 60,000 XP, and baseline mounts 160,000 XP;
rarity overrides are doubled in the generator as well (for example, Invincible
pays 4,000,000 XP). Reward mirrors prevent
relearning, relogging, or another character from paying the same unlock again.

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

### Future one-time sources (deferred, not in scope)

Bestiary first-kills per creature entry · first clear per dungeon/raid boss ·
reputation milestones (each Exalted) · profession mastery · rare spawns.
Basic XP behavior ships first; these come as add-on modules afterward.

---

## Eligibility: starts at level 80

Paragon XP accrues **only at level 80** (`MINIMUM_LEVEL_FOR_PARAGON_XP = 80`).

**Pre-80 banking (placeholder solution, decided).** One-time XP rewards earned
before level 80 are **banked** and paid out as a single lump sum on reaching 80.
This covers achievements and profession high-water points. Other future
one-time sources can inherit the same banking mechanic when they ship.

Regular-lane sources (mobs, quests, and repeatable profession actions) are
deliberately **not** banked. Mob and quest rewards continue feeding normal
character leveling where applicable; repeatable profession Paragon pay simply
begins at level 80.

Known residue, accepted for now: banking is forward-only — achievements earned
before the feature ships (and existing level-80s) get nothing retroactively
unless a one-shot audit is added later. [open]

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

- First level: **30,000 XP**.
- Per-level growth with decaying rate:

  ```
  cost(L) = cost(L−1) × (1 + r(L))      r(L) = r₀ / (1 + L/k)
  ```

  Realm constants: **r₀ = 0.0429, k = 20** [tunable]. Early levels compound
  noticeably; by the deep hundreds growth is nearly flat — an endless but
  honest tail.

Projected shape (base = 30,000, r₀ = 0.0429, k = 20; income assumption
~400k XP/hour of
dedicated endgame play — heroic clears + dailies at the values above):

| Paragon level | Cost of that level | Total XP to reach | ≈ Hours |
|---|---|---|---|
| 1 | 30,000 | 30k | minutes |
| 10 | 40,295 | 352k | <1 |
| 50 | 82,134 | 2.84M | ~7 |
| 100 | 129,816 | 8.17M | ~20 |
| 250 | 259,353 | 37.6M | ~94 |
| 500 | 454,462 | 127M | ~318 |
| 1000 | 809,508 | 445M | ~1,112 |

Tuning method: pick three anchors and solve the base/r₀/k combination to fit —
don't tune constants in the abstract. The current 30k/0.0429/20 realm preset
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
4. **Profession economy loops** — a full new 1–450 high-water track is 900k
   exact XP once per progression scope. Track repeatable action XP/hour by
   profession and tier; tune generated resource weights rather than the curve.
5. Bot accrual noise in the paragon tables.

## Implementation map (brief)

The progression logic remains under `paragon/modules/`. Exact creature values
and reward attribution use the ALE additions carried by
`patches/05-mod-ale.patch`; profession action attribution additionally requires
`patches/02-core-profession-xp.patch` and `patches/07-mod-ale-profession-xp.patch`:

| Piece | Mechanism |
|---|---|
| At-level creature/quest values | ALE `Creature:GetAtLevelXPReward()` for kills; generated QuestXP data for quests |
| Achievement points scaling | `OnBeforeUpdatePlayerExperience` for achievement source |
| Level-80 gate | existing `MINIMUM_LEVEL_FOR_PARAGON_XP` config |
| Achievement banking | `banked_experience` column on the paragon character table; achievement hook accrues to it below 80; level-80 event pays it out through the normal XP pipeline |
| Party credit | ALE event 75, forwarded from `OnPlayerRewardKillRewarder`, once per core-credited recipient |
| Profession actions | ALE event 76; generated `paragon_profession_data.Resolve()` valuation; per-session action-token dedupe |
| Profession mastery | ALE event 62; `paragon_profession_progress` high-water/pending ledger; flat award path |
| Curve | recompute next-level cost on `OnParagonLevelChanged` via `SetExperienceForNextLevel` |

## Open decisions

1. ~~Level-80 start~~ — resolved: pre-80 banking of one-time rewards
   (achievements for now). Remaining sub-question: retroactive audit for
   already-earned achievements / existing 80s.
2. ~~Bot exclusion~~ — resolved: bots count in the group divisor but do not receive account-wide Paragon XP.
3. ~~Party share~~ — resolved: use the standard party/raid group-rate schedule,
   counting participants before Paragon eligibility and splitting equally.
4. Curve constants after anchor discussion (r₀, k).
