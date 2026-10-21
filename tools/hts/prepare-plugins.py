#!/usr/bin/env python3
"""Link the unmodified BCFtools plugin algorithms into one executable.

Each plugin retains its own copies of the helper translation units listed in
its upstream make recipe.  A first compilation discovers *all* external
definitions with nm; a second namespaces them before the final static link.
This keeps the private state and helper implementations of the original DSOs
separate, including globals that are not part of the public plugin API.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess


COMMON_PLUGINS = {
    'GTisec', 'GTsubset', 'ad-bias', 'add-variantkey', 'af-dist',
    'allele-length', 'check-ploidy', 'check-sparsity', 'color-chrs',
    'contrast', 'counts', 'dosage', 'fill-AN-AC', 'fill-from-fasta',
    'fill-tags', 'fixploidy', 'fixref', 'frameshifts', 'guess-ploidy',
    'gvcfz', 'impute-info', 'indel-stats', 'isecGT', 'missing2ref',
    'parental-origin', 'prune', 'remove-overlaps', 'scatter', 'setGT',
    'smpl-stats', 'split', 'split-vep', 'tag2tag', 'trio-dnm2',
    'trio-stats', 'trio-switch-rate', 'variant-distance', 'variantkey-hex',
}
HELPERS = {
    'GTisec': [], 'GTsubset': [], 'ad-bias': ['convert.c'],
    'af-dist': ['bin.c'], 'color-chrs': ['HMM.c'],
    'fill-from-fasta': ['filter.c'], 'fixploidy': ['ploidy.c', 'regidx.c'],
    'isecGT': ['smpl_ilist.c'],
}
API = {
    'version': ('void', 'const char **, const char **', 'dl_version_f'),
    'run': ('int', 'int, char **', 'dl_run_f'),
    'init': ('int', 'int, char **, bcf_hdr_t *, bcf_hdr_t *', 'dl_init_f'),
    'about': ('const char *', 'void', 'dl_about_f'),
    'usage': ('const char *', 'void', 'dl_usage_f'),
    'process': ('bcf1_t *', 'bcf1_t *', 'dl_process_f'),
    'destroy': ('void', 'void', 'dl_destroy_f'),
}
IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z_0-9]*\Z')


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_plugins(version: str) -> list[str]:
    if version not in ('1.16', '1.21'):
        raise ValueError(f'Unsupported BCFtools version: {version}')
    return sorted(COMMON_PLUGINS | {'mendelian' if version == '1.16' else 'mendelian2'})


def run(command: list[str], root: Path) -> str:
    result = subprocess.run(command, cwd=root, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'Command failed: {shlex.join(command)}\n{result.stdout}{result.stderr}')
    return result.stdout


def defined_symbols(nm: list[str], path: Path, root: Path) -> set[str]:
    """POSIX nm output is shared by GNU nm and LLVM nm (including wasm)."""
    symbols = set()
    for line in run([*nm, '--defined-only', '--extern-only', '--format=posix', str(path)], root).splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        name, kind = fields[:2]
        if kind in ('U', 'u'):
            raise RuntimeError(f'nm unexpectedly returned an undefined symbol: {line}')
        if not IDENTIFIER.fullmatch(name):
            raise RuntimeError(f'Cannot safely namespace external definition {name!r} in {path}')
        symbols.add(name)
    if not symbols:
        raise RuntimeError(f'No external definitions found in {path}')
    return symbols


def plugin_sources(root: Path, name: str) -> list[str]:
    """Check every special upstream make recipe, rather than silently omitting it."""
    recipe = root / 'plugins' / f'{name}.mk'
    if recipe.exists() != (name in HELPERS):
        raise RuntimeError(f'Unexpected plugin make recipe: {recipe}')
    helpers = HELPERS.get(name, [])
    if recipe.exists():
        commands = [line for line in recipe.read_text().splitlines() if line.startswith('\t')]
        if len(commands) != 1 or '$<' not in commands[0]:
            raise RuntimeError(f'Unexpected plugin build command: {recipe}')
        actual = re.findall(r'(?<![A-Za-z_0-9/])([A-Za-z_][A-Za-z_0-9/.-]*\.c)\b', commands[0])
        if actual != [*helpers, 'version.c']:
            raise RuntimeError(f'Changed helper sources in {recipe}: {actual}')
    sources = [f'plugins/{name}.c', *helpers, 'version.c']
    for source in sources:
        if not (root / source).is_file():
            raise RuntimeError(f'Missing plugin source: {source}')
    return sources


def replace_once(text: str, before: str, after: str) -> str:
    if text.count(before) != 1:
        raise RuntimeError(f'Upstream source does not contain exactly one expected patch anchor: {before[:100]}')
    return text.replace(before, after, 1)


BUILTIN_LOAD = r'''#include ".builtin-plugins/registry.h"

static int load_plugin(args_t *args, const char *fname, int exit_on_error, plugin_t *plugin)
{
    size_t i;
    for (i=0; i<BCFTOOLS_BUILTIN_PLUGIN_COUNT; i++)
    {
        const builtin_plugin_t *entry = &bcftools_builtin_plugins[i];
        if ( strcmp(entry->name,fname) ) continue;
        plugin->name = strdup(fname);
        if ( !plugin->name ) error("Out of memory loading built-in plugin %s\n",fname);
        plugin->version = entry->version;
        plugin->run = entry->run;
        plugin->init = entry->init;
        plugin->about = entry->about;
        plugin->usage = entry->usage ? entry->usage : entry->about;
        plugin->process = entry->process;
        plugin->destroy = entry->destroy;
        if ( args->verbose > 1 ) fprintf(stderr,"%s:\n\tbuilt-in plugin .. ok\n",fname);
        return 0;
    }
    if ( exit_on_error )
        error("No built-in plugin named \"%s\". Run bcftools plugin -l for the compiled plugin list.\n",fname);
    return -1;
}
'''

BUILTIN_LIST = r'''// The registry is sorted with the same bytewise ordering as upstream strcmp.
static int list_plugins(args_t *args)
{
    if ( !args ) return BCFTOOLS_BUILTIN_PLUGIN_COUNT;
    size_t i;
    for (i=0; i<BCFTOOLS_BUILTIN_PLUGIN_COUNT; i++)
    {
        const builtin_plugin_t *entry = &bcftools_builtin_plugins[i];
        if ( args->verbose ) printf("\n-- %s --\n%s",entry->name,entry->about());
        else printf("%s\n",entry->name);
    }
    if ( args->verbose ) printf("\n");
    return 0;
}
'''


def patch_loader(root: Path) -> dict[str, str]:
    path = root / 'vcfplugin.c'
    text = path.read_text()
    if 'BCFTOOLS_BUILTIN_PLUGINS' in text:
        raise RuntimeError('Plugin loader is already patched; prepare a fresh source tree')
    before = sha256(path)
    system_headers = '#ifdef _WIN32\n#include <windows.h>\n#else\n#include <dlfcn.h>\n#endif'
    text = replace_once(text, system_headers, '#ifndef BCFTOOLS_BUILTIN_PLUGINS\n' + system_headers + '\n#endif')
    start = text.index('static void add_plugin_paths(')
    end = text.index('static void check_version(', start)
    text = text[:start] + '#ifdef BCFTOOLS_BUILTIN_PLUGINS\n' + BUILTIN_LOAD + '\n#else\n' + text[start:end] + '#endif\n\n' + text[end:]
    start = text.index('static int cmp_plugin_name(')
    end = text.index('int count_plugins(', start)
    text = text[:start] + '#ifdef BCFTOOLS_BUILTIN_PLUGINS\n' + BUILTIN_LIST + '\n#else\n' + text[start:end] + '#endif\n\n' + text[end:]
    close = '#ifdef _WIN32\n    FreeLibrary(args->plugin.handle);\n#else\n    dlclose(args->plugin.handle);\n#endif'
    text = replace_once(text, close, '#ifndef BCFTOOLS_BUILTIN_PLUGINS\n' + close + '\n#endif')
    help_line = '    fprintf(stderr, "   -l, --list-plugins             List available plugins. See BCFTOOLS_PLUGINS environment variable and man page for details\\n");'
    text = replace_once(text, help_line, '#ifdef BCFTOOLS_BUILTIN_PLUGINS\n    fprintf(stderr, "   -l, --list-plugins             List compiled-in plugins\\n");\n#else\n' + help_line + '\n#endif')
    path.write_text(text)
    return {'before': before, 'after': sha256(path)}


def patch_gtisec(root: Path) -> dict[str, str]:
    path = root / 'plugins/GTisec.c'
    text = path.read_text()
    before = sha256(path)
    # quick is uint64_t*: unsigned long is only 32 bits in the wasm32 ABI.
    text = replace_once(text, 'sizeof(unsigned long)', 'sizeof(*args.quick)')
    path.write_text(text)
    return {'before': before, 'after': sha256(path)}


def compile_plugin(root: Path, name: str, cc: list[str], nm: list[str], flags: list[str]) -> dict:
    sources = plugin_sources(root, name)
    directory = root / '.builtin-plugins' / name
    directory.mkdir(parents=True, exist_ok=True)
    all_symbols: set[str] = set()
    per_source = []
    for index, source in enumerate(sources):
        probe = directory / f'probe-{index}.o'
        run([*cc, *flags, '-c', source, '-o', str(probe)], root)
        symbols = defined_symbols(nm, probe, root)
        if all_symbols & symbols:
            raise RuntimeError(f'Duplicate external definitions within {name}: {sorted(all_symbols & symbols)}')
        all_symbols.update(symbols)
        per_source.append((source, symbols))
        probe.unlink()
    main_symbols = per_source[0][1]
    if 'about' not in main_symbols or not ({'init', 'run'} & main_symbols):
        raise RuntimeError(f'Plugin lacks required API exports: {name}')
    if 'run' not in main_symbols and not {'init', 'process', 'destroy'} <= main_symbols:
        raise RuntimeError(f'Plugin lacks the stream-processing API: {name}')
    if 'version' not in all_symbols:
        raise RuntimeError(f'Plugin lacks version export: {name}')
    prefix = 'bcftools_builtin_' + name.replace('-', '_') + '_'
    mapping = {symbol: prefix + symbol for symbol in sorted(all_symbols)}
    definitions = [f'-D{symbol}={replacement}' for symbol, replacement in mapping.items()]
    objects = []
    renamed_symbols = set()
    for index, (source, original_symbols) in enumerate(per_source):
        output = directory / f'{index}.o'
        run([*cc, *flags, *definitions, '-c', source, '-o', str(output)], root)
        actual = defined_symbols(nm, output, root)
        expected = {mapping[symbol] for symbol in original_symbols}
        if actual != expected:
            raise RuntimeError(f'Namespacing changed the external definition set of {source}: {sorted(actual ^ expected)}')
        renamed_symbols.update(actual)
        objects.append(output.relative_to(root).as_posix())
    api = {key: mapping[key] for key in API if key == 'version' or key in main_symbols}
    if 'run' in api:
        # The upstream loader returns immediately after finding run(), before
        # looking up process()/destroy(), even if those names are exported.
        api.pop('process', None)
        api.pop('destroy', None)
    return {'name': name, 'sources': {source: sha256(root/source) for source in sources},
            'objects': objects, 'symbols': mapping, 'api': api}


def registry(records: list[dict]) -> str:
    lines = ['/* Generated by tools/hts/prepare-plugins.py; do not edit. */']
    for record in records:
        for api, symbol in record['api'].items():
            result, arguments, _ = API[api]
            lines.append(f'extern {result} {symbol}({arguments});')
    lines += ['typedef struct {', '    const char *name;']
    lines += [f'    {kind} {api};' for api, (_, _, kind) in API.items()]
    lines += ['} builtin_plugin_t;', 'static const builtin_plugin_t bcftools_builtin_plugins[] = {']
    for record in records:
        lines.append(f'    {{ .name = "{record["name"]}",')
        for api, (_, _, kind) in API.items():
            symbol = record['api'].get(api)
            lines.append(f'      .{api} = ({kind}){symbol if symbol else "NULL"},')
        lines.append('    },')
    lines += ['};', '#define BCFTOOLS_BUILTIN_PLUGIN_COUNT ((int)(sizeof(bcftools_builtin_plugins)/sizeof(bcftools_builtin_plugins[0])))', '']
    return '\n'.join(lines)


def prepare(root: Path, version: str, cc: list[str], nm: list[str], flags: list[str], jobs: int) -> dict:
    root = root.resolve()
    names = expected_plugins(version)
    actual = sorted(path.stem for path in (root / 'plugins').glob('*.c'))
    if actual != names:
        raise RuntimeError(f'Stock plugin inventory differs for {version}: expected {names}, found {actual}')
    if (root / '.builtin-plugins/manifest.json').exists():
        raise RuntimeError('Built-in plugins already prepared; prepare a fresh source tree')
    patches = {'plugins/GTisec.c': patch_gtisec(root), 'vcfplugin.c': patch_loader(root)}
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        records = list(pool.map(lambda name: compile_plugin(root, name, cc, nm, flags), names))
    header = root / '.builtin-plugins/registry.h'
    header.write_text(registry(records))
    objects = [obj for record in records for obj in record['objects']]
    manifest = {'schema': 1, 'version': version, 'pluginCount': len(records),
                'plugins': records, 'objects': objects, 'patches': patches,
                'registrySha256': sha256(header), 'mode': 'built-in',
                'compiler': cc, 'nm': nm, 'flags': flags}
    (root / '.builtin-plugins/manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--version', choices=('1.16', '1.21'), required=True)
    parser.add_argument('--cc', default='cc', help='Compiler command, shell-word parsed without a shell')
    parser.add_argument('--nm', default='nm', help='GNU nm or LLVM nm command')
    parser.add_argument('--cflags-json', default='[]', help='JSON array containing all compile/include/feature flags')
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    flags = json.loads(args.cflags_json)
    if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
        parser.error('--cflags-json must be an array of strings')
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    cc, nm = shlex.split(args.cc), shlex.split(args.nm)
    if not cc or not nm:
        parser.error('--cc and --nm must not be empty')
    result = prepare(args.source, args.version, cc, nm, flags, args.jobs)
    print(json.dumps({'plugins': result['pluginCount'], 'objects': len(result['objects']),
                      'manifest': str(args.source / '.builtin-plugins/manifest.json')}))


if __name__ == '__main__':
    main()
