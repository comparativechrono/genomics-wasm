#!/usr/bin/env python3
"""Build the native DESeq2 reference from the browser bundle's pinned sources."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def download(artifact, directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / artifact["filename"]
    if not path.exists():
        temporary = path.with_name(path.name + ".tmp")
        subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--max-time",
                        "300", artifact["url"], "-o", str(temporary)], check=True)
        temporary.replace(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
        raise SystemExit(f"Checksum mismatch: {path}")
    return path


def description(path):
    with tarfile.open(path) as archive:
        member = next(m for m in archive if m.name.count("/") == 1
                      and m.name.endswith("/DESCRIPTION"))
        text = archive.extractfile(member).read().decode("utf-8")
    fields = {}
    key = None
    for line in text.splitlines():
        if line and line[0].isspace():
            fields[key] += " " + line.strip()
        elif ":" in line:
            key, value = line.split(":", 1)
            fields[key] = value.strip()
    return fields


def dependencies(fields):
    result = set()
    for field in ("Depends", "Imports", "LinkingTo"):
        for item in fields.get(field, "").split(","):
            if item.strip():
                result.add(re.split(r"\s|\(", item.strip())[0])
    return result - {"R"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r", default=shutil.which("R"), help="R executable")
    parser.add_argument("--bootstrap-r", action="store_true",
                        help="Extract pinned Posit R for Ubuntu 24.04 into the build directory")
    parser.add_argument("--output", type=Path, default=ROOT / "build/deseq2-native")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    output = args.output.resolve()
    lock = json.loads((ROOT / "tools/deseq2/sources.lock.json").read_text())
    for folder in ("tmp", "logs", "runtime-library", "build-library"):
        (output / folder).mkdir(parents=True, exist_ok=True)
    if args.bootstrap_r:
        native = json.loads((ROOT / "tools/deseq2/native.lock.json").read_text())
        archive = download(native, ROOT / "build/downloads")
        prefix = output / "posit" / "opt/R" / native["rVersion"]
        launcher = prefix / "lib/R/bin/R"
        if not launcher.exists():
            subprocess.run(["dpkg-deb", "-x", str(archive), str(output / "posit")], check=True)
            launcher.write_text(launcher.read_text().replace(
                f'/opt/R/{native["rVersion"]}', str(prefix)))
        args.r = str(launcher)
    if not args.r:
        parser.error("Install R 4.6.0 or use --bootstrap-r on Ubuntu 24.04 x86_64.")
    r = str(Path(shutil.which(args.r) or args.r).resolve())
    runtime_library = output / "runtime-library"
    build_library = output / "build-library"
    environment = dict(os.environ, TMPDIR=str(output / "tmp"),
                       R_LIBS_USER=str(runtime_library), R_LIBS=str(runtime_library),
                       MAKEFLAGS=f"-j{args.jobs}", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")

    def evaluate(code, env=environment):
        return subprocess.check_output([r, "--vanilla", "--slave", "-e", code],
                                       env=env, text=True).strip()

    if evaluate("cat(as.character(getRversion()))") != lock["rVersion"]:
        raise SystemExit(f'Native R must be {lock["rVersion"]}.')
    r_home = Path(evaluate("cat(R.home())"))
    sources = {}
    fields = {}
    entries = lock["packages"] + lock["build_dependencies"]
    for package in entries:
        key = (package["name"], package["version"])
        source = download(package["source"], ROOT / "build/deseq2/sources")
        info = description(source)
        if (info["Package"], info["Version"]) != key:
            raise SystemExit(f"Source DESCRIPTION mismatch: {source}")
        sources[key], fields[key] = source, info

    def installed(env):
        rows = evaluate('write.table(installed.packages()[,c("Package","Version")],'
                        'stdout(),sep="\\t",quote=FALSE,row.names=FALSE,col.names=FALSE)', env)
        versions = {}
        for line in rows.splitlines():
            name, version = line.split("\t")
            versions.setdefault(name, version)
        return versions

    def install_group(packages, library, env):
        pending = list(packages)
        versions = installed(env)
        while pending:
            progressed = False
            for package in list(pending):
                key = (package["name"], package["version"])
                # R's recommended packages are supplied by the pinned native R.
                # Existing local packages must have a matching source receipt.
                receipt = library / package["name"] / "genomics-wasm-source.json"
                expected_receipt = dict(package["source"])
                if key[0] == "DESeq2":
                    expected_receipt["nativeBuildDependencies"] = {
                        p["name"]: p["source"] for p in lock["build_dependencies"]
                    }
                already = versions.get(key[0]) == key[1]
                recommended = key[0] in {"Matrix", "lattice", "codetools"}
                pinned = receipt.exists() and json.loads(receipt.read_text()) == expected_receipt
                if already and (recommended or pinned):
                    print(f"Using {key[0]} {key[1]}", flush=True)
                elif dependencies(fields[key]) <= versions.keys():
                    print(f"Building {key[0]} {key[1]}", flush=True)
                    log = output / "logs" / f'{key[0]}-{key[1]}-{library.name}.log'
                    with log.open("w") as handle:
                        result = subprocess.run([r, "CMD", "INSTALL", f"--library={library}",
                                                 "--no-multiarch", "--no-docs", "--no-html",
                                                 str(sources[key])], env=env,
                                                stdout=handle, stderr=subprocess.STDOUT)
                    if result.returncode:
                        print(log.read_text()[-12000:])
                        raise SystemExit(f"Native installation failed; see {log}")
                    receipt.write_text(json.dumps(expected_receipt, indent=2) + "\n")
                    versions[key[0]] = key[1]
                else:
                    continue
                pending.remove(package)
                progressed = True
            if not progressed:
                raise SystemExit("Unresolved native dependencies: " + ", ".join(p["name"] for p in pending))

    # Build headers are separate because the published DESeq2 WASM binary used
    # newer Rcpp headers than the Rcpp runtime in webR's package repository.
    build_environment = dict(environment, R_LIBS_USER=str(build_library),
                             R_LIBS=os.pathsep.join((str(build_library), str(runtime_library))))
    install_group(lock["build_dependencies"], build_library, build_environment)
    runtime_build_environment = dict(environment, R_LIBS=os.pathsep.join((str(runtime_library), str(build_library))))
    install_group([p for p in lock["packages"] if p["name"] != "DESeq2"],
                  runtime_library, runtime_build_environment)
    # R CMD INSTALL puts its destination library first when locating headers.
    # Compile here, then relocate the complete installed package for execution.
    install_group([p for p in lock["packages"] if p["name"] == "DESeq2"],
                  build_library, build_environment)
    deseq2_runtime = runtime_library / "DESeq2"
    if deseq2_runtime.exists():
        shutil.rmtree(deseq2_runtime)
    shutil.copytree(build_library / "DESeq2", deseq2_runtime)

    versions = installed(environment)
    for package in lock["packages"]:
        if versions.get(package["name"]) != package["version"]:
            raise SystemExit(f'Native runtime version mismatch: {package["name"]}')
    evaluate('suppressPackageStartupMessages(library(DESeq2)); cat(as.character(packageVersion("DESeq2")))')
    wrapper = output / "Rscript"
    wrapper.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" +
                       f"export R_HOME={shlex.quote(str(r_home))}\n" +
                       f"export RHOME={shlex.quote(str(r_home))}\n" +
                       f"export R_LIBS_USER={shlex.quote(str(runtime_library))}\n" +
                       f"export R_LIBS={shlex.quote(str(runtime_library))}\n" +
                       f"export TMPDIR={shlex.quote(str(output / 'tmp'))}\n" +
                       f"export LD_LIBRARY_PATH={shlex.quote(str(r_home / 'lib'))}:\"${{LD_LIBRARY_PATH:-}}\"\n" +
                       "export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1\n" +
                       f"exec {shlex.quote(str(r_home / 'bin/Rscript'))} \"$@\"\n")
    wrapper.chmod(0o755)
    report = {"rVersion": lock["rVersion"], "rHome": str(r_home),
              "packages": {p["name"]: p["version"] for p in lock["packages"]},
              "buildDependencies": {p["name"]: p["version"] for p in lock["build_dependencies"]},
              "sourcesLockSha256": hashlib.sha256((ROOT / "tools/deseq2/sources.lock.json").read_bytes()).hexdigest()}
    (output / "native-build.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Native reference ready: {wrapper}")


if __name__ == "__main__":
    main()
