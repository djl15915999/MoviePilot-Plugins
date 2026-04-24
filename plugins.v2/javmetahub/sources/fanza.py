"""
FANZA/DMM Web API 数据源。

官方文档：https://affiliate.dmm.com/api/

使用说明：
- 需要 API ID 与 Affiliate ID；Affiliate ID 需要使用 ``xxx-990`` ~ ``xxx-999`` 结尾；
- 单次请求 ``hits`` 上限为 100；
- 本实现只请求元数据（标题、封面、演员、标签、发行日期、时长、评分、简介），
  不处理任何视频资源链接。

这是 JAV 数据的首选主源。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.log import logger
from app.utils.http import RequestUtils

from app.plugins.javmetahub.models import JavActor, JavMetadata, JavSearchItem
from app.plugins.javmetahub.sources import JavSource, normalize_code


class FanzaSource(JavSource):
    """FANZA / DMM 官方 Web API。"""

    name = "fanza"
    label = "FANZA/DMM"
    priority = 10

    API_BASE = "https://api.dmm.com/affiliate/v3/ItemList"

    # 默认检索 R18 成人影片数字版
    DEFAULT_SITE = "FANZA"
    DEFAULT_SERVICE = "digital"
    DEFAULT_FLOOR = "videoa"

    def __init__(self, config: Dict[str, Any], *, proxy: bool = False) -> None:
        super().__init__(config, proxy=proxy)
        self.api_id: str = str(self.config.get("api_id", "")).strip()
        self.affiliate_id: str = str(self.config.get("affiliate_id", "")).strip()
        self.site: str = str(self.config.get("site", self.DEFAULT_SITE)).strip() or self.DEFAULT_SITE
        self.service: str = str(self.config.get("service", self.DEFAULT_SERVICE)).strip() or self.DEFAULT_SERVICE
        self.floor: str = str(self.config.get("floor", self.DEFAULT_FLOOR)).strip() or self.DEFAULT_FLOOR

    # ---- 可用性 ----

    def is_available(self) -> bool:
        if not self.enabled:
            return False
        if not self.api_id or not self.affiliate_id:
            return False
        # Affiliate ID 约束：990 ~ 999 结尾
        tail = self.affiliate_id.rsplit("-", 1)[-1]
        if tail.isdigit() and not (990 <= int(tail) <= 999):
            logger.warning(
                "[FanzaSource] affiliate_id 的数字后缀需要在 990~999 范围内，当前：%s", self.affiliate_id
            )
            return False
        return True

    # ---- API 调用 ----

    def _request(self, **params: Any) -> List[Dict[str, Any]]:
        if not self.is_available():
            return []
        query: Dict[str, Any] = {
            "api_id": self.api_id,
            "affiliate_id": self.affiliate_id,
            "site": self.site,
            "service": self.service,
            "floor": self.floor,
            "output": "json",
            "hits": 20,
            "sort": "rank",
        }
        query.update({k: v for k, v in params.items() if v not in (None, "")})
        try:
            res = RequestUtils(
                accept_type="application/json",
                proxies=settings.PROXY if self.proxy else None,
                timeout=15,
            ).get_res(self.API_BASE, params=query)
        except Exception as err:  # pragma: no cover - 网络错误
            logger.error("[FanzaSource] 请求 DMM API 失败: %s", err)
            return []
        if res is None:
            logger.warning("[FanzaSource] DMM API 无响应")
            return []
        if not res.ok:
            logger.warning("[FanzaSource] DMM API 状态码 %s，响应：%s", res.status_code, res.text[:200])
            return []
        try:
            payload = res.json() or {}
        except Exception as err:  # pragma: no cover - 解析异常
            logger.error("[FanzaSource] DMM API JSON 解析失败: %s", err)
            return []
        result = payload.get("result") or {}
        if result.get("status") and int(result.get("status", 200)) >= 400:
            logger.warning("[FanzaSource] DMM API 错误: %s", result.get("message"))
            return []
        return result.get("items") or []

    # ---- 搜索 ----

    def search(self, keyword: str, *, limit: int = 20) -> List[JavSearchItem]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        items = self._request(keyword=keyword, hits=min(max(limit, 1), 100))
        out: List[JavSearchItem] = []
        for item in items:
            code = normalize_code(item.get("content_id") or item.get("product_id") or "")
            cover = self._pick_image(item.get("imageURL") or {})
            out.append(
                JavSearchItem(
                    source=self.name,
                    code=code,
                    title=item.get("title") or "",
                    cover=cover,
                    release_date=item.get("date"),
                    url=item.get("URL") or item.get("affiliateURL"),
                )
            )
        return out

    # ---- 详情 ----

    def fetch(self, code: str) -> Optional[JavMetadata]:
        code = normalize_code(code)
        if not code:
            return None
        # DMM 的 content_id 通常是小写连字符或紧凑形式，直接用番号做 keyword 即可
        items = self._request(keyword=code, hits=20)
        if not items:
            return None
        item = self._best_match(items, code) or items[0]
        return self._to_metadata(item, code)

    # ---- 内部 ----

    @staticmethod
    def _pick_image(image_url: Dict[str, Any]) -> Optional[str]:
        for key in ("large", "list", "small"):
            value = image_url.get(key)
            if value:
                return value
        return None

    @staticmethod
    def _best_match(items: List[Dict[str, Any]], code: str) -> Optional[Dict[str, Any]]:
        code_key = code.replace("-", "").lower()
        for item in items:
            pid = (item.get("product_id") or "").replace("-", "").lower()
            cid = (item.get("content_id") or "").lower()
            if pid == code_key or cid.endswith(code_key):
                return item
        return None

    def _to_metadata(self, item: Dict[str, Any], code: str) -> JavMetadata:
        iteminfo = item.get("iteminfo") or {}
        sample = item.get("sampleImageURL") or {}

        actors = []
        for actress in iteminfo.get("actress") or []:
            actors.append(
                JavActor(
                    name=actress.get("name") or "",
                    name_romaji=actress.get("ruby"),
                    actor_id=str(actress.get("id")) if actress.get("id") else None,
                )
            )

        genres = [g.get("name") for g in iteminfo.get("genre") or [] if g.get("name")]

        screenshots: List[str] = []
        for key in ("sample_s", "sample_l"):
            sub = sample.get(key) or {}
            for url in sub.get("image") or []:
                if url:
                    screenshots.append(url)

        review = item.get("review") or {}
        rating = None
        if review.get("average") is not None:
            try:
                rating = float(review["average"]) * 2  # DMM 为 5 分制，统一成 10 分制
            except (TypeError, ValueError):
                rating = None

        studio = None
        for maker in iteminfo.get("maker") or []:
            if maker.get("name"):
                studio = maker["name"]
                break

        label = None
        for lb in iteminfo.get("label") or []:
            if lb.get("name"):
                label = lb["name"]
                break

        series = None
        for sr in iteminfo.get("series") or []:
            if sr.get("name"):
                series = sr["name"]
                break

        director = None
        for dr in iteminfo.get("director") or []:
            if dr.get("name"):
                director = dr["name"]
                break

        duration = None
        vol = item.get("volume")
        if vol:
            try:
                duration = int(str(vol).split(":", 1)[0])
            except (TypeError, ValueError):
                duration = None

        url = item.get("URL") or item.get("affiliateURL")
        cover = self._pick_image(item.get("imageURL") or {})

        meta = JavMetadata(
            code=code,
            source=self.name,
            sources=[self.name],
            title=item.get("title") or "",
            title_original=item.get("title"),
            release_date=item.get("date"),
            duration=duration,
            studio=studio,
            label=label,
            series=series,
            director=director,
            cover=cover,
            poster=cover,
            screenshots=screenshots,
            actors=actors,
            genres=genres,
            rating=rating,
            description=(iteminfo.get("comment") or {}).get("comment") if isinstance(iteminfo.get("comment"), dict) else None,
            urls={self.name: url} if url else {},
            raw={self.name: item},
        )
        return meta
