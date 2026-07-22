# Installation

The Python package side is straightforward: `uv sync --all-packages`
and you have all four packages installed in editable mode along with
the dev tools.

The simulators are optional but enable additional functionality.

## Python deps

Tested on Python 3.11, 3.12, 3.13.

```bash
git clone https://github.com/RFingAdam/mcp-ltspice-qucs
cd mcp-ltspice-qucs
uv sync --all-packages
uv run pytest -q
```

You should see ~180 tests pass. The two simulator-integration tests
skip until you install ngspice or LTspice (below).

If you don't want the dev tools (ruff / mypy / pytest / mkdocs), pass
`--no-dev` to `uv sync`.

## ngspice (Linux native)

The `mcp-ltspice` server runs ngspice as its primary fallback. ngspice
is in most distro repos:

```bash
sudo apt install ngspice ngspice-doc        # Debian / Ubuntu
sudo dnf install ngspice                    # Fedora
sudo pacman -S ngspice                       # Arch
brew install ngspice                         # macOS (Homebrew)
```

Verify it runs:

```bash
ngspice --version          # should print 44.x or newer
```

Once ngspice is on `$PATH`, the runner's auto-detection picks it up
and the `@pytest.mark.ngspice` tests run.

## LTspice via Wine (Linux / macOS)

LTspice is the *preferred* simulator when working with `.asc` files
because it preserves subcircuit and `.meas` directive semantics that
ngspice approximates loosely. On Linux/macOS, install through Wine.

### Linux

```bash
sudo apt install wine winetricks
wineboot -u                                    # initialize default prefix
# Download LTspice installer from analog.com:
wget https://ltspice.analog.com/software/LTspice64.msi -O /tmp/LTspice64.msi
wine msiexec /i /tmp/LTspice64.msi /qn         # /qn = silent
# After install, LTspice will live at:
#   ~/.wine/drive_c/Program Files/ADI/LTspice/LTspice.exe
```

#### First run blocks batch mode

**Answer LTspice's first-run prompt before using it headlessly.** Recent
releases open a modal *"Anonymously Share LTspice Usage Data"* dialog the
first time they run in a Wine prefix. It appears even under `-b`, and since
batch mode has nobody to click it, the process blocks until the caller's
timeout expires, leaving an empty log and no `.raw`. Nothing in the symptom
points at a consent prompt.

Either launch it once interactively and answer the dialog:

```bash
wine ~/.wine/drive_c/Program\ Files/ADI/LTspice/LTspice.exe
```

…or pre-seed the setting, which also opts out of telemetry — the better
option for CI and headless boxes:

```bash
printf '[Options]\nCaptureAnalytics=false\n' \
    > ~/.wine/drive_c/users/$USER/AppData/Roaming/LTspice.ini
```

`mcp-ltspice` checks for that file before every LTspice run: it warns up
front when the file is missing, and if the run then times out it says so
explicitly instead of surfacing a bare `TimeoutExpired`.

### macOS

LTspice has a native macOS build:

```bash
brew install --cask ltspice
# binary: /Applications/LTspice.app/Contents/MacOS/LTspice
```

### Verify

```bash
echo "LTSPICE_PATH=$(find ~/.wine -iname 'LTspice.exe' 2>/dev/null | head -1)"
export LTSPICE_PATH=...    # set in your shell rc, or pass to MCP server
uv run pytest -m ltspice
```

The runner uses the `LTSPICE_PATH` env var first, then `$PATH`, then the
standard install locations for Windows, macOS, and Wine (honouring
`WINEPREFIX` if you use a non-default prefix). If `LTSPICE_PATH` is set
but doesn't point at a real file, the runner logs a warning and keeps
looking — it will not silently fall back to ngspice without telling you.

### A note on exit codes

**LTspice under Wine exits with code 1 even on a successful run.** The
runner therefore treats the presence of the `.raw` output as the success
condition, not the return code; a nonzero code is logged as a warning
and reported in the envelope's `returncode` field. Any stale `.raw` is
deleted before each run, so a leftover file from a previous invocation
can't be mistaken for a fresh result. The same policy applies to ngspice
and matches `mcp-qucs-s`, which has always keyed on artifact presence.

If you see `did not produce <file>.raw`, that is a genuine failure — the
error carries the exact command, the return code, and the path to the
full log.

### Choosing a simulator

`MCP_LTSPICE_SIMULATOR` pins the choice for deployments that want one
specific engine — most usefully an ngspice-only box that shouldn't pay
for Wine at all:

