# Pinned bases for the patches in this directory

| patch | applies to | base commit |
|---|---|---|
| `01-core-paragon.patch` | `github.com/mod-playerbots/azerothcore-wotlk` branch `Playerbot` | `efe123fab543c5faf3c477674ec17a18fd59f09f` |
| `02-core-profession-xp.patch` | same | same |
| `04-core-docker-build-jobs.patch` | same | same |
| `05-mod-ale.patch` | `github.com/azerothcore/mod-eluna` | `9e5b8c66efeb383871ec58b925e47094c92cc8d5` |
| `06-AccountBound.patch` | `github.com/AlsoNotMehh/AccountBound` | `f7ba75b14bdf04f4a4e711f0b6f71a0589ea4649` |
| `07-mod-ale-profession-xp.patch` | `github.com/azerothcore/mod-eluna` | `9e5b8c66efeb383871ec58b925e47094c92cc8d5` |

Apply the core patches in `01`, `02`, `04` order. Apply the mod-ale patches in
`05`, `07` order. The profession-XP patches are deliberately separate layers:
`02` expects the core state produced by `01`, and `07` expects the ALE state
produced by `05`.

This fork itself is based on `Grim-Batol/Paragon-Anniversary` @ `a3cb1bb5d9b3983154b9e7a71459b199fcea0d9f`.
Its sole authoritative install branch is `main` at
`github.com/tomfranz2000-glitch/Paragon-Anniversary`; do not assemble a release
from a feature or historical branch.

## Required module pin

| module | authoritative branch | required commit | reason |
|---|---|---|---|
| `github.com/tomfranz2000-glitch/mod-transmog` | `master` | `31633595cad7b12042b6484ffe3ea34f355b9821` | Includes `StoreNewItem` appearance capture and the required transmog configuration defaults. |

Clone the fork, not stock `azerothcore/mod-transmog`, and detach at the tested
revision:

```bash
cd /path/to/azerothcore
git clone --branch master --single-branch \
    https://github.com/tomfranz2000-glitch/mod-transmog.git \
    modules/mod-transmog
git -C modules/mod-transmog checkout --detach \
    31633595cad7b12042b6484ffe3ea34f355b9821
```

## !! `01-core-paragon.patch` TOUCHES 13 FILES, AND ONE CARRIES NO MARKER !!

Twelve of the thirteen patched core files contain a `Paragon` comment marker.
`src/server/shared/DataStores/DBCfmt.h` does **not**. Anything that rebuilds
this patch by grepping for markers will silently drop it, and then
`MAX_TALENT_RANK 9` in `DBCStructure.h` misparses every row of `Talent.dbc`
because `TalentEntryfmt` still describes the 5-rank layout. The two are one
change in two files; never apply or regenerate one without the other.

## mod-ale is required, not optional

Clone `github.com/azerothcore/mod-eluna` with the explicit target directory
**`modules/mod-ale`**:

```bash
cd /path/to/azerothcore
git clone https://github.com/azerothcore/mod-eluna.git modules/mod-ale
git -C modules/mod-ale checkout --detach \
    9e5b8c66efeb383871ec58b925e47094c92cc8d5
git -C modules/mod-ale rev-parse HEAD
```

The default clone directory, `modules/mod-eluna`, is not a supported name.
The core's `modules/CMakeLists.txt` only runs `ConfigureALEModule` when the
directory matches `mod-ale`; under the default name the build discovers the
sources but omits the Lua headers and `lualib`, then fails at `lua.h`.

The server Lua will not load on stock `mod-eluna`. `05-mod-ale.patch` adds the
`PLAYER_EVENT_ON_CAN_LEARN_TALENT` (74), `PLAYER_EVENT_ON_KILL_REWARD` (75),
and `MAP_EVENT_ON_ENCOUNTER_COMPLETE` (36) hooks; the
`Creature:GetAtLevelXPReward()` and `Player:IsPlayerBot()` methods; and widens
`ItemMethods` to the full enchantment-slot range. The follow-up
`07-mod-ale-profession-xp.patch` adds the authoritative
`PLAYER_EVENT_ON_PROFESSION_ACTION` (76) bridge. These patches are
Paragon-exclusive, which is why they live here rather than in a separate fork.

Paragon also depends on mod-ale's `ObjectVariables.ext`, which defines
`SetData` and `GetData`. CMake installing the extension is not sufficient for
the production Docker image: copy the module's complete `LuaEngine/extensions`
directory into the configured `ALE.ScriptPath` as documented in
[`doc/INSTALL.md`](../doc/INSTALL.md#4-generate-profession-data-and-install-server-lua).
