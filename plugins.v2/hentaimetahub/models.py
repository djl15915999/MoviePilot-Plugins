"""
HentaiMetaHub 元数据模型。

聚焦于成人动画（里番）条目，字段面向动画本身（标题、集数、标签、制作方等），
不涉及任何下载资源。
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AnimeCharacter(BaseModel):
    """角色信息。"""

    name: str = ""
    name_en: Optional[str] = None
    name_romaji: Optional[str] = None
    role: Optional[str] = None  # MAIN / SUPPORTING
    image: Optional[str] = None


class AnimeSearchItem(BaseModel):
    """搜索结果基础条目。"""

    source: str = ""
    source_id: str = ""
    title: str = ""
    title_en: Optional[str] = None
    title_romaji: Optional[str] = None
    title_native: Optional[str] = None
    cover: Optional[str] = None
    year: Optional[int] = None
    is_adult: bool = False
    url: Optional[str] = None


class AnimeMetadata(BaseModel):
    """聚合后的动画元数据。"""

    # 以"主源 ID"为主键；外部一般按 AniDB ID 或 AniList ID 聚合
    source: str = ""
    source_id: str = ""
    sources: List[str] = Field(default_factory=list)
    source_ids: Dict[str, str] = Field(default_factory=dict, description="各源 id 字典")

    title: str = ""
    title_en: Optional[str] = None
    title_romaji: Optional[str] = None
    title_native: Optional[str] = None
    title_cn: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)

    format: Optional[str] = None  # TV / OVA / ONA / MOVIE / SPECIAL
    status: Optional[str] = None  # FINISHED / RELEASING / NOT_YET_RELEASED
    episodes: Optional[int] = None
    duration: Optional[int] = Field(default=None, description="单集分钟")

    season: Optional[str] = None
    season_year: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    cover: Optional[str] = None
    banner: Optional[str] = None

    genres: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    studios: List[str] = Field(default_factory=list)
    producers: List[str] = Field(default_factory=list)

    characters: List[AnimeCharacter] = Field(default_factory=list)

    rating: Optional[float] = Field(default=None, description="0-10")
    rating_count: Optional[int] = None
    is_adult: bool = True  # 默认认为是成人向（里番）

    description: Optional[str] = None

    urls: Dict[str, str] = Field(default_factory=dict)
    raw: Dict[str, Any] = Field(default_factory=dict)

    def merge_from(self, other: "AnimeMetadata", *, overwrite: bool = False) -> None:
        """合并另一个源的元数据。"""
        for field in (
            "title",
            "title_en",
            "title_romaji",
            "title_native",
            "title_cn",
            "format",
            "status",
            "episodes",
            "duration",
            "season",
            "season_year",
            "start_date",
            "end_date",
            "cover",
            "banner",
            "rating",
            "rating_count",
            "description",
        ):
            val = getattr(other, field)
            if val in (None, "", 0):
                continue
            current = getattr(self, field)
            if overwrite or current in (None, "", 0):
                setattr(self, field, val)

        for attr in ("synonyms", "genres", "tags", "studios", "producers"):
            extra = getattr(other, attr) or []
            if not extra:
                continue
            current = getattr(self, attr)
            seen = {x.lower() for x in current if isinstance(x, str)}
            for item in extra:
                if not item or not isinstance(item, str):
                    continue
                if item.lower() in seen:
                    continue
                current.append(item)
                seen.add(item.lower())

        if other.characters:
            seen = {c.name for c in self.characters if c.name}
            for c in other.characters:
                if c.name and c.name not in seen:
                    self.characters.append(c)
                    seen.add(c.name)

        for key, url in (other.urls or {}).items():
            self.urls.setdefault(key, url)
        for key, sid in (other.source_ids or {}).items():
            self.source_ids.setdefault(key, sid)
        for key, raw in (other.raw or {}).items():
            self.raw.setdefault(key, raw)
        for src in other.sources:
            if src not in self.sources:
                self.sources.append(src)
