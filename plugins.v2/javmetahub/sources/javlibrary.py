"""
JavLibrary HTML 刮削源。

仅做元数据补全：多语言标题、评分、标签、演员名字。
JavLibrary 并非官方 API，稳定性受 Cloudflare/反爬影响。失败时应当回退到其他源。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin

from app.core.config import settings
from app.log import logger
from app.utils.http import RequestUtils

from app.plugins.javmetahub.models import JavActor, JavMetadata, JavSearchItem
from app.plugins.javmetahub.sources import JavSource, normalize_code


class JavLibrarySource(JavSource):
    """JavLibrary (www.javlibrary.com) scraper。"""

    name = "javlibrary"
    label = "JavLibrary"
    priority = 30

    DEFAULT_BASE = "https://www.javlibrary.com"
    DEFAULT_LANG = "cn"  # cn / ja / en / tw / ko

    def __init__(self, config: Dict[str, Any], *, proxy: bool = False) -> None:
        super().__init__(config, proxy=proxy)
        self.base_url: str = (self.config.get("base_url") or self.DEFAULT_BASE).rstrip("/")
        self.lang: str = str(self.config.get("lang", self.DEFAULT_LANG)).strip() or self.DEFAULT_LANG
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
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
            logger.error("[JavLibrarySource] 请求失败: %s %s", url, err)
            return None
        if res is None:
            return None
        if res.status_code in (403, 503):
            logger.warning("[JavLibrarySource] 可能被 Cloudflare 拦截，status=%s", res.status_code)
            return None
        if not res.ok:
            logger.warning("[JavLibrarySource] HTTP %s: %s", res.status_code, url)
            return None
        res.encoding = res.encoding or "utf-8"
        return res.text

    def _lang_path(self, path: str) -> str:
        return f"/{self.lang}/{path.lstrip('/')}"

    # ---- 搜索 ----

    def search(self, keyword: str, *, limit: int = 20) -> List[JavSearchItem]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        try:
            from bs4 import BeautifulSoup
        except ImportError:  # pragma: no cover
            logger.error("[JavLibrarySource] 缺少 beautifulsoup4 依赖")
            return []
        html = self._get(self._lang_path("vl_searchbyid.php"), keyword=keyword)
        if not html:
            return []
        soup = BeautifulSoup(html, "lxml")
        # 精确命中单条时，会直接 302 到详情页；此时 search 也需要能解析详情
        if soup.select_one("#video_title"):
            meta = self._parse_detail(html)
            if meta:
                return [
                    JavSearchItem(
                        source=self.name,
                        code=meta.code,
                        title=meta.title,
                        cover=meta.cover,
                        release_date=meta.release_date,
                        url=self._first_url(meta.urls),
                    )
                ]
            return []
        return self._parse_list(html, limit=limit)

    # ---- 探索 ----

    def discover(
        self,
        *,
        keyword: str = "",
        sort: str = "date",
        year: Optional[str] = None,
        page: int = 1,
        count: int = 30,
    ) -> List[JavMetadata]:
        """浏览 JavLibrary 列表页；受 Cloudflare 影响时会返回空列表。"""
        keyword = (keyword or "").strip()
        if keyword:
            return self._items_to_metadata(self.search(keyword, limit=count))

        page = max(int(page or 1), 1)
        count = min(max(int(count or 30), 1), 100)
        path = "vl_bestrated.php" if sort in ("rank", "review") else "vl_newrelease.php"
        params = {"page": page} if page > 1 else {}
        html = self._get(self._lang_path(path), **params)
        if not html:
            return []
        items = self._parse_list(html, limit=count)
        if year and str(year).isdigit():
            filtered = [item for item in items if (item.release_date or "").startswith(str(year))]
            if filtered:
                items = filtered
        return self._items_to_metadata(items)

    def _parse_list(self, html: str, *, limit: int = 20) -> List[JavSearchItem]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:  # pragma: no cover
            logger.error("[JavLibrarySource] 缺少 beautifulsoup4 依赖")
            return []
        soup = BeautifulSoup(html, "lxml")
        items: List[JavSearchItem] = []
        for node in soup.select(".videothumblist .video")[:limit]:
            a = node.select_one("a")
            if not a:
                continue
            href = a.get("href") or ""
            title = (node.select_one(".title") or a).get_text(strip=True)
            code_node = node.select_one(".id")
            code = normalize_code(code_node.get_text(strip=True) if code_node else "")
            cover_node = node.select_one("img")
            cover = cover_node.get("src") if cover_node else None
            if cover and cover.startswith("//"):
                cover = "https:" + cover
            elif cover and cover.startswith("/"):
                cover = urljoin(self.base_url + "/", cover.lstrip("/"))
            items.append(
                JavSearchItem(
                    source=self.name,
                    code=code,
                    title=title,
                    cover=cover,
                    url=urljoin(self.base_url + "/", href.lstrip("/")),
                )
            )
        return items

    def _items_to_metadata(self, items: List[JavSearchItem]) -> List[JavMetadata]:
        metas: List[JavMetadata] = []
        for item in items:
            if not item.code:
                continue
            metas.append(
                JavMetadata(
                    code=item.code,
                    source=self.name,
                    sources=[self.name],
                    title=item.title or item.code,
                    release_date=item.release_date,
                    cover=item.cover,
                    poster=item.cover,
                    urls={self.name: item.url} if item.url else {},
                )
            )
        return metas

    # ---- 详情 ----

    def fetch(self, code: str) -> Optional[JavMetadata]:
        code = normalize_code(code)
        if not code:
            return None
        html = self._get(self._lang_path("vl_searchbyid.php"), keyword=code)
        if not html:
            return None
        return self._parse_detail(html, code_hint=code)

    # ---- 解析 ----

    def _parse_detail(self, html: str, *, code_hint: Optional[str] = None) -> Optional[JavMetadata]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:  # pragma: no cover
            return None
        soup = BeautifulSoup(html, "lxml")
        title_node = soup.select_one("#video_title a")
        if not title_node:
            return None
        full_title = title_node.get_text(" ", strip=True)
        url = title_node.get("href") or ""
        if url:
            url = urljoin(self.base_url + "/", url.lstrip("/"))

        code_node = soup.select_one("#video_id .text")
        code = normalize_code(code_node.get_text(strip=True) if code_node else code_hint or "")
        if not code:
            return None

        # 去掉标题前缀里的番号
        title = full_title
        if code_node:
            raw_code = code_node.get_text(strip=True)
            if title.lower().startswith(raw_code.lower()):
                title = title[len(raw_code):].strip()

        cover_node = soup.select_one("#video_jacket_img")
        cover = cover_node.get("src") if cover_node else None
        if cover and cover.startswith("//"):
            cover = "https:" + cover

        release_date = self._single_text(soup, "#video_date .text")
        duration_text = self._single_text(soup, "#video_length .text")
        duration = None
        if duration_text and duration_text.isdigit():
            duration = int(duration_text)

        studio = self._single_text(soup, "#video_maker .text")
        label = self._single_text(soup, "#video_label .text")
        director = self._single_text(soup, "#video_director .text")

        genres = [a.get_text(strip=True) for a in soup.select("#video_genres .genre a") if a.get_text(strip=True)]
        actors = [
            JavActor(name=a.get_text(strip=True))
            for a in soup.select("#video_cast .cast .star a")
            if a.get_text(strip=True)
        ]

        rating = None
        score = soup.select_one("#video_review .score")
        if score:
            try:
                raw = score.get_text(strip=True).strip("() ")
                rating = float(raw)
            except ValueError:
                rating = None

        return JavMetadata(
            code=code,
            source=self.name,
            sources=[self.name],
            title=title,
            release_date=release_date,
            duration=duration,
            studio=studio,
            label=label,
            director=director,
            cover=cover,
            poster=cover,
            actors=actors,
            genres=genres,
            rating=rating,
            urls={self.name: url} if url else {},
        )

    @staticmethod
    def _single_text(soup, selector: str) -> Optional[str]:
        node = soup.select_one(selector)
        if not node:
            return None
        text = node.get_text(strip=True)
        return text or None

    @staticmethod
    def _first_url(urls: Dict[str, str]) -> Optional[str]:
        if not urls:
            return None
        return next(iter(urls.values()))
