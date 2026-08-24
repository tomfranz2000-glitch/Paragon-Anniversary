# PvP Merit Paragon XP

PvP Merit rewards every supported Wrath PvP lane through
`serverside/paragon/modules/paragon_pvp_xp.lua`. The values in
`sql/04_insert_default_config.sql` and `sql/05_apply_anniversary_config.sql`
are the final authoritative **base** values. There is no global PvP multiplier.
Each semantic reward calls `Hook.AwardExperience(..., true)` once, after caps
and diminishing returns, so the player's ordinary Paragon XP bonuses apply
once without changing the base cap ledger.

## Economy

| Activity | Base Paragon XP |
|---|---:|
| Final honor | 8 per final honor point |
| Battleground / Wintergrasp presence | 4,000 per credited active minute |
| Win bonus | +1,000 per credited active minute |
| Draw bonus | +500 per credited active minute |
| Major / standard / assist match objective | 8,000 / 4,000 / 2,000, cumulatively capped at 20% of active-minute base |
| Rated arena 2v2 | 37,500 win / 26,250 loss |
| Rated arena 3v3 | 45,000 win / 31,500 loss |
| Rated arena 5v5 | 56,250 win / 39,000 loss |
| Arena skirmish | 11,250 win / 7,500 loss; 56,250 base XP per account/reset-day |
| Legacy OutdoorPvP objective | 30,000 major / 15,000 standard |
| Completed duel (`duelType=1`) | 5,000 winner / 2,000 loser for the first three distinct opponents per account/reset-day |
| Weekly breadth | 20,000 for each distinct qualifying category |

Weekly breadth categories are each battleground type/map completed, the first
rated win in each of 2v2/3v3/5v5, the first completed Wintergrasp battle, and
the first completed objective in each legacy OutdoorPvP type/zone. Skirmishes
and duels do not create breadth categories.

A real player receives the same values when some or all opponents are
playerbots. There is no bot-only practice rate. Playerbots are valid opponents
but can never receive account-wide Paragon XP themselves. A same-account
opponent invalidates the settlement; same-IP play is retained as an audit flag
and remains governed by the normal pair/roster DR rather than being blocked,
so households are not punished.

## Participation and anti-farm rules

Battleground and Wintergrasp settlement requires all of the following:

