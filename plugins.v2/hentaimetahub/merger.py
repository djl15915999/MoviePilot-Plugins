"""
成人动画元数据的多源合并逻辑。
"""
from __future__ import annotations

from typing import Iterable, List, Optional

from app.log import logger

from app.plugins.hentaimetahub.models import AnimeMetadata, AnimeSearchItem
from app.plugins.hentaimetahub.sources import AnimeSource


class AnimeMerger:
    def __init__(self, sources: Iterable[AnimeSource]):
        self._sources: List[AnimeSource] = sorted(
            [s for s in sources if s.is_available()],
            key=lambda s: s.priority,
        )

    @property
    def active_sources(self) -> List[AnimeSource]:
        return list(self._sources)

    # ---- 搜索 ----

    def search(self, keyword: str, *, limit: int = 20, adult_only: bool = True) -> List[AnimeSearchItem]:
        results: List[AnimeSearchItem] = []
        seen = set()
        for source in self._sources:
            try:
                items = source.search(keyword, limit=limit, adult_only=adult_only) or []
            except Exception as err:  # pragma: no cover
                logger.warning("[AnimeMerger] 源 %s 搜索异常: %s", source.name, err)
                continue
            for item in items:
                key = f"{source.name}:{item.source_id}"
                if key in seen:
                    continue
                seen.add(key)
                results.append(item)
            if len(results) >= limit:
                break
        return results[:limit]

    # ---- 详情 ----

    def fetch(
        self,
        *,
        source: str,
        source_id: str,
        keyword_fallback: Optional[str] = None,
        strategy: str = "merge",
        adult_only: bool = True,
    ) -> Optional[AnimeMetadata]:
        """按给定主源+ID 抓取详情，必要时用关键字在其他源中再次搜索以补全。

        :param source: 主源名（如 anilist）
        :param source_id: 主源 ID
        :param keyword_fallback: 在其他源中搜索时使用的关键字，通常是主源抓到的标题
        :param strategy: ``first`` 只返回主源；``merge`` 跨源合并
        """
        primary_source = next((s for s in self._sources if s.name == source), None)
        if not primary_source:
            return None
        try:
            aggregated = primary_source.fetch(source_id)
        except Exception as err:  # pragma: no cover
            logger.warning("[AnimeMerger] 主源 %s 抓取异常: %s", primary_source.name, err)
            aggregated = None
        if aggregated is None:
            return None
        if strategy == "first":
            return aggregated

        kw = keyword_fallback or aggregated.title_romaji or aggregated.title_en or aggregated.title or ""
        if not kw:
            return aggregated
        for s in self._sources:
            if s.name == primary_source.name:
                continue
            try:
                candidates = s.search(kw, limit=5, adult_only=adult_only) or []
            except Exception as err:  # pragma: no cover
                logger.warning("[AnimeMerger] 源 %s 搜索补全异常: %s", s.name, err)
                continue
            best = self._best_match(candidates, aggregated)
            if not best:
                continue
            try:
                meta = s.fetch(best.source_id)
            except Exception as err:  # pragma: no cover
                logger.warning("[AnimeMerger] 源 %s 抓取 %s 异常: %s", s.name, best.source_id, err)
                continue
            if meta:
                aggregated.merge_from(meta, overwrite=False)
        return aggregated

    @staticmethod
    def _best_match(candidates: List[AnimeSearchItem], target: AnimeMetadata) -> Optional[AnimeSearchItem]:
        if not candidates:
            return None
        targets = {
            (target.title or "").lower(),
            (target.title_en or "").lower(),
            (target.title_romaji or "").lower(),
            (target.title_native or "").lower(),
        }
        targets.discard("")
        for c in candidates:
            for t in (c.title, c.title_en, c.title_romaji, c.title_native):
                if t and t.lower() in targets:
                    return c
        # 模糊匹配：若年份相近、标题互为包含
        for c in candidates:
            if target.season_year and c.year and abs(target.season_year - c.year) <= 1:
                return c
        return candidates[0]
