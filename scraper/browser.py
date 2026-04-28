from __future__ import annotations

import asyncio
import math
import random
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import BrowserContext, Page, async_playwright

from .grid import Cell

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MAP_TILE_SIZE = 256
MAPS_SEARCH_OFFSET_X_PX = -288
MAPS_SEARCH_OFFSET_Y_PX = 18


async def launch_context(profile_dir: Path, headless: bool = False) -> tuple[object, BrowserContext]:
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=headless,
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        viewport={"width": 1366, "height": 860},
        user_agent=USER_AGENT,
        args=["--disable-blink-features=AutomationControlled"],
    )
    return pw, context


async def handle_consent(page: Page) -> None:
    """Click the Google consent banner if one is present. Silent no-op otherwise."""
    selectors = [
        'button:has-text("Aceitar tudo")',
        'button:has-text("Accept all")',
        'button[aria-label*="Aceitar"]',
        'button[aria-label*="Accept all"]',
        'form[action*="consent"] button',
    ]
    for selector in selectors:
        try:
            btn = page.locator(selector).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=8000)
                return
        except Exception:
            continue


def _shift_web_mercator(lat: float, lon: float, zoom: int, dx_px: float, dy_px: float) -> tuple[float, float]:
    world_px = MAP_TILE_SIZE * (2 ** zoom)
    sin_lat = math.sin(math.radians(lat))
    x = (lon + 180.0) / 360.0 * world_px
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * world_px

    x = (x + dx_px) % world_px
    y = min(max(y + dy_px, 0.0), world_px)

    shifted_lon = x / world_px * 360.0 - 180.0
    merc = math.pi * (1 - 2 * y / world_px)
    shifted_lat = math.degrees(math.atan(math.sinh(merc)))
    return shifted_lat, shifted_lon


def build_search_url(query: str, cell: Cell) -> str:
    lat, lon = cell.center
    zoom = cell.zoom_for_maps()
    # Maps' search UI biases the requested map center under the left results pane.
    # Pre-shift in tile pixels so the visible search area lands on the cell center.
    lat, lon = _shift_web_mercator(lat, lon, zoom, MAPS_SEARCH_OFFSET_X_PX, MAPS_SEARCH_OFFSET_Y_PX)
    return f"https://www.google.com/maps/search/{quote(query)}/@{lat:.7f},{lon:.7f},{zoom}z/data=!4m2!2m1!6e5?hl=pt-BR"


async def goto_search(page: Page, query: str, cell: Cell) -> None:
    url = build_search_url(query, cell)
    clat, clon = cell.center
    print(f"  [nav→] center=({clat:.7f},{clon:.7f}) {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await handle_consent(page)
    # Maps never reaches networkidle (continuous tile fetches). Wait for the results
    # feed OR the "no results" banner — whichever comes first.
    try:
        await page.locator('div[role="feed"]').or_(
            page.get_by_text("O Google Maps não encontrou")
        ).first.wait_for(timeout=20000)
    except Exception:
        pass
    print(f"  [nav←] {page.url}")


async def is_captcha(page: Page) -> bool:
    url = page.url or ""
    if "/sorry/" in url or "consent.google.com/sorry" in url:
        return True
    try:
        if await page.locator("form#captcha-form, #recaptcha").count() > 0:
            return True
    except Exception:
        pass
    return False


async def wait_for_captcha_resolution(page: Page) -> None:
    print("\n[!] CAPTCHA detected. Solve it in the browser window, then press Enter here to continue...")
    await asyncio.get_event_loop().run_in_executor(None, input)


async def human_delay(min_ms: int = 800, max_ms: int = 2200) -> None:
    await asyncio.sleep(random.uniform(min_ms / 1000.0, max_ms / 1000.0))
