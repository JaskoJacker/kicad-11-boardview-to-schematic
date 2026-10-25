# kicad-11-boardview-to-schematic

> **Important: this project targets KiCad 11, which has not been released yet.** It was started
> before the release, on the KiCad 11 nightly builds (internal version 10.99). The schematic part of
> the IPC API it relies on is still under development and may change or break between builds, and
> nothing guarantees that it will work unchanged with the final release. It does not work with
> KiCad 10 or earlier, whose API does not cover the schematic editor.

An experiment with the **KiCad 11 IPC API (nightly 10.99)** to build an electrical schematic from
a boardview. A command-line program reads the netlist of a boardview (GENCAD format), places the
components in KiCad with symbols built on their real pins, circles the pins to be connected and,
once the technician has drawn the wires, checks that the nets match those of the boardview.

The `.kicad_sch` file is never touched directly: the program talks to the running KiCad instance
through its API.

> Status: experimental.

## Requirements

- Windows (developed and tested on Windows 11)
- **KiCad 11 nightly** (internal version 10.99; not released yet, see the note at the top) with the
  API server enabled: *Preferences > Plugins > Enable API server*
- **Python 3.12** (the bundled `kicad-python` wheel is built for CPython 3.12, Windows x64)

## Installation

The repository is self-contained: the pure-Python dependencies are pinned in `requirements.txt`
and the `kicad-python` wheel is included in `vendor/`.

Open a normal Command Prompt (not "Run as administrator": a folder created that way is owned by the
Administrators group and Git then refuses to work in it). Go to the folder where you want the
program, then:

