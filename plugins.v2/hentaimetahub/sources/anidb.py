"""
AniDB HTTP API 数据源。

文档：https://wiki.anidb.net/HTTP_API_Definition

重要约束：
- 必须在 anidb.net 注册一个 "HTTP Client"，拿到 ``client`` 名和版本号；
- AniDB 对 HTTP API 的请求速率非常严格（建议 ≥ 2 秒/请求），违反可能会被封 IP；
- HTTP API 本身只支持按 ``aid`` 查询详情，不支持关键字搜索；
  搜索通过官方离线 dump ``anime-titles.xml.gz`` 本地匹配实现。
"""
from __future__ import annotations

import gzip
import io
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

from app.core.config import settings
from app.log import logger
from app.utils.http import RequestUtils

from app.plugins.hentaimetahub.models import AnimeCharacter, AnimeMetadata, AnimeSearchItem
from app.plugins.hentaimetahub.sources import AnimeSource


HTTP_API_URL = "http://api.anidb.net:9001/httpapi"
TITLES_DUMP_URL = "http://anidb.net/api/anime-titles.xml.gz"
ADULT_TAG_KEYWORDS = {"hentai", "18 restricted", "erotic game", "erotica"}


class AniDBSource(AnimeSource):
    name = "anidb"
    label = "AniDB"
    priority = 10

    _rate_lock = threading.Lock()
    _last_request_at: float = 0.0
    _titles_cache: Optional[List[Dict[str, Any]]] = None
    _titles_cache_path: Optional[Path] = None

    def __init__(self, config: Dict[str, Any], *, proxy: bool = False) -> None:
        super().__init__(config, proxy=proxy)
        self.client: str = str(self.config.get("client", "")).strip()
        self.clientver: str = str(self.config.get("clientver", "1")).strip() or "1"
        self.protover: str = str(self.config.get("protover", "1")).strip() or "1"
        self.rate_limit_seconds: float = float(self.config.get("rate_limit_seconds", 2.2) or 2.2)
        titles_path = self.config.get("titles_cache_path") or ""
        self.titles_cache_path: Optional[Path] = Path(titles_path) if titles_path else None

    def is_available(self) -> bool:
        return self.enabled and bool(self.client)

    # ---- 速率控制 ----

    def _respect_rate_limit(self) -> None:
        with AniDBSource._rate_lock:
            elapsed = time.time() - AniDBSource._last_request_at
            if elapsed < self.rate_limit_seconds:
                time.sleep(self.rate_limit_seconds - elapsed)
            AniDBSource._last_request_at = time.time()

    # ---- HTTP API ----

    def _request_anime(self, aid: int) -> Optional[ET.Element]:
        if not self.is_available():
            return None
        self._respect_rate_limit()
        params = {
            "request": "anime",
            "client": self.client,
            "clientver": self.clientver,
            "protover": self.protover,
            "aid": aid,
        }
        try:
            res = RequestUtils(
                proxies=settings.PROXY if self.proxy else None,
                timeout=20,
            ).get_res(HTTP_API_URL, params=params)
        except Exception as err:  # pragma: no cover
            logger.error("[AniDBSource] HTTP API 异常: %s", err)
            return None
        if res is None or not res.ok:
            logger.warning(
                "[AniDBSource] HTTP API 失败 status=%s", getattr(res, "status_code", "NO_RESP")
            )
            return None
        raw = res.content or b""
        # 返回可能是 gzip；即便不是 gzip，解压异常时退回原始字节
        try:
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
        except Exception:
            pass
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as err:
            logger.error("[AniDBSource] XML 解析失败: %s", err)
            return None
        if root.tag == "error":
            logger.warning("[AniDBSource] API 返回错误: %s", root.text)
            return None
        return root

    # ---- titles 缓存 ----

    def _load_titles(self) -> List[Dict[str, Any]]:
        if AniDBSource._titles_cache is not None:
            return AniDBSource._titles_cache
        if not self.titles_cache_path:
            return []
        path = self.titles_cache_path
        if not path.exists():
            if not self._download_titles(path):
                return []
        try:
            tree = ET.parse(path)
        except ET.ParseError as err:
            logger.error("[AniDBSource] anime-titles.xml 解析失败: %s", err)
            return []
        entries: List[Dict[str, Any]] = []
        for anime in tree.getroot().iter("anime"):
            aid = anime.attrib.get("aid")
            if not aid:
                continue
            titles = [
                {
                    "type": t.attrib.get("type"),
                    "lang": t.attrib.get("{http://www.w3.org/XML/1998/namespace}lang"),
                    "text": t.text or "",
                }
                for t in anime.findall("title")
                if t.text
            ]
            entries.append({"aid": int(aid), "titles": titles})
        AniDBSource._titles_cache = entries
        AniDBSource._titles_cache_path = path
        logger.info("[AniDBSource] 载入 anime-titles 记录 %d 条", len(entries))
        return entries

    def _download_titles(self, path: Path) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("[AniDBSource] 下载 anime-titles dump 到 %s", path)
        try:
            res = RequestUtils(
                proxies=settings.PROXY if self.proxy else None,
                timeout=60,
            ).get_res(TITLES_DUMP_URL)
        except Exception as err:  # pragma: no cover
            logger.error("[AniDBSource] anime-titles 下载异常: %s", err)
            return False
        if res is None or not res.ok:
            logger.warning("[AniDBSource] anime-titles 下载失败")
            return False
        raw = res.content or b""
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except Exception as err:
                logger.error("[AniDBSource] anime-titles 解压失败: %s", err)
                return False
        try:
            path.write_bytes(raw)
        except Exception as err:  # pragma: no cover
            logger.error("[AniDBSource] anime-titles 写入失败: %s", err)
            return False
        return True

    # ---- 搜索 ----

    def search(self, keyword: str, *, limit: int = 20, adult_only: bool = True) -> List[AnimeSearchItem]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        if not self.is_available():
            return []
        entries = self._load_titles()
        if not entries:
            logger.warning("[AniDBSource] 未配置 anime-titles.xml 本地缓存路径或下载失败，无法关键字搜索")
            return []
        kw = keyword.lower()
        hits: List[Dict[str, Any]] = []
        for entry in entries:
            matched = False
            for t in entry["titles"]:
                if kw in (t["text"] or "").lower():
                    matched = True
                    break
            if not matched:
                continue
            hits.append(entry)
            if len(hits) >= limit:
                break
        out: List[AnimeSearchItem] = []
        for h in hits:
            title_en = title_jp = title_romaji = title_main = None
            for t in h["titles"]:
                lang = t.get("lang")
                type_ = t.get("type")
                text = (t.get("text") or "").strip()
                if not text:
                    continue
                if type_ == "main" and not title_main:
                    title_main = text
                if lang == "en" and not title_en:
                    title_en = text
                if lang == "ja" and not title_jp:
                    title_jp = text
                if lang == "x-jat" and not title_romaji:
                    title_romaji = text
            out.append(
                AnimeSearchItem(
                    source=self.name,
                    source_id=str(h["aid"]),
                    title=title_jp or title_main or title_romaji or title_en or "",
                    title_en=title_en,
                    title_romaji=title_romaji,
                    title_native=title_jp,
                    is_adult=True,
                    url=f"https://anidb.net/anime/{h['aid']}",
                )
            )
        return out

    # ---- 详情 ----

    def fetch(self, source_id: str) -> Optional[AnimeMetadata]:
        if not source_id:
            return None
        try:
            aid = int(str(source_id).strip())
        except (TypeError, ValueError):
            return None
        root = self._request_anime(aid)
        if root is None:
            return None
        return self._to_metadata(root, aid)

    def _to_metadata(self, root: ET.Element, aid: int) -> AnimeMetadata:
        titles_el = root.find("titles")
        title_en = title_main = title_jp = title_romaji = None
        synonyms: List[str] = []
        if titles_el is not None:
            for t in titles_el.findall("title"):
                lang = t.attrib.get("{http://www.w3.org/XML/1998/namespace}lang")
                type_ = t.attrib.get("type")
                text = (t.text or "").strip()
                if not text:
                    continue
                if type_ == "main":
                    title_main = text
                if lang == "en" and not title_en:
                    title_en = text
                if lang == "ja" and not title_jp:
                    title_jp = text
                if lang == "x-jat" and not title_romaji:
                    title_romaji = text
                if type_ == "synonym":
                    synonyms.append(text)

        start_date = (root.findtext("startdate") or "").strip() or None
        end_date = (root.findtext("enddate") or "").strip() or None
        episodes_text = (root.findtext("episodecount") or "").strip()
        episodes = int(episodes_text) if episodes_text.isdigit() else None

        description = (root.findtext("description") or "").strip() or None
        picture_name = (root.findtext("picture") or "").strip()
        cover = f"https://cdn.anidb.net/images/main/{picture_name}" if picture_name else None

        rating_el = root.find("ratings")
        rating = None
        rating_count = None
        if rating_el is not None:
            perm = rating_el.find("permanent")
            if perm is not None and perm.text:
                try:
                    rating = float(perm.text)
                    rating_count = int(perm.attrib.get("count", "0") or 0) or None
                except ValueError:
                    pass

        tags: List[str] = []
        is_adult = True
        tags_el = root.find("tags")
        if tags_el is not None:
            for tag in tags_el.findall("tag"):
                name = tag.findtext("name")
                if name:
                    tags.append(name.strip())
                    if name.strip().lower() in ADULT_TAG_KEYWORDS:
                        is_adult = True

        characters: List[AnimeCharacter] = []
        chars_el = root.find("characters")
        if chars_el is not None:
            for ch in chars_el.findall("character"):
                name = ch.findtext("name")
                if not name:
                    continue
                characters.append(
                    AnimeCharacter(
                        name=name,
                        role=ch.attrib.get("type"),
                    )
                )

        studios = []
        creators_el = root.find("creators")
        if creators_el is not None:
            for c in creators_el.findall("name"):
                role = c.attrib.get("type") or ""
                if role.lower() in {"animation work", "work"} and c.text:
                    studios.append(c.text.strip())

        season_year = None
        if start_date and start_date[:4].isdigit():
            season_year = int(start_date[:4])

        return AnimeMetadata(
            source=self.name,
            source_id=str(aid),
            sources=[self.name],
            source_ids={self.name: str(aid)},
            title=title_jp or title_main or title_romaji or title_en or "",
            title_en=title_en,
            title_romaji=title_romaji,
            title_native=title_jp,
            synonyms=synonyms,
            format=root.findtext("type") or None,
            episodes=episodes,
            start_date=start_date,
            end_date=end_date,
            season_year=season_year,
            cover=cover,
            banner=cover,
            tags=tags,
            studios=studios,
            characters=characters,
            rating=rating,
            rating_count=rating_count,
            is_adult=is_adult,
            description=description,
            urls={self.name: f"https://anidb.net/anime/{aid}"},
        )
