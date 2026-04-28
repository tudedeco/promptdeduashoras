from __future__ import annotations

import argparse
import asyncio
import re
import signal
import sys
import threading
import webbrowser
from pathlib import Path

import requests

from scraper.browser import (
    goto_search,
    handle_consent,
    human_delay,
    is_captcha,
    launch_context,
    wait_for_captcha_resolution,
)
from scraper.extract import parse_cards
from scraper.grid import (
    Cell,
    clip_to_polygon,
    generate_initial_cells,
    load_brazil_polygon,
    subdivide,
)
from scraper.scroll import scroll_feed_to_end
from scraper.store import Store

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
GEOJSON_PATH = DATA_DIR / "brazil.geojson"
STATE_DB = DATA_DIR / "state.db"
PROFILE_DIR = DATA_DIR / "chrome-profile"
DENSE_SUBDIVIDE_THRESHOLD = 60

BRAZIL_GEOJSON_URLS = [
    "https://raw.githubusercontent.com/mledoze/countries/master/data/bra.geo.json",
    "https://raw.githubusercontent.com/codeforamerica/click_that_hood/master/public/data/brazil-states.geojson",
]

_stop_event = threading.Event()


def parse_positive_int(s: str) -> int:
    try:
        value = int(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError("value must be an integer") from e
    if value < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return value


def _slug(q: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", q.strip().lower()).strip("_")
    return s or "query"


def ensure_brazil_geojson() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if GEOJSON_PATH.exists() and GEOJSON_PATH.stat().st_size > 1000:
        return GEOJSON_PATH
    for url in BRAZIL_GEOJSON_URLS:
        try:
            print(f"[geo] downloading Brazil border from {url}")
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            GEOJSON_PATH.write_bytes(r.content)
            print(f"[geo] saved to {GEOJSON_PATH}")
            return GEOJSON_PATH
        except Exception as e:
            print(f"[geo] failed: {e}")
            continue
    raise SystemExit(
        f"[geo] Could not download Brazil GeoJSON. Place a polygon file at {GEOJSON_PATH} manually."
    )


def parse_bbox(s: str) -> tuple[float, float, float, float]:
    raw = [x.strip() for x in s.split(",")]
    if len(raw) != 4:
        raise argparse.ArgumentTypeError("--bbox must be 'min_lat,min_lon,max_lat,max_lon'")
    try:
        min_lat, min_lon, max_lat, max_lon = (float(x) for x in raw)
    except ValueError as e:
        raise argparse.ArgumentTypeError("--bbox values must be valid numbers") from e
    if min_lat >= max_lat or min_lon >= max_lon:
        raise argparse.ArgumentTypeError("--bbox must satisfy min_lat < max_lat and min_lon < max_lon")
    return min_lat, min_lon, max_lat, max_lon


def seed_grid(store: Store, start_km: float, bbox: tuple | None) -> int:
    if bbox is None:
        poly = load_brazil_polygon(ensure_brazil_geojson())
        cells = generate_initial_cells(km=start_km)
        cells = clip_to_polygon(cells, poly)
    else:
        from scraper import grid as _g
        saved = _g.BRAZIL_BBOX
        _g.BRAZIL_BBOX = bbox
        try:
            cells = generate_initial_cells(km=start_km)
        finally:
            _g.BRAZIL_BBOX = saved
    return store.seed_cells(cells)


def _profile_dir_for_instance(instance_id: int, instances: int) -> Path:
    if instances == 1 or instance_id == 1:
        return PROFILE_DIR
    return DATA_DIR / f"chrome-profile-{instance_id}"


async def scrape_loop(store: Store, headless: bool, start_km: float, min_km: float, instances: int) -> None:
    active_cells: set[str] = set()
    tasks = [
        asyncio.create_task(
            scrape_worker(store, headless, min_km, instance_id=i, instances=instances, active_cells=active_cells)
        )
        for i in range(1, instances + 1)
    ]
    await asyncio.gather(*tasks)


async def scrape_worker(
    store: Store,
    headless: bool,
    min_km: float,
    instance_id: int,
    instances: int,
    active_cells: set[str],
) -> None:
    worker = f"w{instance_id}"
    profile_dir = _profile_dir_for_instance(instance_id, instances)
    pw, context = await launch_context(profile_dir, headless=headless)
    page = await context.new_page()

    # Warm up — load maps once so any consent dialog is handled up front.
    try:
        await page.goto("https://www.google.com/maps?hl=pt-BR", wait_until="domcontentloaded", timeout=30000)
        await handle_consent(page)
    except Exception as e:
        print(f"[warn] initial load: {e}")
    await human_delay(600, 1200)

    try:
        while not _stop_event.is_set():
            cell = store.next_pending_cell()
            if cell is None:
                summary = store.cells_summary()
                if active_cells:
                    await human_delay(500, 900)
                    continue
                print(f"[done:{worker}] no more pending cells. {summary}")
                _stop_event.set()
                break
            print(f"[cell:{worker}] {cell.cell_id} ~{cell.size_km:.1f}km")
            active_cells.add(cell.cell_id)
            try:
                await goto_search(page, store.query, cell)
                if await is_captcha(page):
                    await wait_for_captcha_resolution(page)

                count, should_subdivide = await scroll_feed_to_end(page)
                try:
                    records = await parse_cards(page)
                except Exception:
                    await human_delay(600, 1200)
                    records = await parse_cards(page)
                in_cell = [
                    r for r in records
                    if r.lat is not None and r.lon is not None
                    and cell.contains_point(r.lat, r.lon)
                ]
                dropped = len(records) - len(in_cell)
                in_count = len(in_cell)
                new_rows = store.upsert_places(in_cell, cell.cell_id)
                dense_subdivide = in_count > DENSE_SUBDIVIDE_THRESHOLD
                subdivide_requested = in_count > 0 and (should_subdivide or dense_subdivide)

                if subdivide_requested:
                    children = subdivide(cell, min_km=min_km)
                    if children:
                        store.seed_cells(children)
                        store.mark_cell_subdivided(cell.cell_id, in_count, hit_cap=should_subdivide)
                        child_km = children[0].size_km
                        reason = "scroll cap/loading" if should_subdivide else f">{DENSE_SUBDIVIDE_THRESHOLD} places"
                        print(f"  [{worker}] -> {in_count} in-cell results (dropped {dropped} outside), subdivided into 4 ~{child_km:.2f}km ({reason}, new places: {new_rows})")
                    else:
                        store.mark_cell_done(cell.cell_id, in_count, should_subdivide)
                        print(f"  [{worker}] -> {in_count} in-cell results (dropped {dropped} outside), subdivision requested but at floor {min_km}km, stopping (new places: {new_rows})")
                else:
                    hit_cap = should_subdivide and in_count > 0
                    store.mark_cell_done(cell.cell_id, in_count, hit_cap)
                    note = ", skipped subdivision because no in-cell results" if should_subdivide and in_count == 0 else ""
                    print(f"  [{worker}] -> {in_count} in-cell results (dropped {dropped} outside){note} (new places: {new_rows})")
                await human_delay(1500, 3500)
            except Exception as e:
                print(f"  [{worker}] !! error: {e}")
                store.mark_cell_error(cell.cell_id, str(e))
                await human_delay(2000, 4000)
            finally:
                active_cells.discard(cell.cell_id)
    finally:
        try:
            await context.close()
        except Exception:
            pass
        try:
            await pw.stop()
        except Exception:
            pass


def start_dashboard(store: Store, port: int) -> threading.Thread:
    from dashboard.app import create_app

    app = create_app(store)

    def run():
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)

    t = threading.Thread(target=run, name="dashboard", daemon=True)
    t.start()
    return t


def main() -> None:
    ap = argparse.ArgumentParser(description="Google Maps Brazil sweep scraper")
    ap.add_argument("--query", required=True, help='Search term, e.g. "farmácia"')
    ap.add_argument("--headless", action="store_true", help="Run Chromium headless")
    ap.add_argument("--resume", action="store_true", help="Resume existing run (skip seeding)")
    ap.add_argument("--reset", action="store_true", help="Clear this query's saved cells/places and start it again")
    ap.add_argument("--no-dashboard", action="store_true", help="Don't start local web dashboard")
    ap.add_argument("--instances", type=parse_positive_int, default=1,
                    help="How many Google Maps browser instances to run in parallel (default 1)")
    ap.add_argument("--start-cell-km", type=float, default=25.0, help="Initial cell size in km (default 25)")
    ap.add_argument("--min-cell-km", type=float, default=0.5,
                    help="Stop recursive subdivision below this cell size in km (default 0.5)")
    ap.add_argument("--bbox", type=parse_bbox, default=None,
                    help="Restrict to bbox 'min_lat,min_lon,max_lat,max_lon' (testing)")
    ap.add_argument("--port", type=int, default=5000, help="Dashboard port (default 5000)")
    args = ap.parse_args()
    if args.reset and args.resume:
        ap.error("--reset cannot be used with --resume")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    xlsx_path = DATA_DIR / f"results_{_slug(args.query)}.xlsx"
    store = Store(db_path=STATE_DB, xlsx_path=xlsx_path, query=args.query)

    if args.reset:
        reset_cells, reset_places = store.reset_query()
        print(f"[reset] cleared {reset_cells} cells and {reset_places} places for query {args.query!r}")

    if not args.resume:
        n = seed_grid(store, start_km=args.start_cell_km, bbox=args.bbox)
        seed_note = "fresh cells" if args.reset else "new cells (existing pending cells kept)"
        print(f"[seed] inserted {n} {seed_note}")
    else:
        reverted = store.reset_in_progress()
        print(f"[resume] reverted {reverted} in_progress cells to pending")

    summary = store.cells_summary()
    print(f"[state] cells: {summary}")
    print(f"[state] xlsx: {xlsx_path}")
    print(f"[state] browser instances: {args.instances}")

    if not args.no_dashboard:
        start_dashboard(store, args.port)
        url = f"http://127.0.0.1:{args.port}/?query={args.query}"
        print(f"[dash] {url}")
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def _sigint(_sig, _frm):
        print("\n[exit] stopping after current cell...")
        _stop_event.set()
    signal.signal(signal.SIGINT, _sigint)

    try:
        asyncio.run(scrape_loop(
            store,
            headless=args.headless,
            start_km=args.start_cell_km,
            min_km=args.min_cell_km,
            instances=args.instances,
        ))
    except KeyboardInterrupt:
        pass
    finally:
        store.close()
        print("[exit] bye")


if __name__ == "__main__":
    main()
