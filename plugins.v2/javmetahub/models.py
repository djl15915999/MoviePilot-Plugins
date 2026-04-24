"""
JavMetaHub 元数据模型。
只包含元数据字段（标题、封面、演员、标签、发行日期、简介等），
不涉及下载资源与磁力链接。
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class JavActor(BaseModel):
    """JAV 演员。"""

    name: str = ""
    name_en: Optional[str] = None
    name_romaji: Optional[str] = None
    actor_id: Optional[str] = None
    image: Optional[str] = None


class JavSearchItem(BaseModel):
    """搜索结果中的基础条目。"""

    source: str = ""
    code: str = ""
    title: str = ""
    cover: Optional[str] = None
    release_date: Optional[str] = None
    url: Optional[str] = None


class JavMetadata(BaseModel):
    """聚合后的 JAV 元数据。"""

    code: str = Field(..., description="番号，如 ABP-123，统一大写+连字符")

    source: str = Field("", description="主命中来源")
    sources: List[str] = Field(default_factory=list, description="命中此作品的来源列表")

    title: str = ""
    title_en: Optional[str] = None
    title_original: Optional[str] = None

    release_date: Optional[str] = None
    duration: Optional[int] = Field(default=None, description="分钟")

    studio: Optional[str] = None
    label: Optional[str] = None
    series: Optional[str] = None
    director: Optional[str] = None

    cover: Optional[str] = None
    poster: Optional[str] = None
    screenshots: List[str] = Field(default_factory=list)

    actors: List[JavActor] = Field(default_factory=list)
    genres: List[str] = Field(default_factory=list)

    rating: Optional[float] = Field(default=None, description="0-10")
    description: Optional[str] = None

    urls: Dict[str, str] = Field(default_factory=dict, description="各源详情页 URL")
    raw: Dict[str, Any] = Field(default_factory=dict, description="各源原始数据（调试用）")

    def merge_from(self, other: "JavMetadata", *, overwrite: bool = False) -> None:
        """把另一个来源的元数据合并进当前对象。

        - overwrite=False 时，只有当前字段为空才使用 other 的值
        - 列表字段（actors/genres/screenshots）会合并去重
        """
        for field in (
            "title",
            "title_en",
            "title_original",
            "release_date",
            "duration",
            "studio",
            "label",
            "series",
            "director",
            "cover",
            "poster",
            "rating",
            "description",
        ):
            val = getattr(other, field)
            if val in (None, "", 0):
                continue
            current = getattr(self, field)
            if overwrite or current in (None, "", 0):
                setattr(self, field, val)

        if other.screenshots:
            seen = set(self.screenshots)
            for url in other.screenshots:
                if url and url not in seen:
                    self.screenshots.append(url)
                    seen.add(url)

        if other.genres:
            seen = {g.lower() for g in self.genres}
            for g in other.genres:
                if g and g.lower() not in seen:
                    self.genres.append(g)
                    seen.add(g.lower())

        if other.actors:
            seen = {a.name for a in self.actors if a.name}
            for actor in other.actors:
                if not actor.name:
                    continue
                if actor.name in seen:
                    continue
                self.actors.append(actor)
                seen.add(actor.name)

        for source, url in (other.urls or {}).items():
            self.urls.setdefault(source, url)

        for source in other.sources:
            if source not in self.sources:
                self.sources.append(source)

        for source, raw in (other.raw or {}).items():
            self.raw.setdefault(source, raw)
