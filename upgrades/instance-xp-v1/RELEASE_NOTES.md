# Instance creature XP v1

This server-only upgrade adds expansion-aware creature-XP factors:

- TBC heroic dungeon: `1.25x`
- WotLK heroic dungeon: `1.5x`
- TBC raid: `2x`
- WotLK normal raid: `2.5x`
- WotLK heroic raid: `4x`

The multiplier is applied to the native at-level creature XP pool before the
existing gray penalty and native group share. Normal dungeons, Classic raids,
world creatures, zero-XP creatures, PvP, professions, quests, and one-time
collection rewards are unchanged. Map 249 is deliberately treated as WotLK so
the level-80 Onyxia encounter is not classified by its reused Classic Map.dbc
entry.

The release adds the compiled ALE method `Map:GetExpansion()`. Replacing Lua or
restarting an old image is insufficient: the worldserver must be rebuilt and
its container recreated. There are no client files in this upgrade.

## Install

This package supports an existing installation based on Paragon commit
`05ea122dc80b6a08ba01a6f0506523a13cdbe1c2`. Verify the downloaded ZIP with its
adjacent `.zip.sha256` file, extract it without changing the directory layout,
and follow `README.md`: run the default read-only plan first, then repeat the
same command with `--apply`. Unsupported, custom, or partially patched states
are refused without being guessed at.
