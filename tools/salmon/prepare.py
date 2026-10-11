#!/usr/bin/env python3
"""Prepare pinned Salmon sources and Cargo vendors without changing global state."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OWNER = "genomics-wasm/salmon/prepare-v1"


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError(f"Unsafe relative path: {value!r}")
    if not path.parts or any(part in ("", ".") for part in value.split("/")):
        raise ValueError(f"Invalid relative path: {value!r}")
    return path


def checked_archive(record: dict, downloads: Path) -> Path:
    relative = relative_path(record["archive"])
    expected = record["sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError(f"Invalid SHA256 for {relative}")
    path = downloads / relative
    if not path.resolve().is_relative_to(downloads):
        raise ValueError(f"Archive path leaves downloads directory: {relative}")
    if path.is_symlink():
        raise ValueError(f"Archive must not be a symbolic link: {path}")
    if not path.exists():
        if not record["url"].startswith("https://"):
            raise ValueError(f"Archive URL must use HTTPS: {relative}")
        path.parent.mkdir(parents=True, exist_ok=True)
        log(f"Downloading {relative}")
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as output:
                request = urllib.request.Request(record["url"], headers={"User-Agent": OWNER})
                with urllib.request.urlopen(request, timeout=120) as response:
                    shutil.copyfileobj(response, output)
            actual = digest(temporary_path)
            if actual != expected:
                raise ValueError(f"Checksum mismatch for {relative}: expected {expected}, got {actual}")
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
    actual = digest(path)
    if actual != expected:
        raise ValueError(f"Checksum mismatch for {relative}: expected {expected}, got {actual}")
    return path


def extract_archive(archive: Path, destination: Path, expected_root: str | None = None) -> None:
    """Extract one common archive root using Python's safe data filter."""
    if not hasattr(tarfile, "data_filter"):
        raise RuntimeError("Python with tarfile.data_filter is required (Python 3.12 or later).")
    with tempfile.TemporaryDirectory(prefix=".extract-", dir=destination.parent) as temporary:
        extraction = Path(temporary)
        with tarfile.open(archive, "r:*") as handle:
            members = handle.getmembers()
            roots: set[str] = set()
            paths: set[str] = set()
            for member in members:
                name = member.name.rstrip("/")
                relative = relative_path(name)
                roots.add(relative.parts[0])
                if name in paths:
                    raise ValueError(f"Duplicate archive entry: {archive.name}: {name}")
                paths.add(name)
                if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                    raise ValueError(f"Unsupported archive entry type: {archive.name}: {name}")
                if (member.issym() or member.islnk()) and PurePosixPath(member.linkname).is_absolute():
                    raise ValueError(f"Absolute archive link: {archive.name}: {name}")
            if len(roots) != 1:
                raise ValueError(f"Archive must contain exactly one root directory: {archive}")
            root_name = next(iter(roots))
            if expected_root is not None and root_name != expected_root:
                raise ValueError(f"Unexpected crate root in {archive}: {root_name}, expected {expected_root}")
            handle.extractall(extraction, members=members, filter="data")
        extracted = extraction / root_name
        if not extracted.is_dir() or extracted.is_symlink():
            raise ValueError(f"Archive root is not a directory: {archive}")
        for item in extracted.rglob("*"):
            if item.is_symlink() and not item.resolve().is_relative_to(extracted):
                raise ValueError(f"Archive link leaves its package directory: {item}")
        extracted.rename(destination)


def files_manifest(root: Path, *, exclude_checksum: bool = False) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if exclude_checksum and relative == ".cargo-checksum.json":
            continue
        if path.is_symlink():
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"Source link leaves its tree: {path}")
            # Cargo checks file contents, including files reached via safe links.
            if path.is_file():
                result[relative] = digest(path)
        elif path.is_file():
            result[relative] = digest(path)
    return result


def write_checksums(vendor: Path, crates: list[dict]) -> None:
    for crate in crates:
        directory = vendor / f"{crate['name']}-{crate['version']}"
        data = {"files": files_manifest(directory, exclude_checksum=True), "package": crate["sha256"]}
        (directory / ".cargo-checksum.json").write_text(json.dumps(data, sort_keys=True) + "\n")


