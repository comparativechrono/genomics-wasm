#!/usr/bin/env python3
"""Compare observed pyflow commands and independently replay runtime SHA edges.

The native command inventory is read from pyflow, never reconstructed from the
JavaScript scheduler.  Command order is a multiset because native pyflow can run
independent tasks in a different order.  Runtime file dependencies are checked
in execution order against independently supplied initial/final byte hashes.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import shlex


SHA = re.compile(r'[0-9a-f]{64}')
CONFIG_PATH_OPTIONS = {'--normalBam', '--tumorBam', '--tumourBam', '--referenceFasta', '--runDir',
                       '--callRegions', '--snvScoringModelFile', '--indelScoringModelFile',
                       '--config', '--indelCandidates', '--forcedGT', '--noiseVcf'}
COMPILED = {'GetChromDepth', 'strelka2', 'bgzip', 'bgzf_cat', 'tabix', 'MergeRunStats', 'htsfile'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def work_path(value):
    if not isinstance(value, str) or not value or '\0' in value:
        raise ValueError('invalid workflow path')
    path = posixpath.normpath(value if value.startswith('/') else '/work/' + value)
    if path != '/work' and not path.startswith('/work/'):
        raise ValueError('workflow path escapes /work: ' + value)
    return path


def hash_map(value, label):
    if not isinstance(value, dict):
        raise ValueError(label + ' must be a path-to-SHA256 object')
    result = {}
    for path, sha in value.items():
        path = work_path(path)
        if path in result or not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise ValueError(label + ' contains a duplicate path or invalid SHA256')
        result[path] = sha
    return result


class NativePaths:
    def __init__(self, work, package):
        self.work = str(PurePosixPath(work))
        self.config = str(PurePosixPath(package) / 'share/config')

    def __call__(self, value):
        if value.startswith('--') and '=' in value:
            key, item = value.split('=', 1)
            return key + '=' + self(item)
        for prefix, replacement in ((self.config, '/work/__strelka2/config'), (self.work, '/work')):
            if value == prefix or value.startswith(prefix + '/'):
                return replacement + value[len(prefix):]
        return value


def configure_command(command, normalise):
    tokens = shlex.split(command)
    if not tokens or PurePosixPath(tokens[0]).name != 'configureStrelkaSomaticWorkflow.py':
        raise ValueError('unexpected VCF reheader configuration command')
    result = ['configureStrelkaSomaticWorkflow.py']
    path_next = False
    for token in tokens[1:]:
        if path_next:
            result.append(work_path(normalise(token)))
            path_next = False
            continue
        flag, separator, value = token.partition('=')
        if flag in CONFIG_PATH_OPTIONS:
            if separator:
                result.append(flag + '=' + work_path(normalise(value)))
            else:
                result.append(flag)
                path_next = True
        else:
            result.append(normalise(token))
    if path_next:
        raise ValueError('configuration path option is missing its value')
    return shlex.join(result)


def shell_groups(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>')
    lexer.whitespace_split = True
    lexer.commenters = ''
    groups, current = [], []
    for token in lexer:
        if token == '&&':
            if not current:
                raise ValueError('empty native shell command')
            groups.append(current)
            current = []
        elif token in (';', '|', '||', '&', '>>', '<<'):
            raise ValueError('unsupported native shell operator: ' + token)
        else:
            current.append(token)
    if not current:
        raise ValueError('empty native command or trailing shell operator')
    return groups + [current]


def executable(tokens):
    if not tokens:
        raise ValueError('empty executable command')
    if re.fullmatch(r'python(?:[0-9]+(?:\.[0-9]+)*)?', PurePosixPath(tokens[0]).name):
        tokens = tokens[1:]
        if tokens and tokens[0] == '-E':
            tokens = tokens[1:]
        if not tokens or tokens[0].startswith('-'):
            raise ValueError('unsupported native Python invocation')
    return PurePosixPath(tokens[0]).name, tokens[1:]


def native_stages(text, native_work, native_package):
    normalise = NativePaths(native_work, native_package)
    stages = []
    rows = text.splitlines()
    if not rows or rows[0].split('\t') != ['#taskLabel', 'taskNamespace', 'taskType', 'nCores', 'memMb',
                                           'priority', 'isForceLocal', 'dependencies', 'cwd', 'command']:
        raise ValueError('unexpected pyflow task ledger header')
    identities = set()
    for row in rows[1:]:
        fields = row.split('\t', 9)
        if len(fields) != 10:
            raise ValueError('malformed pyflow task ledger row')
        identity = tuple(fields[:2])
        if identity in identities:
            raise ValueError('duplicate pyflow task identity')
        identities.add(identity)
        if fields[2] == 'workflow':
            continue
        if fields[2] != 'command':
            raise ValueError('unexpected pyflow task type')
        if not fields[9]:  # Upstream dependency-only completion barrier.
            continue
        groups = shell_groups(fields[9])
        index = 0
        while index < len(groups):
            tool, args = executable(groups[index])
            index += 1
            if tool in ('mkdir', 'rm'):
                continue
            if tool == 'vcfCmdlineSwapper.py':
                if len(args) != 5 or args[1] != '<' or args[3] != '>':
                    raise ValueError('unexpected native VCF reheader redirections')
                command, source, temporary = args[0], normalise(args[2]), normalise(args[4])
                if index >= len(groups):
                    raise ValueError('native VCF reheader lacks replacement move')
                move_tool, move_args = executable(groups[index])
                index += 1
                if move_tool != 'mv' or [normalise(value) for value in move_args] != [temporary, source]:
                    raise ValueError('native VCF reheader replacement move differs')
                args = [configure_command(command, normalise), source, source]
            else:
                if any(value in ('<', '>') for value in args):
                    raise ValueError('unexpected native helper redirection')
                args = [normalise(value) for value in args]
            stages.append({'tool': tool, 'args': args})
    if not stages:
        raise ValueError('native command ledger is empty')
    return stages


def options(args, flag):
    result = []
    for index, value in enumerate(args):
        if value == flag:
            if index + 1 == len(args):
                raise ValueError('missing command value: ' + flag)
            result.append(work_path(args[index + 1]))
        elif value.startswith(flag + '='):
            result.append(work_path(value.split('=', 1)[1]))
    return result


def compiled_outputs(tool, args):
    if tool == 'GetChromDepth':
        return options(args, '--output')
    if tool == 'strelka2':
        return sum((options(args, flag) for flag in ('--somatic-snv-file', '--somatic-indel-file',
                                                   '--somatic-callable-regions-file', '--stats-file')), [])
    if tool == 'MergeRunStats':
        return options(args, '--output-file') + options(args, '--report-file')
    if tool == 'bgzf_cat':
        return options(args, '-o')
    if tool == 'bgzip':
        if len(args) != 1 or args[0].startswith('-'):
            raise ValueError('unrecognised workflow bgzip invocation')
        return [work_path(args[0]) + '.gz']
    if tool == 'tabix':
        if len(args) == 3 and args[:1] == ['-p'] and args[1] in ('vcf', 'bed'):
            return [work_path(args[2]) + '.tbi']
        if len(args) == 2 and args[0] == '-l':
            return []
        raise ValueError('unrecognised workflow tabix invocation')
    if tool == 'htsfile' and len(args) == 2 and args[0] == '-h':
        return []
    raise ValueError('unrecognised compiled workflow helper: ' + tool)


def runtime_stages(workflow, initial, retained):
    if not isinstance(workflow, dict) or not isinstance(workflow.get('stages'), list) or not workflow['stages']:
        raise ValueError('runtime workflow ledger has no stages')
    known, producers = dict(initial), {path: 'initial' for path in initial}
    models = hash_map(workflow.get('models'), 'workflow models')
    if not models or any(initial.get(path) != sha for path, sha in models.items()):
        raise ValueError('model identity is absent from independently hashed input bytes')
    if any(path not in models for path in initial if path.startswith('/work/__strelka2/config/')):
        raise ValueError('loaded default model is missing from model provenance')
    semantic, edges, generated, identifiers = [], [], set(), set()
    for stage in workflow['stages']:
        if not isinstance(stage, dict) or not isinstance(stage.get('id'), str) or not stage['id'] or stage['id'] in identifiers:
            raise ValueError('invalid or duplicate runtime stage identity')
        identifier = stage['id']
        identifiers.add(identifier)
        if type(stage.get('exitCode')) is not int or stage['exitCode'] != 0 or stage.get('missingOutputs') != []:
            raise ValueError('runtime stage failed or omitted required outputs: ' + identifier)
        tool, args = stage.get('tool'), stage.get('args')
        if not isinstance(tool, str) or PurePosixPath(tool).name != tool or not isinstance(args, list) or any(not isinstance(a, str) or '\0' in a for a in args):
            raise ValueError('invalid runtime executable arguments')
        inputs = hash_map(stage.get('inputs'), identifier + ' inputs')
        hashes = hash_map(stage.get('outputHashes'), identifier + ' outputs')
        output_list = stage.get('outputs')
        if not isinstance(output_list, list):
            raise ValueError('missing runtime output list')
        output_paths = [work_path(path) for path in output_list]
        if len(set(output_paths)) != len(output_paths) or set(output_paths) - hashes.keys():
            raise ValueError('runtime output paths lack unique hash evidence')
        for path, sha in inputs.items():
            if known.get(path) != sha:
                raise ValueError('unknown or corrupted input edge: ' + identifier + ': ' + path)
            edges.append({'stage': identifier, 'path': path, 'sha256': sha, 'producer': producers[path]})
        kind = stage.get('kind')
        command = {'tool': tool, 'args': list(args)}
        deleted = []
        if kind == 'native':
            if tool not in COMPILED or inputs != known:
                raise ValueError('native stage omitted a mounted file or used an unknown helper')
            expected_outputs = set(compiled_outputs(tool, args))
            if not expected_outputs.issubset(hashes):
                raise ValueError('native stage omitted a command output hash')
            changed = {path for path, sha in hashes.items() if known.get(path) != sha}
            if changed != set(output_paths):
                raise ValueError('native changed-output list differs from actual hash changes')
            if stage.get('phase') == 'configure':
                if not ((tool == 'htsfile' and len(args) == 2 and args[0] == '-h') or
                        (tool == 'tabix' and len(args) == 2 and args[0] == '-l')) or hashes:
                    raise ValueError('non-configuration command hidden in configure phase')
            elif stage.get('phase') == 'workflow':
                semantic.append(command)
            else:
                raise ValueError('unknown native workflow phase')
            if tool == 'bgzip':
                deleted.append(work_path(args[0]))
        elif kind == 'translated':
            if set(output_paths) != set(hashes):
                raise ValueError('translated output path/hash mismatch')
            if tool == 'mkdir':
                if len(args) != 2 or args[0] != '-p' or inputs or hashes:
                    raise ValueError('invalid translated mkdir evidence')
                work_path(args[1])
            elif tool == 'rm':
                if len(args) != 2 or args[0] != '-rf' or hashes:
                    raise ValueError('invalid translated remove evidence')
                prefix = work_path(args[1]) + '/'
                deleted = [path for path in known if path.startswith(prefix)]
                if set(inputs) != set(deleted):
                    raise ValueError('remove stage omitted live input files')
            elif tool == 'mv':
                if len(args) != 2:
                    raise ValueError('invalid translated move arguments')
                source, target = map(work_path, args)
                if set(inputs) != {source} or hashes != {target: inputs[source]}:
                    raise ValueError('move changed bytes or omitted its source')
                deleted.append(source)
                semantic.append(command)
            elif tool == 'cat.py':
                if len(args) < 3 or args[0] != '--output':
                    raise ValueError('invalid concatenation arguments')
                if set(inputs) != {work_path(path) for path in args[2:]} or set(hashes) != {work_path(args[1])}:
                    raise ValueError('concatenation input/output edges disagree with arguments')
                semantic.append(command)
            elif tool == 'mergeChromDepth.py':
                if set(inputs) != set(options(args, '--in')) or set(hashes) != set(options(args, '--out')) or not hashes:
                    raise ValueError('depth merge edges disagree with arguments')
                semantic.append(command)
            elif tool == 'vcfCmdlineSwapper.py':
                if len(args) != 1 or len(inputs) != 1 or set(hashes) != set(inputs):
                    raise ValueError('invalid in-place VCF reheader evidence')
                source = next(iter(inputs))
                semantic.append({'tool': tool, 'args': [configure_command(args[0], lambda value: value), source, source]})
            else:
                raise ValueError('unrecognised translated helper: ' + tool)
        else:
            raise ValueError('unknown runtime stage kind')
        for path, sha in hashes.items():
            known[path], producers[path] = sha, identifier
        generated.update(output_paths)
        for path in deleted:
            if path not in known:
                raise ValueError('stage deletes an unknown input file: ' + path)
            del known[path]
            del producers[path]
    recorded_generated = workflow.get('generatedFiles')
    if not isinstance(recorded_generated, list) or len(recorded_generated) != len(set(recorded_generated)) or {work_path(path) for path in recorded_generated} != generated:
        raise ValueError('workflow generated-file inventory differs from stage outputs')
    for path, sha in retained.items():
        if known.get(path) != sha:
            raise ValueError('retained output bytes differ from final file dependency: ' + path)
    live_generated = generated & known.keys()
    if live_generated - retained.keys():
        raise ValueError('retained output evidence omits live generated files: ' + ','.join(sorted(live_generated - retained.keys())))
    if not semantic or not live_generated:
        raise ValueError('vacuous runtime workflow')
    return semantic, {'successfulStages': len(workflow['stages']), 'dependencyEdges': len(edges), 'edges': edges,
                      'retainedOutputs': len(retained), 'retainedOutputHashes': retained, 'models': models}


def compare_workflow(native_text, actual, *, native_work, native_package, input_hashes=None,
                     output_hashes=None, actual_native_work=None, actual_native_package=None):
    expected = native_stages(native_text, native_work, native_package)
    if isinstance(actual, dict) and 'nativeCommands' in actual:
        if not isinstance(actual['nativeCommands'], str) or not actual_native_work or not actual_native_package:
            raise ValueError('native comparison requires actual commands/work/package')
        stages = native_stages(actual['nativeCommands'], actual_native_work, actual_native_package)
        evidence = {'successfulStages': len(stages), 'mode': 'native-command-ledger',
                    'statusEvidence': 'successful native return code must be verified by caller'}
    else:
        workflow = actual.get('workflow', actual) if isinstance(actual, dict) else actual
        stages, evidence = runtime_stages(workflow, hash_map(input_hashes, 'initial hashes'),
                                          hash_map(output_hashes, 'retained hashes'))
        evidence['mode'] = 'runtime-command-ledger-and-file-edges'
    left, right = Counter(json.dumps(stage, sort_keys=True) for stage in expected), Counter(json.dumps(stage, sort_keys=True) for stage in stages)
    return {'schema': 1, 'equal': left == right, 'nativeStages': expected, 'stages': stages,
            'nativeStageCount': len(expected), 'matchedStageCount': sum((left & right).values()),
            'missingCommands': [json.loads(value) for value in (left - right).elements()],
            'extraCommands': [json.loads(value) for value in (right - left).elements()],
            'commandDigest': digest(sorted(right.items())), **evidence}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('native_commands'); parser.add_argument('runtime_json')
    parser.add_argument('--native-work', required=True); parser.add_argument('--native-package', required=True)
    parser.add_argument('--actual-native-work'); parser.add_argument('--actual-native-package')
    parser.add_argument('--input-hashes', required=True); parser.add_argument('--output-hashes', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    try:
        result = compare_workflow(Path(args.native_commands).read_text(), json.loads(Path(args.runtime_json).read_text()),
                                  native_work=args.native_work, native_package=args.native_package,
                                  input_hashes=json.loads(Path(args.input_hashes).read_text()),
                                  output_hashes=json.loads(Path(args.output_hashes).read_text()),
                                  actual_native_work=args.actual_native_work, actual_native_package=args.actual_native_package)
    except Exception as error:
        result = {'schema': 1, 'equal': False, 'error': str(error)}
    Path(args.report).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['equal'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
