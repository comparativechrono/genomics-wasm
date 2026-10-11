# Python for the native comparison

Strelka2 2.9.10's original configuration and workflow scripts require Python 2. This isolated CPython 2.7.18 build runs those scripts unchanged when checking the browser port against the native workflow. It is not used by the browser application.

Run `python3 scripts/setup-strelka2-python.py` with a C compiler and make installed. The interpreter is installed at `build/strelka2-python/bin/python2.7`. The recipe verifies the official source archive before building it. A standalone Strelka2 package includes that archive in `dist/strelka2/sources/` so this step can run without downloading it again.

The source is unmodified. `build/strelka2-python/build-manifest.json` records the recipe, source archive and installed files. Validation records the interpreter used for the native workflow. Python 2 is no longer maintained; this interpreter is used only for this local comparison of the pinned upstream workflow.

`LICENSE` is copied from the Python 2.7.18 source release. The full source archive retains its additional notices.
