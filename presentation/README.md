# presentation/

Higher-level overview of the suite — written for someone who wants to
understand what the project does without reading the source. Useful as
a project briefing, a talk handout, or a starting point for a deck.

## Files

| File | Format | Built by |
|---|---|---|
| [`OVERVIEW.md`](OVERVIEW.md) | Markdown source of truth | hand-written |
| [`OVERVIEW.docx`](OVERVIEW.docx) | Word distributable, with TOC | `pandoc OVERVIEW.md -o OVERVIEW.docx --toc -V geometry:margin=1in` |
| [`OVERVIEW.pptx`](OVERVIEW.pptx) | 10-slide 16:9 deck | `uv run python build_pptx.py` |
| [`assets/hero.png`](assets/hero.png) | Capability fan-out chart | `uv run python build_charts.py` |
| [`assets/demo1_lpf.png`](assets/demo1_lpf.png) | RF LPF response + Monte Carlo yield | `build_charts.py` |
| [`assets/demo2_emc.png`](assets/demo2_emc.png) | SMPS conducted emissions vs CISPR 32 Class B | `build_charts.py` |
| [`assets/demo3_sallen_key.png`](assets/demo3_sallen_key.png) | 4th-order Sallen-Key response + op-amp pick | `build_charts.py` |
| [`assets/demo1_1ghz_lpf.asc`](assets/demo1_1ghz_lpf.asc) | LTspice schematic (opens in LTspice) | copied from `examples/basic_lpf/` |
| [`assets/demo1_1ghz_lpf.s2p`](assets/demo1_1ghz_lpf.s2p) | 1001-point Touchstone S-parameters | copied from `examples/basic_lpf/` |
| [`assets/demo1_1ghz_lpf.schematic.png`](assets/demo1_1ghz_lpf.schematic.png) | Auto-rendered ladder schematic | copied from `examples/basic_lpf/` |

## Rebuilding everything from source

```bash
uv run python presentation/build_charts.py     # regenerates the 4 PNGs
uv run python presentation/build_pptx.py       # rebuilds OVERVIEW.pptx
pandoc presentation/OVERVIEW.md \
       -o presentation/OVERVIEW.docx \
       --toc --toc-depth=2 \
       -V geometry:margin=1in                  # rebuilds OVERVIEW.docx
```

The chart builder runs the actual MCP tools end-to-end (no fakery), so
the numbers in the deck always reflect what the code does today.
