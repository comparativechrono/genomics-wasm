#!/usr/bin/env python3
"""Reuse a pinned CI build; always run the current native/browser tests afterwards.

Only missing/expired seed artifacts fall back to compiling. A provenance or
checksum error stops the job. Downloads stay in a staging directory until every
executable and build input has been checked. This script makes read-only GitHub
API requests; actions/download-artifact handles authenticated artifact transfer.
Delete .github/quant-build-reuse.json (or set enabled:false) for a fresh build
after changing compiler inputs. Its artifacts otherwise expire after 14 days.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {'.patch', '.h', '.hpp', '.c', '.cc', '.cpp', '.rs', '.py', '.sh', '.json', '.lock'}
COMPILE_STEPS = (
    'Install build dependencies',
    'Install pinned WebAssembly compiler',
    'Install the pinned Rust compiler and standard library sources',
    'Build original native, patched native and WebAssembly programs',
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def output(**values):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as handle:
            for key, value in values.items():
                handle.write(f'{key}={value}\n')


def api(repository: str, endpoint: str):
    # Never send the token to a caller-supplied host or follow an API redirect.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    if os.environ.get('GH_TOKEN'):
        headers['Authorization'] = f'Bearer {os.environ["GH_TOKEN"]}'
    request = urllib.request.Request(f'https://api.github.com/repos/{repository}/{endpoint}', headers=headers)
    with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
        return json.load(response)


def pages(repository, endpoint, field):
    result = []
    for page in range(1, 101):
        data = api(repository, f'{endpoint}{"&" if "?" in endpoint else "?"}per_page=100&page={page}')
        result.extend(data[field])
        if len(result) >= data['total_count']:
            return result
    raise RuntimeError('Unexpectedly large artifact/job listing')


def build_commands(workflow: str):
    """Compare compiler commands without tying reuse to UI/test workflow edits.

    This deliberately accepts the simple structure used by quant.yml only.
    New job-level environments/defaults or build-step fields require review.
    """
    match = re.search(r'^  build:\n(.*?)(?=^  [\w-]+:|\Z)', workflow, re.M | re.S)
    require(match, 'Missing build job in compiler workflow')
    job = match[1]
    require(not re.search(r'^(?:env|defaults):', workflow, re.M), 'Global build environment/defaults require a fresh reviewed build')
    require(not re.search(r'^    (?:env|defaults|container|services):', job, re.M), 'Build environment overrides require a fresh reviewed build')
    require(re.search(r'^    runs-on: ubuntu-24\.04$', job, re.M), 'Build reuse requires ubuntu-24.04')
    blocks = re.split(r'^      - ', job, flags=re.M)[1:]
    selected = {}
    for name in COMPILE_STEPS:
        matches = [block for block in blocks if block.startswith(f'name: {name}\n')]
        require(len(matches) == 1, f'Missing or duplicate compiler step: {name}')
        lines = matches[0].splitlines()
        keys = {line.strip().split(':', 1)[0] for line in lines[1:] if re.match(r'^        \S', line)}
        require(keys <= {'if', 'run'}, f'Unrecognised compiler settings in {name}')
        # The reuse condition changes whether compilation runs, not its inputs.
        # npm installs the current browser test runner, not compiled tool inputs.
        selected[name] = '\n'.join(line for line in lines if not line.startswith('        if:')
                                   and line.strip() != 'npm ci').rstrip()
    return selected


def plan(tool: str):
    hint_path = ROOT / '.github/quant-build-reuse.json'
    hint = json.loads(hint_path.read_text()) if hint_path.is_file() else {}
    override = os.environ.get('COMPILED_RUN_ID', '').strip()
    if override:
        hint = {'schema': 1, 'repository': os.environ['GITHUB_REPOSITORY'],
                'runId': override, 'headSha': os.environ.get('COMPILED_HEAD_SHA', '').strip()}
    if not hint or hint.get('enabled') is False:
        print('No compiled build selected; building from source.')
        output(available='false')
        return
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository), 'Invalid current repository')
    if not override and hint.get('repository') != repository:
        print('Pinned build belongs to another repository; building this fork from source.')
        output(available='false')
        return
    require(hint.get('schema') == 1 and hint.get('repository') == repository,
            'The reuse hint must identify this repository')
    run_id, head = str(hint.get('runId', '')), hint.get('headSha', '')
    require(re.fullmatch(r'[1-9][0-9]*', run_id) and re.fullmatch(r'[0-9a-f]{40}', head),
            'Reuse requires an explicit run ID and full source commit SHA')
    require(os.environ.get('RUNNER_OS') == 'Linux' and os.environ.get('RUNNER_ARCH') == 'X64',
            'Compiled native programs require a Linux X64 runner')
    try:
        run = api(repository, f'actions/runs/{run_id}')
    except urllib.error.HTTPError as error:
        if error.code in (404, 410) and not override:
            print('Pinned run is no longer available; building from source.')
            output(available='false')
            return
        raise
    require(str(run['id']) == run_id and run['head_sha'] == head,
            'Pinned run does not match its source commit')
    require(run['repository']['full_name'] == repository and run['head_repository']['full_name'] == repository,
            'Refusing build artifacts from another repository or a fork')
    require(run['path'] == '.github/workflows/quant.yml' and run['event'] in ('push', 'workflow_dispatch'),
            'Reuse requires the same quant workflow from a push or explicit dispatch')
    jobs = pages(repository, f'actions/runs/{run_id}/jobs?filter=latest', 'jobs')
    jobs = [job for job in jobs if job['name'] == f'build ({tool})']
    require(len(jobs) == 1 and jobs[0]['conclusion'] == 'success' and jobs[0]['head_sha'] == head
            and jobs[0]['labels'] == ['ubuntu-24.04'], 'Pinned tool build did not succeed on ubuntu-24.04')
    job = jobs[0]
    artifacts = pages(repository, f'actions/runs/{run_id}/artifacts', 'artifacts')
    chosen, artifact_digests = {}, {}
    for kind, name in [('compiled', f'quant-compiled-{tool}'), ('report', f'quant-validation-{tool}-node')]:
        matches = [artifact for artifact in artifacts if artifact['name'] == name]
        require(len(matches) <= 1, f'Ambiguous artifact: {name}')
        if not matches or matches[0]['expired']:
            require(not override, f'Requested artifact is missing or expired: {name}')
            print(f'Pinned artifact missing or expired ({name}); building from source.')
            output(available='false')
            return
        artifact = matches[0]
        origin = artifact['workflow_run']
        require(str(origin['id']) == run_id and origin['head_sha'] == head
                and origin['repository_id'] == run['repository']['id']
                and origin['head_repository_id'] == run['repository']['id'], 'Artifact origin mismatch')
        require(datetime.fromisoformat(job['started_at']) <= datetime.fromisoformat(artifact['created_at'])
                <= datetime.fromisoformat(job['completed_at']), 'Artifact was not created by the selected successful build attempt')
        chosen[kind] = artifact['id']
        artifact_digests[kind] = artifact.get('digest')
    # The older build manifests predate this reuse helper and do not record the
    # shared SDK installer or workflow compiler commands. Compare pinned source.
    extra = {}
    for name in ('scripts/setup-emsdk.sh', '.github/workflows/quant.yml'):
        source = api(repository, f'contents/{name}?ref={head}')
        require(source.get('encoding') == 'base64', f'Cannot read pinned build input: {name}')
        prior = base64.b64decode(source['content'])
        current = (ROOT / name).read_bytes()
        if name.endswith('.yml'):
            require(build_commands(prior.decode()) == build_commands(current.decode()),
                    'Compiler workflow commands changed; remove the reuse hint and build from source')
            extra['compilerCommands'] = build_commands(current.decode())
        else:
            require(prior == current, 'Compiler setup changed; remove the reuse hint and build from source')
            extra[name] = hashlib.sha256(current).hexdigest()
    stage = ROOT / f'build/quant-reuse/{tool}'
    stage.mkdir(parents=True, exist_ok=True)
    (stage / 'provenance.json').write_text(json.dumps({**hint, 'tool': tool, 'artifacts': chosen,
                                                   'artifactDigests': artifact_digests, 'jobId': job['id'],
                                                   'runAttempt': run['run_attempt'], 'runner': 'ubuntu-24.04-X64',
                                                   'extraInputs': extra}, indent=2) + '\n')
    output(available='true', run_id=run_id, compiled_id=chosen['compiled'], report_id=chosen['report'])
    print(f'Approved provenance for {tool} build from run {run_id} at {head}; checking downloaded bytes next.')


def verify(tool: str):
    stage = ROOT / f'build/quant-reuse/{tool}'
    compiled = stage / 'compiled'
    for path in compiled.rglob('*'):
        require(not path.is_symlink(), f'Symlink in compiled artifact: {path}')
    dist = compiled / f'dist/{tool}'
    manifest = json.loads((dist / 'build-manifest.json').read_text())
    require(manifest.get('schema') == 1 and manifest.get('tool') == tool, 'Invalid compiled build manifest')
    expected = {f'scripts/build-{tool}.sh'}
    expected.update(path.relative_to(ROOT).as_posix() for path in (ROOT / f'tools/{tool}').rglob('*')
                    if path.is_file() and path.suffix in SUFFIXES)
    inputs = manifest.get('inputs', {})
    require(expected == inputs.keys(), 'Compiled build input set differs from the current source tree')
    for name, checksum in inputs.items():
        require(digest(ROOT / name) == checksum, f'Compiled build source mismatch: {name}; remove the reuse hint to rebuild')
    assets = {path.relative_to(dist).as_posix(): digest(path) for path in dist.rglob('*')
              if path.is_file() and path.suffix in ('.mjs', '.js', '.wasm')}
    require({f'{tool}.mjs', f'{tool}.wasm'} <= assets.keys() and manifest.get('assets') == assets,
            'Compiled runtime asset checksum mismatch')
    matches = list((stage / 'report').rglob(f'summary-{tool}-all.json'))
    require(len(matches) == 1, 'Missing or ambiguous prior Node comparison report')
    report = json.loads(matches[0].read_text())
    cases = report.get('cases', [])
    ids = [case['id'] for case in cases]
    require(report.get('schema') == 1 and report.get('engine') == 'node' and report.get('status') == 'passed'
            and cases and all(case.get('status') == 'passed' for case in cases)
            and len(set(ids)) == len(ids) and sorted(ids) == sorted(report.get('expectedCases', [])),
            'Prior Node comparison report is not complete and passing')
    tested = report.get('builds', {}).get(tool, {})
    require(tested.get('assets') == assets and tested.get('module') == assets[f'{tool}.mjs']
            and tested.get('wasm') == assets[f'{tool}.wasm'], 'Runtime assets differ from the previously tested build')
    for variant in ('native', 'patched-native'):
        require(digest(compiled / f'build/{variant}/{tool}') == tested.get(variant),
                f'{variant} binary differs from the previously tested build')
    for organism in ('human', 'yeast'):
        paired = next((case for case in cases if case['id'] == f'{tool}-{organism}-paired'), {})
        require(all(paired.get('comparisons', {}).get(target, {}).get('equal') is True
                    for target in ('patched-native', 'wasm')), 'Prior report lacks native comparisons on both datasets')
    provenance = json.loads((stage / 'provenance.json').read_text())
    provenance.update(inputs=inputs, assets=assets,
                      native={variant: tested[variant] for variant in ('native', 'patched-native')})
    fingerprint = {'inputs': inputs, 'extraInputs': provenance['extraInputs'], 'runner': provenance['runner']}
    provenance['buildInputSha256'] = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    # Copy only the checked tool directory and checked executables, never source
    # files or previous validation reports. Every current check runs again.
    shutil.copytree(dist, ROOT / f'dist/{tool}', dirs_exist_ok=True)
    for variant in ('native', 'patched-native'):
        target = ROOT / f'build/{variant}/{tool}'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(compiled / f'build/{variant}/{tool}', target)
        target.chmod(0o755)
    (stage / 'verified.json').write_text(json.dumps(provenance, indent=2, sort_keys=True) + '\n')
    print(f'Verified {tool}: current build inputs, all runtime assets, and both native executables. Current tests must now pass.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'verify'))
    parser.add_argument('--tool', required=True, choices=('kallisto', 'salmon'))
    args = parser.parse_args()
    try:
        {'plan': plan, 'verify': verify}[args.command](args.tool)
    except (RuntimeError, KeyError, OSError, ValueError) as error:
        sys.exit(f'Compiled build reuse refused: {error}')
