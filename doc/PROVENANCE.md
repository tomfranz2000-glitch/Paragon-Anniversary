# Provenance

Fork of <https://github.com/Grim-Batol/Paragon-Anniversary>, **AGPL-3.0**.

**Base commit:** `a3cb1bb5d9b3983154b9e7a71459b199fcea0d9f` (branch `main`).

Established by byte comparison against a fresh clone, not by metadata: the
origin tree carried no `.git`. 24 files were byte-identical to upstream and 9
carried local deltas; nothing upstream was missing.

Being AGPL-3.0, this fork is AGPL-3.0. If you run a modified version as a
network service, the licence requires you to offer its source to users.

## Prerequisites this repo does not contain

| what | why |
|---|---|
| `mod-eluna` + `patches/05-mod-ale.patch` | the server Lua needs two hooks and an `IsPlayerBot` method stock ALE does not have |
| `mod-transmog` (forked) | `paragon_transmog_bonus.lua` and `paragon_collection_rewards.lua` read `custom_unlocked_appearances` |
| `mod-collections` (optional) | the collection XP source reads `account_collection_*`; without it those awards are simply zero |
| a core with `patches/01-core-paragon.patch` applied | 13 files |

## Relationship to the other repos in this split

`ezcollections` and `allthethings` are independent. Paragon reads two of their
tables where present and degrades to zero rewards where absent; neither reads
anything of Paragon's.