def apply_browser_patch(stage: Path, patch: Path) -> None:
    # Bound patch targets as well as archive paths, including added/deleted files.
    headers = 0
    for line in patch.read_text().splitlines():
        if line.startswith(("--- ", "+++ ")):
            name = line[4:].split("\t", 1)[0]
            if name == "/dev/null":
                continue
            relative = relative_path(name)
            if len(relative.parts) < 3 or relative.parts[0] not in ("a", "b") or relative.parts[1] not in ("source", "vendor"):
                raise ValueError(f"Patch target must be under source/ or vendor/: {name}")
            headers += 1
    if not headers:
        raise ValueError(f"No unified diff file headers found in {patch}")
    with patch.open("rb") as handle:
        completed = subprocess.run(
            ["patch", "--batch", "--forward", "--fuzz=0", "--strip=1", "--directory", str(stage)],
            stdin=handle, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
    if completed.stdout:
        log(completed.stdout.rstrip())
    if completed.returncode:
        raise RuntimeError(f"Browser patch failed with exit code {completed.returncode}")


def managed_directory(path: Path) -> bool:
    marker = path / ".salmon-prepare-owner"
    return not path.is_symlink() and marker.is_file() and marker.read_text().strip() == OWNER


def cache_matches(state: dict, fingerprint: str, source: Path, vendor: Path) -> bool:
    if state.get("owner") != OWNER or state.get("fingerprint") != fingerprint:
        return False
    if not managed_directory(source) or not managed_directory(vendor):
        return False
    # Verify prepared input files; Cargo may add build outputs without invalidating them.
    for label, root in (("source", source), ("vendor", vendor)):
        expected = state.get("files", {}).get(label)
        if not isinstance(expected, dict) or not expected:
            return False
        for relative, expected_digest in expected.items():
            path = root / relative_path(relative)
            if not path.is_file() or not path.resolve().is_relative_to(root) or digest(path) != expected_digest:
                return False
    return True


def prepare(mode: str, build: Path, downloads: Path) -> Path:
    lock_path = HERE / "sources.lock.json"
    lock = json.loads(lock_path.read_text())
    crates = lock["dependencies"]["crates"]
    names = [f"{crate['name']}-{crate['version']}" for crate in crates]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate crate name/version in sources.lock.json")
    for name in names:
        if len(relative_path(name).parts) != 1:
            raise ValueError(f"Invalid crate directory name: {name}")
    variant = "native" if mode == "native" else "patched"
    source, vendor = build / f"source-{variant}", build / f"vendor-{variant}"
    inputs = {"sourceLock": digest(lock_path), "prepareScript": digest(Path(__file__)), "vendorPath": str(vendor), "variant": variant}
    if variant == "patched":
        for name in ("browser.patch", "Cargo.lock"):
            path = HERE / name
            if not path.is_file():
                raise FileNotFoundError(f"Patched preparation requires {path}")
            inputs[name] = digest(path)
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    records = [lock, *crates]
    archive_names = [record["archive"] for record in records]
    if len(set(archive_names)) != len(archive_names):
        raise ValueError("Duplicate archive path in sources.lock.json")
    log(f"Verifying {len(records)} pinned archives")
    with ThreadPoolExecutor(max_workers=8) as pool:
        archives = list(pool.map(lambda record: checked_archive(record, downloads), records))
    state_path = build / f".prepare-{variant}.json"
    try:
        state = json.loads(state_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    if cache_matches(state, fingerprint, source, vendor):
        log(f"Reusing verified {variant} sources")
        return source
    for destination in (source, vendor):
        if destination.exists() and not managed_directory(destination):
            raise RuntimeError(f"Refusing to replace an unmanaged directory: {destination}")
    with tempfile.TemporaryDirectory(prefix=f".prepare-{variant}-", dir=build) as temporary:
        stage = Path(temporary)
        staged_source, staged_vendor = stage / "source", stage / "vendor"
        log(f"Extracting {variant} source and {len(crates)} crates")
        extract_archive(archives[0], staged_source)
        original_cargo = lock.get("rust", {}).get("upstreamCargoLockSha256")
        if original_cargo and digest(staged_source / "Cargo.lock") != original_cargo:
            raise ValueError("Upstream Cargo.lock does not match the pinned source lock")
        staged_vendor.mkdir()
        for crate, archive in zip(crates, archives[1:], strict=True):
            name = f"{crate['name']}-{crate['version']}"
            extract_archive(archive, staged_vendor / name, name)
        if variant == "patched":
            apply_browser_patch(stage, HERE / "browser.patch")
            shutil.copyfile(HERE / "Cargo.lock", staged_source / "Cargo.lock")
        write_checksums(staged_vendor, crates)
        config = staged_source / ".cargo" / "config.toml"
        config.parent.mkdir(exist_ok=True)
        upstream_config = config.read_text() if config.exists() else ""
        if re.search(r"(?m)^\s*\[source[.\]]", upstream_config):
            raise ValueError("Upstream Cargo configuration already contains a source override")
        config.write_text(upstream_config.rstrip() + "\n\n[source.crates-io]\nreplace-with = \"vendored-sources\"\n\n[source.vendored-sources]\ndirectory = " + json.dumps(str(vendor)) + "\n")
        for directory in (staged_source, staged_vendor):
            (directory / ".salmon-prepare-owner").write_text(OWNER + "\n")
        new_state = {"owner": OWNER, "fingerprint": fingerprint, "inputs": inputs, "files": {"source": files_manifest(staged_source), "vendor": files_manifest(staged_vendor)}}
        # Delete only exact, marked outputs owned by this preparer, after their
        # replacements have been fully extracted, patched, and checksummed.
        for prepared, destination in ((staged_source, source), (staged_vendor, vendor)):
            if destination.exists():
                if not managed_directory(destination):
                    raise RuntimeError(f"Output ownership changed during preparation: {destination}")
                shutil.rmtree(destination)
            prepared.rename(destination)
        state_temp = stage / "state.json"
        state_temp.write_text(json.dumps(new_state, sort_keys=True) + "\n")
        state_temp.replace(state_path)
    log(f"Prepared {variant} sources")
    return source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("native", "patched-native", "wasm"))
    parser.add_argument("--build-root", type=Path, default=ROOT / "build" / "salmon")
    parser.add_argument("--downloads-root", type=Path, default=ROOT / "build" / "downloads")
    args = parser.parse_args()
    build, downloads = args.build_root.resolve(), args.downloads_root.resolve()
    if build == ROOT or build == downloads or build.is_relative_to(HERE):
        parser.error("Build root must be a separate build directory")
    build.mkdir(parents=True, exist_ok=True)
    downloads.mkdir(parents=True, exist_ok=True)
    try:
        with (build / ".prepare.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            source = prepare(args.mode, build, downloads)
        print(source)
        return 0
    except (OSError, ValueError, RuntimeError, tarfile.TarError) as error:
        log(f"Salmon preparation failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
