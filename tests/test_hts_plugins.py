"""Exercise plugin symbol isolation using actual compiled and linked C objects."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('hts_prepare_plugins', ROOT/'tools/hts/prepare-plugins.py')
PLUGINS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLUGINS)


@unittest.skipUnless(shutil.which('cc') and shutil.which('nm'), 'C compiler and nm required')
class CompiledPluginIsolation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root/'plugins').mkdir()
        (self.root/'api.h').write_text(
            'typedef struct { int value; } bcf1_t;\n'
            'typedef struct { int value; } bcf_hdr_t;\n'
            'extern int host_value;\n')
        (self.root/'version.c').write_text(
            'void version(const char **a, const char **b) { *a="1.21"; *b="1.21"; }\n')

    def tearDown(self):
        self.temporary.cleanup()

    def compile(self, name):
        return PLUGINS.compile_plugin(self.root, name, ['cc'], ['nm'], ['-O0', '-I.'])

    def test_private_globals_are_isolated_and_host_imports_are_preserved(self):
        # Both plugins deliberately export the same private state/helper names.
        # The host import must remain shared, while each plugin state is private.
        source = '''#include "api.h"
int counter = INITIAL;
int helper(void) { return ++counter + host_value; }
const char *about(void) { return "test"; }
int init(int argc, char **argv, bcf_hdr_t *in, bcf_hdr_t *out) { return helper(); }
bcf1_t *process(bcf1_t *record) { record->value += counter; return record; }
void destroy(void) { counter = INITIAL; }
'''
        for name, value in [('counts', 10), ('dosage', 100)]:
            (self.root/f'plugins/{name}.c').write_text(source.replace('INITIAL', str(value)))
        records = [self.compile(name) for name in ('counts', 'dosage')]
        for record in records:
            self.assertIn('counter', record['symbols'])
            self.assertIn('helper', record['symbols'])
            self.assertNotIn('host_value', record['symbols'])
        main = '''#include "api.h"
int host_value = 1000;
int bcftools_builtin_counts_init(int, char **, bcf_hdr_t *, bcf_hdr_t *);
int bcftools_builtin_dosage_init(int, char **, bcf_hdr_t *, bcf_hdr_t *);
void bcftools_builtin_counts_destroy(void);
int main(void) {
 if (bcftools_builtin_counts_init(0,0,0,0) != 1011) return 1;
 if (bcftools_builtin_dosage_init(0,0,0,0) != 1101) return 2;
 if (bcftools_builtin_counts_init(0,0,0,0) != 1012) return 3;
 host_value = 2000;
 bcftools_builtin_counts_destroy();
 if (bcftools_builtin_counts_init(0,0,0,0) != 2011) return 4;
 if (bcftools_builtin_dosage_init(0,0,0,0) != 2102) return 5;
 return 0;
}
'''
        (self.root/'main.c').write_text(main)
        objects = [item for record in records for item in record['objects']]
        subprocess.run(['cc', '-I.', 'main.c', *objects, '-o', 'check'], cwd=self.root, check=True)
        subprocess.run([str(self.root/'check')], cwd=self.root, check=True)

    def test_helper_translation_units_share_the_plugin_namespace(self):
        (self.root/'plugins/af-dist.mk').write_text(
            'plugins/af-dist.so: plugins/af-dist.c version.h version.c bin.h bin.c\n'
            '\t$(CC) -o $@ bin.c version.c $< $(LIBS)\n')
        (self.root/'bin.h').write_text('extern int state;\nint helper(void);\n')
        (self.root/'bin.c').write_text('#include "bin.h"\nint state=4;\nint helper(void) { return ++state; }\n')
        (self.root/'plugins/af-dist.c').write_text(
            '#include "api.h"\n#include "bin.h"\n'
            'const char *about(void) { return "test"; }\n'
            'int run(int argc,char **argv) { return helper(); }\n'
            'void destroy(void) { state = -10; }\n')
        record = self.compile('af-dist')
        self.assertEqual(record['symbols']['state'], 'bcftools_builtin_af_dist_state')
        # Upstream load_plugin returns on run() before resolving destroy().
        self.assertNotIn('destroy', record['api'])
        (self.root/'main.c').write_text(
            'int bcftools_builtin_af_dist_run(int,char **);\n'
            'int main(void) { return bcftools_builtin_af_dist_run(0,0) == 5 ? 0 : 1; }\n')
        subprocess.run(['cc', 'main.c', *record['objects'], '-o', 'check'], cwd=self.root, check=True)
        subprocess.run([str(self.root/'check')], cwd=self.root, check=True)

    def test_unrecognized_upstream_helper_recipe_is_rejected(self):
        (self.root/'plugins/counts.c').write_text('int example;\n')
        (self.root/'plugins/counts.mk').write_text('plugins/counts.so: extra.c\n\t$(CC) extra.c $<\n')
        with self.assertRaisesRegex(RuntimeError, 'Unexpected plugin make recipe'):
            PLUGINS.plugin_sources(self.root, 'counts')

    def test_changed_inventory_is_rejected_before_source_is_modified(self):
        marker = self.root/'vcfplugin.c'
        marker.write_text('untouched\n')
        with self.assertRaisesRegex(RuntimeError, 'Stock plugin inventory differs'):
            PLUGINS.prepare(self.root, '1.21', ['cc'], ['nm'], [], 1)
        self.assertEqual(marker.read_text(), 'untouched\n')


if __name__ == '__main__':
    unittest.main()