- at least 60 seconds of both match duration and the recipient's accumulated
  enrolled presence (the bridge's historical `activeSeconds` field);
- presence at the authoritative finish callback and no deserter/inactive flag;
- at least two credited active 60-second buckets;
- active buckets equal to at least 30% of enrolled presence buckets.

The bridge credits an active bucket from legitimate final honor, player-PvP
damage, effective allied PvP healing, an objective/flag/siege credit, or a
successful tactical action. Payout minutes are capped after eligibility: WSG
25, AB 30, EotS 25, AV 45, SotA 25, IoC 40, generic battleground 30, and
Wintergrasp 40.

Arena settlement requires a core-valid match of at least 15 seconds plus a
killing blow, at least 10,000 combined player-PvP damage/effective healing, or
a credited tactical action. Bot-only opposing rosters remain valid; the
canonical roster key includes all distinct opposing account IDs.

Final-honor credits against the same victim account in the rolling previous 30
minutes pay 100%, 50%, 10%, then 0%. Only player-HK honor uses victim-pair DR.
Faction/racial-leader honor and proven battleground bonus/objective honor use
the normal 8-per-point conversion without that pair DR. Unclassified fixed
`RewardHonor` calls fail closed so commands, quests, items, and unrelated
scripts cannot mint Paragon XP.

For event 77 source 3, real battleground type IDs remain unchanged and the
bridge uses collision-free context sentinels `254` for active
Battlefield/Wintergrasp honor and `253` for recognized legacy OutdoorPvP
honor. Lua accepts those nonzero proven contexts; source 4 remains denied.

Exact opposing arena-account rosters use a rolling 60-minute settlement DR:
games 1-3 pay 100%, games 4-5 pay 50%, game 6 pays 10%, and game 7 onward pays
0%. Both DR systems and every cap operate on base XP before personal modifiers.

Battleground score fields map to objective tiers as follows:

| Battleground | Mapping |
|---|---|
| WSG | flag capture major; flag return assist |
| EotS | flag capture major |
| AB / IoC | base assault and defense standard |
| AV | tower assault major; graveyard assault and tower defense standard; graveyard defense and mine capture assist |
| SotA | gate destruction major; demolisher destruction standard |

Wintergrasp supplies already classified counts: final destructible wall/tower
blow major, workshop capture standard, and wall/tower damaged-state transition
assist. Tactical actions are eligibility evidence, never an objective-count
substitute.

### Bridge attribution limits

The 3.3.5 core has no authoritative generic score for interrupts, dispels,
crowd control, resurrection, or damage absorption. Those actions therefore do
not increment `tacticalActions`; the reliable activity signals are credited
honor, opposing-player damage, allied-other effective healing, killing blows,
and objective/siege score changes. `UnitScript::OnHeal` runs after health is
modified, so `pvpHealingDone` excludes overheal, but absorbs are not healing
and are not counted.

Generic legacy OutdoorPvP captures credit each online member of the capturing
team inside the capture point's active radius. Silithyst turn-ins and the
Zangarmarsh graveyard flag instead credit the actual actor. Tracker state is
intentionally process-local: if ALE is hot-loaded or the server restarts during
an already-running match, battlefield, duel, and activity history is not
reconstructed, and the affected settlement fails closed rather than awarding
XP from incomplete evidence.

## Durable settlement and resets

`acore_ale.paragon_pvp_reward_claim` is the account-wide idempotency,
write-ahead, DR, cap, and breadth ledger. Every bridge callback must supply its
stable opaque ASCII token. A claim is inserted before awarding; the resulting
Paragon level/experience and the paid acknowledgement are then persisted in
one InnoDB update. Pending claims drain when Paragon state becomes ready, and
an in-session applied guard retries acknowledgement without invoking the XP
hook a second time.

Each claim also records its recipient character GUID. DR, caps, idempotency,
and entitlements remain account-wide in both progression modes. When
`LEVEL_LINKED_TO_ACCOUNT=0`, however, only that original character may drain a
pending payout; a crash-recovery claim can never migrate to another character
on the account. Account-linked mode continues to drain against the shared
account progression row.

Daily and weekly period keys follow the character database's AzerothCore reset
worldstates (`20005` daily, `20002` weekly). If either timestamp is absent or
invalid, the configured interval and fallback epoch are used with database
UTC time. Paid audit rows are retained for 90 days and stale pending rows for
365 days by default; those finite retention windows bound table growth and the
guaranteed delayed-replay horizon.

Durable award processing deliberately performs synchronous character-database
round trips for claim reservation, DR/cap calculation, payout, and
acknowledgement. This favors exact-once recovery over speculative throughput
optimizations, but it is an accepted operational scale risk: profile busy AV
and Wintergrasp settlements before production deployment. Do not weaken the
write-ahead or acknowledgement contract to reduce that latency.

The module never registers player event 12 and never converts generic
`XPSOURCE_BATTLEGROUND`. Honor conversion, match settlement, and breadth are
the only owned paths, preventing native battleground XP from double-paying.

## ALE bridge contract

The required extension events are:

```text
77 (player, victimOrNil, finalHonor, honorSource, battlegroundTypeId,
    arenaType, rated, generatedBattlegroundXP, eventToken)

78 (player, matchKind, result, durationSeconds, activeSeconds,
    presenceBuckets, activeBuckets, tacticalActions, battlegroundTypeId,
    mapId, instanceId, arenaType, rated, bracketId, playerTeam, winnerTeam,
    killingBlows, deaths, honorableKills, bonusHonor, damageDone, healingDone,
    pvpDamageDone, pvpHealingDone, objective1..objective5, isBot, accountId,
    opponentCount, realOpponentCount, botOpponentCount,
    uniqueOpponentAccounts, sameAccountOpponent, sameIpOpponent, inactive,
    deserter, opponentRosterKey, eventToken)

79 (player, battlefieldTypeId, battleId, zoneId, mapId, result,
    durationSeconds, activeSeconds, presenceBuckets, activeBuckets,
    tacticalActions, playerTeam, winnerTeam, attackerTeam,
    defenderTeamAtStart, endedByTimer, isBot, accountId, playerKills,
    pvpDamageDone, pvpHealingDone, objectiveMajor, objectiveStandard,
    objectiveAssist, realOpponentCount, botOpponentCount,
    uniqueOpponentAccounts, sameAccountOpponent, sameIpOpponent, inactive,
    deserter, opponentRosterKey, eventToken)

80 (player, outdoorPvPTypeId, objectiveId, objectiveEntry, objectiveTier,
    mapId, zoneId, team, participantCount, eventToken)

81 (winner, loser, duelType, durationSeconds, sameAccount, sameIp,
    winnerIsBot, loserIsBot, eventToken)
```

Apply the pinned core and ALE PvP Merit patches before building. The canonical
installer deploys the Lua module and schema, and `--check` verifies both.
