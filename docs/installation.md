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

You should see ~150 tests pass. The two simulator-integration tests
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
sudo apt install wine64 winetricks
winecfg                                        # initialize default prefix
# Download LTspice installer from analog.com:
wget https://ltspice.analog.com/software/LTspice64.msi -O /tmp/LTspice64.msi
wine msiexec /i /tmp/LTspice64.msi
# After install, LTspice will live at:
#   ~/.wine/drive_c/Program Files/ADI/LTspice/LTspice.exe
```

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

The runner uses the `LTSPICE_PATH` env var first, then falls back to
`$PATH` and standard Wine install locations.

## Qucs-S (for `mcp-qucs-s`)

Qucs-S is not in apt; build from source:

```bash
sudo apt install build-essential cmake git pkg-config \
    qt6-base-dev qt6-svg-dev qt6-tools-dev \
    libqt6svgwidgets6 libqt6charts6-dev
git clone https://github.com/ra3xdh/qucs_s /tmp/qucs_s
cd /tmp/qucs_s && mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=$HOME/.local ..
make -j$(nproc)
make install
# binary: ~/.local/bin/qucs-s
```

For harmonic-balance support you also need Xyce:

```bash
# Pre-built debs at:
#   https://xyce.sandia.gov/downloads/index.html
# Or build from source — see Sandia's Xyce build guide.
```

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
