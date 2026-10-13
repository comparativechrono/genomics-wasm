#!/usr/bin/env python3
"""Build the pinned MACS3 native comparisons and Pyodide distribution.

Requires Linux x86_64, CPython 3.14.2, Node, a C compiler, patch and tar.
Compiler binaries are downloaded and verified, but are not redistributed.
All other locked archives can be recovered from dist/macs3/sources in a
standalone source distribution before attempting a network download.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
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
TOOL = ROOT / "tools/macs3"
BUILD = ROOT / "build/macs3"
DOWNLOADS = ROOT / "build/downloads"
DIST = ROOT / "dist/macs3"
LOCK = json.loads((TOOL / "sources.lock.json").read_text())


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def hashes(directory: Path) -> dict[str, str]:
    return {p.relative_to(directory).as_posix(): sha256(p)
            for p in sorted(directory.rglob("*")) if p.is_file()}


def inputs() -> dict[str, str]:
    result = {f"tools/macs3/{name}": value for name, value in hashes(TOOL).items()
              if "__pycache__" not in Path(name).parts and not name.endswith((".pyc", ".pyo"))}
    result["scripts/build-macs3.py"] = sha256(Path(__file__))
    return result


def archive_records():
    records = [LOCK, *LOCK["dependencies"].values()]
    for requirement in LOCK["buildRequirements"]:
        records.extend((requirement["wheel"], requirement["source"]))
    for dependency in LOCK["dependencies"].values():
        for field in ("wheel", "nativeWheel"):
            if field in dependency:
                records.append(dependency[field])
    seen = set()
    for record in records:
        if record["archive"] not in seen:
            seen.add(record["archive"])
            yield record


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
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYODIDE_ROOT", "EM_CONFIG", "EMSDK",
                 "CC", "CXX", "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS",
                 "LDSHARED", "AR", "RANLIB", "LD_LIBRARY_PATH"):
        env.pop(name, None)
    env.update({"PATH": str(directory / "bin") + os.pathsep + env["PATH"],
                "VIRTUAL_ENV": str(directory), "TMPDIR": str(BUILD / "tmp"),
                "PIP_CACHE_DIR": str(BUILD / "pip-cache"), "PIP_NO_INDEX": "1",
                "PYTHONDONTWRITEBYTECODE": "1", "SOURCE_DATE_EPOCH": "1767225600",
                "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "CC": "gcc", "CXX": "g++"})
    python = directory / "bin/python"
    # Hashes have already been verified by fetch(); --no-deps prevents resolution.
    wheels = [fetch(r["wheel"]) for r in LOCK["buildRequirements"]]
    wheels += [fetch(LOCK["dependencies"][name]["nativeWheel"]) for name in ("numpy", "pkgconfig")]
    run([python, "-m", "pip", "install", "--no-index", "--no-deps", *wheels], env=env)
    return python, env


def source_tree(package: str, variant: str) -> Path:
    record = LOCK if package == "macs3" else LOCK["dependencies"][package]
    source = extract(fetch(record), BUILD / "sources" / variant / package)
    if package == "macs3" and variant != "native":
        run(["patch", "--batch", "--fuzz=0", "-p1", "-i", TOOL / "browser.patch"], cwd=source)
        run(["patch", "--batch", "--fuzz=0", "-p1", "-i", TOOL / "integer-width.patch"], cwd=source)
    if package == "hmmlearn" and variant == "wasm":
        run(["patch", "--batch", "--fuzz=0", "-p1", "-i", TOOL / "hmmlearn-wasm.patch"], cwd=source)
    return source


def wheel_for(directory: Path, package: str) -> Path:
    matches = list(directory.glob(package + "-*.whl"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {package} wheel, found {len(matches)}")
    return matches[0]


def build_hdf5(env):
    source = source_tree("native-hdf5", "dependencies")
    prefix = BUILD / "hdf5"
    fresh(prefix)
    run([source / "configure", f"--prefix={prefix}", "--disable-tests", "--disable-tools",
         "--enable-hl", "--disable-fortran", "--disable-cxx", "--enable-shared", "--disable-static"], cwd=source, env=env)
    run(["make", "-j4"], cwd=source, env=env)
    run(["make", "install"], cwd=source, env=env)
    return prefix


def build_native_dependencies(python: Path, env):
    wheels = BUILD / "wheels/native-dependencies"
    fresh(wheels)
    hdf5 = build_hdf5(env)
    for name in ("h5py", "hmmlearn"):
        source = source_tree(name, "native-dependencies")
        package_env = {**env, "HDF5_DIR": str(hdf5), "H5PY_ROS3": "0", "H5PY_DIRECT_VFD": "0"}
        run([python, "-m", "build", "--no-isolation", "--wheel", source, "--outdir", wheels], env=package_env)
    return wheels


def build_native(kind: str, python: Path, env, dependency_wheels: Path):
    wheel_directory = BUILD / "wheels" / kind
    fresh(wheel_directory)
    source = source_tree("macs3", kind)
    run([python, "-m", "build", "--no-isolation", "--wheel", source, "--outdir", wheel_directory], env=env)
    install_native(kind, wheel_directory, dependency_wheels, env)


def install_native(kind: str, wheel_directory: Path, dependency_wheels: Path, env):
    target = ROOT / "build" / kind
    target.mkdir(parents=True, exist_ok=True)
    packages = target / "macs3-packages"
    fresh(packages)
    wheels = [wheel_for(wheel_directory, "macs3")]
    for name in LOCK["runtimePackages"]:
        if name == "macs3":
            continue
        record = LOCK["dependencies"][name]
        wheels.append(fetch(record["nativeWheel"]) if "nativeWheel" in record else wheel_for(dependency_wheels, name))
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                path = Path(name)
                if path.is_absolute() or ".." in path.parts:
                    raise RuntimeError(f"Unsafe wheel path: {name}")
            archive.extractall(packages)
    libraries = target / "macs3-libs"
    fresh(libraries)
    for library in (BUILD / "hdf5/lib").glob("*.so*"):
        shutil.copyfile(library, libraries / library.name)
    source = extract(fetch(LOCK), BUILD / "cli-source" / kind)
    cli = target / "macs3-cli"
    fresh(cli)
    shutil.copyfile(source / "bin/macs3", cli / "macs3")
    shutil.copyfile(TOOL / "native-entry.py", target / "macs3-entry.py")
    launcher = target / "macs3"
    launcher.write_text('#!/bin/sh\nset -eu\n'
                        'directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                        'export LD_LIBRARY_PATH="$directory/macs3-libs"\n'
                        'export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1\n'
                        'exec "${MACS3_PYTHON:-python3.14}" -I -S -B "$directory/macs3-entry.py" "$@"\n')
    launcher.chmod(0o755)
    run([launcher, "--version"], env={**env, "MACS3_PYTHON": sys.executable})
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
    # Build the pinned zlib source locally: no Emscripten ports downloads.
    zlib = source_tree("runtime-zlib", "wasm-zlib")
    run([sdk / "upstream/emscripten/emconfigure", "./configure", "--static"], cwd=zlib, env={**sdk_env, "CFLAGS": "-O2 -fPIC"})
    # Compile only the library, with relocatable objects for the Python side module.
    zlib_sources = "adler32 crc32 deflate infback inffast inflate inftrees trees zutil compress uncompr gzclose gzlib gzread gzwrite".split()
    for name in zlib_sources:
        run([sdk / "upstream/emscripten/emcc", "-O2", "-fPIC", "-D_LARGEFILE64_SOURCE=1",
             "-DHAVE_HIDDEN", "-c", name + ".c", "-o", name + ".o"], cwd=zlib, env=sdk_env)
    run([sdk / "upstream/emscripten/emar", "rcs", "libz.a", *[name + ".o" for name in zlib_sources]], cwd=zlib, env=sdk_env)
    sdk_env["CFLAGS"] = f"-I{zlib}"
    sdk_env["LDFLAGS"] = f"-L{zlib}"
    # Native builds finish before this overlay. Cython must see wasm32 NumPy
    # ABI headers, never the host LP64 headers. The wheel cache stays pristine.
    native_numpy = Path(subprocess.check_output([str(BUILD / "venv/bin/python"), "-c",
                         "import numpy; print(numpy.__path__[0])"], env=env, text=True).strip())
    shutil.copytree(xbuild / "site-packages-extras/numpy", native_numpy, dirs_exist_ok=True)
    return sdk_env


def build_wasm(python: Path, env):
    wasm_env = wasm_environment(env)
    wheel_directory = BUILD / "wheels/wasm"
    fresh(wheel_directory)
    for name in ("hmmlearn", "macs3"):
        source = source_tree(name, "wasm")
        run([python.parent / "pyodide", "build", "--no-isolation",
             "--skip-emscripten-install", source, "--outdir", wheel_directory], env=wasm_env)
    return wheel_directory


def native_identity(kind: str):
    directory = ROOT / "build" / kind
    files = {}
    for path in sorted(directory.glob("macs3*")):
        if path.is_dir():
            files.update({f"{path.name}/{name}": value for name, value in hashes(path).items()})
        else:
            files[path.name] = sha256(path)
    if not (directory / "macs3").is_file() or not (directory / "macs3-packages").is_dir():
        raise RuntimeError(f"Missing {kind} comparison environment; run all or build it first")
    return {"launcher": files["macs3"], "files": files,
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
    for name in LOCK["runtimePackages"]:
        record = LOCK if name == "macs3" else LOCK["dependencies"][name]
        stock = record.get("origin") == "pyodide"
        wheel = fetch(record["wheel"]) if "wheel" in record else wheel_for(wheels, name)
        filename = ("pyodide/" if stock else "wheels/") + wheel.name
        shutil.copyfile(wheel, staging / filename)
        packages.append({"name": name, "version": record["version"], "source": "pyodide" if stock else "wheel",
                         "filename": filename, "sha256": sha256(wheel)})
    source = extract(fetch(LOCK), BUILD / "cli-source/wasm")
    shutil.copyfile(source / "bin/macs3", staging / "macs3.py")
    write_json(staging / "manifest.json", {"schema": 1, "tool": "macs3", "version": LOCK["version"],
               "pyodide": {"version": LOCK["pyodide"], "path": "pyodide/", "python": LOCK["python"]},
               "packages": packages, "entrypoint": {"filename": "macs3.py", "sha256": sha256(staging / "macs3.py")}})
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
    write_json(staging / "build-manifest.json", {"schema": 1, "tool": "macs3", "inputs": source_inputs,
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
    if args.mode in ("all", "native", "patched-native"):
        dependency_wheels = build_native_dependencies(python, env)
        kinds = [kind for kind in ("native", "patched-native") if args.mode in ("all", kind)]
        # Each comparison gets an independent source tree and output directory.
        with ThreadPoolExecutor(max_workers=len(kinds)) as executor:
            futures = [executor.submit(build_native, kind, python, env, dependency_wheels) for kind in kinds]
            for future in futures:
                future.result()
    if args.mode in ("all", "wasm"):
        assemble(build_wasm(python, env), source_inputs)


if __name__ == "__main__":
    main()
