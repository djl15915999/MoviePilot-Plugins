"""
Bangumi 番组计划 API（v0）数据源。

文档：https://bangumi.github.io/api/

- 新 API：POST /v0/search/subjects、GET /v0/subjects/{id}
- 可选 ``ACCESS_TOKEN``（Authorization: Bearer xxx）
- Bangumi 的 NSFW 字段需要登录后的 token 才能获取完整标记，未鉴权时也能搜但部分字段缺失
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.log import logger
from app.utils.http import RequestUtils

from app.plugins.hentaimetahub.models import AnimeMetadata, AnimeSearchItem
from app.plugins.hentaimetahub.sources import AnimeSource


class BangumiSource(AnimeSource):
    name = "bangumi"
    label = "Bangumi"
    priority = 30

    API_BASE = "https://api.bgm.tv"

    def __init__(self, config: Dict[str, Any], *, proxy: bool = False) -> None:
        super().__init__(config, proxy=proxy)
        self.token: str = str(self.config.get("token", "")).strip()
        self.user_agent: str = (
            self.config.get("user_agent")
            or "MoviePilot-HentaiMetaHub/1.0 (https://github.com/jxxghp/MoviePilot)"
        )

    def _headers(self) -> Dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    # ---- 搜索 ----

    def search(self, keyword: str, *, limit: int = 20, adult_only: bool = True) -> List[AnimeSearchItem]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        body: Dict[str, Any] = {
            "keyword": keyword,
            "filter": {
                "type": [2],  # 2 = 动画
            },
        }
        if adult_only:
            body["filter"]["nsfw"] = True
        url = f"{self.API_BASE}/v0/search/subjects?limit={min(max(limit, 1), 50)}&offset=0"
        try:
            res = RequestUtils(
                headers=self._headers(),
                content_type="application/json",
                proxies=settings.PROXY if self.proxy else None,
                timeout=15,
            ).post_res(url, json=body)
        except Exception as err:  # pragma: no cover
            logger.error("[BangumiSource] 搜索异常: %s", err)
            return []
        if res is None or not res.ok:
            logger.warning(
                "[BangumiSource] 搜索失败 status=%s",
                getattr(res, "status_code", "NO_RESP"),
            )
            return []
        try:
            payload = res.json() or {}
        except Exception as err:  # pragma: no cover
            logger.error("[BangumiSource] 搜索 JSON 解析失败: %s", err)
            return []
        items: List[AnimeSearchItem] = []
        for it in payload.get("data") or []:
            images = it.get("images") or {}
            year = None
            date = it.get("date") or ""
            if date and len(date) >= 4 and date[:4].isdigit():
                year = int(date[:4])
            items.append(
                AnimeSearchItem(
                    source=self.name,
                    source_id=str(it.get("id")),
                    title=it.get("name_cn") or it.get("name") or "",
                    title_native=it.get("name"),
                    cover=images.get("large") or images.get("common"),
                    year=year,
                    is_adult=bool(it.get("nsfw")),
                    url=f"https://bgm.tv/subject/{it.get('id')}" if it.get("id") else None,
                )
            )
        return items

    # ---- 详情 ----

    def fetch(self, source_id: str) -> Optional[AnimeMetadata]:
        if not source_id:
            return None
        try:
            sid = int(str(source_id).strip())
        except (TypeError, ValueError):
            return None
        url = f"{self.API_BASE}/v0/subjects/{sid}"
        try:
            res = RequestUtils(
                headers=self._headers(),
                proxies=settings.PROXY if self.proxy else None,
                timeout=15,
            ).get_res(url)
        except Exception as err:  # pragma: no cover
            logger.error("[BangumiSource] 详情异常: %s", err)
            return None
        if res is None or not res.ok:
            logger.warning("[BangumiSource] 详情失败 status=%s", getattr(res, "status_code", "NO_RESP"))
            return None
        try:
            data = res.json() or {}
        except Exception as err:  # pragma: no cover
            logger.error("[BangumiSource] 详情 JSON 解析失败: %s", err)
            return None
        return self._to_metadata(data)

    def _to_metadata(self, d: Dict[str, Any]) -> AnimeMetadata:
        images = d.get("images") or {}
        date = d.get("date") or ""
        year = None
        if date and len(date) >= 4 and date[:4].isdigit():
            year = int(date[:4])

        rating = None
        rating_count = None
        rating_obj = d.get("rating") or {}
        if rating_obj.get("score") is not None:
            try:
                rating = float(rating_obj["score"])
            except (TypeError, ValueError):
                rating = None
        if rating_obj.get("total") is not None:
            try:
                rating_count = int(rating_obj["total"])
            except (TypeError, ValueError):
                rating_count = None

        tags = [t.get("name") for t in d.get("tags") or [] if t.get("name")]

        synonyms = []
        infobox = d.get("infobox") or []
        for entry in infobox:
            key = (entry.get("key") or "").strip()
            val = entry.get("value")
            if key in {"别名", "其他名称", "別名", "Aliases"} and val:
                if isinstance(val, list):
                    synonyms.extend([v.get("v") if isinstance(v, dict) else str(v) for v in val])
                else:
                    synonyms.append(str(val))

        episodes = d.get("eps") or d.get("total_episodes")

        return AnimeMetadata(
            source=self.name,
            source_id=str(d.get("id")),
            sources=[self.name],
            source_ids={self.name: str(d.get("id"))},
            title=d.get("name_cn") or d.get("name") or "",
            title_cn=d.get("name_cn") or None,
            title_native=d.get("name"),
            synonyms=[s for s in synonyms if s],
            episodes=episodes,
            start_date=date or None,
            season_year=year,
            cover=images.get("large") or images.get("common"),
            banner=images.get("large"),
            tags=tags,
            rating=rating,
            rating_count=rating_count,
            is_adult=bool(d.get("nsfw", True)),
            description=d.get("summary"),
            urls={self.name: f"https://bgm.tv/subject/{d.get('id')}"} if d.get("id") else {},
            raw={self.name: d},
        )
