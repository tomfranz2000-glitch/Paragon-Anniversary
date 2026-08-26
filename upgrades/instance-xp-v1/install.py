#!/usr/bin/env python3
"""Install the Paragon instance-creature-XP server upgrade safely.

The default mode is a read-only plan.  ``--apply`` performs the native patch,
candidate image build, stopped-server SQL/Lua cutover, verification, and
automatic rollback.  This program intentionally has no client-side code and
uses only the Python 3.10+ standard library.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


RELEASE = "instance-xp-v1"
BASELINE_COMMIT = "05ea122dc80b6a08ba01a6f0506523a13cdbe1c2"
ALE_BASE_COMMIT = "9e5b8c66efeb383871ec58b925e47094c92cc8d5"
FOCAL_LUA_RELATIVE = Path("modules/paragon_rework_sources.lua")
FOCAL_PACKAGE_PATH = "server/serverside/paragon/modules/paragon_rework_sources.lua"
BASELINE_FOCAL_SHA256 = "c202f2e1c432d4f6e8627d9cd1f28c029e288d79042c50148ead41714bae8db3"
BASELINE_FOCAL_CRLF_SHA256 = "444dbfc5518d68b3a0a9faeb8ddede291c06e826d5011d101961d14a4d0e1303"
PACKAGE_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = PACKAGE_ROOT / "manifest.json"
ALE_RELATIVE = Path("modules/mod-ale")
NATIVE_FILES = (
    Path("src/LuaEngine/LuaFunctions.cpp"),
    Path("src/LuaEngine/methods/MapMethods.h"),
)
READY_DEFAULT = r"WORLD:\s+World Initialized In"
LOG_FAILURES = (
    re.compile(r"Map:GetExpansion missing", re.IGNORECASE),
    re.compile(r"\[Paragon\].*\b(?:error|failed|missing)\b", re.IGNORECASE),
    re.compile(r"(?:Lua|ALE).*\b(?:error|failed)\b", re.IGNORECASE),
    re.compile(r"error loading.*paragon", re.IGNORECASE),
)

CONFIG_VALUES = {
    "PARAGON_CREATURE_XP_TBC_HEROIC_DUNGEON_MULTIPLIER": "1.25",
    "PARAGON_CREATURE_XP_WOTLK_HEROIC_DUNGEON_MULTIPLIER": "1.5",
    "PARAGON_CREATURE_XP_TBC_RAID_MULTIPLIER": "2",
    "PARAGON_CREATURE_XP_WOTLK_NORMAL_RAID_MULTIPLIER": "2.5",
    "PARAGON_CREATURE_XP_WOTLK_HEROIC_RAID_MULTIPLIER": "4",
}

PRIOR_MARKERS = (
    (Path("src/LuaEngine/LuaFunctions.cpp"), '"IsPlayerBot", &LuaPlayer::IsPlayerBot'),
    (Path("src/LuaEngine/LuaFunctions.cpp"), '"GetAtLevelXPReward", &LuaCreature::GetAtLevelXPReward'),
    (Path("src/LuaEngine/Hooks.h"), "PLAYER_EVENT_ON_KILL_REWARD"),
    (Path("src/LuaEngine/Hooks.h"), "PLAYER_EVENT_ON_PROFESSION_ACTION"),
    (Path("src/LuaEngine/Hooks.h"), "PLAYER_EVENT_ON_PVP_HONOR"),
    (Path("src/LuaEngine/Hooks.h"), "PLAYER_EVENT_ON_PVP_MATCH_COMPLETE"),
    (Path("src/LuaEngine/Hooks.h"), "PLAYER_EVENT_ON_PVP_BATTLEFIELD_COMPLETE"),
    (Path("src/LuaEngine/Hooks.h"), "PLAYER_EVENT_ON_PVP_OUTDOOR_OBJECTIVE"),
    (Path("src/LuaEngine/Hooks.h"), "PLAYER_EVENT_ON_PVP_DUEL_COMPLETE"),
)


class UpgradeError(RuntimeError):
    """An expected, actionable upgrade failure."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise UpgradeError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def crlf_variant_sha256(path: Path) -> str:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise UpgradeError(f"cannot read LF payload {path}: {error}") from error
    if b"\r" in content:
        raise UpgradeError(
            f"canonical Lua payload must contain LF only, not CR/CRLF bytes: {path}")
    return hashlib.sha256(content.replace(b"\n", b"\r\n")).hexdigest()


def make_private_directory(path: Path) -> None:
    try:
        missing: list[Path] = []
        current = path
        while not current.exists():
            missing.append(current)
            if current.parent == current:
                break
            current = current.parent
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise UpgradeError(f"private backup path is not a real directory: {path}")
        if os.name != "nt":
            for directory in reversed(missing):
                if directory.is_symlink() or not directory.is_dir():
                    raise UpgradeError(
                        f"private backup path is not a real directory: {directory}")
                os.chmod(directory, 0o700)
            os.chmod(path, 0o700)
    except OSError as error:
        raise UpgradeError(
            f"cannot create private backup directory {path}: {error}") from error


def run(command: Sequence[str], *, cwd: Path | None = None,
        input_bytes: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            list(command), cwd=str(cwd) if cwd else None, input=input_bytes,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as error:
        raise UpgradeError(
            f"cannot launch required command {command[0]}: {error}") from error
    if check and result.returncode:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        if len(detail) > 1600:
            detail = detail[-1600:]
        raise UpgradeError(
            f"command failed with exit {result.returncode}: "
            f"{subprocess.list2cmdline(list(command))}\n{detail}")
    return result


def output(command: Sequence[str], *, cwd: Path | None = None) -> str:
    return run(command, cwd=cwd).stdout.decode("utf-8", "replace").strip()


def run_streamed(command: Sequence[str], *, cwd: Path | None = None) -> None:
    print("+ " + subprocess.list2cmdline(list(command)), flush=True)
    try:
        result = subprocess.run(list(command), cwd=str(cwd) if cwd else None,
                                check=False)
    except OSError as error:
        raise UpgradeError(
            f"cannot launch required command {command[0]}: {error}") from error
    if result.returncode:
        raise UpgradeError(
            f"command failed with exit {result.returncode}: "
            f"{subprocess.list2cmdline(list(command))}")


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    make_private_directory(path.parent)
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(4))
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        if os.name != "nt":
            os.fchmod(handle.fileno(), 0o600)
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise UpgradeError(f"cannot read valid JSON from {path}: {error}") from error
    if not isinstance(value, dict):
        raise UpgradeError(f"expected a JSON object in {path}")
    return value


class UpgradeLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> "UpgradeLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError as error:
            detail = ""
            with contextlib.suppress(OSError):
                detail = self.path.read_text(encoding="utf-8").strip()
            raise UpgradeError(
                f"another upgrade owns {self.path}; remove it only after proving "
                f"that process is gone. Lock contents: {detail or 'unreadable'}") from error
        os.write(self.fd, f"pid={os.getpid()} started={utc_now()}\n".encode())
        os.fsync(self.fd)
        return self

    def __exit__(self, *_: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
        with contextlib.suppress(OSError):
            self.path.unlink()


@dataclass(frozen=True)
class Layout:
    release_root: Path
    lua_source: Path
    cumulative_patches: Path
    incremental_patch: Path
    sql: Path
    packaged: bool


@dataclass(frozen=True)
class LuaTransition:
    baseline_sha256: str
    target_sha256: str
    baseline_crlf_sha256: str
    target_crlf_sha256: str

    @property
    def accepted_installed_hashes(self) -> frozenset[str]:
        return frozenset((
            self.baseline_sha256, self.target_sha256,
            self.baseline_crlf_sha256, self.target_crlf_sha256,
        ))


@dataclass(frozen=True)
class PackageArtifacts:
    incremental_patch_sha256: str
    sql_sha256: str


@dataclass(frozen=True)
class FileMetadata:
    mode: int
    uid: int | None
    gid: int | None
    windows_security_b64: str | None = None

    def to_json(self) -> dict[str, int | str | None]:
        return {
            "mode": self.mode, "uid": self.uid, "gid": self.gid,
            "windows_security_b64": self.windows_security_b64,
        }

    @classmethod
    def from_json(cls, value: object) -> "FileMetadata":
        if not isinstance(value, dict):
            raise UpgradeError("rollback journal lacks focal-file metadata")
        mode, uid, gid = value.get("mode"), value.get("uid"), value.get("gid")
        windows_security = value.get("windows_security_b64")
        if (not isinstance(mode, int) or isinstance(mode, bool) or
                not 0 <= mode <= 0o777 or
                (uid is not None and (
                    not isinstance(uid, int) or isinstance(uid, bool) or uid < 0)) or
                (gid is not None and (
                    not isinstance(gid, int) or isinstance(gid, bool) or gid < 0)) or
                (os.name != "nt" and (uid is None or gid is None)) or
                (windows_security is not None and not isinstance(windows_security, str))):
            raise UpgradeError("rollback journal has invalid focal-file metadata")
        if os.name == "nt":
            if uid is not None or gid is not None or not windows_security:
                raise UpgradeError("rollback journal lacks Windows focal security metadata")
            try:
                raw_security = base64.b64decode(windows_security, validate=True)
            except (ValueError, TypeError) as error:
                raise UpgradeError("rollback journal has invalid Windows security metadata") from error
            if not raw_security or len(raw_security) > 1024 * 1024:
                raise UpgradeError("rollback journal has invalid Windows security metadata size")
            if base64.b64encode(raw_security).decode("ascii") != windows_security:
                raise UpgradeError("rollback journal has non-canonical Windows security metadata")
        elif windows_security is not None:
            raise UpgradeError("POSIX rollback journal unexpectedly carries Windows security metadata")
        return cls(mode, uid, gid, windows_security)


@dataclass(frozen=True)
class ComposeContract:
    rendered_sha256: str
    service_config_hash: str
    env_files: tuple[tuple[str, str], ...]

    def env_json(self) -> list[dict[str, str]]:
        return [{"path": path, "sha256": digest}
                for path, digest in self.env_files]



@dataclass(frozen=True)
class Config:
    core_root: Path
    lua_root: Path
    lua_source_override: Path | None
    database_container: str
    worldserver_container: str
    compose_service: str
    compose_project: str | None
    compose_files: tuple[Path, ...]
    compose_env_files: tuple[Path, ...]
    backup_root: Path
    ready_pattern: str
    readiness_timeout: int
    stop_timeout: int
    worldserver_binary: str
    allow_development_layout: bool

    @property
    def ale_root(self) -> Path:
        return self.core_root / ALE_RELATIVE

    @property
    def lua_destination(self) -> Path:
        return self.lua_root / "paragon"

    @property
    def lua_focal_destination(self) -> Path:
        return self.lua_destination / FOCAL_LUA_RELATIVE


def locate_layout(config: Config) -> Layout:
    # Release archives flatten this template at their root.  In the source
    # repository, the development fallback lives two levels above it.
    candidates = (PACKAGE_ROOT, PACKAGE_ROOT.parents[1])
    for root in candidates:
        packaged_lua = root / "server" / "serverside" / "paragon"
        patches = root / "patches"
        if packaged_lua.is_dir() and patches.is_dir():
            if config.lua_source_override is not None:
                raise UpgradeError(
                    "--lua-source is forbidden for packaged upgrades; only the "
                    "checksummed server/serverside/paragon payload may be used")
            source = packaged_lua
            return Layout(root, source.resolve(), patches.resolve(),
                          (PACKAGE_ROOT / "native/mod-ale-instance-xp.patch").resolve(),
                          (PACKAGE_ROOT / "sql/instance-xp.sql").resolve(), True)

    repository = PACKAGE_ROOT.parents[1]
    development_lua = repository / "serverside" / "paragon"
    patches = repository / "patches"
    if config.allow_development_layout and development_lua.is_dir() and patches.is_dir():
        source = config.lua_source_override or development_lua
        return Layout(repository, source.resolve(), patches.resolve(),
                      (PACKAGE_ROOT / "native/mod-ale-instance-xp.patch").resolve(),
                      (PACKAGE_ROOT / "sql/instance-xp.sql").resolve(), False)
    raise UpgradeError(
        "release payload not found: expected server/serverside/paragon and "
        "patches beside install.py. In a source checkout only, pass "
        "--allow-development-layout explicitly")


def verify_manifest(layout: Layout) -> tuple[
        dict[str, Any], LuaTransition, PackageArtifacts]:
    manifest = read_json(MANIFEST_PATH)
    if manifest.get("schema") != 1 or manifest.get("release") != RELEASE:
        raise UpgradeError(f"unsupported package manifest: {MANIFEST_PATH}")
    if manifest.get("config") != CONFIG_VALUES:
        raise UpgradeError("manifest instance-XP values do not match the installer contract")
    if manifest.get("from_paragon_commit") != BASELINE_COMMIT:
        raise UpgradeError("manifest does not name the supported prior Paragon release")
    if manifest.get("ale_base_commit") != ALE_BASE_COMMIT:
        raise UpgradeError("manifest does not name the supported mod-ale pin")
    if manifest.get("lua_payload") != FOCAL_PACKAGE_PATH:
        raise UpgradeError("manifest points at an unexpected focal Lua payload")
    if manifest.get("incremental_ale_patch") != "native/mod-ale-instance-xp.patch":
        raise UpgradeError("manifest points at an unexpected ALE delta")
    if manifest.get("sql_migration") != "sql/instance-xp.sql":
        raise UpgradeError("manifest points at an unexpected SQL migration")

    checksum_path = layout.release_root / "SHA256SUMS"
    if layout.packaged:
        checksums = verify_checksum_file(layout.release_root, checksum_path)
    elif not checksum_path.exists():
        print("WARNING: development layout has no release SHA256SUMS; "
              "critical contents will be checked semantically", file=sys.stderr)
        checksums = {
            "native/mod-ale-instance-xp.patch": sha256_file(layout.incremental_patch),
            "sql/instance-xp.sql": sha256_file(layout.sql),
        }
    else:
        checksums = verify_checksum_file(layout.release_root, checksum_path)
    if layout.packaged:
        release = read_json(layout.release_root / "RELEASE.json")
        if (release.get("formatVersion") != 1 or
                release.get("releaseId") != RELEASE or
                release.get("baselineCommit") != BASELINE_COMMIT or
                release.get("clientChanges") is not False):
            raise UpgradeError("RELEASE.json does not describe this server-only upgrade")
        pins = release.get("pins") or {}
        if (pins.get("modAle") or {}).get("commit") != ALE_BASE_COMMIT:
            raise UpgradeError("RELEASE.json carries an unexpected mod-ale pin")
        transition = release.get("serverLuaTransition")
        if not isinstance(transition, dict):
            raise UpgradeError("RELEASE.json lacks serverLuaTransition")
        if transition.get("path") != FOCAL_PACKAGE_PATH:
            raise UpgradeError("RELEASE.json names an unexpected focal Lua path")
        baseline_hash = transition.get("baselineSha256")
        target_hash = transition.get("targetSha256")
        baseline_crlf_hash = transition.get("baselineCrlfSha256")
        target_crlf_hash = transition.get("targetCrlfSha256")
    else:
        baseline_hash = BASELINE_FOCAL_SHA256
        target_hash = sha256_file(layout.lua_source / FOCAL_LUA_RELATIVE)
        baseline_crlf_hash = BASELINE_FOCAL_CRLF_SHA256
        target_crlf_hash = crlf_variant_sha256(
            layout.lua_source / FOCAL_LUA_RELATIVE)

    hash_pattern = re.compile(r"[0-9a-f]{64}")
    if (not isinstance(baseline_hash, str) or not hash_pattern.fullmatch(baseline_hash)
            or not isinstance(target_hash, str) or not hash_pattern.fullmatch(target_hash)
            or not isinstance(baseline_crlf_hash, str)
            or not hash_pattern.fullmatch(baseline_crlf_hash)
            or not isinstance(target_crlf_hash, str)
            or not hash_pattern.fullmatch(target_crlf_hash)):
        raise UpgradeError("focal Lua transition hashes must be lowercase SHA-256 values")
    if baseline_hash != BASELINE_FOCAL_SHA256:
        raise UpgradeError(
            "RELEASE.json focal baseline hash does not match the supported prior release")
    if baseline_crlf_hash != BASELINE_FOCAL_CRLF_SHA256:
        raise UpgradeError(
            "RELEASE.json CRLF focal baseline hash does not match the supported prior release")
    focal_source = layout.lua_source / FOCAL_LUA_RELATIVE
    if not focal_source.is_file() or sha256_file(focal_source) != target_hash:
        raise UpgradeError(
            "packaged focal Lua bytes do not match serverLuaTransition.targetSha256")
    if crlf_variant_sha256(focal_source) != target_crlf_hash:
        raise UpgradeError(
            "serverLuaTransition.targetCrlfSha256 is not the exact CRLF variant "
            "of the packaged LF focal Lua bytes")
    if target_hash == baseline_hash:
        raise UpgradeError("focal Lua transition has identical baseline and target hashes")
    verify_critical_contents(layout)
    patch_hash = checksums.get("native/mod-ale-instance-xp.patch")
    sql_hash = checksums.get("sql/instance-xp.sql")
    if not isinstance(patch_hash, str) or not isinstance(sql_hash, str):
        raise UpgradeError("release checksums lack the incremental ALE patch or SQL migration")
    return (manifest, LuaTransition(
                baseline_hash, target_hash, baseline_crlf_hash, target_crlf_hash),
            PackageArtifacts(patch_hash, sql_hash))


def verify_checksum_file(root: Path, checksum_path: Path) -> dict[str, str]:
    try:
        lines = checksum_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise UpgradeError(f"release checksum file is missing: {checksum_path}") from error
    entries: dict[str, str] = {}
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
        if not match:
            raise UpgradeError(f"invalid SHA256SUMS line {number}: {raw!r}")
        name = match.group(2).replace("\\", "/").removeprefix("./")
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts:
            raise UpgradeError(f"unsafe checksum path: {name}")
        if name in entries:
            raise UpgradeError(f"duplicate checksum entry: {name}")
        entries[name] = match.group(1).lower()

    critical = {
        "install.py", "manifest.json", "RELEASE_NOTES.md", "README.md",
        "native/mod-ale-instance-xp.patch", "sql/instance-xp.sql",
        "server/serverside/paragon/modules/paragon_rework_sources.lua",
        "patches/05-mod-ale.patch", "patches/07-mod-ale-profession-xp.patch",
        "patches/09-mod-ale-pvp-merit.patch", "patches/PINS.md",
        "RELEASE.json", "LICENSE",
    }
    missing = sorted(critical - set(entries))
    if missing:
        raise UpgradeError("SHA256SUMS omits critical release files: " + ", ".join(missing))
    actual_files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise UpgradeError(f"release symlinks are not accepted: {path}")
        if path.is_file() and path != checksum_path:
            actual_files.add(path.relative_to(root).as_posix())
    extras = sorted(actual_files - set(entries))
    omitted = sorted(set(entries) - actual_files)
    if extras or omitted:
        details = []
        if extras:
            details.append("unchecksummed files: " + ", ".join(extras[:20]))
        if omitted:
            details.append("missing files: " + ", ".join(omitted[:20]))
        raise UpgradeError("release file set differs from SHA256SUMS (" + "; ".join(details) + ")")
    for name, expected in entries.items():
        path = (root / Path(*PurePosixPath(name).parts)).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as error:
            raise UpgradeError(f"checksum path escapes the release root: {name}") from error
        if not path.is_file():
            raise UpgradeError(f"checksummed release file is missing: {name}")
        actual = sha256_file(path)
        if actual != expected:
            raise UpgradeError(
                f"release checksum mismatch for {name}: expected {expected}, got {actual}")
    return entries


def verify_critical_contents(layout: Layout) -> None:
    for path in (layout.incremental_patch, layout.sql):
        if not path.is_file():
            raise UpgradeError(f"required package artifact is missing: {path}")
    for name in ("05-mod-ale.patch", "07-mod-ale-profession-xp.patch",
                 "09-mod-ale-pvp-merit.patch", "PINS.md"):
        if not (layout.cumulative_patches / name).is_file():
            raise UpgradeError(f"cumulative release patch is missing: {name}")

    patch = layout.incremental_patch.read_text(encoding="utf-8")
    cumulative = (layout.cumulative_patches / "05-mod-ale.patch").read_text(encoding="utf-8")
    lua_file = layout.lua_source / "modules" / "paragon_rework_sources.lua"
    try:
        lua = lua_file.read_text(encoding="utf-8")
        sql = layout.sql.read_text(encoding="utf-8")
    except OSError as error:
        raise UpgradeError(f"cannot read release payload: {error}") from error
    for token in ("map->GetEntry()->Expansion()", "&LuaMap::GetExpansion"):
        if token not in patch or token not in cumulative:
            raise UpgradeError(f"ALE patches lack required token: {token}")
    for field, value in CONFIG_VALUES.items():
        if field not in lua:
            raise UpgradeError(f"Lua payload lacks instance-XP setting: {field}")
        if not re.search(r"\('" + re.escape(field) + r"',\s*'" +
                         re.escape(value) + r"'\)", sql):
            raise UpgradeError(f"SQL migration lacks exact value {field}={value}")
    if "InstanceCreatureXPMultiplier" not in lua or "map:GetExpansion()" not in lua:
        raise UpgradeError("Lua payload lacks the target instance-XP implementation")


@dataclass(frozen=True)
class NativeAssessment:
    state: str
    explanation: str


def assess_native(ale_root: Path) -> NativeAssessment:
    if not ale_root.is_dir():
        return NativeAssessment("unknown", f"mod-ale directory is missing: {ale_root}")
    texts: dict[Path, str] = {}
    needed = set(NATIVE_FILES) | {path for path, _ in PRIOR_MARKERS}
    for relative in needed:
        path = ale_root / relative
        try:
            texts[relative] = path.read_text(encoding="utf-8")
        except OSError:
            return NativeAssessment("unknown", f"required ALE source is missing: {path}")

    functions = texts[NATIVE_FILES[0]]
    methods = texts[NATIVE_FILES[1]]
    declaration_count = len(re.findall(r"\bint\s+GetExpansion\s*\(", methods))
    body_count = len(re.findall(
        r"\bGetExpansion\s*\([^)]*\)\s*\{.*?"
        r"map\s*->\s*GetEntry\s*\(\s*\)\s*->\s*Expansion\s*\(\s*\)",
        methods, re.DOTALL))
    registration_count = len(re.findall(
        r'\{\s*"GetExpansion"\s*,\s*&LuaMap::GetExpansion\s*\}', functions))
    footprint = (declaration_count, body_count, registration_count,
                 methods.count("GetExpansion"), functions.count("GetExpansion"))
    present = [(path, marker) for path, marker in PRIOR_MARKERS
               if marker in texts[path]]

    target = declaration_count == body_count == registration_count == 1
    if target and len(present) == len(PRIOR_MARKERS):
        return NativeAssessment("target", "all prior bridges and exact Map:GetExpansion target are present")
    if any(footprint):
        return NativeAssessment(
            "partial", "Map:GetExpansion footprint is incomplete, duplicated, or has an unknown body "
            f"(declaration/body/registration counts: {footprint[:3]})")
    if len(present) == len(PRIOR_MARKERS):
        if functions.count('{ "GetDifficulty", &LuaMap::GetDifficulty },') != 1:
            return NativeAssessment("partial", "LuaFunctions.cpp has an ambiguous map getter anchor")
        if methods.count("int GetDifficulty(lua_State* L, Map* map)") != 1:
            return NativeAssessment("partial", "MapMethods.h has an ambiguous difficulty-method anchor")
        return NativeAssessment("prior", "known prior-release ALE bridges are present and target additions are absent")
    if present:
        missing = [marker for path, marker in PRIOR_MARKERS if marker not in texts[path]]
        return NativeAssessment("partial", "only part of the known prior native contract is present; missing: " + ", ".join(missing))
    return NativeAssessment(
        "unknown", "source is neither the known prior Paragon ALE state nor the target; "
        "use the cumulative 05/07/09 patches for a fresh installation")


def write_new_verified_file(content: bytes, destination: Path, expected_hash: str) -> None:
    if destination.exists():
        raise UpgradeError(f"refusing to overwrite staged/backup file: {destination}")
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise UpgradeError(f"bytes intended for {destination} do not match {expected_hash}")
    make_private_directory(destination.parent)
    with destination.open("xb") as handle:
        if os.name != "nt":
            os.fchmod(handle.fileno(), 0o600)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    if sha256_file(destination) != expected_hash:
        raise UpgradeError(f"durable file verification failed: {destination}")


def stage_verified_artifact(source: Path, destination: Path,
                            expected_hash: str) -> None:
    try:
        content = source.read_bytes()
    except OSError as error:
        raise UpgradeError(f"cannot stage package artifact {source}: {error}") from error
    write_new_verified_file(content, destination, expected_hash)
    try:
        os.chmod(destination, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    except OSError as error:
        raise UpgradeError(f"cannot seal staged artifact read-only: {destination}: {error}") from error
    if sha256_file(destination) != expected_hash:
        raise UpgradeError(f"sealed staged artifact hash changed: {destination}")


def read_verified_bytes(path: Path, expected_hash: str) -> bytes:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise UpgradeError(f"cannot read staged artifact {path}: {error}") from error
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected_hash:
        raise UpgradeError(
            f"staged artifact changed before use: {path}; expected {expected_hash}, got {actual}")
    return content


def read_verified_text(path: Path, expected_hash: str) -> str:
    content = read_verified_bytes(path, expected_hash)
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UpgradeError(f"staged artifact is not UTF-8: {path}") from error


def windows_security_descriptor(path: Path) -> str:
    if os.name != "nt":
        raise UpgradeError("Windows security metadata requested on a non-Windows host")
    import ctypes
    from ctypes import wintypes

    security_information = 0x00000001 | 0x00000002 | 0x00000004
    get_security = ctypes.WinDLL(
        "advapi32", use_last_error=True).GetFileSecurityW
    get_security.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    )
    get_security.restype = wintypes.BOOL
    needed = wintypes.DWORD()
    get_security(str(path), security_information, None, 0, ctypes.byref(needed))
    if needed.value == 0:
        error = ctypes.WinError(ctypes.get_last_error())
        raise UpgradeError(
            f"cannot size Windows owner/DACL metadata for {path}: {error}") from error
    buffer = ctypes.create_string_buffer(needed.value)
    if not get_security(
            str(path), security_information, buffer, needed.value,
            ctypes.byref(needed)):
        error = ctypes.WinError(ctypes.get_last_error())
        raise UpgradeError(
            f"cannot read Windows owner/DACL metadata for {path}: {error}") from error
    return base64.b64encode(buffer.raw[:needed.value]).decode("ascii")


def apply_windows_security_descriptor(path: Path, encoded: str) -> None:
    if os.name != "nt":
        raise UpgradeError("Windows security metadata supplied on a non-Windows host")
    import ctypes
    from ctypes import wintypes

    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise UpgradeError("invalid encoded Windows owner/DACL metadata") from error
    descriptor = ctypes.create_string_buffer(raw)
    get_control = ctypes.WinDLL(
        "advapi32", use_last_error=True).GetSecurityDescriptorControl
    get_control.argtypes = (
        wintypes.LPVOID, ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    )
    get_control.restype = wintypes.BOOL
    control = wintypes.WORD()
    revision = wintypes.DWORD()
    if not get_control(descriptor, ctypes.byref(control), ctypes.byref(revision)):
        error = ctypes.WinError(ctypes.get_last_error())
        raise UpgradeError(f"cannot parse saved Windows security metadata: {error}") from error
    security_information = 0x00000001 | 0x00000002 | 0x00000004
    if control.value & 0x1000:  # SE_DACL_PROTECTED
        security_information |= 0x80000000  # PROTECTED_DACL_SECURITY_INFORMATION
    else:
        security_information |= 0x20000000  # UNPROTECTED_DACL_SECURITY_INFORMATION
    set_security = ctypes.WinDLL(
        "advapi32", use_last_error=True).SetFileSecurityW
    set_security.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID)
    set_security.restype = wintypes.BOOL
    if not set_security(str(path), security_information, descriptor):
        error = ctypes.WinError(ctypes.get_last_error())
        raise UpgradeError(
            f"cannot restore Windows owner/DACL metadata for {path}: {error}") from error


