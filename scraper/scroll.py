from __future__ import annotations

import asyncio

from playwright.async_api import Page

FEED_SELECTOR = 'div[role="feed"]'
CAP_THRESHOLD = 85
# How many idle polls (no growth, no loader) before we conclude the feed is stuck.
# 0.65s per poll; 6 gives ~4s grace after the last change.
MAX_STALL_ITERATIONS = 6
# How many loader-only polls (still loading, no growth) before we force
# subdivision to escape an indefinitely searching cell.
MAX_LOADING_STALL_ITERATIONS = 8
MAX_SCROLL_ITERATIONS = 120
LOADING_POLL_SECONDS = 0.45
SCROLL_POLL_SECONDS = 0.65

# Regexes that catch all end-of-list strings Google has used recently.
END_SENTINEL_JS = r"""/(?:chegou ao (?:fim|final) da lista|end of the list|fim (?:da )?lista de resultados)/i"""

_SAMPLE_JS = r"""
() => {
  const feed = document.querySelector('div[role="feed"]');
  if (!feed) return { noFeed: true };
  const anchors = feed.querySelectorAll('a[href*="/maps/place/"]').length;
  const text = (feed.innerText || '');
  const endSentinel = END_RE.test(text);
  // Loader detection: Google renders skeleton shimmer cards or a spinner while
  // fetching more. Signals we check:
  //  - any element with a spinning/progress role
  //  - the feed's last child has no place anchor inside it AND the feed is
  //    still clearly below its natural end (no end sentinel).
  const progressbars = feed.querySelectorAll('[role="progressbar"], [aria-busy="true"]').length;
  const last = feed.lastElementChild;
  const lastHasAnchor = !!(last && last.querySelector && last.querySelector('a[href*="/maps/place/"]'));
  const lastText = last ? (last.innerText || '').trim() : '';
  const lastIsEndMsg = END_RE.test(lastText);
  const loading = progressbars > 0 || (!endSentinel && last && !lastHasAnchor && !lastIsEndMsg);
  return {
    anchors,
    endSentinel,
    loading,
    scrollHeight: feed.scrollHeight,
    scrollTop: feed.scrollTop,
  };
}
""".replace("END_RE", END_SENTINEL_JS)


async def _sample(page: Page) -> dict:
    try:
        return await page.evaluate(_SAMPLE_JS)
    except Exception:
        return {"noFeed": True}


async def scroll_feed_to_end(page: Page) -> tuple[int, bool]:
    """
    Scroll the Google Maps result feed until the end-of-list sentinel appears
    or the feed gets stuck.

    Returns (final_card_count, should_subdivide_flag).
    """
    try:
        await page.wait_for_selector(FEED_SELECTOR, timeout=12000)
    except Exception:
        return 0, False

    last_count = 0
    last_height = 0
    stall = 0
    loading_stall = 0

    for _ in range(MAX_SCROLL_ITERATIONS):
        s = await _sample(page)
        if s.get("noFeed"):
            return last_count, False

        count = s.get("anchors", 0)
        end = bool(s.get("endSentinel"))
        loading = bool(s.get("loading"))
        height = s.get("scrollHeight", 0)

        if end:
            return count, False

        progressed = count > last_count or height > last_height
        if progressed:
            stall = 0
            loading_stall = 0
        elif loading:
            loading_stall += 1
        else:
            stall += 1
            loading_stall = 0

        last_count = count
        last_height = height

        if loading_stall >= MAX_LOADING_STALL_ITERATIONS:
            return count, True

        if stall >= MAX_STALL_ITERATIONS:
            # Truly stalled with no loader and no end sentinel. Treat as cap hit
            # only if we reached the threshold; otherwise it's likely complete.
            return count, count >= CAP_THRESHOLD

        try:
            await page.evaluate(
                'el => { el.scrollTop = el.scrollHeight; }',
                await page.query_selector(FEED_SELECTOR),
            )
        except Exception:
            pass
        await asyncio.sleep(LOADING_POLL_SECONDS if loading else SCROLL_POLL_SECONDS)

    # Hard iteration cap. If still loader-stuck, force subdivision.
    if loading_stall > 0:
        return last_count, True
    return last_count, last_count >= CAP_THRESHOLD
