"""
JavMetaHub - JAV 元数据多源聚合插件。

能力：
1. 对外暴露 Hub API：``/search``、``/fetch``、``/sources``，支持番号或关键字聚合检索；
2. 通过 ``ChainEventType.DiscoverSource`` 把 FANZA 源接入 MoviePilot 探索页；
3. 通过 ``EventType.NameRecognize`` 在主程序识别失败时，按番号返回规范化标题。

该插件只负责元数据（标题、封面、演员、标签、发行日期、时长、评分、简介等），
不涉及任何资源下载/磁力链接。
"""
from __future__ import annotations

import re
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


def _maybe_register(event_type):
    """兼容旧版 MoviePilot：当事件类型不存在时跳过注册，避免加载插件时报错。"""

    def decorator(fn):
        if event_type is None:
            return fn
        return eventmanager.register(event_type)(fn)

    return decorator

from app.plugins.javmetahub.merger import JavMerger
from app.plugins.javmetahub.models import JavMetadata
from app.plugins.javmetahub.sources import JavSource, normalize_code
from app.plugins.javmetahub.sources.fanza import FanzaSource
from app.plugins.javmetahub.sources.javdb import JavDBSource
from app.plugins.javmetahub.sources.javlibrary import JavLibrarySource


CODE_IN_NAME_PATTERN = re.compile(r"([A-Z]{2,6})[-_ ]?(\d{2,5})", re.IGNORECASE)


