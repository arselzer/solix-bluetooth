# Repository Guidelines

## Project Structure & Module Organization

The Vue 3 browser app lives in `src/`: UI panels are in `src/components/`, and Bluetooth framing, cryptography, and telemetry decoding are in `src/protocol/`. Static icons are in `public/`. `tools/decode-capture.ts` analyzes saved captures. The installable Python package is in `python/solix_gen2/`; its tests are in `python/tests/`. Protocol findings and observed device behavior belong in `docs/`, especially `docs/gen2-protocol.md`. Keep device-specific protocol logic out of UI components.

## Build, Test, and Development Commands

- `npm ci` installs the locked browser dependencies.
- `npm run dev` starts the Vite development server; `npm run build` type-checks with `vue-tsc` and creates `dist/`.
- `python3 -m pip install -e './python[server,mqtt]'` installs the CLI, HTTP server, and MQTT bridge for local development.
- `PYTHONPATH=python python3 -m pytest python/tests -q` runs the Python tests. Install `pytest` separately if needed.

## Coding Style & Naming Conventions

Follow the surrounding code: two-space indentation and semicolons in TypeScript/Vue, four-space indentation and type hints in Python. Use `PascalCase.vue` for components, `snake_case.py` and `test_*.py` for Python modules and tests, and descriptive names for protocol fields. No formatter or linter is configured; use `npm run build` for frontend type checks and keep patches consistent with nearby files.

## Testing Guidelines

Python tests use `pytest` and synthetic packet fixtures. Add focused tests when changing packet encoding, telemetry offsets, or server behavior. The browser app has no automated test suite; verify UI changes with the Vite server and include a screenshot for visible changes. Live device tests require a recorded baseline, telemetry confirmation, and restoration of changed settings. The C2000 powers servers: never toggle its AC output during tests.

## Commit & Pull Request Guidelines

Recent commits use short imperative subjects such as `Fix fragment assembly...` and `Add capture decoder tool...`; follow that pattern. Pull requests should explain the behavior changed, devices and firmware tested, commands run, and any remaining protocol uncertainty. Link an issue when one exists and add screenshots for UI changes.

## Security & Configuration

Keep pairing IDs, credentials, phone captures, and raw private logs in the ignored `.solix-private/` directory with restricted permissions. Do not commit or paste them into issues or pull requests. Use sanitized examples in documentation.
