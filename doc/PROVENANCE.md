# Provenance

Fork of <https://github.com/Grim-Batol/Paragon-Anniversary>, **AGPL-3.0**.

**Base commit:** `a3cb1bb5d9b3983154b9e7a71459b199fcea0d9f` (branch `main`).

Established by byte comparison against a fresh clone, not by metadata: the
origin tree carried no `.git`. 24 files were byte-identical to upstream and 9
carried local deltas; nothing upstream was missing.

## Authoritative release line

The sole authoritative install branch of this fork is `main`:

```bash
git clone --branch main --single-branch \
    https://github.com/tomfranz2000-glitch/Paragon-Anniversary.git
```

Feature, staging, and historical branches are not release inputs. Installation
documentation, generated payloads, patches, and server scripts are versioned
together on `main`.

Being AGPL-3.0, this fork is AGPL-3.0. If you run a modified version as a
network service, the licence requires you to offer its source to users.

## Prerequisites this repo does not contain

| what | why |
|---|---|
| `mod-eluna` cloned as **`modules/mod-ale`** + `patches/05-mod-ale.patch` | the core configures Lua only for that module directory name; the server Lua also needs two hooks and an `IsPlayerBot` method stock ALE does not have |
| [`tomfranz2000-glitch/mod-transmog`](https://github.com/tomfranz2000-glitch/mod-transmog), branch `master`, pinned at `31633595cad7b12042b6484ffe3ea34f355b9821` | `paragon_transmog_bonus.lua` and `paragon_collection_rewards.lua` read `custom_unlocked_appearances`; the pinned fork also captures appearances in `StoreNewItem` and carries the required configuration defaults |
| `mod-collections` (optional) | the collection XP source reads `account_collection_*`; without it those awards are simply zero |
| a core with `patches/01-core-paragon.patch` applied | 13 files |

## Relationship to the other repos in this split

`ezcollections` and `allthethings` are independent. Paragon reads two of their
tables where present and degrades to zero rewards where absent; neither reads
anything of Paragon's.
