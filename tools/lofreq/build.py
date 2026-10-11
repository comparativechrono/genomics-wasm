#!/usr/bin/env python3
"""Build the same upstream LoFreq C sources for native and WebAssembly."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/lofreq"
BUILD = ROOT / "build/lofreq"
DIST = ROOT / "dist/lofreq"
DOWNLOADS = ROOT / "build/downloads"
LOCK = json.loads((TOOL / "sources.lock.json").read_text())


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_inputs():
    paths = [ROOT / "scripts/build-lofreq.sh", *sorted(TOOL.rglob("*"))]
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in paths if p.is_file()}


def run(args, cwd, env):
    print("+ " + " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, env=env, check=True)


def fetch(record):
    target = DOWNLOADS / record["archive"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        fallback = DIST / "sources" / record["archive"]
        if fallback.is_file():
            shutil.copyfile(fallback, target)
        else:
            temporary = target.with_suffix(target.suffix + ".partial")
            request = urllib.request.Request(record["url"], headers={"User-Agent": "genomics-wasm-build"})
            with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            temporary.replace(target)
    if digest(target) != record["sha256"]:
        raise RuntimeError(f"Source archive checksum mismatch: {target}")
    return target


def extract(record, destination):
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with tarfile.open(fetch(record)) as archive:
        archive.extractall(destination, filter="data")
    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise RuntimeError(f"Expected one source directory in {record['archive']}")
    return roots[0]


def compile_variant(kind, env):
    work = BUILD / kind
    work.mkdir(parents=True, exist_ok=True)
    source = extract(LOCK, work / "source")
    hts = extract(LOCK["dependencies"]["htslib"], work / "htslib")
    zlib = extract(LOCK["dependencies"]["zlib"], work / "zlib")
    wasm = kind == "wasm"
    compiler = "emcc" if wasm else "cc"
    variant_env = {**env, "CC": compiler, "AR": "emar" if wasm else "ar",
                   "RANLIB": "emranlib" if wasm else "ranlib", "CFLAGS": "-O2"}
    if wasm:
        version = subprocess.check_output([compiler, "--version"], env=variant_env, text=True).splitlines()[0]
        if LOCK["emscripten"] not in version:
            raise RuntimeError(f"Expected Emscripten {LOCK['emscripten']}: {version}")
    if kind != "native":
        run(["patch", "--batch", "--fuzz=0", "-p1", "-i", TOOL / "browser.patch"], source, env)
    prefix = work / "zlib-prefix"
    run(["./configure", "--static", f"--prefix={prefix}"], zlib, variant_env)
    run(["make", "-j2", "libz.a"], zlib, variant_env)
    run(["make", "install"], zlib, variant_env)
    hts_env = {**variant_env, "CPPFLAGS": f"-I{prefix / 'include'}",
               "LDFLAGS": f"-L{prefix / 'lib'}"}
    configure = ["emconfigure", "./configure", "--host=wasm32-unknown-emscripten"] if wasm else ["./configure"]
    run([*configure, "--disable-libcurl", "--disable-bz2", "--disable-lzma",
         "--without-libdeflate", "--disable-plugins"], hts, hts_env)
    run(["make", "-j2", "libhts.a"], hts, hts_env)
    # Take the translation-unit list from the pinned upstream Makefile.  Only
    # the packaging/configuration layer is replaced, not the calling code.
    makefile = (source / "src/lofreq/Makefile.am").read_text()
    section = makefile.split("lofreq_SOURCES =", 1)[1].split("#lofreq_bamstats", 1)[0]
    filenames = [word for word in section.replace("\\\n", " ").split() if word.endswith(".c")]
    units = [source / "src/lofreq" / filename for filename in filenames]
    units += [source / "src/cdflib90" / filename for filename in ("dcdflib.c", "ipmpar.c")]
    math_units = []
    math_flags = []
    if kind != "native":
        openlibm = extract(LOCK["dependencies"]["openlibm"], work / "openlibm")
        precision = "ld128" if wasm else "ld80"
        math_units = [TOOL / "portable_math.c", openlibm / precision / "e_expl.c",
                      openlibm / precision / "e_log10l.c", openlibm / "src/polevll.c"]
        math_flags = ["-DOPENLIBM_USE_HOST_MATH_H", f"-I{openlibm / 'include'}",
                      f"-I{openlibm / 'src'}", f"-I{TOOL}"]
    binary = work / ("lofreq.mjs" if wasm else "lofreq")
    flags = ["-O2", "-D_GNU_SOURCE", "-D_FILE_OFFSET_BITS=64", "-D_LARGEFILE64_SOURCE", "-DNDEBUG",
             '-DPACKAGE="lofreq_star"', f'-DPACKAGE_VERSION="{LOCK["version"]}"',
             f'-DPACKAGE_STRING="LoFreq_Star {LOCK["version"]}"', f'-DGIT_VERSION="{LOCK["revision"]}"',
             f"-I{source / 'src/cdflib90'}", f"-I{source / 'src/uthash'}", f"-I{hts}", f"-I{prefix / 'include'}"]
    link = [hts / "libhts.a", prefix / "lib/libz.a", "-lm"]
    if wasm:
        link += ["-Wl,--fatal-warnings", "-sMODULARIZE=1", "-sEXPORT_ES6=1", "-sEXPORT_NAME=createLoFreq",
                 "-sINVOKE_RUN=0", "-sEXPORTED_RUNTIME_METHODS=FS,callMain",
                 "-sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram",
                 "-sENVIRONMENT=web,worker,node", "-sALLOW_MEMORY_GROWTH=1", "-sABORTING_MALLOC=1",
                 "-sINITIAL_MEMORY=67108864", "-sMAXIMUM_MEMORY=2147483648", "-sSTACK_SIZE=8388608",
                 "-sEXIT_RUNTIME=1", "-sFORCE_FILESYSTEM=1", "-sPRINTF_LONG_DOUBLE=1"]
    else:
        link += ["-lpthread"]
    run([compiler, *flags, *math_flags, *units, *math_units, *link, "-o", binary], work, variant_env)
    numerical = work / ("numerics.mjs" if wasm else "lofreq-numerics")
    probe_flags = ["-DLOFREQ_PORTABLE_NUMERICS"] if kind != "native" else []
    run([compiler, *flags, *math_flags, *probe_flags, TOOL / "numerics.c", *math_units,
         *link, "-o", numerical], work, variant_env)
    if not wasm:
        target = ROOT / "build" / kind
        target.mkdir(exist_ok=True)
        shutil.copyfile(binary, target / "lofreq")
        (target / "lofreq").chmod(0o755)
        shutil.copyfile(numerical, target / "lofreq-numerics")
        (target / "lofreq-numerics").chmod(0o755)
        for script in (source / "src/scripts").glob("*.py"):
            shutil.copyfile(script, target / script.name)
            (target / script.name).chmod(0o755)
        run([target / "lofreq", "version"], work, env)
    return work


def finish(work, before):
    if source_inputs() != before:
        raise RuntimeError("LoFreq build inputs changed during compilation; rebuild before release")
    DIST.mkdir(parents=True, exist_ok=True)
    for name in ("lofreq.mjs", "lofreq.wasm", "numerics.mjs", "numerics.wasm"):
        shutil.copyfile(work / name, DIST / name)
    for record in [LOCK, *LOCK["dependencies"].values()]:
        destination = DIST / "sources" / record["archive"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fetch(record), destination)
    for name in LOCK["requiredNotices"]:
        destination = DIST / "notices" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TOOL / name, destination)
    assets = {p.relative_to(DIST).as_posix(): digest(p) for p in sorted(DIST.rglob("*"))
              if p.is_file() and p.name != "build-manifest.json"}
    native = {}
    for kind in ("native", "patched-native"):
        directory = ROOT / "build" / kind
        files = [directory / "lofreq", directory / "lofreq-numerics", *sorted(directory.glob("lofreq2_*.py"))]
        if all(p.is_file() for p in files):
            native[kind] = {p.name: digest(p) for p in files}
    (DIST / "build-manifest.json").write_text(json.dumps({"schema": 1, "tool": "lofreq", "inputs": before,
             "assets": assets, "nativeFiles": native}, indent=2, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", default="all", choices=("all", "native", "patched-native", "wasm"))
    args = parser.parse_args()
    before = source_inputs()
    (BUILD / "tmp").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TMPDIR": str(BUILD / "tmp"), "SOURCE_DATE_EPOCH": "1577836800"}
    for record in [LOCK, *LOCK["dependencies"].values()]:
        fetch(record)
    for kind in ("native", "patched-native", "wasm"):
        if args.mode in ("all", kind):
            work = compile_variant(kind, env)
            if kind == "wasm":
                finish(work, before)


if __name__ == "__main__":
    main()