```bash
git clone https://github.com/JaskoJacker/kicad-11-boardview-to-schematic.git
cd kicad-11-boardview-to-schematic
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

`git clone` creates a new folder named `kicad-11-boardview-to-schematic`: the `cd` that follows
enters it, and the virtual environment (`.venv`) must be created **inside that folder**, not next
to it. If you use VS Code, open that same folder (File > Open Folder): it then finds `.venv` by
itself. If it does not, press Ctrl+Shift+P, choose *Python: Select Interpreter* and pick
`.venv\Scripts\python.exe`. An error like `No module named 'kipy'` means the program was started
with another Python than the one of the `.venv`.

Check the installation:

```bash
python -c "from kipy.schematic import Schematic; print('ok')"
```

### About the bundled kicad-python

`pip install kicad-python` from PyPI is **not enough**: the PyPI version (0.8.0) has no
`get_schematic()` and no `Schematic` class. The wheel in `vendor/` is `kicad-python`
`0.9.0.dev0`, built from the `main` branch of the official repository
(<https://gitlab.com/kicad/code/kicad-python>, commit
`3dcb6c9c08bad2b2ed08e9d468ae0700697217ce`, MIT license). The wheel was **repacked** with the
regenerated `kipy/proto` files: the stock `poetry build` output fails with
`ModuleNotFoundError: No module named 'common'`.

If you need a different version or another platform, rebuild it from source. You need
[Poetry](https://python-poetry.org/docs/) 2.x and the Protocol Buffers compiler `protoc`
**25.3 or older** ([download](https://github.com/protocolbuffers/protobuf/releases/tag/v25.3)):

```bash
git clone https://gitlab.com/kicad/code/kicad-python.git
cd kicad-python
git checkout 3dcb6c9c08bad2b2ed08e9d468ae0700697217ce
git submodule update --init
```

Do not trust `poetry build` for the `.proto` files: it builds in an isolated environment that does
not see `protoc`, and silently packages broken `*_pb2.py` files. Instead:

1. restore the committed files with `git checkout -- kipy/proto` (this also restores the static
   `__init__.py` files, which the generator never writes);
2. with `protoc` and `protoc-gen-mypy` both on the `PATH`, run `python tools/generate_protos.py`
   by hand: it calls `protoc` and then fixes the absolute imports (`from common.types import ...`
   becomes relative);
3. build the wheel and check it with `python -c "from kipy.schematic import Schematic"` from a
   folder that has no local `kipy/` subfolder.

On Windows, if Poetry's virtual environment ends up in a path longer than 260 characters, run
`poetry config virtualenvs.in-project true --local` inside the project folder.

## Usage

1. Open KiCad 11 with a project and an empty schematic (`.kicad_sch`), API server enabled.
2. Run the entry point:

```bash
python interactive_session.py
```

3. The program asks for the boardview file (formats registered in `boardview.py`, today GENCAD
   `.cad`). The first time, it creates the **boardview
   data folder** next to the repository (`<repository folder>_data`, outside the repository, see
   "Boardview data" below) and prints where it is. The folder does not exist before this first run:
   you do not have to create it. Copy your `.cad` there and enter its path, or just type or paste
   the path of a `.cad` file stored anywhere (quotes from Windows "Copy as path" are accepted; a
   wrong path is asked again).

The program asks for the boardview, the component and the starting pin, then guides the expansion
net by net. On the **first run on a project** it creates the library `boardview_custom.kicad_sym`
and its row in `sym-lib-table`: KiCad must re-read the table (Preferences > Manage symbol
libraries > OK, or reopen the project) and the program must be relaunched. This happens once per
project.

## Troubleshooting

| Message | Cause and fix |
|---|---|
| `Cannot reach KiCad` | KiCad is not running, or its API server is disabled: Preferences > Plugins. |
| `KiCad answers, but no schematic editor is open` | Only the KiCad project manager is open. Open the project's schematic (double-click the `.kicad_sch`) and keep the editor window open. |
| `KiCad is busy` | A dialog or an interactive command is open in KiCad: close it. |
| `File not found` | The boardview path is wrong; type the full path (quotes are fine). |

## Continuing a net later

At the restart prompt the program lists the **nets you have worked on that still have members
not placed** (with how many). Type the **net name** to reopen its menu, or `REFDES.PIN` of any
component of the boardview (for example `C6118.1`) to place it and open the menu of its net. A
`REFDES` alone (any case) is enough when the component has a single pin that can be expanded
(`C6118` starts from `C6118.1`); otherwise the program shows its pins and asks which one. The
list of free pins only shows pins of components already on the schematic.

In the menu of a net, type the pin to insert as `REFDES.PIN` (for example `UT27.7`, any case), a
`REFDES` alone (the program asks for its pin, as at the restart prompt) or its number in the list; `C`
checks what you have wired so far and stays on the net; `0` or Enter closes the net. A `REFDES.PIN` of **another net** moves on to that net (after the usual check
of the one you leave): a net does not have to be closed before starting another.

If KiCad answers "busy" (a dialog or an interactive command such as a wire being drawn is open),
the program says so and retries when you press Enter instead of stopping.

**Saving and resuming.** The program never saves your schematic: save it yourself (Ctrl+S); KiCad warns
you if you close it with unsaved changes. When you start the program on a schematic that already holds
components of the boardview, it rebuilds where you were from the schematic alone (which pins are
confirmed, which are still to confirm) and goes straight to the restart prompt.

## The circles

A circle marks a pin of the net you are working on that is not confirmed yet. The circles follow the
net of the **last pin you chose**: opening a net circles at once its unconfirmed pins on components
that are already on the schematic, and choosing a pin of another net removes the old circles (they come
back when you reopen that net). Closing a net with `0` leaves its circles until you choose the next
pin. A pin loses its circle when it is wired and the net carries the boardview name; if you move or
rotate the component the circle follows (it is checked every few seconds), and a circle left where no
pin is (an earlier run, a component deleted) is removed by the program.

## What the checks cover

The checks run when you close a net, on command `C`, before you move to another net and when you end
the session. Each run does two things:

1. **The pins you placed for each net**: are they wired, on the same KiCad net, and is that net
   named like the boardview net? Pins that pass lose their circle (a pin whose net later breaks
   gets its circle back).
2. **The whole schematic** (every wired pin of every placed component, circled or not, confirmed
   earlier or never highlighted), so a mistake made after a pin was confirmed is still caught.

What it finds is either a **GRAVE error** or a **NOTICE**:

| Finding | Severity |
|---|---|
| a wire joining pins of different boardview nets (**short**), a wire on the **wrong pin** of a component | GRAVE |
| a **dangling net**: a wire attached to a single pin, joining nothing | GRAVE |
| a net with the **wrong name** | GRAVE |
| a net **closed** with `0` that has **no name** | GRAVE |
| a net with **no name yet**, while you are still working on it | NOTICE |
| pins of a net that are **not wired** (or wired in separate pieces) | NOTICE, always: nobody is forced to wire every pin |

A NOTICE is printed and you carry on. A GRAVE error **blocks the program**: it accepts no command
until it is solved. When the fix is mechanical the program applies it by itself: it deletes the
wires and labels of the offending net (a wrong name only loses its label). That is one undo step,
so Ctrl+Z in KiCad brings them back; set `AUTO_REMOVE_GRAVE_NETS = False` in
`interactive_session.py` to be asked instead. What only you can solve (wiring a net that is not
wired, naming a net you closed) is explained and the program waits: fix it in KiCad and press Enter
to check again. At the very end you can also type `QUIT` to finish with errors left, and Ctrl+C
always aborts.

A net with a single pin placed so far cannot be wired yet, so it is never reported.

**Naming a net directly on a pin.** A net that closes on no other component cannot be drawn, and
you do not have to close a whole net before starting another. So a pin can simply carry the net
name: put a label with the boardview net name right on the pin, no wire (in KiCad: `L`, click on the
pin end). The pin counts as connected, the net is not dangling, the circle goes away, and the pin is
joined to the rest of the net later by the name (same label = same net). A wrong name is still a
GRAVE error. A wire that starts on the pin and goes nowhere stays a dangling net, label or not.

## Components that are not on the real board

When the boardview says nothing about a component the program asks for its part identifier. Answer
`NC` when the component exists only in the boardview and not on the physical board (a boardview
description of `NOASSEMBLED` is a hint): the component is **left off the schematic** and its pins are
not proposed again.

## How a component gets its symbol

For every component the program places, it first works out **which part it is** from the boardview
description (e.g. `OVP IC TPD6S300ARUKR//TI WQFN-20(3*3)` -> `TPD6S300ARUKR`). If the boardview
says nothing usable it asks you for the part identifier (`NC` = leave it off the schematic; Enter =
the letters of its reference, e.g. `R` for R871, when it has up to 3 pins, otherwise unknown). Then:

