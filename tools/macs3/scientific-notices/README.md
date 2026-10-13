# Scientific-package sources and notices

The adjacent directories contain unchanged licence files from the exact source
archives and browser wheels selected in `../sources.lock.json`. Files ending
in `.notice` preserve complete embedded licence comments. `manifest.json`
records the source archive, archive checksum, member path, output path and
output checksum for each notice. Regenerate them after fetching the locked
archives with `python tools/macs3/extract-scientific-notices.py`.

The MACS3 notices include the incorporated fermi-lite, ksw, swalign and SIMDe
code, including the CC0 notices in the portable-snippets and Hedley headers.
Scientific Python notices preserve upstream bundled-code licences, including
NumPy random-number generators, SciPy numerical libraries, pandas incorporated
code, scikit-learn vendored libraries and joblib's CloudPickle.

The SciPy browser wheel contains `scipy.libs/libopenblas.so`, whose embedded
configuration identifies OpenBLAS 0.3.31. Its source recipe also incorporates
libf2c from CLAPACK 3.2.1. The h5py wheel contains `h5py.libs/libhdf5.so` and
`libhdf5_hl.so`; the embedded HDF5 version is 1.12.1. Those exact original
sources, Boost 1.84.0 and the scientific zlib 1.3.1 release archive accompany
the distribution. NumPy's two upstream `.a` development archives contain
WebAssembly objects and remain unchanged inside its official wheel.

The scientific recipes and their patches are preserved in the exact
`pyodide-recipes-314-20260911.tar.gz` release snapshot, commit
`fc8587207ecbf0fb54305d5da25ab7ed70f126d3`, selected by the pinned cross-build
environment. In particular this retains the HDF5 generated settings and
patches, h5py patches, OpenBLAS and libf2c patches, and SciPy build recipe.
The older recipes in the Pyodide core source tree are not used to infer the
versions of these separately published scientific wheels.

Some source archives also include tools, tests, documentation or platform
implementations that are not linked into these browser wheels. Their notices
are retained conservatively; their inclusion does not assert that they are
runtime dependencies. Native comparison binaries and host compilers are not
part of the browser runtime. Core Python/Pyodide notices are documented in
`../runtime-notices/README.md`.

The MPL-2.0 source for the scientific recipe files is included in the pinned
recipe archive alongside the original library sources. The official runtime
and scientific wheels are redistributed unchanged; this project does not
claim bit-for-bit reconstruction of those upstream binary releases.
