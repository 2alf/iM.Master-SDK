# Contributing to immaster

Thanks for your interest! This project is **meant** to be hacked on.

## Ground rules

- **The core stays pure.** `immaster/protocol.py` and `immaster/agent.py` must not
  import any hardware or third-party BLE library. Everything hardware-facing lives
  behind `immaster/driver.py`. This is what lets the whole decision loop be tested
  on a laptop. Please keep it that way.
- **New optional features get an extra.** If a module needs a third-party package
  (Pillow, Vosk, numpy, mcp), import it *lazily inside the function* and add it to
  the right `[project.optional-dependencies]` group in `pyproject.toml`, so the
  core install stays stdlib-only.
- **Everything runs dry.** New example behaviors should honor `IMMASTER_DRY_RUN=1`
  (or accept a `DryRobot`) so people can try them without a robot.

## Getting set up

```bash
git clone https://github.com/2alf/immasterSDK.git
cd immasterSDK
pip install -e ".[dev]"
pytest -q
```

The tests are pure logic (protocol, agent, llm, camera) and need no hardware.
**They must pass before you open a PR.**

## Testing on the actual robot

Robot control requires Linux + raw BLE HCI. Always test a new
behavior first with `IMMASTER_DRY_RUN=1`, then on the robot in open space with the
safety watchdog enabled.

## Pull requests

1. Branch off `main`.
2. Keep changes focused; add or update tests for anything in `protocol` / `agent`.
3. Run `pytest -q` and make sure it's green.
4. Describe *what* changed and *why*, and note whether you tested on real hardware.

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
