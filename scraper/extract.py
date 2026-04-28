from __future__ import annotations

import re

from playwright.async_api import Page

from .store import PlaceRecord

# Coords appear in two forms. Sidebar anchors use the `data=` segment:
# `!3d<lat>!4d<lon>` is the authoritative marker coordinate. `/@<lat>,<lon>,Zz`
# appears only when we're viewing a place directly.
_LATLON_DATA_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
_LATLON_AT_RE = re.compile(r"/@(-?\d+\.\d+),(-?\d+\.\d+),")
# CID from "data=!4m...!1s0xHEX:0xHEX..." — we keep the hex pair as the stable place id.
_CID_RE = re.compile(r"!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)")
# rating like "4,5 estrelas" / "4.5 stars"
_RATING_RE = re.compile(r"(\d+[.,]\d+)\s*(?:estrela|star)", re.IGNORECASE)
# reviews count in parens, e.g. "(1.234)" or "(12)"
_REVIEWS_RE = re.compile(r"\(([\d\.\,]+)\)")
_PHONE_RE = re.compile(r"(\+?55[\s\-]?)?\(?\d{2,3}\)?[\s\-]?\d{4,5}[\s\-]?\d{4}")
_OPEN_STATUS_RE = re.compile(
    r"^(aberto|fechado|fecha em breve|abre em breve|open|closed|closes soon|opens soon)\b",
    re.IGNORECASE,
)
_PART_SEPARATOR_RE = re.compile(r"\s*(?:·|Â·)\s*")


_EXTRACT_JS = r"""
() => {
  const feed = document.querySelector('div[role="feed"]');
  if (!feed) return [];
  const out = [];
  const seen = new Set();
  const anchors = feed.querySelectorAll('a[href*="/maps/place/"]');
  anchors.forEach(a => {
    const href = a.getAttribute('href') || '';
    if (seen.has(href)) return;
    seen.add(href);

    // Walk up to a container that holds the full card text (name, rating, address...)
    let card = a;
    for (let i = 0; i < 6 && card && card.parentElement; i++) {
      card = card.parentElement;
      if (card.getAttribute('role') === 'article') break;
      if (card.querySelector('[role="img"][aria-label*="estrela"], [role="img"][aria-label*="star"]')) break;
    }
    const name = a.getAttribute('aria-label') || '';
    const ratingEl = card.querySelector('[role="img"][aria-label*="estrela"], [role="img"][aria-label*="star"]');
    const ratingLabel = ratingEl ? (ratingEl.getAttribute('aria-label') || '') : '';
    const text = card ? (card.innerText || '') : '';
    const ariaText = card ? Array.from(card.querySelectorAll('[aria-label]'))
      .map(el => el.getAttribute('aria-label') || '')
      .filter(Boolean)
      .join('\n') : '';
    const website = card ? (Array.from(card.querySelectorAll('a[href]'))
      .map(el => el.href || el.getAttribute('href') || '')
      .find(link => /^https?:\/\//i.test(link)
        && !/^https?:\/\/[^/]*google\./i.test(link)
        && !/\/maps\/place\//i.test(link)) || '') : '';
    out.push({ href, name, ratingLabel, text, ariaText, website });
  });
  return out;
}
"""


def _parse_number(s: str | None) -> int | None:
    if not s:
        return None
    cleaned = s.replace(".", "").replace(",", "").strip()
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_rating(rating_label: str, fallback_text: str) -> float | None:
    for source in (rating_label, fallback_text):
        if not source:
            continue
        m = _RATING_RE.search(source)
        if m:
            return float(m.group(1).replace(",", "."))
    return None


def _parse_reviews(rating_label: str, text: str) -> int | None:
    candidates: list[str] = []
    if rating_label:
        candidates.append(rating_label)
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        lower = line.lower()
        if _RATING_RE.search(line) or "estrela" in lower or "star" in lower:
            candidates.append(line)
    for candidate in candidates:
        m = _REVIEWS_RE.search(candidate)
        if not m:
            continue
        value = _parse_number(m.group(1))
        if value is not None:
            return value
    return None


