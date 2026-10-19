# Contributing to SeatSplit

Thank you for your interest in contributing! This project is intentionally small
and single-file, so the bar to entry is low.

## How to contribute

1. **Bug reports** — open a GitHub Issue. Include your distro/version, the
   exact error message or unexpected behaviour, and whether `--demo` mode
   reproduces the problem.

2. **Feature requests** — open an Issue first to discuss before coding.

3. **Pull requests**:
   - Fork the repo and create a feature branch (`git checkout -b my-fix`).
   - Make your changes to `multiseat_manager.py` (and the scripts if needed).
   - Test manually: `python3 multiseat_manager.py --demo` should run without
     errors and the new behaviour should work as expected.
   - Keep commits small and descriptive.
   - Open the PR against `main`.

## Code style

- Follow PEP 8 (max line length ~100 chars).
- The project avoids non-standard dependencies intentionally — please don't
  add new `import`s from PyPI.
- Write docstrings for any new functions or classes.
- Comment complex logic inline.

## Testing

There is no automated test suite yet. Manual testing with `--demo` mode covers
most UI paths without touching real hardware. For backend changes (udev /
loginctl calls) you will need a real Linux machine with multiple GPUs.

## Running in demo mode (no hardware needed)

```bash
python3 multiseat_manager.py --demo
```

This starts the app with fake GPUs, keyboards, and mice so you can test all UI
interactions without administrator access or special hardware.