```bash
export MCP_LTSPICE_SIMULATOR=ngspice   # or: ltspice
```

An explicit `prefer=` argument on `run_simulation` still wins over the
env var. When the pinned simulator isn't installed the call fails with a
clear error rather than quietly using the other one.

## Qucs-S (for `mcp-qucs-s`)

Qucs-S is not in apt; build from source:

```bash
sudo apt install build-essential cmake git pkg-config \
    qt6-base-dev qt6-svg-dev qt6-tools-dev \
    libqt6svgwidgets6 qt6-charts-dev \
    flex bison gperf dos2unix
# --recurse-submodules is required: the simulation engine (qucsator-RF)
# is a submodule. Without it cmake still configures and you get the GUI
# but no simulator, which surfaces much later as "Qucs-S not installed".
git clone --recurse-submodules https://github.com/ra3xdh/qucs_s /tmp/qucs_s
cd /tmp/qucs_s && mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=$HOME/.local ..
make -j$(nproc)
make install
# binaries: ~/.local/bin/qucs-s and ~/.local/bin/qucsator_rf
```

`flex`, `bison`, `gperf` and `dos2unix` are all build-time requirements of
qucsator-RF. Missing ones fail late and unhelpfully — `gperf` aborts at
cmake time, `dos2unix` only at ~78% of the build with
`/bin/sh: 1: dos2unix: not found`.

### The engine, not the GUI

`qucs-s` is the Qt GUI; `qucsator_rf` is the simulation engine, and it is
the one `mcp-qucs-s` invokes. Handing a netlist to the GUI opens a window
and blocks forever on a headless machine, so discovery deliberately looks
for `qucsator_rf` / `qucsator` and never falls back to `qucs-s`.

If you have the GUI but no engine — the usual result of cloning without
`--recurse-submodules` — the server says so explicitly rather than hanging.
Check with:

```bash
qucsator_rf --version        # engine; this is the one that matters
```

Set `QUCS_S_PATH` to point directly at the engine binary if it lives
somewhere non-standard.

Already cloned without submodules? No need to start over:

```bash
cd /tmp/qucs_s && git submodule update --init --recursive
```

For harmonic-balance support you also need Xyce:

**There is no working binary route on Debian/Ubuntu.** Sandia's download
page states that its Linux binaries are RHEL 8 RPMs which "will **not**
work on systems such as Debian or Ubuntu", and that the team no longer
provides open-source binaries at all. The GitHub releases carry only
release-notes PDFs. Build from source:

```bash
# Build deps (all in the Ubuntu archive). GCC 13 is required — see below.
sudo apt install cmake g++ gfortran make bison flex libfl-dev \
    libfftw3-dev libsuitesparse-dev libblas-dev liblapack-dev libtool git \
    gcc-13 g++-13 gfortran-13

# Trilinos >= 14.4 (Xyce 7.10's CMake build requires it)
curl -L -o trilinos.tar.gz \
  https://github.com/trilinos/Trilinos/archive/refs/heads/trilinos-release-14-4-branch.tar.gz
tar xzf trilinos.tar.gz
git clone --depth 1 --branch Release-7.10.0 https://github.com/Xyce/Xyce.git Xyce-src

mkdir tri-build && cd tri-build
cmake -C ../Xyce-src/cmake/trilinos/trilinos-base.cmake \
  -D CMAKE_INSTALL_PREFIX=/usr/local/trilinos_serial -D CMAKE_BUILD_TYPE=Release \
  -D CMAKE_C_COMPILER=gcc-13 -D CMAKE_CXX_COMPILER=g++-13 \
  -D CMAKE_Fortran_COMPILER=gfortran-13 \
  -D CMAKE_CXX_FLAGS="-O3 -fPIC" -D CMAKE_C_FLAGS="-O3 -fPIC" \
  -D CMAKE_Fortran_FLAGS="-O3 -fPIC -fallow-argument-mismatch" \
  -D Trilinos_ENABLE_Stokhos=OFF -D Trilinos_ENABLE_ROL=OFF \
  -D Trilinos_ENABLE_TESTS=OFF -D Trilinos_ENABLE_EXAMPLES=OFF \
  -D BUILD_SHARED_LIBS=OFF -D TPL_ENABLE_MPI=OFF \
  -D TPL_ENABLE_AMD=ON -D AMD_LIBRARY_DIRS=/usr/lib/x86_64-linux-gnu \
  -D TPL_AMD_INCLUDE_DIRS=/usr/include/suitesparse \
  -D TPL_ENABLE_BLAS=ON -D TPL_ENABLE_LAPACK=ON \
  ../Trilinos-trilinos-release-14-4-branch
make -j8 && sudo make install

mkdir ../xyce-build && cd ../xyce-build
cmake -D CMAKE_INSTALL_PREFIX=/usr/local/xyce_serial \
      -D Trilinos_ROOT=/usr/local/trilinos_serial \
      -D CMAKE_BUILD_TYPE=Release \
      -D CMAKE_C_COMPILER=gcc-13 -D CMAKE_CXX_COMPILER=g++-13 \
      -D CMAKE_CXX_FLAGS="-O3" -D BUILD_SHARED_LIBS=OFF ../Xyce-src
cmake --build . -j 8 && sudo cmake --install .

sudo ln -sf /usr/local/xyce_serial/bin/Xyce /usr/local/bin/Xyce
sudo ln -sf /usr/local/xyce_serial/bin/Xyce /usr/local/bin/xyce
```

