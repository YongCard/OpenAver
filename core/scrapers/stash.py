"""Stash GraphQL scraper for western scenes."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

import requests
from requests import exceptions as requests_exceptions

from core.logger import get_logger
from core.western_scene import parse_western_scene_filename

from .base import BaseScraper
from .models import Actress, Video, ScraperConfig

logger = get_logger(__name__)


class StashScraper(BaseScraper):
    """Scrape western scene metadata from a local Stash instance."""

    FIND_SCENES_QUERY = """
    query FindScenes($filter: String!) {
      findScenes(scene_filter: { q: $filter }, filter: { per_page: 5 }) {
        scenes {
          id
          title
          date
          details
          urls
          paths { screenshot }
          files { path basename }
          studio { name }
          performers { name }
          tags { name }
        }
      }
    }
    """

    LIST_SCENES_QUERY = """
    query ListScenes($perPage: Int!) {
      findScenes(filter: { per_page: $perPage }) {
        scenes {
          id
          title
          date
          details
          urls
          paths { screenshot }
          files { path basename }
          studio { name }
          performers { name }
          tags { name }
        }
      }
    }
    """

    VERSION_QUERY = "query Version { version { version } }"

    def __init__(self, config: Optional[ScraperConfig] = None, stash_config: Optional[dict] = None):
        super().__init__(config)
        from core.config import load_config

        raw = stash_config if stash_config is not None else load_config().get("stash", {})
        self.enabled = raw.get("enabled") is True
        self.base_url = (raw.get("url") or "http://127.0.0.1:9999").rstrip("/")
        self.api_key = raw.get("api_key") or ""
        self.proxy_url = (raw.get("proxy_url") or "").strip()
        self._last_error: dict[str, Any] | None = None

    def _get_source_name(self) -> str:
        return "stash"

    @property
    def graphql_url(self) -> str:
        return f"{self.base_url}/graphql"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["ApiKey"] = self.api_key
        return headers

    def _proxies(self) -> dict[str, str] | None:
        if not self.proxy_url:
            return {"http": None, "https": None}
        return {"http": self.proxy_url, "https": self.proxy_url}

    def _set_error(self, code: str, message: str, details: Any = None) -> None:
        self._last_error = {"code": code, "message": message}
        if details:
            self._last_error["details"] = details

    def _graphql(self, query: str, variables: Optional[dict] = None) -> dict[str, Any] | None:
        self._last_error = None
        if not self.enabled:
            logger.info("Stash source is disabled")
            self._set_error("stash_disabled", "Stash 來源尚未啟用")
            return None
        try:
            response = requests.post(
                self.graphql_url,
                json={"query": query, "variables": variables or {}},
                headers=self._headers(),
                proxies=self._proxies(),
                timeout=self.config.timeout,
            )
            if response.status_code != 200:
                logger.warning("Stash GraphQL failed: HTTP %s", response.status_code)
                code = "stash_auth_failed" if response.status_code in {401, 403} else "stash_http_error"
                self._set_error(code, f"Stash 回傳 HTTP {response.status_code}", response.text[:500])
                return None
            data = response.json()
            if data.get("errors"):
                logger.warning("Stash GraphQL errors: %s", data.get("errors"))
                self._set_error("stash_graphql_error", "Stash GraphQL 回傳錯誤", data.get("errors"))
                return None
            return data.get("data") or {}
        except requests_exceptions.ProxyError as exc:
            logger.warning("Stash proxy request failed: %s", exc)
            self._set_error("stash_proxy_failed", "Proxy 連線失敗，請檢查 Stash Proxy 設定", str(exc))
            return None
        except requests_exceptions.Timeout as exc:
            logger.warning("Stash request timed out: %s", exc)
            self._set_error("stash_timeout", "連線 Stash 逾時", str(exc))
            return None
        except requests_exceptions.ConnectionError as exc:
            logger.warning("Stash request connection failed: %s", exc)
            self._set_error("stash_unreachable", "無法連線到 Stash，請確認 URL / Proxy", str(exc))
            return None
        except ValueError as exc:
            logger.warning("Stash response JSON parse failed: %s", exc)
            self._set_error("stash_invalid_response", "Stash 回應不是有效 JSON", str(exc))
            return None
        except Exception as exc:
            logger.warning("Stash request failed: %s", exc)
            self._set_error("stash_request_failed", "Stash 請求失敗", str(exc))
            return None

    def test_connection(self) -> dict[str, Any]:
        data = self._graphql(self.VERSION_QUERY)
        if not data:
            error = self._last_error or {"code": "stash_connection_failed", "message": "無法連線到 Stash，請確認 URL / ApiKey"}
            return {"success": False, **error}
        version = data.get("version")
        if isinstance(version, dict):
            version = version.get("version")
        return {"success": True, "version": version or "", "message": "Stash 連線成功"}

    def search(self, number: str) -> Optional[Video]:
        query_text = number.strip()
        parsed = parse_western_scene_filename(query_text)
        scene_id = ""
        if parsed:
            query_text = parsed.scene_query
            scene_id = parsed.scene_id
        elif query_text.upper().startswith("WEST-"):
            scene_id = query_text.upper()
        if query_text.upper().startswith("WEST-"):
            fallback_query = self._query_from_western_number(query_text)
            if fallback_query:
                query_text = fallback_query

        data = self._graphql(self.FIND_SCENES_QUERY, {"filter": query_text})
        scenes = ((data or {}).get("findScenes") or {}).get("scenes") or []
        if not scenes:
            return None
        scene = scenes[0]
        return self._scene_to_video(scene, fallback_query=query_text, fallback_number=scene_id)

    def search_by_filename(self, filename: str, fallback_number: str = "") -> Optional[Video]:
        """Find a Stash scene by file basename before falling back to text search."""
        basename = Path(filename).name
        if not basename:
            return self.search(fallback_number or filename)

        data = self._graphql(self.LIST_SCENES_QUERY, {"perPage": 100})
        scenes = ((data or {}).get("findScenes") or {}).get("scenes") or []
        scene = self._match_scene_by_basename(scenes, basename)
        if scene:
            return self._scene_to_video(
                scene,
                fallback_query=basename,
                fallback_number=fallback_number,
            )

        parsed = parse_western_scene_filename(basename)
        fallback_query = parsed.scene_query if parsed else basename
        video = self.search(fallback_query)
        if video:
            return video.model_copy(update={"number": fallback_number}) if fallback_number else video
        return self.search(fallback_number) if fallback_number else None

    def search_by_keyword(self, keyword: str, limit: int = 20) -> list[Video]:
        data = self._graphql(self.FIND_SCENES_QUERY, {"filter": keyword.strip()})
        scenes = ((data or {}).get("findScenes") or {}).get("scenes") or []
        return [self._scene_to_video(scene, fallback_query=keyword) for scene in scenes[:limit]]

    def _match_scene_by_basename(self, scenes: list[dict[str, Any]], basename: str) -> dict[str, Any] | None:
        target = _casefold_name(basename)
        target_stem = _normalized_stem(basename)
        for scene in scenes:
            for file_info in scene.get("files") or []:
                candidates = [
                    str(file_info.get("basename") or ""),
                    Path(str(file_info.get("path") or "")).name,
                ]
                for candidate in candidates:
                    if not candidate:
                        continue
                    if _casefold_name(candidate) == target:
                        return scene
                    if _normalized_stem(candidate) == target_stem:
                        return scene
        return None

    def _scene_to_video(self, scene: dict[str, Any], fallback_query: str, fallback_number: str = "") -> Video:
        studio = ((scene.get("studio") or {}).get("name") or "").strip()
        performers = [
            Actress(name=p.get("name", "").strip())
            for p in scene.get("performers") or []
            if p.get("name", "").strip()
        ]
        tags = [
            t.get("name", "").strip()
            for t in scene.get("tags") or []
            if t.get("name", "").strip()
        ]
        title = (scene.get("title") or fallback_query).strip()
        date = (scene.get("date") or "").strip()
        cover_url = self._absolute_stash_url(((scene.get("paths") or {}).get("screenshot") or "").strip())
        urls = scene.get("urls") or []
        detail_url = urls[0] if urls else ""
        number = fallback_number or self._stable_number(studio, date, performers, title, scene.get("id"))
        return Video(
            number=number,
            title=title,
            actresses=performers,
            date=date,
            maker=studio,
            cover_url=cover_url,
            tags=tags,
            source=self.source_name,
            detail_url=detail_url,
            summary=(scene.get("details") or "").strip(),
        )

    def _stable_number(
        self,
        studio: str,
        date: str,
        performers: list[Actress],
        title: str,
        scene_id: Any,
    ) -> str:
        import re

        date_part = re.sub(r"\D+", "", date) or "00000000"
        studio_part = re.sub(r"[^A-Za-z0-9]+", "-", (studio or "STASH").upper()).strip("-")
        actor_text = performers[0].name if performers else title or str(scene_id or "SCENE")
        actor_part = re.sub(r"[^A-Za-z0-9]+", "-", actor_text.upper()).strip("-")
        return f"WEST-{studio_part or 'STASH'}-{date_part}-{actor_part or 'SCENE'}"

    def _absolute_stash_url(self, value: str) -> str:
        if not value:
            return ""
        if value.startswith(("http://", "https://")):
            return value
        return urljoin(f"{self.base_url}/", value.lstrip("/"))

    def _query_from_western_number(self, number: str) -> str:
        import re

        match = re.match(r"^WEST-([A-Z0-9-]+)-(\d{8})-(.+)$", (number or "").upper())
        if not match:
            return ""
        studio, yyyymmdd, tail = match.groups()
        year, month, day = yyyymmdd[:4], yyyymmdd[4:6], yyyymmdd[6:8]
        return " ".join(
            part.replace("-", " ")
            for part in (studio, f"{year}-{month}-{day}", tail)
            if part
        )


def _casefold_name(value: str) -> str:
    return Path(value).name.casefold()


def _normalized_stem(value: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "", Path(value).stem.casefold())