1. a symbol with the **same name in full** and the **same pin numbers** in KiCad's own libraries is
   used (`TPD6S300A` is not enough for `TPD6S300ARUKR`: the name must be identical);
2. otherwise the same check in our library `boardview_custom` (in the KiCad project folder), so a
   part built once is reused;
3. otherwise a symbol named after the part is built from the boardview pins and added to
   `boardview_custom`. If that name exists with other pins, the new one is saved as `<name>_2`, `_3`...

A component of up to 3 pins answered with Enter takes the letters of its reference as the part (`R` for
R871, `C` for C2430): it gets KiCad's own `R` or `C` symbol when the pins match, otherwise a symbol of
that name is built. A bigger component answered with Enter is unknown and gets a generic symbol that only
depends on its pins (`GENERIC_8P`...).

Each placed component keeps the boardview text of **its own** description in the `Description` field.
For resistors, capacitors, inductors and ferrite beads the electrical value read from it (`RES 1K OHM
...` -> `1K`, `MLCC 0.1UF/10V ...` -> `0.1UF`) goes into `Value`; otherwise `Value` stays empty: the part
identifier is the name of the symbol, not its value.

## Adding a boardview format

A new format means ONE function in `boardview.py`, nothing else: it reads the file and returns a
`BoardviewData` (the unified format, `boardview_data.py`), and it is registered with a decorator:

