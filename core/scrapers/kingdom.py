"""KingDom official website scraper."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from core.logger import get_logger

from .base import BaseScraper
from .models import Actress, Video
from .utils import get_html, rate_limit

logger = get_logger(__name__)

KINGDOM_BASE_URL = "https://kingdom.vc"
KINGDOM_PREFIXES = {"KIDM"}


def is_kingdom_number(number: str) -> bool:
    """Return True when a number belongs to the KingDom official catalog."""
    match = re.match(r"([A-Z]+)", (number or "").strip().upper())
    prefix = match.group(1) if match else ""
    return prefix in KINGDOM_PREFIXES


def _normalize_number_for_compare(number: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (number or "").upper())


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _normalize_date(value: str) -> str:
    match = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", value or "")
    if not match:
        return ""
    year, month, day = match.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


class KingDomScraper(BaseScraper):
    """Scraper for the official KingDom website."""

    def _get_source_name(self) -> str:
        return "kingdom"

    def _get_html(self, url: str) -> Optional[str]:
        return get_html(
            url,
            timeout=self.config.timeout,
            headers={
                "User-Agent": self.config.user_agent,
                "Referer": KINGDOM_BASE_URL + "/",
                "Accept-Language": "ja-JP,ja;q=0.9,zh-TW;q=0.8,zh;q=0.7,en;q=0.6",
            },
        )

    def _extract_detail_links(self, html: str, limit: int = 16) -> list[str]:
        soup = BeautifulSoup(html or "", "html.parser")
        links: list[str] = []
        seen: set[str] = set()
        for link in soup.select('a[href*="/products/detail/"]'):
            href = str(link.get("href") or "")
            if not href:
                continue
            detail_url = urljoin(KINGDOM_BASE_URL, href)
            if detail_url in seen:
                continue
            seen.add(detail_url)
            links.append(detail_url)
            if len(links) >= limit:
                break
        return links

    def _field_by_label(self, soup: BeautifulSoup, label: str) -> str:
        for row in soup.select(".table-product .tr"):
            th = _clean_text(row.select_one(".th").get_text(" ", strip=True) if row.select_one(".th") else "")
            if label not in th:
                continue
            td = row.select_one(".td")
            return _clean_text(td.get_text(" ", strip=True) if td else "")
        return ""

    def _links_by_label(self, soup: BeautifulSoup, label: str) -> list[str]:
        for row in soup.select(".table-product .tr"):
            th = _clean_text(row.select_one(".th").get_text(" ", strip=True) if row.select_one(".th") else "")
            if label not in th:
                continue
            return [_clean_text(a.get_text(" ", strip=True)) for a in row.select(".td a") if _clean_text(a.get_text(" ", strip=True))]
        return []

    def _parse_detail(self, html: str, detail_url: str, requested_number: str | None = None) -> Optional[Video]:
        soup = BeautifulSoup(html or "", "html.parser")
        number = _clean_text(soup.select_one(".product-code-default").get_text(" ", strip=True) if soup.select_one(".product-code-default") else "")
        if not number:
            number = self._field_by_label(soup, "商品番号")
        number = self.normalize_number(number)

        if requested_number and _normalize_number_for_compare(number) != _normalize_number_for_compare(requested_number):
            return None

        title = _clean_text(soup.select_one(".detail-title").get_text(" ", strip=True) if soup.select_one(".detail-title") else "")
        if not title:
            og_title = soup.select_one('meta[property="og:title"]')
            title = _clean_text(str(og_title.get("content") or "")) if og_title else ""

        cover_url = ""
        og_image = soup.select_one('meta[property="og:image"]')
        if og_image and og_image.get("content"):
            cover_url = urljoin(KINGDOM_BASE_URL, str(og_image.get("content")))
        if not cover_url:
            cover = soup.select_one(".item_visual img, .slide-item img")
            cover_url = urljoin(KINGDOM_BASE_URL, str(cover.get("src") or "")) if cover else ""

        date = _normalize_date(self._field_by_label(soup, "発売日"))
        actress_names = self._links_by_label(soup, "女優名")
        actresses = [Actress(name=name) for name in actress_names]

        categories = self._links_by_label(soup, "関連カテゴリ")
        media_noise = {"メディア", "レーベル", "DVD", "Blu-ray Disc", "動画", "すべての商品"}
        tags = [item for item in categories if item and item not in media_noise]

        maker = "King Dom"
        label = ""
        for candidate in ("Kingdom", "Queen", "Princess", "Empress", "Bambini", "bambini"):
            if candidate in categories:
                label = candidate
                break

        summary_elem = soup.select_one(".detail-profile__meta__desc")
        summary = _clean_text(summary_elem.get_text(" ", strip=True) if summary_elem else "")

        sample_images = []
        for img in soup.select(".item_visual img, .slide-item img"):
            src = str(img.get("src") or "")
            if src:
                url = urljoin(KINGDOM_BASE_URL, src)
                if url not in sample_images:
                    sample_images.append(url)

        if not number or not title:
            return None

        return Video(
            number=number,
            title=title,
            actresses=actresses,
            date=date,
            maker=maker,
            cover_url=cover_url,
            tags=tags,
            source=self.source_name,
            detail_url=detail_url,
            label=label,
            sample_images=sample_images,
            summary=summary,
        )

    def search(self, number: str) -> Optional[Video]:
        number = self.normalize_number(number)
        if not self.validate_number(number):
            raise ValueError(f"Invalid number format: {number}")
        if not is_kingdom_number(number):
            return None

        search_url = f"{KINGDOM_BASE_URL}/products/list?name={quote(number)}"
        html = self._get_html(search_url)
        if not html:
            return None

        for detail_url in self._extract_detail_links(html):
            rate_limit(self.config.delay)
            detail_html = self._get_html(detail_url)
            if not detail_html:
                continue
            video = self._parse_detail(detail_html, detail_url, requested_number=number)
            if video:
                return video
        return None

    def search_by_keyword(self, keyword: str, limit: int = 20) -> list[Video]:
        html = self._get_html(f"{KINGDOM_BASE_URL}/products/list?name={quote(keyword)}")
        if not html:
            return []
        results: list[Video] = []
        for detail_url in self._extract_detail_links(html, limit=limit):
            if len(results) >= limit:
                break
            rate_limit(self.config.delay)
            detail_html = self._get_html(detail_url)
            if not detail_html:
                continue
            video = self._parse_detail(detail_html, detail_url)
            if video:
                results.append(video)
        return results