def normalized_windows_security(encoded: str) -> bytes:
    raw = bytearray(base64.b64decode(encoded, validate=True))
    if len(raw) < 20:
        raise UpgradeError("saved Windows security descriptor is truncated")
    control = int.from_bytes(raw[2:4], "little")
    # These flags describe how the descriptor was obtained/defaulted rather
    # than its owner, group, DACL bytes, or DACL-protection policy. Windows may
    # recompute them when SetFileSecurityW installs an equivalent descriptor.
    volatile = 0x0001 | 0x0002 | 0x0008 | 0x0020 | 0x0100 | 0x0200 | 0x0400 | 0x0800
    raw[2:4] = (control & ~volatile).to_bytes(2, "little")
    return bytes(raw)


def metadata_equivalent(actual: FileMetadata, expected: FileMetadata) -> bool:
    if (actual.mode, actual.uid, actual.gid) != (
            expected.mode, expected.uid, expected.gid):
        return False
    if os.name != "nt":
        return actual.windows_security_b64 == expected.windows_security_b64
    if not actual.windows_security_b64 or not expected.windows_security_b64:
        return False
    return normalized_windows_security(
        actual.windows_security_b64) == normalized_windows_security(
            expected.windows_security_b64)


def file_metadata(path: Path, *, require_safe: bool = True) -> FileMetadata:
    try:
        current = path.lstat()
    except OSError as error:
        raise UpgradeError(f"cannot inspect file metadata for {path}: {error}") from error
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
        raise UpgradeError(f"focal Lua path must be a regular, non-symlink file: {path}")
    mode = stat.S_IMODE(current.st_mode)
    if require_safe and mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
        raise UpgradeError(
            f"focal Lua file has unsafe special mode bits {mode:#o}: {path}")
    if (require_safe and os.name != "nt" and
            mode & (stat.S_IWGRP | stat.S_IWOTH)):
        raise UpgradeError(
            f"focal Lua file is group/world writable ({mode:#o}); refusing replacement: {path}")
    uid = getattr(current, "st_uid", None) if os.name != "nt" else None
    gid = getattr(current, "st_gid", None) if os.name != "nt" else None
    windows_security = windows_security_descriptor(path) if os.name == "nt" else None
    return FileMetadata(mode, uid, gid, windows_security)


