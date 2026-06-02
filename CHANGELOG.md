# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-06-02

First public release.

### Added
- **Overview tab** — total cost, tokens, daily average, peak day, and a 30-day forecast; monthly cost trend, model cost split, daily detail, plus project & model tables.
- **Token trends tab** — stacked monthly tokens by model, top-model lines, and a 4-type token mix (input / output / cache-write / cache-read).
- **Pricing tab** — per-model unit prices you can edit; live recompute and next-30-day forecast. Defaults to official list prices; `--infer-prices` backs out your effective relay rates.
- Dark / light theme, English / 中文 i18n, and USD ⇄ CNY toggle with editable rate.
- Export charts as PNG, tables as CSV, and print to PDF — all offline (Chart.js inlined).
- Sortable & searchable tables; drill down by month or model.
- `refresh-dashboard.py` — cross-platform generator (`Path.home`, `--claude-dir`, `--output`, `--rate`, `--infer-prices`) that injects data into the template's `DATA` block idempotently.
- Demo data shipped in `template.html`; generated output is git-ignored to protect real spend figures.

[Unreleased]: https://github.com/Tangweiwei227/ai-usage-dashboard/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Tangweiwei227/ai-usage-dashboard/releases/tag/v1.0.0