```python
@reader(".ext")
def read_myformat(path: str) -> BoardviewData:
    data = BoardviewData(format_name="MYFORMAT", file=Path(path).name)
    ...                      # add components and pins ONLY with data.add_pin(...), in file order
    return data
```

The rest of the program only uses the derived views (`comp_to_pins`, `pin_to_net`, `net_to_pins`), so
the prompt, the file check and the checks work with the new extension by themselves. Rules:

- fill what the format contains and leave `None` for the rest (never invent a datum); every datum of
  the file goes into the unified format, however the original format stores it (for example a bill of
  materials kept in a separate block is merged into the components' description);
- keep the pin, pad and net names exactly as written in the file (a format that numbers a pin `1`
  where another writes `A1` keeps `1`): the symbols are built from those names;
- test points and vias are optional: add them as components when the format allows it, otherwise
  leave them out;
- lengths in millimetres; `BoardviewError` with a message for the technician when the file cannot
  be read;
- a format that needs a key or other secret must read it itself (for example from a file in the
  boardview data folder) and say where to put it when it is missing: never put one in the repository.

## Boardview data

Boardview files (`.cad`, `.fz`, ...) have unvalidated provenance and **must never be published**,
so they are kept **outside the repository**, in a folder that is not under git:

- by default the folder next to the repository named after it with a `_data` suffix
  (e.g. `Desktop\kicad-11-boardview-to-schematic` -> `Desktop\kicad-11-boardview-to-schematic_data`);
- or any folder you choose by setting the environment variable `BOARDVIEW_DATA_DIR`.

The folder is created, empty, the first time the program asks for the boardview file and does not
find it; the program tells you where it is. `data_location.py` refuses a folder inside the
repository, and `.gitignore` blocks `*.cad`, `*.fz` and `GL.bin` as a second safety net.

## Layout

| Path | Role |
|---|---|
| `interactive_session.py` | entry point (menu and orchestration) |
| `netlist_solver.py` | GENCAD parser and traversal rules; the session uses its "excluded net" rule (standard library only) |
| `connection_help.py` | explains KiCad connection problems instead of a traceback |
| `kicad_worker.py` | a single thread touches the KiCad API, everything goes through a queue |
| `net_check.py` | logic of the net checks |
| `net_policy.py` | how serious each finding is (GRAVE or NOTICE) and the automatic fixes |
| `part_identity.py` | which chip is it: part identifier from a boardview description; symbol names |
| `symbol_resolver.py` | chooses the symbol of a component: KiCad libraries, then ours, then a new one |
| `symbol_builder.py` | symbol geometry (single source) |
| `symbol_library.py` | writing of the `.kicad_sym` library and of the library-table row |
| `boardview.py`, `boardview_data.py` | boardview reading (netlist, component descriptions, coordinates) and unified data format |
| `data_location.py` | where the (unpublishable) boardview data folder is: outside the repository |
| `vendor/` | pre-built `kicad-python` wheel |

## Known limits

- The program does not draw wires: the technician does.
- The position of pins on components rotated by 90° or 270° follows KiCad's convention and has only
  been tried on a few components (180° is verified).
- It has been tried on a single boardview so far: another file may bring differences in naming or
  format that are not handled yet.
- The GENCAD format is the only one read today.

## Disclaimer

The program only reads the boardview files that you give it. Every boardview file used with this
program is the property of whoever runs it, or is used under that person's own rights: none is
supplied, hosted or endorsed by this project, and the author has **no responsibility** for those
files, for how they were obtained or for how they are used. The software is provided "as is",
without warranty of any kind (see the [MIT License](LICENSE)).

## Contributing

Issues are welcome, pull requests are not: see [CONTRIBUTING.md](CONTRIBUTING.md). You are free to
fork the code under the MIT License.

## License

Released under the [MIT License](LICENSE). The bundled `kicad-python` build in `vendor/` is also
MIT (© The KiCad Developers); see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