def safe_file_metadata(path: Path) -> FileMetadata:
    return file_metadata(path, require_safe=True)


def apply_file_metadata(path: Path, metadata: FileMetadata) -> None:
    try:
        if os.name == "nt":
            os.chmod(path, metadata.mode)
        else:
            os.chmod(path, metadata.mode, follow_symlinks=False)
            if metadata.uid is None or metadata.gid is None:
                raise UpgradeError("POSIX focal metadata lacks owner or group")
            os.chown(path, metadata.uid, metadata.gid, follow_symlinks=False)
        if os.name == "nt":
            if metadata.windows_security_b64 is None:
                raise UpgradeError("Windows replacement metadata lacks owner/DACL data")
            apply_windows_security_descriptor(path, metadata.windows_security_b64)
    except OSError as error:
        raise UpgradeError(
            f"cannot preserve focal mode/ownership on replacement {path}: {error}") from error
    actual = safe_file_metadata(path)
    if not metadata_equivalent(actual, metadata):
        raise UpgradeError(
            f"replacement metadata verification failed for {path}: "
            f"expected {metadata}, got {actual}")


def replace_file_preserving_windows_security(temporary: Path, destination: Path) -> None:
    # ReplaceFileW retains the replaced file's security metadata while swapping
    # same-volume bytes atomically. os.replace would instead leave the new
    # tempfile's owner/DACL on Windows.
    import ctypes
    from ctypes import wintypes

    replace_file = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace_file.argtypes = (
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID,
    )
    replace_file.restype = wintypes.BOOL
    if not replace_file(str(destination), str(temporary), None, 0, None, None):
        error = ctypes.WinError(ctypes.get_last_error())
        raise UpgradeError(
            f"atomic Windows replacement failed for {destination}: {error}") from error