def _parse_coords(href: str) -> tuple[float | None, float | None]:
    m = _LATLON_DATA_RE.search(href)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = _LATLON_AT_RE.search(href)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def _parse_cid(href: str) -> str | None:
    m = _CID_RE.search(href)
    return m.group(1) if m else None


def _parse_phone(text: str) -> str | None:
    m = _PHONE_RE.search(text)
    if not m:
        return None
    return m.group(0).strip()


def _compact_text(text: str) -> str | None:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return " | ".join(lines) if lines else None


def _parse_open_status_and_hours(text: str) -> tuple[str | None, str | None]:
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or not _OPEN_STATUS_RE.search(line):
            continue
        parts = [p for p in _PART_SEPARATOR_RE.split(line) if p]
        if not parts:
            return line, None
        hours_parts = [p for p in parts[1:] if not _PHONE_RE.search(p)]
        return parts[0], " | ".join(hours_parts) or None
    return None, None


def _is_glyph(s: str) -> bool:
    """Private-use-area icon chars Google Maps injects as separators (e.g. \\ue934)."""
    return all(ord(c) >= 0xE000 and ord(c) <= 0xF8FF for c in s) if s else True


def _parse_category_and_address(text: str, name: str) -> tuple[str | None, str | None]:
    """
    Sidebar card text layout (pt-BR):
        <name>
        <rating>(<reviews_count>)
        <category> · <glyph> · <address>
        Aberto ... · <phone>
    The category/address line is the first '·'-joined line whose parts contain
    no rating/reviews markers. Category is the first non-glyph part; address is
    the last non-glyph part that differs from the category.
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if lines and name and lines[0].strip() == name.strip():
        lines = lines[1:]
    for ln in lines:
        if "·" not in ln and "Â·" not in ln:
            continue
        if _OPEN_STATUS_RE.search(ln):
            continue
        raw_parts = [p.strip() for p in _PART_SEPARATOR_RE.split(ln)]
        parts = [p for p in raw_parts if p and not _is_glyph(p)]
        if not parts:
            continue
        if any(_RATING_RE.search(p) or _REVIEWS_RE.search(p) for p in parts):
            continue
        category = parts[0]
        address = parts[-1] if len(parts) > 1 and parts[-1] != category else None
        return category, address
    return None, None


async def parse_cards(page: Page) -> list[PlaceRecord]:
    raw = await page.evaluate(_EXTRACT_JS)
    records: list[PlaceRecord] = []
    seen_keys: set[str] = set()
    for item in raw:
        href = item.get("href") or ""
        name = (item.get("name") or "").strip() or None
        text = item.get("text") or ""
        aria_text = item.get("ariaText") or ""
        rating_label = item.get("ratingLabel") or ""

        lat, lon = _parse_coords(href)
        cid = _parse_cid(href)
        rating = _parse_rating(rating_label, text)
        reviews = _parse_reviews(rating_label, text)
        phone = _parse_phone("\n".join([text, aria_text]))
        category, address = _parse_category_and_address(text, name or "")
        open_status, hours = _parse_open_status_and_hours(text)
        card_text = _compact_text(text)
        website = (item.get("website") or "").strip() or None
        # Build an absolute URL if href is relative
        place_url = href if href.startswith("http") else f"https://www.google.com{href}"

        rec = PlaceRecord(
            name=name, address=address, phone=phone, rating=rating,
            reviews_count=reviews, category=category,
            lat=lat, lon=lon, place_url=place_url, cid=cid,
            website=website, open_status=open_status, hours=hours, card_text=card_text,
        )
        key = rec.dedupe_key()
        if key in seen_keys:
            continue
        seen_keys.add(key)
        records.append(rec)
    return records
