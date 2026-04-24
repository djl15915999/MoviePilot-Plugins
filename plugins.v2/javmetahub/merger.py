"""
多源合并。

核心思想：
- 按 ``priority`` 从小到大的顺序依次抓取，小值为高优先级；
- 高优先级源命中后优先填充字段，低优先级源只填补缺失字段；
- 列表字段（演员、标签、截图）会进行去重合并，以获得更全信息；
- 调用方可选择串行抓取（精确）或仅取最高优先级命中源（最快）。
"""
from __future__ import annotations

from typing import Iterable, List, Optional

from app.log import logger

from app.plugins.javmetahub.models import JavMetadata, JavSearchItem
from app.plugins.javmetahub.sources import JavSource, normalize_code


class JavMerger:
    """把多个源的结果聚合为一条完整元数据。"""

    def __init__(self, sources: Iterable[JavSource]):
        # 只保留启用且可用的源，并按 priority 升序排序（值越小越优先）
        self._sources: List[JavSource] = sorted(
            [s for s in sources if s.is_available()],
            key=lambda s: s.priority,
        )

    @property
    def active_sources(self) -> List[JavSource]:
        return list(self._sources)

    # ---- 搜索 ----

    def search(self, keyword: str, *, limit: int = 20) -> List[JavSearchItem]:
        results: List[JavSearchItem] = []
        seen_codes = set()
        for source in self._sources:
            try:
                items = source.search(keyword, limit=limit) or []
            except Exception as err:  # pragma: no cover
                logger.warning("[JavMerger] 源 %s 搜索异常: %s", source.name, err)
                continue
            for item in items:
                key = item.code or f"{source.name}:{item.title}"
                if key in seen_codes:
                    continue
                seen_codes.add(key)
                results.append(item)
            if len(results) >= limit:
                break
        return results[:limit]

    # ---- 详情 ----

    def fetch(self, code: str, *, strategy: str = "merge") -> Optional[JavMetadata]:
        """按番号抓取详情。

        :param strategy:
            - ``first``: 返回最高优先级命中的源
            - ``merge`` (默认): 依次抓取所有启用源，按优先级合并
        """
        code = normalize_code(code)
        if not code:
            return None
        aggregated: Optional[JavMetadata] = None
        for source in self._sources:
            try:
                meta = source.fetch(code)
            except Exception as err:  # pragma: no cover
                logger.warning("[JavMerger] 源 %s 抓取 %s 异常: %s", source.name, code, err)
                continue
            if not meta:
                continue
            if aggregated is None:
                aggregated = meta
                if strategy == "first":
                    return aggregated
                continue
            aggregated.merge_from(meta, overwrite=False)
        return aggregated
