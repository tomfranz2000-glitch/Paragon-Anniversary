#!/usr/bin/env python3
"""Build the deterministic, server-only instance-XP upgrade archive.

Release payloads are read from a resolved Git commit, rather than from the
checkout.  The sole exception is the upgrade template when
``--allow-unpublished`` is used; that mode exists so a template can be tested
before it is committed.  The focal server Lua, native patches, pins, and the
license are always read as Git blobs from the resolved target commit.  ZIP
entries are stored without host-dependent compression for byte-identical
archives across supported hosts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import zipfile
from typing import Dict, Iterable, Mapping, Sequence


ROOT = pathlib.Path(__file__).resolve().parents[1]

BASELINE_COMMIT = "05ea122dc80b6a08ba01a6f0506523a13cdbe1c2"
RELEASE_ID = "instance-xp-v1"
TEMPLATE_PATH = "upgrades/instance-xp-v1"
SERVER_LUA_FOCAL_SOURCE = (
    "serverside/paragon/modules/paragon_rework_sources.lua"
)
SERVER_LUA_FOCAL_PAYLOAD = "server/" + SERVER_LUA_FOCAL_SOURCE

TEMPLATE_FILES = frozenset(
    (
        "install.py",
        "manifest.json",
        "README.md",
        "RELEASE_NOTES.md",
        "native/mod-ale-instance-xp.patch",
        "sql/instance-xp.sql",
    )
)

INTENDED_RUNTIME_DELTA = frozenset(
    (
        SERVER_LUA_FOCAL_SOURCE,
        "patches/05-mod-ale.patch",
        "sql/04_insert_default_config.sql",
        "sql/05_apply_anniversary_config.sql",
    )
)
ALLOWED_SUPPORT_CHANGE_PREFIXES = ("doc/", "tools/", "upgrades/")
ALLOWED_SUPPORT_CHANGE_FILES = frozenset(
    (".gitignore", "LICENSE", "README.md", "patches/PINS.md")
)

CUMULATIVE_PATCHES = (
    "patches/05-mod-ale.patch",
    "patches/07-mod-ale-profession-xp.patch",
    "patches/09-mod-ale-pvp-merit.patch",
)
PIN_FILE = "patches/PINS.md"
STATIC_FILES = CUMULATIVE_PATCHES + (PIN_FILE, "LICENSE")

PINS = {
    "azerothCorePlayerbot": {
        "repository": "https://github.com/mod-playerbots/azerothcore-wotlk.git",
        "commit": "efe123fab543c5faf3c477674ec17a18fd59f09f",
    },
    "modAle": {
        "repository": "https://github.com/azerothcore/mod-eluna.git",
        "commit": "9e5b8c66efeb383871ec58b925e47094c92cc8d5",
    },
    "modTransmog": {
        "repository": "https://github.com/tomfranz2000-glitch/mod-transmog.git",
        "commit": "31633595cad7b12042b6484ffe3ea34f355b9821",
    },
}

FORBIDDEN_SUFFIXES = frozenset(
    (".blp", ".dbc", ".key", ".mpq", ".p12", ".pem", ".pfx", ".toc", ".wtf")
)
FORBIDDEN_SEGMENTS = frozenset(
    (
        ".aws",
        ".git",
        ".pytest_cache",
        "__pycache__",
        "addons",
        "cache",
        "caches",
        "clientside",
        "interface",
    )
)
FORBIDDEN_NAMES = frozenset(
    (
        ".env",
        ".npmrc",
        "credentials",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
        "service-account",
        "service-account.json",
        "service_account",
        "service_account.json",
        "token",
        "token.json",
        "token.txt",
    )
)


class BuildError(RuntimeError):
    """A release invariant was not satisfied."""


def _git(
    repository: pathlib.Path,
    arguments: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    command = ["git", "-C", os.fspath(repository), *arguments]
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise BuildError("git is required to build the upgrade: %s" % exc) from exc

    if check and result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise BuildError(
            "git command failed (%s): %s"
            % (" ".join(arguments), detail or "exit %d" % result.returncode)
        )
    return result


def _git_text(repository: pathlib.Path, arguments: Sequence[str]) -> str:
    return _git(repository, arguments).stdout.decode("utf-8", "strict").strip()


def resolve_commit(repository: pathlib.Path, ref: str) -> str:
    if not ref or ref.startswith("-"):
        raise BuildError("the Git ref must be non-empty and must not start with '-'")
    commit = _git_text(repository, ["rev-parse", "--verify", ref + "^{commit}"])
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise BuildError("Git did not resolve %r to a full commit SHA" % ref)
    return commit


def _assert_clean_release_state(repository: pathlib.Path, target_commit: str) -> None:
    dirty = _git_text(
        repository, ["status", "--porcelain=v1", "--untracked-files=all"]
    )
    if dirty:
        first = dirty.splitlines()[0]
        raise BuildError(
            "release builds require a clean worktree (first change: %s); "
            "use --allow-unpublished only for local package tests" % first
        )

    branch_result = _git(
        repository, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False
    )
    branch = branch_result.stdout.decode("utf-8", "replace").strip()
    if branch_result.returncode or branch != "main":
        raise BuildError("release builds must run from the checked-out main branch")

    refs = {
        "HEAD": _git_text(repository, ["rev-parse", "HEAD"]),
        "local main": _git_text(repository, ["rev-parse", "refs/heads/main"]),
        "origin/main": _git_text(
            repository, ["rev-parse", "refs/remotes/origin/main"]
        ),
    }
    for label, commit in refs.items():
        if commit != target_commit:
            raise BuildError(
                "%s is %s, not target %s" % (label, commit, target_commit)
            )

    remote = _git(
        repository,
        ["ls-remote", "--exit-code", "--heads", "origin", "refs/heads/main"],
        check=False,
    )
    if remote.returncode:
        detail = remote.stderr.decode("utf-8", "replace").strip()
        raise BuildError("could not verify live origin/main: %s" % (detail or "missing"))
    fields = remote.stdout.decode("ascii", "strict").split()
    if len(fields) < 2 or fields[0] != target_commit:
        remote_commit = fields[0] if fields else "missing"
        raise BuildError(
            "live origin/main is %s, not target %s" % (remote_commit, target_commit)
        )

    ancestry = _git(
        repository,
        ["merge-base", "--is-ancestor", BASELINE_COMMIT, target_commit],
        check=False,
    )
    if ancestry.returncode:
        raise BuildError(
            "target %s does not contain required baseline %s"
            % (target_commit, BASELINE_COMMIT)
        )


def _validate_target_scope(repository: pathlib.Path, target_commit: str) -> None:
    result = _git(
        repository,
        [
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            BASELINE_COMMIT,
            target_commit,
            "--",
        ],
    )
    changed = frozenset(
        raw.decode("utf-8", "strict")
        for raw in result.stdout.split(b"\0")
        if raw
    )

    missing = sorted(INTENDED_RUNTIME_DELTA - changed)
    unsupported = sorted(
        path
        for path in changed
        if path not in INTENDED_RUNTIME_DELTA
        and path not in ALLOWED_SUPPORT_CHANGE_FILES
        and not path.startswith(ALLOWED_SUPPORT_CHANGE_PREFIXES)
        and not path.lower().endswith(".md")
    )
    if missing or unsupported:
        details = []
        if missing:
            details.append("missing intended runtime paths: " + ", ".join(missing))
        if unsupported:
            details.append("unsupported changed paths: " + ", ".join(unsupported))
        raise BuildError(
            "baseline-to-target scope is not deployable by this focused installer ("
            + "; ".join(details)
            + ")"
        )


def _validate_payload_path(path: str) -> None:
    pure = pathlib.PurePosixPath(path)
    if not path or pure.is_absolute() or ".." in pure.parts or "\\" in path:
        raise BuildError("unsafe payload path: %r" % path)

    lower_parts = tuple(part.lower() for part in pure.parts)
    lower_name = pure.name.lower()
    if any(part in FORBIDDEN_SEGMENTS for part in lower_parts):
        raise BuildError("cache or repository metadata is forbidden: %s" % path)
    if (
        lower_name in FORBIDDEN_NAMES
        or lower_name.startswith(".env.")
        or lower_name.startswith("service-account.")
        or lower_name.startswith("service_account.")
        or lower_name.startswith("token.")
        or pure.suffix.lower() in FORBIDDEN_SUFFIXES
    ):
        raise BuildError("client data or secret-like file is forbidden: %s" % path)


def _list_git_tree(repository: pathlib.Path, commit: str, prefix: str) -> list[str]:
    result = _git(
        repository,
        ["--literal-pathspecs", "ls-tree", "-r", "-z", "--name-only", commit, "--", prefix],
    )
    paths = [
        raw.decode("utf-8", "strict")
        for raw in result.stdout.split(b"\0")
        if raw
    ]
    return sorted(paths)


def _git_blob(repository: pathlib.Path, commit: str, path: str) -> bytes:
    result = _git(repository, ["show", "%s:%s" % (commit, path)])
    return result.stdout


def _add(payloads: Dict[str, bytes], path: str, content: bytes) -> None:
    normalized = pathlib.PurePosixPath(path).as_posix()
    _validate_payload_path(normalized)
    if normalized in payloads:
        raise BuildError("duplicate payload path: %s" % normalized)
    payloads[normalized] = content


def _load_git_tree(
    repository: pathlib.Path,
    commit: str,
    source_prefix: str,
    destination_prefix: str,
) -> Dict[str, bytes]:
    paths = _list_git_tree(repository, commit, source_prefix)
    if not paths:
        raise BuildError(
            "%s is missing or empty at target commit %s" % (source_prefix, commit)
        )

    loaded: Dict[str, bytes] = {}
    source_root = pathlib.PurePosixPath(source_prefix)
    destination_root = pathlib.PurePosixPath(destination_prefix)
    for source in paths:
        relative = pathlib.PurePosixPath(source).relative_to(source_root)
        destination = (destination_root / relative).as_posix()
        _add(loaded, destination, _git_blob(repository, commit, source))
    return loaded


def _load_worktree_template(repository: pathlib.Path) -> Dict[str, bytes]:
    template = repository / pathlib.PurePosixPath(TEMPLATE_PATH)
    if not template.is_dir():
        raise BuildError("upgrade template directory is missing: %s" % template)

    loaded: Dict[str, bytes] = {}
    for base, directories, files in os.walk(template, followlinks=False):
        base_path = pathlib.Path(base)
        for name in list(directories):
            child = base_path / name
            if child.is_symlink():
                raise BuildError("template symlinks are not permitted: %s" % child)
        for name in sorted(files):
            source = base_path / name
            if source.is_symlink():
                raise BuildError("template symlinks are not permitted: %s" % source)
            # Template contents are the user-facing package root.  In
            # particular, install.py must be runnable immediately after unzip.
            relative = source.relative_to(template).as_posix()
            _add(loaded, relative, source.read_bytes())

    if not loaded:
        raise BuildError("upgrade template directory is empty: %s" % template)
    return loaded


def _validate_template_files(template_payloads: Mapping[str, bytes]) -> None:
    actual = frozenset(template_payloads)
    missing = sorted(TEMPLATE_FILES - actual)
    unexpected = sorted(actual - TEMPLATE_FILES)
    if missing or unexpected:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        raise BuildError(
            "%s must contain the exact release-template file set (%s)"
            % (TEMPLATE_PATH, "; ".join(details))
        )


def _server_lua_transition(
    repository: pathlib.Path, target_commit: str
) -> Dict[str, str]:
    baseline_blob = _git_blob(repository, BASELINE_COMMIT, SERVER_LUA_FOCAL_SOURCE)
    target_blob = _git_blob(repository, target_commit, SERVER_LUA_FOCAL_SOURCE)
    if b"\r" in baseline_blob or b"\r" in target_blob:
        raise BuildError(
            "focal Lua Git blobs must use canonical LF line endings so exact "
            "Windows CRLF compatibility hashes can be derived"
        )
    return {
        "path": SERVER_LUA_FOCAL_PAYLOAD,
        "baselineSha256": hashlib.sha256(baseline_blob).hexdigest(),
        "targetSha256": hashlib.sha256(target_blob).hexdigest(),
        "baselineCrlfSha256": hashlib.sha256(
            baseline_blob.replace(b"\n", b"\r\n")
        ).hexdigest(),
        "targetCrlfSha256": hashlib.sha256(
            target_blob.replace(b"\n", b"\r\n")
        ).hexdigest(),
    }


def _release_manifest(
    target_commit: str,
    archive_stem: str,
    server_lua_transition: Mapping[str, str],
) -> bytes:
    document = {
        "archive": archive_stem + ".zip",
        "baselineCommit": BASELINE_COMMIT,
        "clientChanges": False,
        "cumulativeModAlePatches": list(CUMULATIVE_PATCHES),
        "formatVersion": 1,
        "payloads": {
            "installer": "install.py",
            "serverLua": SERVER_LUA_FOCAL_PAYLOAD,
        },
        "pins": PINS,
        "releaseId": RELEASE_ID,
        "serverLuaTransition": dict(server_lua_transition),
        "targetCommit": target_commit,
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _checksum_manifest(payloads: Mapping[str, bytes]) -> bytes:
    lines = []
    for path in sorted(payloads):
        digest = hashlib.sha256(payloads[path]).hexdigest()
        lines.append("%s  %s" % (digest, path))
    return ("\n".join(lines) + "\n").encode("ascii")


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_documented_pins(pins_document: bytes) -> None:
    for name, pin in PINS.items():
        commit = pin["commit"].encode("ascii")
        if commit not in pins_document:
            raise BuildError(
                "%s pin %s is missing from %s" % (name, pin["commit"], PIN_FILE)
            )


def _validate_final_payload_set(payloads: Mapping[str, bytes]) -> None:
    expected = frozenset(TEMPLATE_FILES).union(
        STATIC_FILES,
        (SERVER_LUA_FOCAL_PAYLOAD, "RELEASE.json", "SHA256SUMS"),
    )
    actual = frozenset(payloads)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        raise BuildError(
            "release payload violates the exact allowlist (%s)"
            % "; ".join(details)
        )


def collect_payloads(
    repository: pathlib.Path,
    target_commit: str,
    *,
    allow_unpublished: bool,
) -> Dict[str, bytes]:
    payloads: Dict[str, bytes] = {}

    if allow_unpublished:
        template_payloads = _load_worktree_template(repository)
    else:
        template_payloads = _load_git_tree(
            repository, target_commit, TEMPLATE_PATH, "."
        )
    _validate_template_files(template_payloads)
    for path, content in template_payloads.items():
        _add(payloads, path, content)

    _add(
        payloads,
        SERVER_LUA_FOCAL_PAYLOAD,
        _git_blob(repository, target_commit, SERVER_LUA_FOCAL_SOURCE),
    )

    for source in STATIC_FILES:
        content = _git_blob(repository, target_commit, source)
        _add(payloads, source, content)

    _verify_documented_pins(payloads[PIN_FILE])
    return payloads


def _zip_info(path: str, content: bytes) -> zipfile.ZipInfo:
    # A fixed pre-epoch timestamp and stable POSIX modes make repeat builds
    # byte-identical. Python scripts retain executable mode after Unix unzip.
    info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    executable = path.endswith(".py") or content.startswith(b"#!")
    info.external_attr = ((0o100755 if executable else 0o100644) << 16)
    info.compress_type = zipfile.ZIP_STORED
    return info


def _write_zip(archive: pathlib.Path, root_name: str, payloads: Mapping[str, bytes]) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: pathlib.Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=archive.name + ".", suffix=".tmp", dir=archive.parent, delete=False
        ) as temporary:
            temporary_path = pathlib.Path(temporary.name)

        with zipfile.ZipFile(
            temporary_path,
            mode="w",
            compression=zipfile.ZIP_STORED,
        ) as bundle:
            for relative_path in sorted(payloads):
                archive_path = "%s/%s" % (root_name, relative_path)
                content = payloads[relative_path]
                bundle.writestr(_zip_info(archive_path, content), content)

        os.replace(temporary_path, archive)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _write_zip_sidecar(archive: pathlib.Path) -> pathlib.Path:
    sidecar = pathlib.Path(os.fspath(archive) + ".sha256")
    content = ("%s  %s\n" % (_sha256_file(archive), archive.name)).encode("ascii")
    temporary_path: pathlib.Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=sidecar.name + ".",
            suffix=".tmp",
            dir=sidecar.parent,
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = pathlib.Path(temporary.name)
        os.replace(temporary_path, sidecar)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
    return sidecar


def build_upgrade(
    repository: pathlib.Path,
    *,
    ref: str = "HEAD",
    output_dir: pathlib.Path | None = None,
    allow_unpublished: bool = False,
) -> pathlib.Path:
    repository = repository.resolve()
    if not (repository / ".git").exists():
        raise BuildError("not a Git checkout: %s" % repository)

    target_commit = resolve_commit(repository, ref)
    if not allow_unpublished:
        _assert_clean_release_state(repository, target_commit)
    _validate_target_scope(repository, target_commit)

    archive_stem = "Paragon-Anniversary-upgrade-%s-to-%s" % (
        BASELINE_COMMIT[:7],
        target_commit[:7],
    )
    payloads = collect_payloads(
        repository, target_commit, allow_unpublished=allow_unpublished
    )
    transition = _server_lua_transition(repository, target_commit)
    if hashlib.sha256(payloads[SERVER_LUA_FOCAL_PAYLOAD]).hexdigest() != transition[
        "targetSha256"
    ]:
        raise BuildError("packaged focal Lua does not match its target Git blob")
    _add(
        payloads,
        "RELEASE.json",
        _release_manifest(target_commit, archive_stem, transition),
    )
    _add(payloads, "SHA256SUMS", _checksum_manifest(payloads))
    _validate_final_payload_set(payloads)

    if output_dir is None:
        output_dir = repository.parent / "Paragon-Releases"
    else:
        output_dir = pathlib.Path(output_dir)
        if not output_dir.is_absolute():
            output_dir = pathlib.Path.cwd() / output_dir
    archive = output_dir.resolve() / (archive_stem + ".zip")
    _write_zip(archive, archive_stem, payloads)
    _write_zip_sidecar(archive)
    return archive


def parse_args(arguments: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the deterministic server-only instance-XP upgrade archive."
    )
    parser.add_argument(
        "--ref",
        default="HEAD",
        help="Git commit or tag to package (default: HEAD; resolved to a full commit SHA).",
    )
    parser.add_argument(
        "--output-dir",
        type=pathlib.Path,
        help="archive directory (default: a Paragon-Releases sibling of this checkout)",
    )
    parser.add_argument(
        "--allow-unpublished",
        action="store_true",
        help=(
            "local-test mode: permit dirty/unpushed state and use the working-tree "
            "upgrade template; runtime payloads still come from --ref"
        ),
    )
    return parser.parse_args(arguments)


def main(arguments: Iterable[str] | None = None) -> int:
    options = parse_args(arguments)
    try:
        archive = build_upgrade(
            ROOT,
            ref=options.ref,
            output_dir=options.output_dir,
            allow_unpublished=options.allow_unpublished,
        )
    except BuildError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
