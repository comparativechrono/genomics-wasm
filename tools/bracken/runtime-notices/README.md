# Pyodide runtime sources and notices

Bracken's Python runtime is the unmodified `pyodide-core-314.0.7` release
distribution, with Python 3.14.2 and Emscripten 5.0.3. Bracken's unchanged
Python scripts require only the standard library; no additional wheels or
extension modules are loaded. These notices cover the runtime and its linked
libraries. The separate C++ helper uses Emscripten 3.1.64 and its accompanying
source archive and licence notice.

`../runtime-sources.json` identifies the exact corresponding source archives,
download URLs, SHA-256 checksums, and notice paths. `manifest.json` records the
archive member from which each notice was copied and the resulting checksum.
Complete licence files are copied unchanged. Files ending in `.notice` contain
the complete attribution/licence comment from the named source file. The
original source archives preserve all other per-file notices.

| Component | Source selected by the pinned runtime |
| --- | --- |
| Pyodide | 314.0.7, including CPython/Emscripten patches and vendored stackframe code |
| CPython | 3.14.2, including bundled Expat, libmpdec, HACL*, and standard library code |
| Emscripten | 5.0.3, including its C/C++ runtime sources and mini-lz4 JavaScript |
| libffi | `f08493d249d2067c8b3207ba46693dd858f95db3` |
| hiwire | `6a1e67280a15d929ebeceee54a6358c9c8d5f697` |
| liblzma | XZ Utils 5.2.2 |
| Zstandard | CPython source-deps `zstd-1.5.7` |
| SQLite | 3.39.0 (`sqlite-autoconf-3390000`) |
| bzip2 | Emscripten ports 1.0.6 |
| zlib | 1.3.1 |
| ws | 8.18.3, used only by Pyodide's Node bootstrap |

The linked-library versions come from Pyodide 314.0.7's `Makefile.envs`,
`cpython/Makefile`, and `cpython/Setup.local`, together with Emscripten 5.0.3's
`tools/ports/zlib.py` and `tools/ports/bzip2.py`. The `ws` pin comes from
Pyodide's `src/js/package-lock.json`. The archives include the build recipes
and Pyodide's modifications to CPython and Emscripten. They accompany the
upstream prebuilt runtime; this project does not claim to have rebuilt that
runtime bit for bit.

The complete CPython `Doc/license.rst` retains its additional incorporated-code
notices. Some notices describe optional components not enabled here. Likewise,
the Emscripten notices include its available allocator/runtime alternatives;
their inclusion does not imply that every alternative is linked. Pyodide's
core `Setup.local` disables `_ssl` and `_hashlib`; the separate OpenSSL package
is not loaded or redistributed by this port. Imports of `ssl` and `_ssl` work
because Pyodide supplies the Python stubs `src/py/ssl.py` and `src/py/_ssl.py`
inside `python_stdlib.zip`. Their `OPENSSL_VERSION` is `OpenSSL (stub)`;
successful import does not indicate an OpenSSL library or working TLS support.

XZ Utils' `COPYING` distinguishes the public-domain liblzma library from its
GPL/LGPL build scripts and command-line utilities. The complete source archive
and its accompanying licence texts are retained. SQLite's notice is copied
from the linked amalgamation, rather than from the unrelated Tcl extension.

For Pyodide's MPL-2.0 source distribution, the accompanying source archive is
`pyodide-314.0.7.tar.gz`; it is also available from the pinned URL recorded in
`../runtime-sources.json`. Runtime sources are distributed alongside the
standalone release with the remaining pinned dependency archives.