About 17 minutes of compiling on 8 cores; 7.2 GB of build tree, 470 MB
installed. Trilinos links statically, so nothing beyond stock system
libraries is needed at runtime.

Gotchas worth knowing before you start:

- **GCC 15 does not work.** Ubuntu 25.10's default g++ 15.2 fails Trilinos
  14.4 in `kokkos-kernels`, where GCC 15's `-Wtemplate-body` diagnoses a
  real bug in an uninstantiated template. Pass `gcc-13`/`g++-13` explicitly
  to *both* builds.
- **Distro Trilinos is unusable.** Ubuntu ships 13.2.0, and the Debian
  packaging does not enable the `EpetraExt` / `Amesos KLU` /
  `COMPLEX_DOUBLE` options Xyce needs.
- **Don't build in `/tmp`** if it is a tmpfs — the build tree is 7.2 GB.
- `-j8` rather than `-j16` on a 16 GB box; Kokkos template instantiation
  peaks around 9 GB.
- Xyce writes its result files **next to the netlist**, not into the working
  directory. `mcp-qucs-s` gives every run its own directory for this reason.

### Verify

```bash
Xyce -v      # Xyce Release 7.10.0-opensource
uv run pytest -m xyce
```

### Harmonic-balance gotchas

- `.HB` takes one `NUMFREQ` entry **per tone**. A single value with two
  tones aborts with "The size of numFreq does not match the number of tones
  in .hb!". `run_harmonic_balance` handles this for you.
- Use explicit multiplication in behavioural `B`-source expressions —
  `V(in)*V(in)*V(in)`, not `V(in)^3`. The `^` form makes Xyce's HB startup
  transient diverge with "Time step too small".
- `.PRINT HB_FD` output is a **two-sided** spectrum; single-sided amplitude
  at a positive frequency is twice the magnitude in that row.

The synthesis tools in `mcp-qucs-s` (microstrip, couplers, Richards
transform) are pure-Python closed-form and work without Qucs-S
installed. Tools that need a real simulator (`run_sp_analysis`,
`run_harmonic_balance`, `extract_noise_parameters`,
`export_touchstone`) detect the missing binary and return an envelope
with `status="error"` and a clear install hint, rather than crashing.

## Pre-commit hooks (optional)

```bash
uv run pre-commit install
```

This runs ruff format + ruff check + mypy on every commit.

## Troubleshooting

**`ModuleNotFoundError: No module named 'rf_mcp_common'` after first
`uv sync`** — pass `--all-packages`:

```bash
uv sync --all-packages
```

The default `uv sync` only resolves the workspace root's dependencies;
adding `--all-packages` installs every member into the venv.

**`LTspice did not produce ...raw` from the runner** — the most common
cause on Linux is that Wine's display server tried to pop up the
LTspice GUI. Check `wine LTspice.exe -b -Run yourfile.asc` directly;
if you see Wine errors about X11 / display, install `xvfb` and run
under it (`xvfb-run uv run python ...`).

**`importlib.resources` can't find the `bands/` JSON files** — make
sure you `uv sync --all-packages --reinstall-package mcp-rf-analysis`
after pulling fresh source so the editable install re-discovers the
bundled resources.

**`File voltage.asy not found` from the ngspice runner** — fixed in
0.1.1: the runner now emits ngspice netlists directly from our `.asc`
generator output, bypassing spicelib's symbol-library lookup which
needed LTspice installed.