class JavMetaHub(_PluginBase):
    # 插件名称
    plugin_name = "JAV 元数据聚合"
    # 插件描述
    plugin_desc = "聚合 FANZA/DMM、JavLibrary、JavDB 的元数据，仅提供标题/封面/演员/标签/评分等信息，不涉及下载。"
    # 插件图标
    plugin_icon = "Moviepilot_A.png"
    # 插件版本
    plugin_version = "1.0.1"
    # 插件作者
    plugin_author = "dong"
    # 作者主页
    author_url = "https://github.com/dong"
    # 插件配置项ID前缀
    plugin_config_prefix = "javmetahub_"
    # 加载顺序
    plugin_order = 95
    # 可使用的用户级别
    auth_level = 1

    # 配置项
    _enabled: bool = False
    _proxy: bool = False
    _as_discover_source: bool = False
    _as_recognize: bool = False
    _strategy: str = "merge"

    _fanza_cfg: Dict[str, Any] = {}
    _javlib_cfg: Dict[str, Any] = {}
    _javdb_cfg: Dict[str, Any] = {}

    # 运行时
    _merger: Optional[JavMerger] = None

    def init_plugin(self, config: dict = None):
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._proxy = bool(config.get("proxy"))
        self._as_discover_source = bool(config.get("as_discover_source", True))
        self._as_recognize = bool(config.get("as_recognize", False))
        self._strategy = str(config.get("strategy", "merge")) or "merge"

        self._fanza_cfg = {
            "enabled": bool(config.get("fanza_enabled", True)),
            "priority": int(config.get("fanza_priority", 10) or 10),
            "api_id": config.get("fanza_api_id", ""),
            "affiliate_id": config.get("fanza_affiliate_id", ""),
            "site": config.get("fanza_site", FanzaSource.DEFAULT_SITE),
            "service": config.get("fanza_service", FanzaSource.DEFAULT_SERVICE),
            "floor": config.get("fanza_floor", FanzaSource.DEFAULT_FLOOR),
        }
        self._javlib_cfg = {
            "enabled": bool(config.get("javlib_enabled", False)),
            "priority": int(config.get("javlib_priority", 30) or 30),
            "base_url": config.get("javlib_base_url", JavLibrarySource.DEFAULT_BASE),
            "lang": config.get("javlib_lang", JavLibrarySource.DEFAULT_LANG),
            "cookie": config.get("javlib_cookie", ""),
            "user_agent": config.get("javlib_user_agent", ""),
        }
        self._javdb_cfg = {
            "enabled": bool(config.get("javdb_enabled", False)),
            "priority": int(config.get("javdb_priority", 40) or 40),
            "base_url": config.get("javdb_base_url", JavDBSource.DEFAULT_BASE),
            "cookie": config.get("javdb_cookie", ""),
            "user_agent": config.get("javdb_user_agent", ""),
        }

        self._merger = self._build_merger()

    def _build_merger(self) -> JavMerger:
        sources: List[JavSource] = [
            FanzaSource(self._fanza_cfg, proxy=self._proxy),
            JavLibrarySource(self._javlib_cfg, proxy=self._proxy),
            JavDBSource(self._javdb_cfg, proxy=self._proxy),
        ]
        return JavMerger(sources)

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
                "summary": "搜索 JAV 元数据",
                "description": "按关键字/番号在启用的数据源中搜索。",
            },
            {
                "path": "/fetch",
                "endpoint": self.api_fetch,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "按番号抓取完整元数据",
                "description": "多源聚合返回一条完整的元数据。",
            },
            {
                "path": "/sources",
                "endpoint": self.api_sources,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "列出当前启用的数据源",
                "description": "返回每个数据源的启用状态与优先级。",
            },
            {
                "path": "/fanza-discover",
                "endpoint": self.api_fanza_discover,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "FANZA 探索数据源",
                "description": "供 MoviePilot 探索页调用的数据源接口。",
            },
        ]

    # ===== 配置表单 =====

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        form = [
            {
                "component": "VForm",
                "content": [
                    # ---- 总开关 ----
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
                                        {"title": "仅取最高优先级命中 (first)", "value": "first"},
                                    ],
                                },
                                comp="VSelect",
                                cols=12,
                                md=6,
                            ),
                        ],
                    },
                    # ---- FANZA ----
                    self._section_title("FANZA / DMM API（推荐主源，需官方 API ID / Affiliate ID）"),
                    {
                        "component": "VRow",
                        "content": [
                            self._col({"model": "fanza_enabled", "label": "启用 FANZA"}, comp="VSwitch"),
                            self._col({"model": "fanza_priority", "label": "优先级", "type": "number"}, cols=12, md=2),
                            self._col({"model": "fanza_api_id", "label": "API ID"}, cols=12, md=3),
                            self._col({"model": "fanza_affiliate_id", "label": "Affiliate ID (xxx-990~999)"}, cols=12, md=4),
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            self._col({"model": "fanza_site", "label": "site"}, cols=12, md=3),
                            self._col({"model": "fanza_service", "label": "service"}, cols=12, md=3),
                            self._col({"model": "fanza_floor", "label": "floor"}, cols=12, md=3),
                        ],
                    },
                    # ---- JavLibrary ----
                    self._section_title("JavLibrary（fallback，HTML 爬取，可能受 Cloudflare 影响）"),
                    {
                        "component": "VRow",
                        "content": [
                            self._col({"model": "javlib_enabled", "label": "启用 JavLibrary"}, comp="VSwitch"),
                            self._col({"model": "javlib_priority", "label": "优先级", "type": "number"}, cols=12, md=2),
                            self._col(
                                {
                                    "model": "javlib_lang",
                                    "label": "语言",
                                    "items": [
                                        {"title": "简体中文", "value": "cn"},
                                        {"title": "繁体中文", "value": "tw"},
                                        {"title": "日本語", "value": "ja"},
                                        {"title": "English", "value": "en"},
                                    ],
                                },
                                comp="VSelect",
                                cols=12,
                                md=3,
                            ),
                            self._col({"model": "javlib_base_url", "label": "镜像地址"}, cols=12, md=3),
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            self._col({"model": "javlib_cookie", "label": "Cookie (可选，用于规避人机验证)"}),
                        ],
                    },
                    # ---- JavDB ----
                    self._section_title("JavDB（fallback，HTML 爬取，可能受地区/反爬限制）"),
                    {
                        "component": "VRow",
                        "content": [
                            self._col({"model": "javdb_enabled", "label": "启用 JavDB"}, comp="VSwitch"),
                            self._col({"model": "javdb_priority", "label": "优先级", "type": "number"}, cols=12, md=2),
                            self._col({"model": "javdb_base_url", "label": "镜像地址"}, cols=12, md=4),
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            self._col({"model": "javdb_cookie", "label": "Cookie (可选，登录后数据更完整)"}),
                        ],
                    },
                    # ---- 提示 ----
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "warning",
                            "variant": "tonal",
                            "class": "mt-3",
                            "text": "本插件仅聚合元数据（标题/封面/演员/标签/评分等），"
                            "不提供任何资源下载/磁力链接；"
                            "使用各数据源时请遵守对应站点的服务条款。",
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
            "fanza_enabled": True,
            "fanza_priority": 10,
            "fanza_api_id": "",
            "fanza_affiliate_id": "",
            "fanza_site": FanzaSource.DEFAULT_SITE,
            "fanza_service": FanzaSource.DEFAULT_SERVICE,
            "fanza_floor": FanzaSource.DEFAULT_FLOOR,
            "javlib_enabled": False,
            "javlib_priority": 30,
            "javlib_base_url": JavLibrarySource.DEFAULT_BASE,
            "javlib_lang": JavLibrarySource.DEFAULT_LANG,
            "javlib_cookie": "",
            "javlib_user_agent": "",
            "javdb_enabled": False,
            "javdb_priority": 40,
            "javdb_base_url": JavDBSource.DEFAULT_BASE,
            "javdb_cookie": "",
            "javdb_user_agent": "",
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
        if not self._merger:
            return []
        sources_info = []
        for s in [
            FanzaSource(self._fanza_cfg, proxy=self._proxy),
            JavLibrarySource(self._javlib_cfg, proxy=self._proxy),
            JavDBSource(self._javdb_cfg, proxy=self._proxy),
        ]:
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
                        "text": "JavMetaHub - 数据源状态",
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
                                    "  GET /api/v1/plugin/JavMetaHub/search?keyword=ABP-123\n"
                                    "  GET /api/v1/plugin/JavMetaHub/fetch?code=ABP-123\n"
                                    "  GET /api/v1/plugin/JavMetaHub/sources"
                                ),
                            }
                        ],
                    },
                ],
            }
        ]

    # ===== Hub API =====

    def api_search(self, keyword: str = "", limit: int = 20) -> Dict[str, Any]:
        if not self._enabled or not self._merger:
            return {"success": False, "message": "插件未启用", "data": []}
        items = self._merger.search(keyword, limit=limit)
        return {
            "success": True,
            "total": len(items),
            "data": [i.dict() for i in items],
        }

    def api_fetch(self, code: str = "", strategy: Optional[str] = None) -> Dict[str, Any]:
        if not self._enabled or not self._merger:
            return {"success": False, "message": "插件未启用", "data": None}
        meta = self._merger.fetch(code, strategy=strategy or self._strategy)
        if not meta:
            return {"success": False, "message": "未找到元数据", "data": None}
        return {"success": True, "data": meta.dict()}

    def api_sources(self) -> Dict[str, Any]:
        if not self._merger:
            return {"success": False, "message": "插件未启用", "data": []}
        payload = []
        for s in [
            FanzaSource(self._fanza_cfg, proxy=self._proxy),
            JavLibrarySource(self._javlib_cfg, proxy=self._proxy),
            JavDBSource(self._javdb_cfg, proxy=self._proxy),
        ]:
            payload.append(
                {
                    "name": s.name,
                    "label": s.label,
                    "priority": s.priority,
                    "enabled": s.enabled,
                    "available": s.is_available(),
                }
            )
        return {"success": True, "data": payload}

    # ===== DiscoverSource =====

    def api_fanza_discover(
        self,
        keyword: str = "",
        sort: str = "rank",
        year: str = "",
        page: int = 1,
        count: int = 30,
    ) -> List[schemas.MediaInfo]:
        """供探索页调用：按过滤条件浏览 FANZA。"""
        if not self._enabled:
            return []
        source = FanzaSource(self._fanza_cfg, proxy=self._proxy)
        if not source.is_available():
            return []
        metas = source.discover(
            keyword=keyword or "",
            sort=sort or "rank",
            year=year or None,
            page=max(int(page or 1), 1),
            count=int(count or 30),
        )
        return [self._to_media_info(m) for m in metas]

    @staticmethod
    def _to_media_info(meta: JavMetadata) -> schemas.MediaInfo:
        year = (meta.release_date or "")[:4] or None
        title = meta.title or meta.code
        overview = meta.description
        if not overview and meta.actors:
            overview = "出演：" + "、".join(a.name for a in meta.actors[:8])
        return schemas.MediaInfo(
            type="电影",
            title=title,
            year=year,
            title_year=f"{title} ({year})" if year else title,
            mediaid_prefix="jav",
            media_id=meta.code,
            poster_path=meta.cover,
            backdrop_path=meta.cover,
            vote_average=meta.rating,
            release_date=meta.release_date,
            overview=overview,
            runtime=meta.duration,
        )

    @eventmanager.register(ChainEventType.DiscoverSource)
    def discover_source(self, event: Event):
        if not self._enabled or not self._as_discover_source:
            return
        source = FanzaSource(self._fanza_cfg, proxy=self._proxy)
        if not source.is_available():
            return
        event_data: DiscoverSourceEventData = event.event_data
        src = schemas.DiscoverMediaSource(
            name="FANZA",
            mediaid_prefix="jav",
            api_path="plugin/JavMetaHub/fanza-discover",
            filter_params={
                "keyword": "",
                "sort": "rank",
                "year": None,
            },
            filter_ui=self._fanza_filter_ui(),
        )
        if not event_data.extra_sources:
            event_data.extra_sources = [src]
        else:
            event_data.extra_sources.append(src)

    @staticmethod
    def _fanza_filter_ui() -> List[dict]:
        """FANZA 探索页过滤器 UI。"""
        import datetime

        sort_dict = {
            "rank": "人气",
            "date": "新作",
            "review": "评分",
            "price": "价格",
            "match": "相关度",
        }
        sort_ui = [
            {
                "component": "VChip",
                "props": {"filter": True, "tile": True, "value": k},
                "text": v,
            }
            for k, v in sort_dict.items()
        ]

        current_year = datetime.datetime.now().year
        year_dict = {str(y): str(y) for y in range(current_year, current_year - 15, -1)}
        year_dict.update({
            "2010": "2010s",
            "2000": "2000s",
            "1990": "1990s",
        })
        year_ui = [
            {
                "component": "VChip",
                "props": {"filter": True, "tile": True, "value": k},
                "text": v,
            }
            for k, v in year_dict.items()
        ]

        return [
            {
                "component": "div",
                "props": {"class": "flex justify-start items-center"},
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "mr-5"},
                        "content": [{"component": "VLabel", "text": "排序"}],
                    },
                    {
                        "component": "VChipGroup",
                        "props": {"model": "sort"},
                        "content": sort_ui,
                    },
                ],
            },
            {
                "component": "div",
                "props": {"class": "flex justify-start items-center mt-1"},
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "mr-5"},
                        "content": [{"component": "VLabel", "text": "年份"}],
                    },
                    {
                        "component": "VChipGroup",
                        "props": {"model": "year"},
                        "content": year_ui,
                    },
                ],
            },
            {
                "component": "div",
                "props": {"class": "flex justify-start items-center mt-1"},
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "mr-5"},
                        "content": [{"component": "VLabel", "text": "关键字"}],
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "keyword",
                            "density": "compact",
                            "hide-details": True,
                            "placeholder": "番号/演员/厂商",
                        },
                    },
                ],
            },
        ]

    # ===== MediaRecognizeConvert =====

    @_maybe_register(_MRC_EVENT)
    def media_recognize_convert(self, event: Event):
        """拦截 ``jav:`` 前缀，防止主程序走 TMDB/豆瓣识别导致 500。

        MoviePilot 会在用户点击探索卡片后派发该事件以把自定义数据源的 ID
        转成 themoviedb / douban 的数据结构。对于 JAV 元数据没法转换，
        所以我们把 media_dict 填上最少必要字段，让前端能安全渲染。
        """
        if not self._enabled:
            return
        event_data = event.event_data
        mediaid = getattr(event_data, "mediaid", None)
        if not event_data or not mediaid:
            return
        if getattr(event_data, "convert_type", None) != "themoviedb":
            return
        if not mediaid.startswith("jav:"):
            return
        code = mediaid.split(":", 1)[1]
        meta = self._merger.fetch(code, strategy=self._strategy) if self._merger else None
        if not meta:
            # 未命中时返回空 dict，避免主程序继续识别
            event_data.media_dict = {}
            return
        year = (meta.release_date or "")[:4]
        event_data.media_dict = {
            "id": 0,
            "title": meta.title or meta.code,
            "original_title": meta.title_original or meta.title or meta.code,
            "overview": meta.description or "",
            "poster_path": meta.cover,
            "backdrop_path": meta.cover,
            "release_date": meta.release_date,
            "vote_average": meta.rating,
            "genres": [{"id": i, "name": g} for i, g in enumerate(meta.genres)],
            "runtime": meta.duration,
            "media_type": "movie",
            "year": year,
        }

    # ===== NameRecognize =====

    @eventmanager.register(EventType.NameRecognize)
    def name_recognize(self, event: Event):
        if not self._enabled or not self._as_recognize:
            return
        title = (event.event_data or {}).get("title") or ""
        if not title:
            return
        match = CODE_IN_NAME_PATTERN.search(title)
        if not match:
            self._send_empty(title)
            return
        code = normalize_code(match.group(0))
        meta = self._merger.fetch(code, strategy="first") if self._merger else None
        if not meta or not meta.title:
            self._send_empty(title)
            return
        logger.info("[JavMetaHub] 识别命中番号 %s -> %s", code, meta.title)
        from app.core.event import eventmanager as em
        em.send_event(
            EventType.NameRecognizeResult,
            {
                "title": title,
                "name": meta.title,
                "year": (meta.release_date or "")[:4] or "",
                "season": 0,
                "episode": 0,
            },
        )

    @staticmethod
    def _send_empty(title: str) -> None:
        from app.core.event import eventmanager as em
        em.send_event(EventType.NameRecognizeResult, {"title": title})

    def stop_service(self):
        self._merger = None
