# HentaiMetaHub - 成人动画元数据聚合

多源聚合的成人动画（里番）元数据插件，只聚合 **元数据**（标题、封面、集数、标签、简介等），不提供任何资源下载/磁力链接。

## 支持的数据源

| 优先级 | 数据源 | 类型 | 说明 |
|--------|-------|------|------|
| 10 | **AniDB** | HTTP XML API | 推荐主源，需注册 Client；速率限制严格 |
| 20 | **AniList** | GraphQL | 免鉴权即可查询，支持 `isAdult: true` 过滤 |
| 30 | Bangumi | v0 REST | 补充中文名 / 中文标签 |

合并策略下，优先级数值越小越优先填充主字段，列表字段（标签/别名/工作室）会跨源合并。

## 各源配置要点

### AniDB

1. 在 <https://anidb.net/software/add> 注册一个 **HTTP Client**，获取 Client 名和版本；
2. 在插件中填入 Client、Clientver；
3. `最小间隔(秒)` 保持 ≥ 2.0，否则可能被封 IP；
4. 关键字搜索依赖离线 dump `anime-titles.xml`；插件会在首次使用时自动下载到 `anime-titles.xml 缓存路径`（默认使用插件数据目录）。

### AniList

- 无需鉴权即可搜索/查询；如需更高配额可在 <https://anilist.co/settings/developer> 生成 Token 并填入；
- 使用 GraphQL `isAdult: true` 精确过滤成人向条目。

### Bangumi

- 使用 v0 新版 API，`https://api.bgm.tv/v0/`；
- 强烈建议自定义 User-Agent（填入自己的项目名/联系方式）；
- 可选 Access Token：在 <https://next.bgm.tv/demo/access-token> 申请。

## 对外 Hub API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/plugin/HentaiMetaHub/search?keyword=xxx` | 多源搜索 |
| GET | `/api/v1/plugin/HentaiMetaHub/fetch?source=anilist&source_id=123` | 指定主源 ID 聚合抓取 |
| GET | `/api/v1/plugin/HentaiMetaHub/sources` | 列出数据源状态 |
| GET | `/api/v1/plugin/HentaiMetaHub/anilist_discover?apikey=...` | 探索页内部使用 |

## 探索页集成

开启 "注入到探索页" 后，MoviePilot 探索页会出现一个 **成人动画 (AniList)** 标签页，可按关键字浏览。

## 名称识别增强

开启 "辅助名称识别" 后，当主程序识别失败时，插件会按标题尝试在数据源中命中并返回规范化信息。

## 风险与合规提示

- 只做元数据聚合，不下载任何资源；
- 遵守各数据源的 Terms of Service，尤其是 AniDB 的速率限制；
- 合规性由使用者自行承担。

## 更新日志

- **v1.0.7** MRC 返回 AniList 当前源完整 `MediaInfo`，配合 MoviePilot 自定义源详情补丁可不依赖 TMDB/豆瓣直接展示。
- **v1.0.6** 修复 AniList 成人向条目详情页未识别：MRC 统一走 TMDB 转换，常规识别失败后使用 `include_adult` 搜索并严格匹配标题/年份。
- **v1.0.5** 修复探索详情页 TMDB id=0 导致进入失败的问题：MRC 仅在映射到真实 TMDB ID 时返回非空 `media_dict`，否则交给标题兜底。
- **v1.0.4** MRC 处理增加抓取过程日志（可用源、命中/为空），方便定位识别失败原因。
- **v1.0.3** 放宽 MediaRecognizeConvert 的 `convert_type` 过滤，增加事件入口日志便于排障。
- **v1.0.2** 兼容旧版 MoviePilot：当 `EventType.NameRecognize` 不存在时跳过注册，避免加载失败。
- **v1.0.1** 修正 `level` 与 `auth_level` 不一致导致插件在已安装列表中不可见的问题。
- **v1.0.0** 首版：AniDB + AniList + Bangumi 三源聚合，Hub API，探索源，名称识别。
