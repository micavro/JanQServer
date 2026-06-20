# Repository Guidelines

## Project Structure & Module Organization

JanQ Lab is a Python 3.11+ research toolkit packaged from `src/`. New Python code belongs under `src/janq_lab`: `assets` parses copied client tables, `model` holds hand/scoring/simulation logic, `strategy` contains decision policies, `analysis` runs experiments, `probe` parses captured events, `automation` drives live runs, and `visualization` emits HTML reviews. Operational scripts live in `scripts/` and root `start_*.ps1` launchers. The C# BepInEx plugin is in `plugin/JanqProbe`. Copied client files and extracted assets are under `sega_net_MJ/`, `JanQcore/`, and `allresourse/`; generated logs, sessions, bridge files, and reports belong under `_runtime/`.

## Build, Test, and Development Commands

Set imports before running modules from a checkout:

```powershell
$env:PYTHONPATH = "src"
```

Useful commands:

```powershell
python -m unittest discover -s tests
python -m janq_lab.assets.nyukyu
python -m janq_lab.assets.special
python -m janq_lab.analysis.monte_carlo --hands 1000 --seed 20260612 --strategy public
python -m janq_lab.automation.bot --config automation.example.yaml --mode dry_run
dotnet build plugin\JanqProbe\JanqProbe.csproj -c Release
```

Use `.\start_account_batch.ps1` for sequential account batches and `.\start_janq_bot.ps1` for one live bot session.

## Coding Style & Naming Conventions

Use 4-space Python indentation, `snake_case` for modules/functions/variables, and `PascalCase` for classes. Prefer typed signatures and frozen dataclasses for immutable simulation records. Keep command modules runnable with `python -m janq_lab.<package>.<module>`. For C#, keep nullable references enabled and follow existing `PascalCase` member/type names.

## Testing Guidelines

Place Python tests under `tests/`, mirroring package paths where practical. Name files `test_*.py` and use `unittest` unless another runner is introduced. Use deterministic seeds for simulation tests. Keep generated artifacts in `_runtime/`, not source directories.

## Commit & Pull Request Guidelines

Git history is not required on experiment servers. When committing elsewhere, use concise imperative subjects such as `Add route EV regression test`. Pull requests should describe behavior changes, list commands run, link related issues or captures, and include replay/report paths when visualization output changes.

## Security & Configuration Tips

Do not commit account data, tokens, copied runtime logs, or generated `_runtime` outputs. Put live accounts in `_runtime/accounts/accounts.json` and use `automation.example.yaml` as the local configuration template.