def atomic_replace_verified_file(source: Path, destination: Path,
                                 expected_hash: str,
                                 metadata: FileMetadata,
                                 *, require_current_metadata: bool = True) -> None:
    if not source.is_file() or sha256_file(source) != expected_hash:
        raise UpgradeError(f"replacement source hash is invalid: {source}")
    if not destination.parent.is_dir():
        raise UpgradeError(f"replacement destination directory is missing: {destination.parent}")
    current_metadata = file_metadata(
        destination, require_safe=require_current_metadata)
    if require_current_metadata and not metadata_equivalent(current_metadata, metadata):
        raise UpgradeError(
            f"focal Lua metadata changed before replacement: expected {metadata}, "
            f"got {current_metadata}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".paragon-instance-xp-", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            content = source.read_bytes()
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if sha256_file(temporary) != expected_hash:
            raise UpgradeError(f"staged replacement hash is invalid: {temporary}")
        apply_file_metadata(temporary, metadata)
        if os.name == "nt":
            replace_file_preserving_windows_security(temporary, destination)
        else:
            os.replace(temporary, destination)
        # ReplaceFileW preserves selected destination attributes. Reapply the
        # recorded descriptor as well so rollback repairs, rather than refuses,
        # an unexpected post-deploy metadata change.
        apply_file_metadata(destination, metadata)
        if sha256_file(destination) != expected_hash:
            raise UpgradeError(f"atomic replacement verification failed: {destination}")
        if not metadata_equivalent(safe_file_metadata(destination), metadata):
            raise UpgradeError(f"atomic replacement changed mode/ownership: {destination}")
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink()


def docker_inspect(container: str) -> dict[str, Any]:
    try:
        value = json.loads(output(("docker", "container", "inspect", container)))
    except json.JSONDecodeError as error:
        raise UpgradeError(f"Docker returned invalid inspect data for {container}") from error
    if not isinstance(value, list) or len(value) != 1:
        raise UpgradeError(f"Docker container is unavailable: {container}")
    return value[0]


def docker_inspect_optional(container: str) -> dict[str, Any] | None:
    result = run(("docker", "container", "inspect", container), check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace")
        if "no such" in detail.lower() and "container" in detail.lower():
            return None
        raise UpgradeError(
            f"cannot inspect Docker container {container}: {detail[-1200:]}")
    try:
        value = json.loads(result.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as error:
        raise UpgradeError(f"Docker returned invalid inspect data for {container}") from error
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise UpgradeError(f"Docker returned unexpected inspect data for {container}")
    return value[0]


def container_running(container: str) -> bool:
    return bool(docker_inspect(container).get("State", {}).get("Running"))


def mysql_command(container: str) -> tuple[str, ...]:
    # Password expansion happens only inside the database container and is not
    # exposed in the host process list or upgrade journal.
    return (
        "docker", "exec", "-i", container, "sh", "-lc",
        'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" '
        '--default-character-set=utf8mb4 --raw --batch --skip-column-names',
    )


def mysql(config: Config, sql: str) -> str:
    return run(mysql_command(config.database_container),
               input_bytes=sql.encode("utf-8")).stdout.decode("utf-8", "replace").strip()


def config_snapshot(config: Config) -> dict[str, str]:
    names = ",".join("'" + field + "'" for field in CONFIG_VALUES)
    rows = mysql(config, "SELECT field, HEX(value) FROM acore_ale.paragon_config "
                 f"WHERE field IN ({names}) ORDER BY field;")
    result: dict[str, str] = {}
    for line in rows.splitlines():
        columns = line.split("\t")
        if len(columns) != 2 or columns[0] not in CONFIG_VALUES:
            raise UpgradeError(f"unexpected config snapshot row: {line!r}")
        try:
            result[columns[0]] = bytes.fromhex(columns[1]).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise UpgradeError(f"invalid config value encoding for {columns[0]}") from error
    return result


def verify_database(config: Config) -> dict[str, str]:
    values = config_snapshot(config)
    if values != CONFIG_VALUES:
        detail = ", ".join(
            f"{field}={values.get(field, '<missing>')} (expected {expected})"
            for field, expected in CONFIG_VALUES.items()
            if values.get(field) != expected)
        raise UpgradeError("instance-XP database verification failed: " + detail)
    return values


def verify_database_preflight(config: Config) -> dict[str, str]:
    if not container_running(config.database_container):
        raise UpgradeError(f"database container is not running: {config.database_container}")
    engine = mysql(
        config,
        "SELECT ENGINE FROM information_schema.tables "
        "WHERE table_schema='acore_ale' AND table_name='paragon_config';")
    if engine.strip().upper() != "INNODB":
        raise UpgradeError(
            "acore_ale.paragon_config must exist and use InnoDB for the "
            f"transactional cutover; found {engine or '<missing>'}")
    return config_snapshot(config)


def dump_database(config: Config, destination: Path) -> None:
    make_private_directory(destination.parent)
    temporary = destination.with_name(destination.name + ".tmp")
    command = (
        "docker", "exec", "-i", config.database_container, "sh", "-lc",
        'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction '
        '--quick --routines --events --triggers --hex-blob --databases acore_ale',
    )
    try:
        with temporary.open("wb") as handle:
            if os.name != "nt":
                os.fchmod(handle.fileno(), 0o600)
            result = subprocess.run(command, stdout=handle, stderr=subprocess.PIPE,
                                    cwd=str(config.core_root), check=False)
            handle.flush()
            os.fsync(handle.fileno())
    except FileNotFoundError as error:
        raise UpgradeError("Docker was not found while creating the database backup") from error
    if result.returncode:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise UpgradeError("mysqldump failed: " + result.stderr.decode("utf-8", "replace")[-1200:])
    if temporary.stat().st_size < 256:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise UpgradeError("mysqldump produced an implausibly small backup")
    header = temporary.read_bytes()[:4096]
    if b"acore_ale" not in header and b"MariaDB dump" not in header and b"MySQL dump" not in header:
        raise UpgradeError("mysqldump backup does not contain a recognizable header")
    os.replace(temporary, destination)


def restore_config(config: Config, before: Mapping[str, str]) -> None:
    names = ",".join("'" + field + "'" for field in CONFIG_VALUES)
    statements = ["START TRANSACTION;", f"DELETE FROM acore_ale.paragon_config WHERE field IN ({names});"]
    if before:
        rows = []
        for field, value in sorted(before.items()):
            if field not in CONFIG_VALUES:
                raise UpgradeError(f"backup contains an unexpected config field: {field}")
            rows.append(f"('{field}', CONVERT(0x{value.encode('utf-8').hex()} USING utf8mb4))")
        statements.append("INSERT INTO acore_ale.paragon_config (field,value) VALUES " + ",".join(rows) + ";")
    statements.append("COMMIT;")
    mysql(config, "\n".join(statements))
    if config_snapshot(config) != dict(before):
        raise UpgradeError("configuration rollback did not reproduce the saved rows")


def compose_base(config: Config) -> list[str]:
    command = ["docker", "compose"]
    for path in config.compose_env_files:
        command.extend(("--env-file", str(path)))
    command.extend(("--project-directory", str(config.core_root)))
    if config.compose_project:
        command.extend(("--project-name", config.compose_project))
    for path in config.compose_files:
        command.extend(("--file", str(path)))
    return command


def compose(config: Config, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return run((*compose_base(config), *arguments), cwd=config.core_root, check=check)


def compose_streamed(config: Config, *arguments: str) -> None:
    run_streamed((*compose_base(config), *arguments), cwd=config.core_root)


def compose_config_digest(config: Config) -> str:
    rendered = run((*compose_base(config), "config"), cwd=config.core_root).stdout
    if not rendered.strip():
        raise UpgradeError("Docker Compose rendered an empty configuration")
    return hashlib.sha256(rendered).hexdigest()


def compose_service_config_hash(config: Config) -> str:
    raw = output(
        (*compose_base(config), "config", "--hash", config.compose_service),
        cwd=config.core_root,
    )
    hashes: list[str] = []
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) == 1 and re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
            hashes.append(fields[0].lower())
        elif (len(fields) == 2 and fields[0] == config.compose_service and
              re.fullmatch(r"[0-9a-fA-F]{64}", fields[1])):
            hashes.append(fields[1].lower())
        elif line.strip():
            raise UpgradeError(
                f"unexpected Docker Compose config --hash output: {line!r}")
    if len(hashes) != 1:
        raise UpgradeError(
            f"Docker Compose did not return exactly one configuration hash for "
            f"{config.compose_service!r}: {raw!r}")
    return hashes[0]


def compose_contract(config: Config) -> ComposeContract:
    env_files_list: list[tuple[str, str]] = []
    for path in config.compose_env_files:
        if not path.is_file():
            raise UpgradeError(f"Compose env file disappeared or is not a file: {path}")
        env_files_list.append((str(path), sha256_file(path)))
    env_files = tuple(env_files_list)
    return ComposeContract(
        compose_config_digest(config), compose_service_config_hash(config), env_files)


def verify_compose(config: Config, inspect: Mapping[str, Any]) -> ComposeContract:
    services = output((*compose_base(config), "config", "--services"), cwd=config.core_root).splitlines()
    if config.compose_service not in services:
        raise UpgradeError(
            f"compose service {config.compose_service!r} is absent; available: {', '.join(services)}")
    labels = inspect.get("Config", {}).get("Labels") or {}
    actual_service = labels.get("com.docker.compose.service")
    actual_project = labels.get("com.docker.compose.project")
    actual_config_hash = labels.get("com.docker.compose.config-hash")
    if actual_service and actual_service != config.compose_service:
        raise UpgradeError(
            f"container {config.worldserver_container} belongs to compose service "
            f"{actual_service!r}, not {config.compose_service!r}")
    if config.compose_project and actual_project and actual_project != config.compose_project:
        raise UpgradeError(
            f"container {config.worldserver_container} belongs to compose project "
            f"{actual_project!r}, not {config.compose_project!r}")
    if actual_project and not config.compose_project:
        raise UpgradeError(
            f"container {config.worldserver_container} belongs to compose project "
            f"{actual_project!r}; pass --compose-project {actual_project} explicitly "
            "so build and cutover cannot target a second project")
    resolved = output(
        (*compose_base(config), "ps", "--all", "--quiet", config.compose_service),
        cwd=config.core_root,
    ).splitlines()
    inspected_id = str(inspect.get("Id") or "")
    if len(resolved) != 1 or not inspected_id or not (
            inspected_id.startswith(resolved[0]) or resolved[0].startswith(inspected_id)):
        raise UpgradeError(
            f"selected Compose project/files resolve service {config.compose_service!r} "
            f"to {resolved or '<none>'}, not inspected container "
            f"{config.worldserver_container} ({inspected_id or '<unknown>'})")
    configured_images = output(
        (*compose_base(config), "config", "--images"), cwd=config.core_root
    ).splitlines()
    old_reference = str(inspect.get("Config", {}).get("Image") or "")
    if old_reference and old_reference not in configured_images:
        raise UpgradeError(
            f"running container image reference {old_reference!r} is not produced by "
            "the selected Compose configuration; check project files/environment")
    contract = compose_contract(config)
    if not isinstance(actual_config_hash, str) or not re.fullmatch(
            r"[0-9a-fA-F]{64}", actual_config_hash):
        raise UpgradeError(
            f"container {config.worldserver_container} lacks a valid "
            "com.docker.compose.config-hash label; it cannot be proven to match "
            "the selected Compose inputs")
    if actual_config_hash.lower() != contract.service_config_hash:
        raise UpgradeError(
            f"selected Compose inputs hash service {config.compose_service!r} as "
            f"{contract.service_config_hash}, but the inspected container records "
            f"{actual_config_hash.lower()}. Pass the exact --compose-file, "
            "--compose-project, and --env-file inputs used to create it")
    return contract


def require_compose_contract(config: Config, expected: ComposeContract,
                             phase: str) -> None:
    actual = compose_contract(config)
    if actual != expected:
        raise UpgradeError(
            f"Docker Compose inputs changed after preflight ({phase}): "
            f"expected {expected}, got {actual}")


def image_id(reference: str) -> str:
    return output(("docker", "image", "inspect", "--format", "{{.Id}}", reference))


def apply_native(config: Config, patch_path: Path, expected_hash: str) -> None:
    patch_bytes = read_verified_bytes(patch_path, expected_hash)
    check = run(("git", "-C", str(config.ale_root), "apply", "--check",
                 "--whitespace=error-all", "-"), input_bytes=patch_bytes,
                check=False)
    if check.returncode:
        detail = (check.stderr or check.stdout).decode("utf-8", "replace").strip()
        raise UpgradeError(
            "known-prior semantic state was found, but the audited ALE delta "
            "does not apply cleanly; no native file was changed:\n" + detail[-1200:])
    run(("git", "-C", str(config.ale_root), "apply", "--whitespace=error-all", "-"),
        input_bytes=patch_bytes)
    assessment = assess_native(config.ale_root)
    if assessment.state != "target":
        raise UpgradeError("ALE delta applied but target verification failed: " + assessment.explanation)


def wait_ready(config: Config, since: str) -> str:
    ready = re.compile(config.ready_pattern, re.IGNORECASE)
    deadline = time.monotonic() + config.readiness_timeout
    last_logs = ""
    while time.monotonic() < deadline:
        inspect = docker_inspect(config.worldserver_container)
        state = inspect.get("State", {})
        if state.get("Status") in ("exited", "dead"):
            logs = output(("docker", "logs", "--since", since,
                           config.worldserver_container))
            raise UpgradeError(
                f"worldserver exited during readiness (code {state.get('ExitCode')}):\n" + logs[-2400:])
        logs_result = run(("docker", "logs", "--since", since,
                           config.worldserver_container), check=False)
        last_logs = (logs_result.stdout + logs_result.stderr).decode("utf-8", "replace")
        # Health can become green before Lua has finished loading. The
        # authoritative readiness boundary is the configured world-init line.
        if state.get("Running") and ready.search(last_logs):
            return last_logs
        time.sleep(2)
    raise UpgradeError(
        f"worldserver did not reach readiness pattern {config.ready_pattern!r} "
        f"within {config.readiness_timeout}s; recent logs:\n{last_logs[-2400:]}")


def verify_logs(logs: str) -> None:
    failures = []
    for line in logs.splitlines():
        if any(pattern.search(line) for pattern in LOG_FAILURES):
            failures.append(line)
    if failures:
        raise UpgradeError("worldserver boot log contains upgrade-related errors:\n" + "\n".join(failures[-20:]))


def verify_binary_api(config: Config) -> None:
    result = run(("docker", "exec", config.worldserver_container, "grep", "-a", "-q",
                  "GetExpansion", config.worldserver_binary), check=False)
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise UpgradeError(
            "running worldserver binary does not contain the Map:GetExpansion "
            f"registration string at {config.worldserver_binary}"
            + (f": {detail}" if detail else ""))


def preflight(config: Config, layout: Layout, transition: LuaTransition) -> tuple[
        NativeAssessment, dict[str, Any], dict[str, str], str, ComposeContract]:
    if not config.core_root.is_dir():
        raise UpgradeError(f"AzerothCore root is missing: {config.core_root}")
    if not config.lua_root.is_dir():
        raise UpgradeError(f"ALE script root is missing: {config.lua_root}")
    if not config.lua_focal_destination.is_file():
        raise UpgradeError(f"installed focal Paragon Lua file is missing: {config.lua_focal_destination}")
    assessment = assess_native(config.ale_root)
    if assessment.state in ("partial", "unknown"):
        raise UpgradeError(f"native state is {assessment.state}: {assessment.explanation}")
    installed_hash = sha256_file(config.lua_focal_destination)
    if installed_hash not in transition.accepted_installed_hashes:
        raise UpgradeError(
            "installed paragon_rework_sources.lua is neither the supported "
            "baseline nor target (including their exact CRLF-only variants); "
            f"found {installed_hash}. Refusing "
            "to overwrite an unknown/custom focal file")
    safe_file_metadata(config.lua_focal_destination)
    inspect = docker_inspect(config.worldserver_container)
    selected_compose = verify_compose(config, inspect)
    database = verify_database_preflight(config)
    return assessment, inspect, database, installed_hash, selected_compose


def print_plan(config: Config, layout: Layout, assessment: NativeAssessment,
               inspect: Mapping[str, Any], database: Mapping[str, str],
               installed_hash: str, selected_compose: ComposeContract,
               transition: LuaTransition) -> None:
    running = bool(inspect.get("State", {}).get("Running"))
    image = inspect.get("Image", "<unknown>")
    print(f"Paragon {RELEASE} server upgrade plan (read-only)")
    print(f"  release root:       {layout.release_root}")
    print(f"  core root:          {config.core_root}")
    print(f"  ALE state:          {assessment.state} ({assessment.explanation})")
    print(f"  Lua source:         {layout.lua_source / FOCAL_LUA_RELATIVE}")
    print(f"  Lua destination:    {config.lua_focal_destination}")
    print(f"  installed focal:    {installed_hash}")
    print(f"  target focal:       {transition.target_sha256}")
    if installed_hash in (
            transition.baseline_crlf_sha256, transition.target_crlf_sha256):
        print("  installed EOLs:     exact supported CRLF variant (target deploys LF)")
    print(f"  Compose digest:     {selected_compose.rendered_sha256}")
    print(f"  service config hash:{selected_compose.service_config_hash}")
    if selected_compose.env_files:
        for path, digest in selected_compose.env_files:
            print(f"  Compose env file:   {path} sha256={digest}")
    else:
        print("  Compose env files:  none")
    print(f"  worldserver:        {config.worldserver_container} running={running} image={image}")
    for field, expected in CONFIG_VALUES.items():
        print(f"  config {field}: {database.get(field, '<absent>')} -> {expected}")
    print("\nApply sequence:")
    print("  1. stage sealed/hash-verified mutation inputs; create backup and journal")
    print("  2. apply audited ALE delta when state=prior; state=target is a native no-op")
    print("  3. preserve old image and build the candidate while the old server runs")
    print("  4. stop worldserver; dump acore_ale and back up the focal installed Lua file")
    print("  5. transactionally apply five settings and atomically replace only that file")
    print("  6. force-recreate worldserver and verify readiness, logs, binary, DB, and Lua")
    print("  7. on failure, restore old image/Lua/config/native files and restart old server")
    print("\nNo client path is accepted or written by this installer.")


def new_journal(config: Config, layout: Layout, assessment: NativeAssessment,
                inspect: Mapping[str, Any], database: Mapping[str, str],
                installed_hash: str, selected_compose: ComposeContract,
                transition: LuaTransition, run_dir: Path) -> dict[str, Any]:
    return {
        "schema": 1, "release": RELEASE, "status": "started",
        "created_at": utc_now(), "updated_at": utc_now(),
        "run_dir": str(run_dir), "core_root": str(config.core_root),
        "lua_root": str(config.lua_root), "lua_source": str(layout.lua_source),
        "database_container": config.database_container,
        "worldserver_container": config.worldserver_container,
        "compose_service": config.compose_service,
        "compose_project": config.compose_project,
        "compose_files": [str(path) for path in config.compose_files],
        "compose_env_files": selected_compose.env_json(),
        "native_initial_state": assessment.state,
        "native_changed": False,
        "focal_baseline_sha256": transition.baseline_sha256,
        "focal_baseline_crlf_sha256": transition.baseline_crlf_sha256,
        "focal_target_sha256": transition.target_sha256,
        "focal_target_crlf_sha256": transition.target_crlf_sha256,
        "focal_installed_preflight_sha256": installed_hash,
        "compose_config_sha256": selected_compose.rendered_sha256,
        "compose_service_config_hash": selected_compose.service_config_hash,
        "config_preflight": dict(database), "config_before": None,
        "world_was_running": bool(inspect.get("State", {}).get("Running")),
        "old_container_id": inspect.get("Id"),
        "old_image_id": inspect.get("Image"),
        "old_image_reference": inspect.get("Config", {}).get("Image"),
        "steps": [],
    }


def record(journal_path: Path, journal: dict[str, Any], step: str | None = None,
           status: str | None = None, **values: Any) -> None:
    if step and step not in journal["steps"]:
        journal["steps"].append(step)
    if status:
        journal["status"] = status
    journal.update(values)
    journal["updated_at"] = utc_now()
    atomic_json(journal_path, journal)


def backup_native(config: Config, run_dir: Path) -> tuple[
        dict[str, str], dict[str, dict[str, int | str | None]]]:
    root = run_dir / "backup/native"
    hashes: dict[str, str] = {}
    metadata: dict[str, dict[str, int | str | None]] = {}
    for relative in NATIVE_FILES:
        source = config.ale_root / relative
        source_metadata = safe_file_metadata(source)
        source_stat = source.stat()
        destination = root / relative
        make_private_directory(destination.parent)
        shutil.copy2(source, destination)
        if os.name != "nt":
            os.chmod(destination, 0o600)
        hashes[relative.as_posix()] = sha256_file(destination)
        metadata[relative.as_posix()] = {
            **source_metadata.to_json(),
            "atime_ns": source_stat.st_atime_ns,
            "mtime_ns": source_stat.st_mtime_ns,
        }
    return hashes, metadata


def restore_native(config: Config, run_dir: Path,
                   expected_hashes: Mapping[str, str],
                   expected_metadata: Mapping[str, object]) -> None:
    root = run_dir / "backup/native"
    for relative in NATIVE_FILES:
        source = root / relative
        if not source.is_file():
            raise UpgradeError(f"native rollback file is missing: {source}")
        expected = expected_hashes.get(relative.as_posix())
        if not expected or sha256_file(source) != expected:
            raise UpgradeError(f"native rollback hash is invalid: {source}")
        raw_metadata = expected_metadata.get(relative.as_posix())
        metadata = FileMetadata.from_json(raw_metadata)
        if not isinstance(raw_metadata, dict):
            raise UpgradeError(f"native rollback metadata is missing: {relative}")
        atime_ns = raw_metadata.get("atime_ns")
        mtime_ns = raw_metadata.get("mtime_ns")
        if (not isinstance(atime_ns, int) or isinstance(atime_ns, bool) or atime_ns < 0 or
                not isinstance(mtime_ns, int) or isinstance(mtime_ns, bool) or mtime_ns < 0):
            raise UpgradeError(f"native rollback timestamps are invalid: {relative}")
        destination = config.ale_root / relative
        current = safe_file_metadata(destination)
        if not current.mode & stat.S_IWUSR:
            if os.name == "nt":
                os.chmod(destination, current.mode | stat.S_IWUSR)
            else:
                os.chmod(destination, current.mode | stat.S_IWUSR,
                         follow_symlinks=False)
        shutil.copyfile(source, destination)
        apply_file_metadata(destination, metadata)
        if os.name == "nt":
            os.utime(destination, ns=(atime_ns, mtime_ns))
        else:
            os.utime(destination, ns=(atime_ns, mtime_ns), follow_symlinks=False)
        if sha256_file(destination) != expected:
            raise UpgradeError(f"native rollback verification failed: {relative}")
        if not metadata_equivalent(safe_file_metadata(destination), metadata):
            raise UpgradeError(f"native rollback metadata verification failed: {relative}")


def stop_worldserver_exact(config: Config,
                           expected_container_ids: Iterable[str], *,
                           candidate_image_id: str | None = None,
                           expected_service_hash: str | None = None) -> None:
    allowed = {value for value in expected_container_ids
               if isinstance(value, str) and value}
    inspected = docker_inspect_optional(config.worldserver_container)
    if inspected is None:
        return
    actual_id = str(inspected.get("Id") or "")
    labels = inspected.get("Config", {}).get("Labels") or {}
    recovered_candidate = bool(
        candidate_image_id and expected_service_hash and config.compose_project and
        str(inspected.get("Image")) == candidate_image_id and
        labels.get("com.docker.compose.service") == config.compose_service and
        labels.get("com.docker.compose.project") == config.compose_project and
        str(labels.get("com.docker.compose.config-hash", "")).lower() ==
        expected_service_hash.lower())
    if (not allowed or actual_id not in allowed) and not recovered_candidate:
        raise UpgradeError(
            f"refusing to stop same-name container {config.worldserver_container}: "
            f"identity {actual_id or '<unknown>'} is not one of the journaled IDs "
            f"{sorted(allowed)}")
    if not inspected.get("State", {}).get("Running"):
        return
    run(("docker", "container", "stop", "--time", str(config.stop_timeout),
         actual_id))
    after = docker_inspect_optional(actual_id)
    if after is not None and after.get("State", {}).get("Running"):
        raise UpgradeError(f"worldserver container remained running: {actual_id}")


def apply_upgrade(config: Config, layout: Layout, assessment: NativeAssessment,
                  inspect: dict[str, Any], database: dict[str, str],
                  installed_hash: str, selected_compose: ComposeContract,
                  transition: LuaTransition, artifacts: PackageArtifacts) -> Path:
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    run_dir = config.backup_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    if os.name != "nt":
        os.chmod(run_dir, 0o700)
    journal_path = run_dir / "journal.json"
    journal = new_journal(config, layout, assessment, inspect, database,
                          installed_hash, selected_compose, transition, run_dir)
    atomic_json(journal_path, journal)

    try:
        staged_focal = run_dir / "staged" / FOCAL_LUA_RELATIVE
        staged_patch = run_dir / "staged/native/mod-ale-instance-xp.patch"
        staged_sql = run_dir / "staged/sql/instance-xp.sql"
        # All mutation inputs are copied from the checksummed package and
        # sealed read-only immediately after the under-lock verification.
        stage_verified_artifact(
            layout.lua_source / FOCAL_LUA_RELATIVE, staged_focal,
            transition.target_sha256)
        stage_verified_artifact(
            layout.incremental_patch, staged_patch,
            artifacts.incremental_patch_sha256)
        stage_verified_artifact(layout.sql, staged_sql, artifacts.sql_sha256)
        record(
            journal_path, journal, "package-artifacts-staged",
            staged_focal_sha256=transition.target_sha256,
            staged_native_patch_sha256=artifacts.incremental_patch_sha256,
            staged_sql_sha256=artifacts.sql_sha256,
        )

        native_hashes, native_metadata = backup_native(config, run_dir)
        record(journal_path, journal, "native-backup",
               native_backup_sha256=native_hashes,
               native_backup_metadata=native_metadata)
        if assessment.state == "prior":
            # Intent is durable before mutation. Restoring an unchanged file is
            # harmless if the subsequent patch check itself fails.
            record(journal_path, journal, "native-patching", native_changed=True)
            apply_native(config, staged_patch, artifacts.incremental_patch_sha256)
            record(journal_path, journal, "native-patched")
        else:
            record(journal_path, journal, "native-already-target")

        old_id = str(journal["old_image_id"] or "")
        old_reference = str(journal["old_image_reference"] or "")
        if not old_id.startswith("sha256:") or not old_reference or old_reference.startswith("sha256:"):
            raise UpgradeError(
                "worldserver container does not expose a restorable tagged image reference; "
                f"id={old_id!r} reference={old_reference!r}")
        old_tag = f"paragon-instance-xp-backup:{run_id.lower()}"
        run(("docker", "image", "tag", old_id, old_tag))
        record(journal_path, journal, "old-image-preserved", old_image_backup_tag=old_tag)

        print("Building candidate image while the current worldserver remains available...", flush=True)
        compose_streamed(config, "build", config.compose_service)
        candidate_id = image_id(old_reference)
        if journal["native_changed"] and candidate_id == old_id:
            raise UpgradeError(
                "compose build returned the old image after native source changed; "
                "refusing a stale-cache cutover")
        candidate_tag = f"paragon-instance-xp-candidate:{run_id.lower()}"
        run(("docker", "image", "tag", candidate_id, candidate_tag))
        record(journal_path, journal, "candidate-built",
               candidate_image_id=candidate_id, candidate_image_tag=candidate_tag)

        require_compose_contract(config, selected_compose, "before worldserver stop")
        record(journal_path, journal, "worldserver-stopping")
        stop_worldserver_exact(config, (str(journal["old_container_id"]),))
        record(journal_path, journal, "worldserver-stopped")

        # Backups are taken with the writer stopped.  The dump is retained even
        # though automatic rollback only needs the exact five-row snapshot.
        database_dump = run_dir / "backup/database/acore_ale.sql"
        dump_database(config, database_dump)
        current_focal_hash = sha256_file(config.lua_focal_destination)
        if current_focal_hash != installed_hash:
            raise UpgradeError(
                "installed focal Lua file changed between locked preflight and cutover: "
                f"expected {installed_hash}, got {current_focal_hash}")
        focal_metadata = safe_file_metadata(config.lua_focal_destination)
        focal_backup = run_dir / "backup/lua" / FOCAL_LUA_RELATIVE
        write_new_verified_file(
            config.lua_focal_destination.read_bytes(), focal_backup, current_focal_hash)
        record(journal_path, journal, "cutover-backups",
               database_dump_sha256=sha256_file(database_dump),
               focal_backup_sha256=current_focal_hash,
               focal_original_metadata=focal_metadata.to_json())

        sql_text = read_verified_text(staged_sql, artifacts.sql_sha256)
        require_compose_contract(config, selected_compose, "before SQL/Lua cutover")
        # Refresh after the writer is stopped and durably journal this exact
        # rollback snapshot in the same intent record immediately before SQL.
        config_before = config_snapshot(config)
        record(journal_path, journal, "database-applying",
               config_before=config_before)
        mysql(config, sql_text)
        record(journal_path, journal, "database-applied")
        verify_database(config)

        record(journal_path, journal, "lua-deploying")
        atomic_replace_verified_file(
            staged_focal, config.lua_focal_destination, transition.target_sha256,
            focal_metadata)
        record(journal_path, journal, "lua-deployed")

        boot_since = utc_now()
        require_compose_contract(config, selected_compose, "before candidate start")
        record(journal_path, journal, "candidate-starting", boot_since=boot_since)
        compose(config, "up", "--detach", "--no-deps", "--force-recreate",
                config.compose_service)
        candidate_container = docker_inspect(config.worldserver_container)
        candidate_container_id = str(candidate_container.get("Id") or "")
        if (not candidate_container_id or
                str(candidate_container.get("Image")) != candidate_id):
            raise UpgradeError(
                "candidate container identity/image could not be verified after recreate")
        record(journal_path, journal, "candidate-started", boot_since=boot_since,
               candidate_container_id=candidate_container_id)
        logs = wait_ready(config, boot_since)
        verify_logs(logs)
        verify_binary_api(config)
        verify_database(config)
        if sha256_file(config.lua_focal_destination) != transition.target_sha256:
            raise UpgradeError("post-start focal Lua verification failed")
        running_image = str(docker_inspect(config.worldserver_container).get("Image"))
        if running_image != candidate_id:
            raise UpgradeError(
                f"running container uses {running_image}, not candidate {candidate_id}")
        record(journal_path, journal, "verified", running_image_id=running_image)
        if not journal["world_was_running"]:
            stop_worldserver_exact(config, (candidate_container_id,))
            record(journal_path, journal, "candidate-restopped")
        record(journal_path, journal, status="complete", completed_at=utc_now())
        print(f"Upgrade completed. Durable rollback record: {run_dir}")
        return run_dir
    except BaseException as error:
        journal_error: BaseException | None = None
        try:
            record(journal_path, journal, status="failed", failure=str(error))
        except BaseException as failure:
            # A full disk or interrupted journal write must not suppress the
            # rollback attempt. The in-memory journal still carries every
            # intent recorded before its mutation.
            journal_error = failure
        print(f"Upgrade failed; rolling back from {run_dir}: {error}", file=sys.stderr)
        try:
            rollback(config, run_dir, journal, automatic=True)
        except BaseException as rollback_error:
            rollback_journal_error: BaseException | None = None
            try:
                record(journal_path, journal, status="rollback-failed",
                       rollback_failure=str(rollback_error))
            except BaseException as failure:
                rollback_journal_error = failure
            journal_suffix = (
                f"; rollback failure could not be journaled: {rollback_journal_error}"
                if rollback_journal_error else "")
            raise UpgradeError(
                f"upgrade failed ({error}); automatic rollback also failed "
                f"({rollback_error}){journal_suffix}. Preserve and inspect {run_dir}") from rollback_error
        if isinstance(error, KeyboardInterrupt):
            raise error
        suffix = f"; failure status could not be journaled: {journal_error}" if journal_error else ""
        raise UpgradeError(f"upgrade failed and was rolled back: {error}{suffix}") from error


def rollback(config: Config, run_dir: Path, journal: dict[str, Any] | None = None,
             *, automatic: bool = False) -> None:
    journal_path = run_dir / "journal.json"
    journal = journal or read_json(journal_path)
    if journal.get("release") != RELEASE:
        raise UpgradeError(f"rollback journal is not for {RELEASE}: {journal_path}")
    if Path(journal.get("core_root", "")).resolve() != config.core_root:
        raise UpgradeError("rollback core root differs from the recorded installation")
    if Path(journal.get("lua_root", "")).resolve() != config.lua_root:
        raise UpgradeError("rollback Lua root differs from the recorded installation")
    recorded_contract = {
        "database_container": journal.get("database_container"),
        "worldserver_container": journal.get("worldserver_container"),
        "compose_service": journal.get("compose_service"),
        "compose_project": journal.get("compose_project"),
        "compose_files": [str(Path(path).resolve())
                          for path in journal.get("compose_files", [])],
        "compose_env_files": [str(Path(entry.get("path", "")).resolve())
                              for entry in journal.get("compose_env_files", [])
                              if isinstance(entry, dict)],
    }
    selected_contract = {
        "database_container": config.database_container,
        "worldserver_container": config.worldserver_container,
        "compose_service": config.compose_service,
        "compose_project": config.compose_project,
        "compose_files": [str(path.resolve()) for path in config.compose_files],
        "compose_env_files": [str(path.resolve())
                              for path in config.compose_env_files],
    }
    if recorded_contract != selected_contract:
        raise UpgradeError(
            "rollback Docker/Compose selection differs from the journal; "
            f"recorded={recorded_contract!r} selected={selected_contract!r}")
    recorded_compose_digest = journal.get("compose_config_sha256")
    recorded_service_hash = journal.get("compose_service_config_hash")
    recorded_env = journal.get("compose_env_files")
    if (not isinstance(recorded_compose_digest, str) or
            not isinstance(recorded_service_hash, str) or
            not isinstance(recorded_env, list)):
        raise UpgradeError("rollback journal lacks the complete Compose input contract")
    env_contract: list[tuple[str, str]] = []
    for entry in recorded_env:
        if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or
                not isinstance(entry.get("sha256"), str)):
            raise UpgradeError("rollback journal has an invalid Compose env-file contract")
        env_contract.append((str(Path(entry["path"]).resolve()), entry["sha256"]))
    expected_compose = ComposeContract(
        recorded_compose_digest, recorded_service_hash, tuple(env_contract))
    compose_drift: UpgradeError | None = None
    try:
        require_compose_contract(config, expected_compose, "rollback")
    except UpgradeError as error:
        # Compose drift gates only a later Compose recreate. Source, focal Lua,
        # database rows, and image tags are restored independently below.
        compose_drift = error

    steps = set(journal.get("steps", []))
    runtime_steps = {
        "worldserver-stopping", "worldserver-stopped", "candidate-starting",
        "candidate-started", "verified", "candidate-restopped",
    }
    stopped_writer_steps = {
        "lua-deploying", "lua-deployed", "database-applying", "database-applied",
    }
    rollback_errors: list[str] = []
    writer_quiesced = not bool(steps & (runtime_steps | stopped_writer_steps))
    if not writer_quiesced:
        try:
            stop_worldserver_exact(
                config,
                (journal.get("old_container_id"),
                 journal.get("candidate_container_id")),
                candidate_image_id=(
                    str(journal.get("candidate_image_id"))
                    if "candidate-starting" in steps and
                    journal.get("candidate_image_id") else None),
                expected_service_hash=recorded_service_hash,
            )
            writer_quiesced = True
        except BaseException as error:
            rollback_errors.append(f"worldserver stop: {error}")

    # Each independent restoration is attempted even when another one fails or
    # the selected Compose inputs drifted after cutover.
    if steps & {"lua-deploying", "lua-deployed"}:
        if not writer_quiesced:
            rollback_errors.append(
                "focal Lua restore skipped because worldserver stop was not proven")
        else:
            try:
                source = run_dir / "backup/lua" / FOCAL_LUA_RELATIVE
                expected = journal.get("focal_backup_sha256")
                if not isinstance(expected, str) or not source.is_file():
                    raise UpgradeError(f"focal Lua rollback backup/hash is missing: {source}")
                metadata = FileMetadata.from_json(journal.get("focal_original_metadata"))
                atomic_replace_verified_file(
                    source, config.lua_focal_destination, expected, metadata,
                    require_current_metadata=False)
            except BaseException as error:
                rollback_errors.append(f"focal Lua restore: {error}")
    if steps & {"database-applying", "database-applied"}:
        if not writer_quiesced:
            rollback_errors.append(
                "database restore skipped because worldserver stop was not proven")
        else:
            try:
                before = journal.get("config_before")
                if not isinstance(before, dict):
                    raise UpgradeError("rollback journal lacks the stopped-server config snapshot")
                restore_config(config, before)
            except BaseException as error:
                rollback_errors.append(f"database restore: {error}")
    if journal.get("native_changed"):
        try:
            hashes = journal.get("native_backup_sha256")
            metadata = journal.get("native_backup_metadata")
            if not isinstance(hashes, dict) or not isinstance(metadata, dict):
                raise UpgradeError("rollback journal lacks native backup hashes/metadata")
            restore_native(config, run_dir, hashes, metadata)
            restored = assess_native(config.ale_root)
            if restored.state != "prior":
                raise UpgradeError("native rollback did not restore the known prior state")
        except BaseException as error:
            rollback_errors.append(f"native restore: {error}")

    old_id = journal.get("old_image_id")
    old_container_id = journal.get("old_container_id")
    old_reference = journal.get("old_image_reference")
    if "old-image-preserved" in steps and old_id and old_reference:
        try:
            run(("docker", "image", "tag", str(old_id), str(old_reference)))
        except BaseException as error:
            rollback_errors.append(f"old image tag restore: {error}")

    if rollback_errors:
        raise UpgradeError("; ".join(rollback_errors))

    restart_required = bool(journal.get("world_was_running") and steps & runtime_steps)
    if restart_required:
        if not isinstance(old_id, str) or not old_id.startswith("sha256:"):
            raise UpgradeError("rollback journal lacks a valid original worldserver image ID")
        existing = docker_inspect_optional(config.worldserver_container)
        existing_is_old = bool(
            existing is not None and
            isinstance(old_container_id, str) and
            str(existing.get("Id")) == old_container_id and
            str(existing.get("Image")) == str(old_id))
        rollback_since = utc_now()
        if existing_is_old:
            # Before force-recreate, Compose merely stopped the exact original
            # container. Its recorded container config remains authoritative,
            # so a later Compose-file drift is irrelevant to restarting it.
            run(("docker", "container", "start", config.worldserver_container))
            restored_container = docker_inspect(config.worldserver_container)
            restored_image = str(restored_container.get("Image"))
            restored_container_id = str(restored_container.get("Id"))
            if restored_image != old_id or restored_container_id != old_container_id:
                raise UpgradeError(
                    "directly restarted container identity changed: "
                    f"container={restored_container_id} image={restored_image}, "
                    f"expected container={old_container_id} image={old_id}")
            wait_ready(config, rollback_since)
        elif compose_drift is not None:
            journal_failure: BaseException | None = None
            try:
                record(
                    journal_path, journal,
                    status="rollback-state-restored-compose-blocked",
                    rollback_state_restored_at=utc_now(),
                    compose_blocker=str(compose_drift),
                )
            except BaseException as error:
                journal_failure = error
            suffix = (f"; additionally could not update the journal: {journal_failure}"
                      if journal_failure else "")
            raise UpgradeError(
                "native/Lua/database/image state was restored, but the old "
                "worldserver was left stopped because its recorded Compose inputs "
                f"have drifted: {compose_drift}{suffix}")
        else:
            require_compose_contract(
                config, expected_compose, "before rollback worldserver start")
            compose(config, "up", "--detach", "--no-deps", "--force-recreate",
                    config.compose_service)
            restored_image = str(
                docker_inspect(config.worldserver_container).get("Image"))
            if restored_image != old_id:
                raise UpgradeError(
                    f"rollback container uses {restored_image}, expected {old_id}")
            wait_ready(config, rollback_since)
    record(journal_path, journal, status="rolled-back",
           rolled_back_at=utc_now(), automatic_rollback=automatic)
    print(f"Rollback completed from {run_dir}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="read-only plan (default)")
    mode.add_argument("--apply", action="store_true", help="perform the upgrade")
    mode.add_argument("--rollback", metavar="RUN_DIRECTORY",
                      help="restore a durable backup created by a prior run")
    parser.add_argument("--core-root", type=Path,
                        help="AzerothCore source/compose root")
    parser.add_argument("--lua-root", type=Path,
                        help="configured ALE.ScriptPath (destination is its paragon child)")
    parser.add_argument("--lua-source", type=Path,
                        help="development-layout focal source override (forbidden in packages)")
    parser.add_argument("--database-container", default="ac-database")
    parser.add_argument("--worldserver-container", default="ac-worldserver")
    parser.add_argument("--compose-service", default="ac-worldserver")
    parser.add_argument("--compose-project")
    parser.add_argument("--compose-file", action="append", type=Path, default=[])
    parser.add_argument(
        "--env-file", action="append", type=Path, default=[],
        help="Compose interpolation env file; repeat in the exact original order")
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--ready-pattern", default=READY_DEFAULT)
    parser.add_argument("--readiness-timeout", type=int, default=300)
    parser.add_argument("--stop-timeout", type=int, default=60)
    parser.add_argument("--worldserver-binary",
                        default="/azerothcore/env/dist/bin/worldserver")
    parser.add_argument("--allow-development-layout", action="store_true",
                        help="allow repo sources without release SHA256SUMS")
    return parser.parse_args(argv)


def make_config(args: argparse.Namespace) -> Config:
    if args.core_root is None or args.lua_root is None:
        raise UpgradeError("--core-root and --lua-root are required")
    core = args.core_root.expanduser().resolve()
    lua = args.lua_root.expanduser().resolve()
    files = tuple(path.expanduser().resolve() for path in args.compose_file)
    for path in files:
        if not path.is_file() and not args.rollback:
            raise UpgradeError(f"compose file is missing: {path}")
    requested_env_files = [path.expanduser().resolve() for path in args.env_file]
    if args.rollback and not requested_env_files:
        rollback_journal = read_json(
            Path(args.rollback).expanduser().resolve() / "journal.json")
        recorded_env = rollback_journal.get("compose_env_files")
        if not isinstance(recorded_env, list):
            raise UpgradeError(
                "rollback journal lacks its recorded Compose env-file contract")
        for entry in recorded_env:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise UpgradeError(
                    "rollback journal has an invalid Compose env-file contract")
            requested_env_files.append(Path(entry["path"]).expanduser().resolve())
    elif not requested_env_files and (core / ".env").is_file():
        # Make Compose's otherwise implicit .env lookup an explicit, hashed
        # input for every later build/recreate command.
        requested_env_files = [(core / ".env").resolve()]
    env_files = tuple(requested_env_files)
    if len(set(env_files)) != len(env_files):
        raise UpgradeError("the same --env-file was selected more than once")
    for path in env_files:
        if not path.is_file() and not args.rollback:
            raise UpgradeError(f"Compose env file is missing: {path}")
    # Keep dumps outside the Docker build context. A retained database backup
    # under core_root would otherwise be uploaded on every later image build.
    backup = (args.backup_root.expanduser().resolve() if args.backup_root else
              core.parent / "paragon-upgrade-backups" / RELEASE)
    if args.readiness_timeout < 10 or args.stop_timeout < 1:
        raise UpgradeError("readiness timeout must be >=10 and stop timeout must be >=1")
    try:
        re.compile(args.ready_pattern)
    except re.error as error:
        raise UpgradeError(f"invalid readiness regex: {error}") from error
    return Config(
        core_root=core,
        lua_root=lua,
        lua_source_override=(
            args.lua_source.expanduser().resolve() if args.lua_source else None),
        database_container=args.database_container,
        worldserver_container=args.worldserver_container,
        compose_service=args.compose_service,
        compose_project=args.compose_project,
        compose_files=files,
        compose_env_files=env_files,
        backup_root=backup,
        ready_pattern=args.ready_pattern,
        readiness_timeout=args.readiness_timeout,
        stop_timeout=args.stop_timeout,
        worldserver_binary=args.worldserver_binary,
        allow_development_layout=args.allow_development_layout,
    )


def canonical_lock_path(config: Config) -> Path:
    identity = os.path.normcase(str(config.core_root)).encode("utf-8")
    suffix = hashlib.sha256(identity).hexdigest()[:16]
    return (config.core_root.parent / "paragon-upgrade-locks" /
            f"{config.core_root.name}-{suffix}-{RELEASE}.lock")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = make_config(args)
        if args.rollback:
            run_dir = Path(args.rollback).expanduser().resolve()
            with UpgradeLock(canonical_lock_path(config)):
                rollback(config, run_dir)
            return 0

        layout = locate_layout(config)
        _, transition, artifacts = verify_manifest(layout)
        assessment, inspect, database, installed_hash, selected_compose = preflight(
            config, layout, transition)
        if not args.apply:
            print_plan(config, layout, assessment, inspect, database,
                       installed_hash, selected_compose, transition)
            return 0
        with UpgradeLock(canonical_lock_path(config)):
            # Re-resolve and re-hash the complete package under the canonical
            # target-scoped lock. No bytes checked before the lock are trusted
            # for staging or cutover.
            layout = locate_layout(config)
            _, transition, artifacts = verify_manifest(layout)
            assessment, inspect, database, installed_hash, selected_compose = preflight(
                config, layout, transition)
            apply_upgrade(config, layout, assessment, inspect, database,
                          installed_hash, selected_compose, transition, artifacts)
        return 0
    except UpgradeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"ERROR: filesystem operation failed: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
