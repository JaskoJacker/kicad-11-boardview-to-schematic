# Third-party notices

This repository's own code is released under the MIT License (see `LICENSE`).
It bundles the following third-party component.

## kicad-python (`vendor/kicad_python-0.9.0.dev0-cp312-cp312-win_amd64.whl`)

- Source: <https://gitlab.com/kicad/code/kicad-python>, commit
  `3dcb6c9c08bad2b2ed08e9d468ae0700697217ce`
- Modification: the wheel was repacked with the regenerated `kipy/proto` files (see the README,
  "About the bundled kicad-python"); no change to the library's own source code.
- License: MIT

```
Copyright The KiCad Developers

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Runtime dependencies installed from PyPI

Listed in `requirements.txt` (protobuf, pynng, jsonschema, zstandard, ...). They are not
redistributed in this repository; each is installed from PyPI under its own license.

## KiCad

The program talks to a running KiCad instance through its IPC API. KiCad itself is not
included or linked; it is a separate program under its own license (GPL).
