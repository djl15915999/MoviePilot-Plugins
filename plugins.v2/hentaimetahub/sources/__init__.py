"""
成人动画元数据源的抽象层。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from app.plugins.hentaimetahub.models import AnimeMetadata, AnimeSearchItem


class AnimeSource(ABC):
    """数据源基类。"""

    name: str = "base"
    label: str = "Base"
    priority: int = 100

    def __init__(self, config: Dict[str, Any], *, proxy: bool = False) -> None:
        self.config = config or {}
        self.proxy = proxy
        self.enabled = bool(self.config.get("enabled"))
        self.priority = int(self.config.get("priority", self.priority))

    @abstractmethod
    def search(self, keyword: str, *, limit: int = 20, adult_only: bool = True) -> List[AnimeSearchItem]:
        """按关键字检索。adult_only 表示是否仅返回成人向条目。"""

    @abstractmethod
    def fetch(self, source_id: str) -> Optional[AnimeMetadata]:
        """按本源 ID 抓取详情。"""

    def is_available(self) -> bool:
        return self.enabled
