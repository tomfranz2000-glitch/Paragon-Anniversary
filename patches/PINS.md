# Pinned bases for the patches in this directory

| patch | applies to | base commit |
|---|---|---|
| `01-core-paragon.patch` | `github.com/mod-playerbots/azerothcore-wotlk` branch `Playerbot` | `efe123fab543c5faf3c477674ec17a18fd59f09f` |
| `04-core-docker-build-jobs.patch` | same | same |
| `05-mod-ale.patch` | `github.com/azerothcore/mod-eluna` | `9e5b8c6` |
| `06-AccountBound.patch` | `github.com/AlsoNotMehh/AccountBound` | `f7ba75b14bdf04f4a4e711f0b6f71a0589ea4649` |

This fork itself is based on `Grim-Batol/Paragon-Anniversary` @ `a3cb1bb5d9b3983154b9e7a71459b199fcea0d9f`.

## !! `01-core-paragon.patch` TOUCHES 13 FILES, AND ONE CARRIES NO MARKER !!

Twelve of the thirteen patched core files contain a `Paragon` comment marker.
`src/server/shared/DataStores/DBCfmt.h` does **not**. Anything that rebuilds
this patch by grepping for markers will silently drop it, and then
`MAX_TALENT_RANK 9` in `DBCStructure.h` misparses every row of `Talent.dbc`
because `TalentEntryfmt` still describes the 5-rank layout. The two are one
change in two files; never apply or regenerate one without the other.

## mod-ale is required, not optional

The server Lua will not load on stock `mod-eluna`. `05-mod-ale.patch` adds the
`PLAYER_EVENT_ON_CAN_LEARN_TALENT` (74) and `MAP_EVENT_ON_ENCOUNTER_COMPLETE`
(36) hooks, an `IsPlayerBot` method, and widens `ItemMethods` to the full
enchantment-slot range. It is Paragon-exclusive, which is why it lives here as
a patch rather than as its own fork.

Paragon also depends on mod-ale's `ObjectVariables.ext`, which defines
`SetData` and `GetData`. CMake installing the extension is not sufficient for
the production Docker image: copy the module's complete `LuaEngine/extensions`
directory into the configured `ALE.ScriptPath` as documented in
[`doc/INSTALL.md`](../doc/INSTALL.md#step-1-copy-the-paragon-scripts-and-ale-extensions).
