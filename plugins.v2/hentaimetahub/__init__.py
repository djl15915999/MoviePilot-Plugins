"""
HentaiMetaHub - 成人动画（里番）元数据多源聚合插件。

能力：
1. 对外暴露 Hub API：``/search``、``/fetch``、``/sources``；
2. 通过 ``ChainEventType.DiscoverSource`` 在探索页新增"里番" 数据源（基于 AniList isAdult 过滤）；
3. 通过 ``EventType.NameRecognize`` 在主程序识别失败时，按标题命中数据源。

只聚合元数据（标题、封面、集数、标签、简介等），不涉及任何资源下载。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app import schemas
from app.core.event import Event, eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import DiscoverSourceEventData
from app.schemas.types import ChainEventType, EventType

try:
    from app.schemas import MediaRecognizeConvertEventData  # noqa: F401
except ImportError:
    MediaRecognizeConvertEventData = None  # type: ignore[assignment]

_MRC_EVENT = getattr(ChainEventType, "MediaRecognizeConvert", None)
_NAME_RECOGNIZE_EVENT = getattr(EventType, "NameRecognize", None)
_NAME_RECOGNIZE_RESULT_EVENT = getattr(EventType, "NameRecognizeResult", None)


def _maybe_register(event_type):
    """兼容旧版 MoviePilot：当事件类型不存在时跳过注册。"""

    def decorator(fn):
        if event_type is None:
            return fn
        return eventmanager.register(event_type)(fn)

    return decorator

from app.plugins.hentaimetahub.merger import AnimeMerger
from app.plugins.hentaimetahub.models import AnimeMetadata
from app.plugins.hentaimetahub.sources import AnimeSource
from app.plugins.hentaimetahub.sources.anidb import AniDBSource
from app.plugins.hentaimetahub.sources.anilist import AniListSource
from app.plugins.hentaimetahub.sources.bangumi import BangumiSource


class HentaiMetaHub(_PluginBase):
    # 插件名称
    plugin_name = "成人动画元数据聚合"
    # 插件描述
    plugin_desc = "聚合 AniDB / AniList / Bangumi 的成人动画（里番）元数据，只提供标题/封面/标签等元信息，不涉及下载。"
    # 插件图标
    plugin_icon = "Moviepilot_A.png"
    # 插件版本
    plugin_version = "1.0.7"
    # 插件作者
    plugin_author = "dong"
    # 作者主页
    author_url = "https://github.com/dong"
    # 插件配置项ID前缀
    plugin_config_prefix = "hentaimetahub_"
    # 加载顺序
    plugin_order = 96
    # 可使用的用户级别
    auth_level = 1

    _enabled: bool = False
    _proxy: bool = False
    _as_discover_source: bool = False
    _as_recognize: bool = False
    _strategy: str = "merge"

    _anidb_cfg: Dict[str, Any] = {}
    _anilist_cfg: Dict[str, Any] = {}
    _bangumi_cfg: Dict[str, Any] = {}

    _merger: Optional[AnimeMerger] = None

    def init_plugin(self, config: dict = None):
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._proxy = bool(config.get("proxy"))
        self._as_discover_source = bool(config.get("as_discover_source", True))
        self._as_recognize = bool(config.get("as_recognize", False))
        self._strategy = str(config.get("strategy", "merge")) or "merge"

        titles_default = str(self.get_data_path() / "anidb-titles.xml")
        self._anidb_cfg = {
            "enabled": bool(config.get("anidb_enabled", False)),
            "priority": int(config.get("anidb_priority", 10) or 10),
            "client": config.get("anidb_client", ""),
            "clientver": config.get("anidb_clientver", "1"),
            "protover": config.get("anidb_protover", "1"),
            "rate_limit_seconds": float(config.get("anidb_rate_limit", 2.2) or 2.2),
            "titles_cache_path": config.get("anidb_titles_path") or titles_default,
        }
        self._anilist_cfg = {
            "enabled": bool(config.get("anilist_enabled", True)),
            "priority": int(config.get("anilist_priority", 20) or 20),
            "token": config.get("anilist_token", ""),
        }
        self._bangumi_cfg = {
            "enabled": bool(config.get("bangumi_enabled", False)),
            "priority": int(config.get("bangumi_priority", 30) or 30),
            "token": config.get("bangumi_token", ""),
            "user_agent": config.get("bangumi_ua", ""),
        }

        self._merger = self._build_merger()

    def _build_merger(self) -> AnimeMerger:
        sources: List[AnimeSource] = [
            AniDBSource(self._anidb_cfg, proxy=self._proxy),
            AniListSource(self._anilist_cfg, proxy=self._proxy),
            BangumiSource(self._bangumi_cfg, proxy=self._proxy),
        ]
        return AnimeMerger(sources)

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/search",
                "endpoint": self.api_search,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "搜索成人动画元数据",
                "description": "跨源搜索成人动画条目。",
            },
            {
                "path": "/fetch",
                "endpoint": self.api_fetch,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "抓取详情并跨源合并",
            },
            {
                "path": "/sources",
                "endpoint": self.api_sources,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "列出启用的数据源",
            },
            {
                "path": "/anilist-discover",
                "endpoint": self.api_anilist_discover,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "AniList 成人向探索",
            },
        ]

    # ===== 配置表单 =====

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        form = [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            self._col({"model": "enabled", "label": "启用插件"}, comp="VSwitch"),
                            self._col({"model": "proxy", "label": "使用代理服务器"}, comp="VSwitch"),
                            self._col({"model": "as_discover_source", "label": "注入到探索页"}, comp="VSwitch"),
                            self._col({"model": "as_recognize", "label": "辅助名称识别"}, comp="VSwitch"),
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            self._col(
                                {
                                    "model": "strategy",
                                    "label": "详情抓取策略",
                                    "items": [
                                        {"title": "多源合并 (merge)", "value": "merge"},
                                        {"title": "仅主源 (first)", "value": "first"},
                                    ],
                                },
                                comp="VSelect",
                                cols=12,
                                md=6,
                            ),
                        ],
                    },
                    # ---- AniDB ----
                    self._section_title("AniDB（推荐主源，需注册 Client，HTTP API 速率限制严格）"),
                    {
                        "component": "VRow",
                        "content": [
                            self._col({"model": "anidb_enabled", "label": "启用 AniDB"}, comp="VSwitch"),
                            self._col({"model": "anidb_priority", "label": "优先级", "type": "number"}, cols=12, md=2),
                            self._col({"model": "anidb_client", "label": "Client 名"}, cols=12, md=3),
                            self._col({"model": "anidb_clientver", "label": "Clientver"}, cols=12, md=2),
                            self._col({"model": "anidb_rate_limit", "label": "最小间隔(秒)", "type": "number"}, cols=12, md=2),
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            self._col(
                                {
                                    "model": "anidb_titles_path",
                                    "label": "anime-titles.xml 缓存路径 (留空使用插件数据目录)",
                                }
                            ),
                        ],
                    },
                    # ---- AniList ----
                    self._section_title("AniList（推荐次源，GraphQL，免鉴权可用）"),
                    {
                        "component": "VRow",
                        "content": [
                            self._col({"model": "anilist_enabled", "label": "启用 AniList"}, comp="VSwitch"),
                            self._col({"model": "anilist_priority", "label": "优先级", "type": "number"}, cols=12, md=2),
                            self._col({"model": "anilist_token", "label": "Token (可选)"}, cols=12, md=7),
                        ],
                    },
                    # ---- Bangumi ----
                    self._section_title("Bangumi 番组计划（补充中文信息，免鉴权可用）"),
                    {
                        "component": "VRow",
                        "content": [
                            self._col({"model": "bangumi_enabled", "label": "启用 Bangumi"}, comp="VSwitch"),
                            self._col({"model": "bangumi_priority", "label": "优先级", "type": "number"}, cols=12, md=2),
                            self._col({"model": "bangumi_token", "label": "Access Token (可选)"}, cols=12, md=7),
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            self._col({"model": "bangumi_ua", "label": "User-Agent (推荐自定义成项目名)"}),
                        ],
                    },
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "warning",
                            "variant": "tonal",
                            "class": "mt-3",
                            "text": "本插件仅聚合元数据，不提供资源下载。"
                            "AniDB 请严格遵守 API 速率限制，Bangumi 请设置友好的 User-Agent。",
                        },
                    },
                ],
            }
        ]
        defaults = {
            "enabled": False,
            "proxy": False,
            "as_discover_source": False,
            "as_recognize": False,
            "strategy": "merge",
            "anidb_enabled": False,
            "anidb_priority": 10,
            "anidb_client": "",
            "anidb_clientver": "1",
            "anidb_protover": "1",
            "anidb_rate_limit": 2.2,
            "anidb_titles_path": "",
            "anilist_enabled": True,
            "anilist_priority": 20,
            "anilist_token": "",
            "bangumi_enabled": False,
            "bangumi_priority": 30,
            "bangumi_token": "",
            "bangumi_ua": "MoviePilot-HentaiMetaHub/1.0",
        }
        return form, defaults

    @staticmethod
    def _col(props: Dict[str, Any], *, comp: str = "VTextField", cols: int = 12, md: int = 3) -> dict:
        return {
            "component": "VCol",
            "props": {"cols": cols, "md": md},
            "content": [{"component": comp, "props": props}],
        }

    @staticmethod
    def _section_title(text: str) -> dict:
        return {
            "component": "VAlert",
            "props": {
                "type": "info",
                "density": "compact",
                "variant": "tonal",
                "text": text,
                "class": "mt-2",
            },
        }

    # ===== 详情页 =====

    def get_page(self) -> List[dict]:
        sources_info = []
        for s in self._instantiate_sources():
            sources_info.append(
                {
                    "component": "VListItem",
                    "props": {"title": f"{s.label} (优先级 {s.priority})"},
                    "content": [
                        {
                            "component": "VListItemSubtitle",
                            "text": "可用" if s.is_available() else "未启用 / 配置不完整",
                        }
                    ],
                }
            )
        return [
            {
                "component": "VCard",
                "props": {"class": "ma-2"},
                "content": [
                    {
                        "component": "VCardTitle",
                        "text": "HentaiMetaHub - 数据源状态",
                    },
                    {
                        "component": "VList",
                        "props": {"density": "comfortable"},
                        "content": sources_info,
                    },
                    {
                        "component": "VCardText",
                        "content": [
                            {
                                "component": "div",
                                "props": {"class": "text-caption"},
                                "text": (
                                    "Hub API：\n"
                                    "  GET /api/v1/plugin/HentaiMetaHub/search?keyword=xxx\n"
                                    "  GET /api/v1/plugin/HentaiMetaHub/fetch?source=anilist&source_id=123\n"
                                    "  GET /api/v1/plugin/HentaiMetaHub/sources"
                                ),
                            }
                        ],
                    },
                ],
            }
        ]

    def _instantiate_sources(self) -> List[AnimeSource]:
        return [
            AniDBSource(self._anidb_cfg, proxy=self._proxy),
            AniListSource(self._anilist_cfg, proxy=self._proxy),
            BangumiSource(self._bangumi_cfg, proxy=self._proxy),
        ]

    # ===== Hub API =====

    def api_search(self, keyword: str = "", limit: int = 20, adult_only: bool = True) -> Dict[str, Any]:
        if not self._enabled or not self._merger:
            return {"success": False, "message": "插件未启用", "data": []}
        items = self._merger.search(keyword, limit=limit, adult_only=bool(adult_only))
        return {"success": True, "total": len(items), "data": [i.dict() for i in items]}

    def api_fetch(
        self,
        source: str = "",
        source_id: str = "",
        keyword: str = "",
        strategy: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self._enabled or not self._merger:
            return {"success": False, "message": "插件未启用", "data": None}
        meta = self._merger.fetch(
            source=source,
            source_id=source_id,
            keyword_fallback=keyword or None,
            strategy=strategy or self._strategy,
        )
        if not meta:
            return {"success": False, "message": "未找到元数据", "data": None}
        return {"success": True, "data": meta.dict()}

    def api_sources(self) -> Dict[str, Any]:
        data = []
        for s in self._instantiate_sources():
            data.append(
                {
                    "name": s.name,
                    "label": s.label,
                    "priority": s.priority,
                    "enabled": s.enabled,
                    "available": s.is_available(),
                }
            )
        return {"success": True, "data": data}

    # ===== DiscoverSource =====

    def api_anilist_discover(
        self,
        keyword: str = "",
        year: str = "",
        season: str = "",
        mformat: str = "",
        genre: str = "",
        sort: str = "POPULARITY_DESC",
        page: int = 1,
        count: int = 30,
    ) -> List[schemas.MediaInfo]:
        if not self._enabled:
            return []
        source = AniListSource(self._anilist_cfg, proxy=self._proxy)
        if not source.is_available():
            return []
        metas = source.discover(
            keyword=keyword or "",
            year=int(year) if str(year).isdigit() else None,
            season=season or None,
            mformat=mformat or None,
            genre=genre or None,
            sort=sort or "POPULARITY_DESC",
            page=max(int(page or 1), 1),
            per_page=min(int(count or 30), 50),
            adult_only=True,
        )
        return [self._to_media_info(m) for m in metas]

    @staticmethod
    def _to_media_info(meta: AnimeMetadata) -> schemas.MediaInfo:
        year = None
        if meta.season_year:
            year = str(meta.season_year)
        elif meta.start_date and meta.start_date[:4].isdigit():
            year = meta.start_date[:4]
        title = meta.title_cn or meta.title or meta.title_en or meta.title_romaji or meta.title_native or ""
        mtype = "电影" if (meta.format or "").upper() == "MOVIE" else "电视剧"
        return schemas.MediaInfo(
            type=mtype,
            title=title,
            year=year,
            title_year=f"{title} ({year})" if year else title,
            mediaid_prefix="anilist",
            media_id=meta.source_id,
            poster_path=meta.cover,
            backdrop_path=meta.banner or meta.cover,
            vote_average=meta.rating,
            release_date=meta.start_date,
            overview=meta.description,
            runtime=meta.duration,
        )

    @staticmethod
    def _to_detail_media_dict(meta: AnimeMetadata, *, mediaid_prefix: str, media_id: str) -> Dict[str, Any]:
        year = None
        if meta.season_year:
            year = str(meta.season_year)
        elif meta.start_date and meta.start_date[:4].isdigit():
            year = meta.start_date[:4]
        title = meta.title_cn or meta.title or meta.title_en or meta.title_romaji or meta.title_native or ""
        mtype = "电影" if (meta.format or "").upper() == "MOVIE" else "电视剧"
        genres = [{"id": i, "name": name} for i, name in enumerate([*meta.genres, *meta.tags]) if name]
        studios = [{"id": i, "name": name} for i, name in enumerate(meta.studios or []) if name]
        episode_run_time = [meta.duration] if meta.duration else []
        season_info = []
        if mtype == "电视剧":
            season_info.append(
                {
                    "season_number": 1,
                    "name": "第 1 季",
                    "air_date": meta.start_date,
                    "poster_path": meta.cover,
                    "overview": meta.description,
                    "vote_average": meta.rating,
                    "episode_count": meta.episodes or 0,
                }
            )
        detail_link = (
            meta.urls.get("anilist")
            or meta.urls.get(mediaid_prefix)
            or next(iter(meta.urls.values()), None)
        )
        return {
            "_custom_media_info": True,
            "source": meta.source or mediaid_prefix,
            "type": mtype,
            "title": title,
            "en_title": meta.title_en or meta.title_romaji,
            "year": year,
            "title_year": f"{title} ({year})" if year else title,
            "mediaid_prefix": mediaid_prefix,
            "media_id": str(media_id),
            "original_title": meta.title_native or meta.title_en or meta.title_romaji or title,
            "original_name": meta.title_native or meta.title_en or meta.title_romaji or title,
            "release_date": meta.start_date,
            "first_air_date": meta.start_date,
            "last_air_date": meta.end_date,
            "poster_path": meta.cover,
            "backdrop_path": meta.banner or meta.cover,
            "vote_average": meta.rating,
            "vote_count": meta.rating_count or 0,
            "overview": meta.description,
            "genres": genres,
            "genre_ids": [item["id"] for item in genres],
            "names": [name for name in meta.synonyms if name],
            "detail_link": detail_link,
            "homepage": detail_link,
            "adult": meta.is_adult,
            "status": meta.status,
            "runtime": meta.duration if mtype == "电影" else None,
            "episode_run_time": episode_run_time,
            "number_of_episodes": meta.episodes or 0,
            "number_of_seasons": 1 if mtype == "电视剧" else 0,
            "season_info": season_info,
            "production_companies": studios,
        }

    @staticmethod
    def _meta_year(meta: AnimeMetadata) -> str:
        if meta.season_year:
            return str(meta.season_year)
        if meta.start_date and meta.start_date[:4].isdigit():
            return meta.start_date[:4]
        return ""

    @staticmethod
    def _meta_title(meta: AnimeMetadata) -> str:
        return meta.title_cn or meta.title or meta.title_en or meta.title_romaji or meta.title_native or ""

    @staticmethod
    def _meta_media_type(meta: AnimeMetadata) -> str:
        return "movie" if (meta.format or "").upper() == "MOVIE" else "tv"

    @eventmanager.register(ChainEventType.DiscoverSource)
    def discover_source(self, event: Event):
        if not self._enabled or not self._as_discover_source:
            return
        source = AniListSource(self._anilist_cfg, proxy=self._proxy)
        if not source.is_available():
            return
        event_data: DiscoverSourceEventData = event.event_data
        src = schemas.DiscoverMediaSource(
            name="成人动画",
            mediaid_prefix="anilist",
            api_path="plugin/HentaiMetaHub/anilist-discover",
            filter_params={
                "keyword": "",
                "year": None,
                "season": None,
                "mformat": None,
                "genre": None,
                "sort": "POPULARITY_DESC",
            },
            filter_ui=self._anilist_filter_ui(),
        )
        if not event_data.extra_sources:
            event_data.extra_sources = [src]
        else:
            event_data.extra_sources.append(src)

    @staticmethod
    def _anilist_filter_ui() -> List[dict]:
        import datetime

        sort_dict = {
            "POPULARITY_DESC": "人气",
            "SCORE_DESC": "评分",
            "TRENDING_DESC": "趋势",
            "START_DATE_DESC": "最新",
            "FAVOURITES_DESC": "收藏",
        }
        format_dict = {
            "TV": "TV",
            "TV_SHORT": "TV短片",
            "MOVIE": "剧场版",
            "SPECIAL": "特别篇",
            "OVA": "OVA",
            "ONA": "ONA",
        }
        season_dict = {
            "WINTER": "冬",
            "SPRING": "春",
            "SUMMER": "夏",
            "FALL": "秋",
        }
        genre_dict = {
            "Action": "动作",
            "Adventure": "冒险",
            "Comedy": "喜剧",
            "Drama": "剧情",
            "Ecchi": "Ecchi",
            "Fantasy": "奇幻",
            "Hentai": "Hentai",
            "Horror": "恐怖",
            "Mecha": "机甲",
            "Music": "音乐",
            "Mystery": "悬疑",
            "Psychological": "心理",
            "Romance": "爱情",
            "Sci-Fi": "科幻",
            "Slice of Life": "日常",
            "Sports": "运动",
            "Supernatural": "超自然",
            "Thriller": "惊悚",
        }
        current_year = datetime.datetime.now().year
        year_dict = {str(y): str(y) for y in range(current_year, current_year - 15, -1)}

        def _chips(model: str, data: Dict[str, str]) -> dict:
            return {
                "component": "VChipGroup",
                "props": {"model": model},
                "content": [
                    {
                        "component": "VChip",
                        "props": {"filter": True, "tile": True, "value": k},
                        "text": v,
                    }
                    for k, v in data.items()
                ],
            }

        def _row(label: str, child: dict) -> dict:
            return {
                "component": "div",
                "props": {"class": "flex justify-start items-center mt-1"},
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "mr-5"},
                        "content": [{"component": "VLabel", "text": label}],
                    },
                    child,
                ],
            }

        return [
            _row("排序", _chips("sort", sort_dict)),
            _row("类型", _chips("mformat", format_dict)),
            _row("年份", _chips("year", year_dict)),
            _row("季度", _chips("season", season_dict)),
            _row("风格", _chips("genre", genre_dict)),
            _row(
                "关键字",
                {
                    "component": "VTextField",
                    "props": {
                        "model": "keyword",
                        "density": "compact",
                        "hide-details": True,
                        "placeholder": "标题 / 别名",
                    },
                },
            ),
        ]

    # ===== MediaRecognizeConvert =====

    @_maybe_register(_MRC_EVENT)
    def media_recognize_convert(self, event: Event):
        """拦截 ``anilist:`` 前缀。"""
        if not self._enabled:
            return
        event_data = event.event_data
        mediaid = getattr(event_data, "mediaid", None)
        convert_type = getattr(event_data, "convert_type", None)
        logger.info(
            "[HentaiMetaHub] MRC 事件进入 mediaid=%r convert_type=%r",
            mediaid, convert_type,
        )
        if not event_data or not mediaid:
            return
        if not mediaid.startswith("anilist:"):
            return
        source_id = mediaid.split(":", 1)[1]
        if not self._merger:
            logger.warning("[HentaiMetaHub] MRC 中止：_merger 未初始化，请检查插件是否启用且至少勾选一个源")
            event_data.media_dict = {}
            return
        active_names = [s.name for s in getattr(self._merger, "active_sources", [])]
        logger.info("[HentaiMetaHub] MRC 开始抓取 source=anilist id=%s 可用源=%s strategy=%s",
                    source_id, active_names, self._strategy)
        meta = None
        try:
            meta = self._merger.fetch(
                source="anilist",
                source_id=source_id,
                strategy=self._strategy,
            )
        except Exception as err:  # pragma: no cover
            logger.warning("[HentaiMetaHub] MRC 抓取异常: %s", err)
        if not meta:
            logger.warning("[HentaiMetaHub] MRC 抓取结果为空 source=anilist id=%s（检查 AniList 是否启用、网络/代理、该 ID 是否存在）",
                           source_id)
            event_data.media_dict = {}
            return
        year = self._meta_year(meta)
        title = self._meta_title(meta)
        media_type = self._meta_media_type(meta)
        logger.info("[HentaiMetaHub] MRC 命中 id=%s title=%r year=%s type=%s",
                    source_id, title, year, media_type)
        event_data.convert_type = "custom"
        event_data.media_dict = self._to_detail_media_dict(
            meta,
            mediaid_prefix="anilist",
            media_id=source_id,
        )

    # ===== NameRecognize =====

    @_maybe_register(_NAME_RECOGNIZE_EVENT)
    def name_recognize(self, event: Event):
        if not self._enabled or not self._as_recognize:
            return
        title = (event.event_data or {}).get("title") or ""
        if not title or not self._merger:
            self._send_empty(title)
            return
        items = self._merger.search(title, limit=1, adult_only=True)
        if not items:
            self._send_empty(title)
            return
        item = items[0]
        meta: Optional[AnimeMetadata] = None
        try:
            meta = self._merger.fetch(
                source=item.source,
                source_id=item.source_id,
                keyword_fallback=title,
                strategy="first",
            )
        except Exception as err:  # pragma: no cover
            logger.warning("[HentaiMetaHub] 识别抓取异常: %s", err)
        if not meta or not (meta.title or meta.title_cn):
            self._send_empty(title)
            return
        logger.info("[HentaiMetaHub] 识别命中 %s -> %s", title, meta.title_cn or meta.title)
        if _NAME_RECOGNIZE_RESULT_EVENT is None:
            return
        from app.core.event import eventmanager as em
        em.send_event(
            _NAME_RECOGNIZE_RESULT_EVENT,
            {
                "title": title,
                "name": meta.title_cn or meta.title,
                "year": str(meta.season_year) if meta.season_year else (meta.start_date or "")[:4],
                "season": 0,
                "episode": 0,
            },
        )

    @staticmethod
    def _send_empty(title: str) -> None:
        if _NAME_RECOGNIZE_RESULT_EVENT is None:
            return
        from app.core.event import eventmanager as em
        em.send_event(_NAME_RECOGNIZE_RESULT_EVENT, {"title": title})

    def stop_service(self):
        self._merger = None
