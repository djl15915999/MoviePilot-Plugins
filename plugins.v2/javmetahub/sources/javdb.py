"""
JavDB HTML 刮削源。

JavDB 没有公开 API，本实现是基于页面 DOM 的 scraper，属于 fallback。
受地区限制/反爬影响较大。支持配置 cookie、base_url（镜像）以及代理。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from app.core.config import settings
from app.log import logger
from app.utils.http import RequestUtils

from app.plugins.javmetahub.models import JavActor, JavMetadata, JavSearchItem
from app.plugins.javmetahub.sources import JavSource, normalize_code


class JavDBSource(JavSource):
    """JavDB 搜索 + 详情 scraper。"""

    name = "javdb"
    label = "JavDB"
    priority = 40

    DEFAULT_BASE = "https://javdb.com"

    def __init__(self, config: Dict[str, Any], *, proxy: bool = False) -> None:
        super().__init__(config, proxy=proxy)
        self.base_url: str = (self.config.get("base_url") or self.DEFAULT_BASE).rstrip("/")
        self.cookie: str = str(self.config.get("cookie", "")).strip()
        self.user_agent: str = (
            self.config.get("user_agent")
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )

    # ---- 网络 ----

    def _headers(self) -> Dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def _get(self, path: str, **params: Any) -> Optional[str]:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        try:
            res = RequestUtils(
                headers=self._headers(),
                proxies=settings.PROXY if self.proxy else None,
                timeout=15,
            ).get_res(url, params=params, allow_redirects=True)
        except Exception as err:  # pragma: no cover
            logger.error("[JavDBSource] 请求失败: %s %s", url, err)
            return None
        if res is None:
            return None
        if res.status_code in (403, 503):
            logger.warning("[JavDBSource] 被反爬/拦截 status=%s", res.status_code)
            return None
        if not res.ok:
            logger.warning("[JavDBSource] HTTP %s: %s", res.status_code, url)
            return None
        res.encoding = res.encoding or "utf-8"
        return res.text

    # ---- 搜索 ----

    def search(self, keyword: str, *, limit: int = 20) -> List[JavSearchItem]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        try:
            from bs4 import BeautifulSoup
        except ImportError:  # pragma: no cover
            logger.error("[JavDBSource] 缺少 beautifulsoup4 依赖")
            return []
        html = self._get("search", q=keyword, f="all")
        if not html:
            return []
        soup = BeautifulSoup(html, "lxml")
        items: List[JavSearchItem] = []
        for node in soup.select(".movie-list .item")[:limit]:
            a = node.select_one("a")
            if not a:
                continue
            href = a.get("href") or ""
            title_node = node.select_one(".video-title")
            title = title_node.get_text(" ", strip=True) if title_node else ""
            code_node = title_node.select_one("strong") if title_node else None
            code = normalize_code(code_node.get_text(strip=True) if code_node else "")
            if code and code_node and title.startswith(code_node.get_text(strip=True)):
                title = title[len(code_node.get_text(strip=True)):].strip()
            cover_node = node.select_one("img")
            cover = None
            if cover_node:
                cover = cover_node.get("data-src") or cover_node.get("src")
            date_node = node.select_one(".meta")
            release_date = date_node.get_text(strip=True) if date_node else None
            items.append(
                JavSearchItem(
                    source=self.name,
                    code=code,
                    title=title,
                    cover=cover,
                    release_date=release_date,
                    url=urljoin(self.base_url + "/", href.lstrip("/")),
                )
            )
        return items

    # ---- 详情 ----

    def fetch(self, code: str) -> Optional[JavMetadata]:
        code = normalize_code(code)
        if not code:
            return None
        results = self.search(code, limit=5)
        if not results:
            return None
        target = next((r for r in results if normalize_code(r.code) == code), results[0])
        if not target.url:
            return None
        # 从完整 URL 中取 path
        path = target.url.replace(self.base_url, "", 1)
        html = self._get(path)
        if not html:
            return None
        return self._parse_detail(html, code=code, url=target.url)

    def _parse_detail(self, html: str, *, code: str, url: str) -> Optional[JavMetadata]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:  # pragma: no cover
            return None
        soup = BeautifulSoup(html, "lxml")

        title_node = soup.select_one(".video-detail .title, .video-meta h2")
        full_title = title_node.get_text(" ", strip=True) if title_node else ""
        # JavDB 的标题常见格式："ABP-123 某某某"
        title = re.sub(r"^[A-Z]{2,6}-\d{2,5}\s*", "", full_title).strip() or full_title

        cover_node = soup.select_one(".video-detail .column-video-cover img, .cover img")
        cover = None
        if cover_node:
            cover = cover_node.get("src") or cover_node.get("data-src")

        panel_blocks = soup.select(".movie-panel-info .panel-block")

        def panel(label: str) -> Optional[str]:
            for blk in panel_blocks:
                strong = blk.select_one("strong")
                value = blk.select_one(".value")
                if not strong or not value:
                    continue
                if label in strong.get_text(strip=True):
                    return value.get_text(" ", strip=True).strip() or None
            return None

        def panel_links(label: str) -> List[str]:
            for blk in panel_blocks:
                strong = blk.select_one("strong")
                value = blk.select_one(".value")
                if not strong or not value:
                    continue
                if label in strong.get_text(strip=True):
                    return [a.get_text(strip=True) for a in value.select("a") if a.get_text(strip=True)]
            return []

        release_date = panel("日期") or panel("Released") or panel("Date")
        duration_text = panel("時長") or panel("时长") or panel("Duration") or ""
        duration = None
        m = re.search(r"(\d+)", duration_text or "")
        if m:
            duration = int(m.group(1))

        studio = panel("片商") or panel("Studio")
        label_val = panel("發行") or panel("发行") or panel("Label")
        series = panel("系列") or panel("Series")
        director = panel("導演") or panel("导演") or panel("Director")

        genres = panel_links("類別") + panel_links("类别") + panel_links("Tags") + panel_links("Genres")
        actor_names = panel_links("演員") + panel_links("演员") + panel_links("Actor") + panel_links("Cast")
        actors = [JavActor(name=name) for name in dict.fromkeys(actor_names) if name]

        rating = None
        score_node = soup.select_one(".score-stars, .score .value")
        if score_node:
            txt = score_node.get_text(" ", strip=True)
            m = re.search(r"(\d+(?:\.\d+)?)", txt)
            if m:
                try:
                    rating = float(m.group(1))
                    if rating > 10:  # 某些页面是百分制
                        rating = rating / 10
                except ValueError:
                    rating = None

        screenshots: List[str] = []
        for img in soup.select(".preview-images a"):
            href = img.get("href") or img.get("data-src")
            if href:
                screenshots.append(href)

        return JavMetadata(
            code=code,
            source=self.name,
            sources=[self.name],
            title=title,
            release_date=release_date,
            duration=duration,
            studio=studio,
            label=label_val,
            series=series,
            director=director,
            cover=cover,
            poster=cover,
            screenshots=screenshots,
            actors=actors,
            genres=list(dict.fromkeys(genres)),
            rating=rating,
            urls={self.name: url},
        )
