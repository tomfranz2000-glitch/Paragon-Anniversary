# Paragon Progression Design

Design for reworking the Paragon-Anniversary experience system on the test server.
Gameplay spec first, implementation notes at the end. Values marked **[tunable]**
are starting points, not commitments. Status: draft for review, 2026-08-17.

---

## Vision

Paragon is an **endgame-onward, effectively unbounded progression track** that
rewards playing the whole game, not just repeating the most efficient activity.
Two lanes deliver that:

- **Repeatable sources** (mobs, quests) pay what the content is worth by
  Blizzard's own difficulty math — endgame pays best per hour, but nothing is
  worthless.
- **One-time sources** (achievements now; more later) are where completionism
  concentrates: they pay once, so chasing breadth beats repeating one farm.

---

## Experience sources

### Creature kills — at-level formula

A mob grants the XP it would be worth to a player *of the mob's own level*,
regardless of the killer's level. No gray-out, no level penalty.

Formula (matches the core's own base-XP math):

| Content tier | Base XP at mob level L | Example |
|---|---|---|
| Classic (1–60) | `5·L + 45` | Level 1 boar = **50** |
| TBC (61–70) | `5·L + 235` | Level 70 mob = 585 |
| WotLK (71–80) | `5·L + 580` | Level 80 mob = 980 |

- Elite: **×2** [tunable].
- **Boss override table**: `paragon_config_experience_creature` holds hand-tuned
  values for marquee kills (target: endgame dungeon boss ≈ **12,000**, raid
  bosses above that) [tunable]. Formula covers the world; the table covers the
  ~hundreds of bosses that matter.

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
**achievement points × 1000** [tunable]: a 10-pointer = 10,000 (≈ a full
paragon level early on), a 50-point meta = 50,000 (≈ four raid bosses).
Inherently one-time, inherently breadth-rewarding.

### Skill-ups — kept, flat 100 per tick

Every skill point gained grants **100 XP** (`UNIVERSAL_SKILL_EXPERIENCE = 100`)
[tunable — placeholder value, real tuning later]. Naturally bounded: a full
1–450 profession ≈ 45k XP (~4–5 paragon levels early on), then that profession
is done. Profession *mastery* one-time bonuses may still come later in the
future-sources batch, on top of ticks.

### Future one-time sources (deferred, not in scope)

Bestiary first-kills per creature entry · first clear per dungeon/raid boss ·
reputation milestones (each Exalted) · profession mastery · rare spawns.
Basic XP behavior ships first; these come as add-on modules afterward.

---

## Eligibility: starts at level 80

Paragon XP accrues **only at level 80** (`MINIMUM_LEVEL_FOR_PARAGON_XP = 80`).

**Pre-80 banking (placeholder solution, decided).** One-time XP rewards earned
before level 80 are **banked** and paid out as a single lump sum on reaching 80.
For now that covers **achievements only** (the only one-time source in scope);
future one-time sources inherit the same banking mechanic when they ship.

Regular-lane sources (mobs, quests) are deliberately **not** banked: before 80
they pay their normal reward — character XP that fuels leveling — so nothing is
being missed. Paragon pay for them simply begins at 80.

Known residue, accepted for now: banking is forward-only — achievements earned
before the feature ships (and existing level-80s) get nothing retroactively
unless a one-shot audit is added later. [open]

## Party and raid credit — ships with this, not after

Today only the killing player earns creature XP; with 12k bosses that breaks
group play (tank/healer earn zero).

- Paragon XP from kills is distributed **exactly like regular kill XP**: split
  across eligible group members with the standard group-size bonus multipliers,
  standard share range, dead members excluded. If the base game would have
  given a member a share of the mob's XP, they get the equivalent paragon
  share; the only extra rule is the level-80 gate.
- Tuning consequence: boss override values are *pre-split* pool values — a
  12,000 boss in a 5-man pays each member ~3,400. Calibrate overrides with
  that in mind (or raise them if the intent is 12k per person).
- **Playerbots note**: bots are Players and would accrue alongside their group.
  Harmless mechanically; consider a config toggle to exclude bot accounts to
  keep the paragon tables clean. [open]

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
4. **Profession powerleveling** — buying mats and spamming 1–450 ≈ 45k XP in
   an hour. Bounded per profession, but check it doesn't beat playing.
5. Bot accrual noise in the paragon tables.

## Implementation map (brief)

All pieces are drop-in files under `paragon/modules/` using the existing
Mediator surface — no upstream file edits:

| Piece | Mechanism |
|---|---|
| At-level creature/quest values | `OnExperienceCalculated` — replace the config-lookup value with the formula result |
| Boss overrides | existing `paragon_config_experience_creature` table (already consulted first) |
| Achievement points scaling | `OnBeforeUpdatePlayerExperience` for achievement source |
| Level-80 gate | existing `MINIMUM_LEVEL_FOR_PARAGON_XP` config |
| Pre-80 banking | `banked_experience` column on the paragon character table; achievement hook accrues to it below 80; level-80 event pays it out through the normal XP pipeline |
| Party credit | kill-event module mirroring core group-XP distribution (split, group bonus, range/alive rules) |
| Curve | recompute next-level cost on `OnParagonLevelChanged` via `SetExperienceForNextLevel` |

## Open decisions

1. ~~Level-80 start~~ — resolved: pre-80 banking of one-time rewards
   (achievements for now). Remaining sub-question: retroactive audit for
   already-earned achievements / existing 80s.
2. Bot exclusion toggle: yes/no.
3. ~~Party share~~ — resolved: mirror regular kill-XP distribution rules.
4. Curve constants after anchor discussion (r₀, k, boss override scale —
   remembering overrides are pre-split pool values).
