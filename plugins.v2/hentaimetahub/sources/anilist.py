"""
AniList GraphQL 数据源。

官方 GraphQL Endpoint：https://graphql.anilist.co/

免费、无需鉴权即可查询基本数据，支持 ``isAdult: true`` 精确过滤成人向条目。
优点：结构化、稳定、自带多语言标题；缺点：以英文/罗马字为主，中文欠缺。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.log import logger
from app.utils.http import RequestUtils

from app.plugins.hentaimetahub.models import AnimeMetadata, AnimeSearchItem
from app.plugins.hentaimetahub.sources import AnimeSource


SEARCH_QUERY = """
query ($search: String, $isAdult: Boolean, $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(type: ANIME, search: $search, isAdult: $isAdult) {
      id
      title { romaji english native }
      coverImage { large }
      seasonYear
      isAdult
      siteUrl
    }
  }
}
"""

DETAIL_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    title { romaji english native }
    synonyms
    description(asHtml: false)
    format
    status
    episodes
    duration
    season
    seasonYear
    startDate { year month day }
    endDate { year month day }
    coverImage { large extraLarge }
    bannerImage
    genres
    tags { name rank isAdult }
    studios { nodes { name isAnimationStudio } }
    averageScore
    popularity
    isAdult
    siteUrl
  }
}
"""


class AniListSource(AnimeSource):
    name = "anilist"
    label = "AniList"
    priority = 20

    API = "https://graphql.anilist.co/"

    def __init__(self, config: Dict[str, Any], *, proxy: bool = False) -> None:
        super().__init__(config, proxy=proxy)
        self.token: str = str(self.config.get("token", "")).strip()

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _post(self, query: str, variables: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            res = RequestUtils(
                headers=self._headers(),
                proxies=settings.PROXY if self.proxy else None,
                timeout=15,
            ).post_res(self.API, json={"query": query, "variables": variables})
        except Exception as err:  # pragma: no cover
            logger.error("[AniListSource] 请求异常: %s", err)
            return None
        if res is None:
            return None
        if not res.ok:
            logger.warning("[AniListSource] HTTP %s: %s", res.status_code, res.text[:200])
            return None
        try:
            return res.json()
        except Exception as err:  # pragma: no cover
            logger.error("[AniListSource] JSON 解析失败: %s", err)
            return None

    # ---- 搜索 ----

    def search(self, keyword: str, *, limit: int = 20, adult_only: bool = True) -> List[AnimeSearchItem]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        payload = self._post(
            SEARCH_QUERY,
            {"search": keyword, "isAdult": True if adult_only else None, "perPage": min(max(limit, 1), 50)},
        )
        if not payload:
            return []
        media_list = (((payload.get("data") or {}).get("Page") or {}).get("media")) or []
        items: List[AnimeSearchItem] = []
        for m in media_list:
            title = m.get("title") or {}
            items.append(
                AnimeSearchItem(
                    source=self.name,
                    source_id=str(m.get("id")),
                    title=title.get("romaji") or title.get("english") or title.get("native") or "",
                    title_en=title.get("english"),
                    title_romaji=title.get("romaji"),
                    title_native=title.get("native"),
                    cover=(m.get("coverImage") or {}).get("large"),
                    year=m.get("seasonYear"),
                    is_adult=bool(m.get("isAdult")),
                    url=m.get("siteUrl"),
                )
            )
        return items

    # ---- 详情 ----

    def fetch(self, source_id: str) -> Optional[AnimeMetadata]:
        if not source_id:
            return None
        try:
            aid = int(str(source_id).strip())
        except (TypeError, ValueError):
            return None
        payload = self._post(DETAIL_QUERY, {"id": aid})
        if not payload:
            return None
        m = (payload.get("data") or {}).get("Media")
        if not m:
            return None
        return self._to_metadata(m)

    def _to_metadata(self, m: Dict[str, Any]) -> AnimeMetadata:
        title = m.get("title") or {}
        start = m.get("startDate") or {}
        end = m.get("endDate") or {}
        tags = [t.get("name") for t in m.get("tags") or [] if t.get("name")]
        genres = list(m.get("genres") or [])
        studios = [
            s.get("name")
            for s in ((m.get("studios") or {}).get("nodes") or [])
            if s.get("name")
        ]

        cover = (m.get("coverImage") or {}).get("extraLarge") or (m.get("coverImage") or {}).get("large")

        start_date = self._fmt_date(start)
        end_date = self._fmt_date(end)

        rating = None
        if m.get("averageScore") is not None:
            try:
                rating = float(m["averageScore"]) / 10.0
            except (TypeError, ValueError):
                rating = None

        return AnimeMetadata(
            source=self.name,
            source_id=str(m.get("id")),
            sources=[self.name],
            source_ids={self.name: str(m.get("id"))},
            title=title.get("romaji") or title.get("english") or title.get("native") or "",
            title_en=title.get("english"),
            title_romaji=title.get("romaji"),
            title_native=title.get("native"),
            synonyms=list(m.get("synonyms") or []),
            format=m.get("format"),
            status=m.get("status"),
            episodes=m.get("episodes"),
            duration=m.get("duration"),
            season=m.get("season"),
            season_year=m.get("seasonYear"),
            start_date=start_date,
            end_date=end_date,
            cover=cover,
            banner=m.get("bannerImage"),
            genres=genres,
            tags=tags,
            studios=studios,
            rating=rating,
            rating_count=m.get("popularity"),
            is_adult=bool(m.get("isAdult", True)),
            description=m.get("description"),
            urls={self.name: m.get("siteUrl")} if m.get("siteUrl") else {},
            raw={self.name: m},
        )

    @staticmethod
    def _fmt_date(date_obj: Dict[str, Any]) -> Optional[str]:
        if not date_obj:
            return None
        y, mo, d = date_obj.get("year"), date_obj.get("month"), date_obj.get("day")
        if not y:
            return None
        return f"{y:04d}-{(mo or 1):02d}-{(d or 1):02d}"
