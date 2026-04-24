"""
JAV 元数据源的统一抽象层。

每个数据源实现都需要继承 ``JavSource``，并实现 ``search`` 与 ``fetch`` 两个方法。
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from app.plugins.javmetahub.models import JavMetadata, JavSearchItem


CODE_PATTERN = re.compile(r"(?P<prefix>[A-Z]{2,6})[-_\s]?(?P<number>\d{2,5})", re.IGNORECASE)


def normalize_code(code: str) -> str:
    """把任意形态的番号规范化为大写 + 连字符格式，如 abp123 -> ABP-123。"""
    if not code:
        return ""
    raw = code.strip()
    match = CODE_PATTERN.search(raw)
    if not match:
        return raw.upper()
    prefix = match.group("prefix").upper()
    number = match.group("number").lstrip("0") or "0"
    # 保留零填充，统一 3 位以上
    padded = number.zfill(max(3, len(match.group("number"))))
    return f"{prefix}-{padded}"


class JavSource(ABC):
    """数据源基类。"""

    name: str = "base"
    label: str = "Base"
    priority: int = 100

    def __init__(self, config: Dict[str, Any], *, proxy: bool = False) -> None:
        self.config = config or {}
        self.proxy = proxy
        self.enabled = bool(self.config.get("enabled"))
        self.priority = int(self.config.get("priority", self.priority))

    # ---- 子类实现 ----

    @abstractmethod
    def search(self, keyword: str, *, limit: int = 20) -> List[JavSearchItem]:
        """按关键字或番号搜索。"""

    @abstractmethod
    def fetch(self, code: str) -> Optional[JavMetadata]:
        """按规范化番号抓取详情。"""

    # ---- 辅助 ----

    def is_available(self) -> bool:
        return self.enabled
