# Examples

End-to-end walkthroughs that exercise multiple tools together.

| Example | What it shows |
|---|---|
| [Basic LPF](basic-lpf.md) | 5th-order Butterworth low-pass filter at 1 GHz. Synthesis → vendor substitution → spec evaluation → Monte Carlo yield. Generic — no application-specific values. |

## Running your own designs privately

The repo gitignores three folders by convention so your in-progress
designs don't accidentally get pushed:

- `examples/halow_lpf/`
- `examples/private/`
- `examples/_local/`

Drop your `design.py` + `spec.json` into any of those and you can use
the full toolchain locally without worrying about visibility.
