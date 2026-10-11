#!/usr/bin/env python3
"""Build the pinned Cutadapt native comparisons and Pyodide distribution.

Requires Linux x86_64, CPython 3.14.2, Node, a C compiler, patch and tar.
Compiler binaries are downloaded and verified, but are not redistributed.
All other locked archives can be recovered from dist/cutadapt/sources in a
standalone source distribution before attempting a network download.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/cutadapt"
BUILD = ROOT / "build/cutadapt"
DOWNLOADS = ROOT / "build/downloads"
DIST = ROOT / "dist/cutadapt"
LOCK = json.loads((TOOL / "sources.lock.json").read_text())


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def hashes(directory: Path) -> dict[str, str]:
    return {p.relative_to(directory).as_posix(): sha256(p)
            for p in sorted(directory.rglob("*")) if p.is_file()}


def inputs() -> dict[str, str]:
    result = {f"tools/cutadapt/{name}": value for name, value in hashes(TOOL).items()}
    result["scripts/build-cutadapt.py"] = sha256(Path(__file__))
    return result


def archive_records():
    yield LOCK
    yield from LOCK["dependencies"].values()
    for requirement in LOCK["buildRequirements"]:
        yield requirement["wheel"]
        yield requirement["source"]


def fetch(record: dict, *, toolchain: bool = False) -> Path:
    name = record["filename" if toolchain else "archive"]
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError(f"Unsafe archive path: {name}")
    destination = DOWNLOADS / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() and not toolchain:
        fallback = DIST / "sources" / relative
        if fallback.is_file():
            if sha256(fallback) != record["sha256"]:
                raise RuntimeError(f"Packaged source checksum mismatch: {name}")
            shutil.copyfile(fallback, destination)
    if not destination.exists():
        print(f"Downloading {name}", flush=True)
        temporary = destination.with_name(destination.name + ".partial")
        request = urllib.request.Request(record["url"], headers={"User-Agent": "genomics-wasm-build"})
        with urllib.request.urlopen(request, timeout=180) as source, temporary.open("wb") as output:
            shutil.copyfileobj(source, output)
        if sha256(temporary) != record["sha256"]:
            temporary.unlink()
            raise RuntimeError(f"Downloaded archive checksum mismatch: {name}")
        temporary.replace(destination)
    if sha256(destination) != record["sha256"]:
        raise RuntimeError(f"Archive checksum mismatch: {name}")
    return destination


def fresh(directory: Path):
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)


def extract(archive: Path, directory: Path) -> Path:
    fresh(directory)
    with tarfile.open(archive) as source:
        source.extractall(directory, filter="data")
    entries = list(directory.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        raise RuntimeError(f"Expected one top-level directory in {archive.name}")
    return entries[0]


def run(command, *, cwd: Path = ROOT, env=None):
    print("+ " + " ".join(str(item) for item in command), flush=True)
    subprocess.run([str(item) for item in command], cwd=cwd, env=env, check=True)


def write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def build_environment():
    directory = BUILD / "venv"
    fresh(directory)
    venv.EnvBuilder(with_pip=True).create(directory)
    env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYODIDE_ROOT", "EM_CONFIG", "EMSDK"):
        env.pop(name, None)
    env.update({"PATH": str(directory / "bin") + os.pathsep + env["PATH"],
                "VIRTUAL_ENV": str(directory), "TMPDIR": str(BUILD / "tmp"),
                "PIP_CACHE_DIR": str(BUILD / "pip-cache"), "PIP_NO_INDEX": "1",
                "PYTHONDONTWRITEBYTECODE": "1", "SOURCE_DATE_EPOCH": "1767225600"})
    python = directory / "bin/python"
    run([python, "-m", "pip", "install", "--no-index", "--find-links",
         DOWNLOADS / "cutadapt-build-wheels", "--require-hashes", "-r",
         TOOL / "build-requirements.lock"], env=env)
    return python, env


def source_tree(package: str, variant: str) -> Path:
    record = LOCK if package == "cutadapt" else LOCK["dependencies"][package]
    source = extract(fetch(record), BUILD / "sources" / variant / package)
    if package == "cutadapt" and variant != "native":
        run(["patch", "--batch", "--fuzz=0", "-p1", "-i", TOOL / "browser.patch"], cwd=source)
    return source


def wheel_for(directory: Path, package: str) -> Path:
    matches = list(directory.glob(package + "-*.whl"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {package} wheel, found {len(matches)}")
    return matches[0]


def build_native(kind: str, python: Path, env):
    wheel_directory = BUILD / "wheels" / kind
    fresh(wheel_directory)
    for package in ("xopen", "dnaio", "cutadapt"):
        source = source_tree(package, kind)
        run([python, "-m", "build", "--no-isolation", "--wheel", source,
             "--outdir", wheel_directory], env=env)
    target = ROOT / "build" / kind
    target.mkdir(parents=True, exist_ok=True)
    packages = target / "cutadapt-packages"
    fresh(packages)
    for wheel in sorted(wheel_directory.glob("*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                path = Path(name)
                if path.is_absolute() or ".." in path.parts:
                    raise RuntimeError(f"Unsafe wheel path: {name}")
            archive.extractall(packages)
    shutil.copyfile(TOOL / "native-entry.py", target / "cutadapt-entry.py")
    launcher = target / "cutadapt"
    launcher.write_text('#!/bin/sh\nset -eu\n'
                        'directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                        'exec "${CUTADAPT_PYTHON:-python3.14}" -I -S -B "$directory/cutadapt-entry.py" "$@"\n')
    launcher.chmod(0o755)
    smoke_env = {**env, "CUTADAPT_PYTHON": sys.executable}
    run([launcher, "--version"], env=smoke_env)
    print(f"Ready: {launcher}", flush=True)


def wasm_environment(env):
    sdk = extract(fetch(LOCK["dependencies"]["emsdk"]), BUILD / "emsdk-source")
    sdk_downloads = sdk / "downloads"
    sdk_downloads.mkdir(exist_ok=True)
    for record in LOCK["toolchainDownloads"]:
        if record.get("role") != "host-toolchain" or record.get("distribute") is not False:
            raise RuntimeError("Compiler download must explicitly be a non-distributed host toolchain")
        archive = fetch(record, toolchain=True)
        (sdk_downloads / archive.name).symlink_to(archive.resolve())
    node = shutil.which("node", path=env["PATH"])
    if not node:
        raise RuntimeError("Node.js is required to build WebAssembly wheels")
    sdk_env = {**env, "TAR_OPTIONS": "--no-same-owner", "EM_NODE_JS": node,
               "EMSDK_QUIET": "1", "EMSDK_KEEP_DOWNLOADS": "1"}
    run([sdk / "emsdk", "install", LOCK["emscripten"]], env=sdk_env)
    for record in LOCK["toolchainDownloads"]:
        fetch(record, toolchain=True)
    run([sdk / "emsdk", "activate", LOCK["emscripten"]], env=sdk_env)
    xbuild = extract(fetch(LOCK["dependencies"]["xbuildenv"]), BUILD / "xbuild")
    sdk_env.update({"EMSDK": str(sdk), "EM_CONFIG": str(sdk / ".emscripten"),
                    "PYODIDE_ROOT": str(xbuild / "pyodide-root"),
                    "PATH": str(sdk / "upstream/emscripten") + os.pathsep + env["PATH"]})
    run([sdk / "upstream/emscripten/emcc", "--version"], env=sdk_env)
    return sdk_env


def build_wasm(python: Path, env):
    wasm_env = wasm_environment(env)
    wheel_directory = BUILD / "wheels/wasm"
    fresh(wheel_directory)
    for package in ("xopen", "dnaio", "cutadapt"):
        source = source_tree(package, "wasm")
        if package == "xopen":
            command = [python, "-m", "build", "--no-isolation", "--wheel", source, "--outdir", wheel_directory]
        else:
            command = [python.parent / "pyodide", "build", "--no-isolation",
                       "--skip-emscripten-install", source, "--outdir", wheel_directory]
        run(command, env=wasm_env)
    return wheel_directory


def native_identity(kind: str):
    directory = ROOT / "build" / kind
    files = {}
    for path in sorted(directory.glob("cutadapt*")):
        if path.is_dir():
            files.update({f"{path.name}/{name}": value for name, value in hashes(path).items()})
        else:
            files[path.name] = sha256(path)
    if not (directory / "cutadapt").is_file() or not (directory / "cutadapt-packages").is_dir():
        raise RuntimeError(f"Missing {kind} comparison environment; run all or build it first")
    return {"launcher": files["cutadapt"], "files": files,
            "python": {"version": platform.python_version(), "implementation": platform.python_implementation()}}


def assemble(wheels: Path, source_inputs):
    staging = BUILD / "distribution"
    fresh(staging)
    core = extract(fetch(LOCK["dependencies"]["pyodide-core"]), BUILD / "core")
    (staging / "pyodide").mkdir()
    for filename in ("pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"):
        shutil.copyfile(core / filename, staging / "pyodide" / filename)
    ws = extract(fetch(LOCK["dependencies"]["runtime-ws"]), BUILD / "ws")
    shutil.copytree(ws, staging / "node_modules/ws")
    (staging / "wheels").mkdir()
    packages = []
    for name in ("xopen", "dnaio", "cutadapt"):
        wheel = wheel_for(wheels, name)
        shutil.copyfile(wheel, staging / "wheels" / wheel.name)
        packages.append({"name": name, "version": LOCK["version"] if name == "cutadapt" else LOCK["dependencies"][name]["version"],
                         "filename": f"wheels/{wheel.name}", "sha256": sha256(wheel)})
    write_json(staging / "manifest.json", {"schema": 1, "tool": "cutadapt", "version": LOCK["version"],
               "pyodide": {"version": LOCK["pyodide"], "path": "pyodide/", "python": LOCK["python"]}, "packages": packages})
    for record in archive_records():
        archive = fetch(record)
        destination = staging / "sources" / record["archive"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, destination)
    for notice in LOCK["requiredNotices"]:
        destination = staging / "notices" / notice
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TOOL / notice, destination)
    if inputs() != source_inputs:
        raise RuntimeError("Build inputs changed during compilation; rebuild before publishing")
    write_json(staging / "build-manifest.json", {"schema": 1, "tool": "cutadapt", "inputs": source_inputs,
               "assets": hashes(staging), "nativeEnvironments": {kind: native_identity(kind) for kind in ("native", "patched-native")}})
    fresh(DIST)
    shutil.copytree(staging, DIST, dirs_exist_ok=True)
    print(f"Complete verified distribution: {DIST}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", default="all", choices=("all", "native", "patched-native", "wasm"))
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        parser.error("The pinned compiler wheels and SDK require Linux x86_64")
    if platform.python_implementation() != "CPython" or platform.python_version() != LOCK["python"]:
        parser.error(f"Run this script with CPython {LOCK['python']}")
    source_inputs = inputs()
    (BUILD / "tmp").mkdir(parents=True, exist_ok=True)
    for record in archive_records():
        fetch(record)
    python, env = build_environment()
    for kind in ("native", "patched-native"):
        if args.mode in ("all", kind):
            build_native(kind, python, env)
    if args.mode in ("all", "wasm"):
        assemble(build_wasm(python, env), source_inputs)


if __name__ == "__main__":
    main()
