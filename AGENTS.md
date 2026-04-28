# AGENTS.md

Context for AI agents working on this repo. Read before editing.

## What this is

A standalone scraper that sweeps Google Maps across Brazil for a CLI-provided query, deduplicates results into an XLSX workbook, and serves a live Flask + Leaflet dashboard that paints cells purple as they complete. No official Places API.

## Stack

- Python 3.11/3.12, managed with `uv` (not pip directly). Run commands as `uv run python scrape.py ...`.
- Playwright (Chromium, persistent context at `data/chrome-profile/`, pt-BR locale, `America/Sao_Paulo` tz).
- Flask + Leaflet for the dashboard; polling endpoints `/api/cells` and `/api/places`.
- SQLite (WAL mode) for state at `data/state.db`; openpyxl streaming write for `data/results_<query>.xlsx`.
- Shapely for the Brazil-polygon clip.

## Run

```
uv run python scrape.py --query "farmácia"
uv run python scrape.py --query "intelbras" --bbox="-23.70,-46.80,-23.45,-46.50" --start-cell-km=30 --headless --no-dashboard
```

Key CLI flags: `--query` (required), `--bbox`, `--start-cell-km` (default 25), `--min-cell-km` (default 0.5), `--headless`, `--resume`, `--no-dashboard`, `--port`.

## Architecture

```
scrape.py                  CLI + orchestrator (scrape_loop)
scraper/browser.py         Playwright launch, consent, navigation
scraper/scroll.py          scroll_feed_to_end — scroll until end sentinel or stall
scraper/extract.py         parse_cards — DOM → PlaceRecord
scraper/grid.py            Cell dataclass, generate_initial_cells, clip_to_polygon, subdivide
scraper/store.py           SQLite DAO + XLSX writer (two locks: _lock for DB, _xlsx_lock for XLSX)
dashboard/app.py           Flask API
dashboard/static/app.js    Leaflet polling, cell coloring, place markers
data/                      brazil.geojson, state.db, chrome-profile/, results_*.xlsx
```

State transitions on a cell: `pending → in_progress → done | subdivided | error`. `subdivided` means 4 children were seeded and the parent is no longer itself scraped.

## Non-obvious behaviors (don't reinvent or break)

- **Navigation**: never `wait_for_load_state("networkidle")` on Maps — it never fires (continuous tile fetches). Wait for `div[role="feed"]` instead.
- **Coords**: sidebar anchor hrefs don't contain `/@lat,lon,`. The authoritative regex is `!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)` in the `data=` segment. `/@lat,lon,` is a fallback only.
- **CID**: `!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)` inside the href. Used as the primary dedupe key (`cid:<CID>`); fallback is `nml:<name>|<lat4>|<lon4>`.
- **Category/address order**: Google renders `<category> · <glyph> · <address>` on one line. Take the *first non-glyph* part as category, last as address. Glyphs are PUA chars in 0xE000–0xF8FF.
- **End-of-list sentinel**: Google uses "Você chegou ao **final** da lista" (also "fim da lista" historically, and the English variants). The regex in `scroll.py` must match all of these — missing one causes complete lists to be misclassified as cap-hit and needlessly subdivided.
- **Loading detection**: Google shows skeleton cards between batches. `scroll.py` treats "last feed child has no `/maps/place` anchor and isn't the end message" OR any `[role="progressbar"] / [aria-busy="true"]` as `loading=true`. Loading iterations must NOT increment the stall counter.
- **Cap detection**: a cell is considered capped only when stalled without the end sentinel AND `result_count >= CAP_THRESHOLD` (currently **85** in `scraper/scroll.py`). Below threshold → mark `done`. The threshold is tuned manually; do not raise it past ~100 (Google's actual hard cap).
- **Subdivision floor**: `scrape.py` calls `subdivide(cell, min_km=args.min_cell_km)`. No hardcoded floor above `MIN_CELL_KM` — previous bug had `> 1.5` which silently stopped recursion.
- **Two locks in `store.py`**: `_lock` for DB, `_xlsx_lock` for XLSX. Don't merge them — dashboard polling would block behind slow XLSX appends.
- **Primary key on cells**: `(query, cell_id)`, not `cell_id` alone. Multiple queries share the same DB.
- **Cell sizing**: `width_km` applies `cos(lat)` correction; `size_km = min(width, height)`. Child widths won't be exactly half of parent because cos(lat) differs at different midpoints — tests tolerate ~1e-3.

## Conventions

- No new comments unless the *why* is non-obvious.
- No backwards-compat shims, removed-code markers, or "used by X" breadcrumbs.
- Prefer editing existing files over creating new ones.
- Keep the dashboard JS stateless beyond `window.map`, `cellLayers`, `seenPlaces`, `lastPlaceId`.

## Testing

- Smoke: small bbox over a dense area (SP center `-23.70,-46.80,-23.45,-46.50`) with `--headless --no-dashboard`.
- Subdivision: run with a city bbox and `--min-cell-km=0.5`; expect recursion 2→1→0.5 km on dense queries.
- Intelbras regression (SP bbox above with `--start-cell-km=30`): cells should finish `done` with `hit_cap=0` around 78–98 results. If any come back `subdivided` on this query, the end-sentinel or loading detection in `scroll.py` has regressed.

## Gotchas

- Running without `--resume` still re-seeds but `seed_cells` is `INSERT OR IGNORE`, so existing cells aren't reset. Use a fresh query name (or delete `state.db`) to start clean.
- CAPTCHAs pause the scraper via `wait_for_captcha_resolution`; don't swallow them silently.
- Scraping Google Maps violates their ToS. This is a personal-use tool; no proxies, no stealth patches — intentionally the "pragmatic middle ground."
