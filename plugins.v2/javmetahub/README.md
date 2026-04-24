# JavMetaHub - JAV 元数据聚合

多源聚合的 JAV 元数据插件，仅负责标题/封面/演员/标签/评分等元数据，不提供任何资源下载或磁力链接。

## 支持的数据源

| 优先级 | 数据源 | 类型 | 说明 |
|--------|-------|------|------|
| 10 | **FANZA / DMM** | 官方 Web API | 推荐主源，需自备 API ID 与 Affiliate ID |
| 30 | JavLibrary | HTML 爬取 | fallback，可能受 Cloudflare 影响 |
| 40 | JavDB | HTML 爬取 | fallback，可能需登录 cookie |

优先级数值越小越优先，列表字段（演员/标签/截图）在合并策略下会跨源合并。

## 获取 FANZA / DMM API 凭证

1. 前往 <https://affiliate.dmm.com/> 注册账号并完成审核；
2. 在会员中心创建 "Web Service API" 应用，获取 **API ID**；
3. 生成 **Affiliate ID**，注意插件要求使用 `xxx-990` ~ `xxx-999` 结尾的子 ID（DMM 官方 API 限制）；
4. 把两个值填入插件配置。

DMM 官方文档：<https://affiliate.dmm.com/api/>

## 对外 Hub API

所有接口都以 `bear` 认证（前端需要带 JWT），部署后路径如下：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/plugin/JavMetaHub/search?keyword=xxx` | 多源搜索 |
| GET | `/api/v1/plugin/JavMetaHub/fetch?code=ABP-123` | 按番号聚合抓取详情 |
| GET | `/api/v1/plugin/JavMetaHub/sources` | 列出数据源状态 |
| GET | `/api/v1/plugin/JavMetaHub/fanza-discover` | FANZA 探索页内部使用，未配置凭证时尝试 fallback |
| GET | `/api/v1/plugin/JavMetaHub/javdb-discover` | JavDB 探索页内部使用 |
| GET | `/api/v1/plugin/JavMetaHub/javlibrary-discover` | JavLibrary 探索页内部使用 |

`/fetch` 接受 `strategy=first|merge`：

- `first`：取最高优先级命中源返回，最快
- `merge`（默认）：按优先级跨源合并补全字段

## 探索页集成

在插件配置中打开 "注入到探索页"，MoviePilot 探索页会新增 **FANZA**、**JavDB**、**JavLibrary** 标签页。
FANZA 未配置 API ID / Affiliate ID 时，FANZA 标签页会按优先级尝试使用 JavDB / JavLibrary 兜底返回列表。

## 名称识别增强

在插件配置中打开 "辅助名称识别"，当主程序遇到 `ABP-123` 这类番号而识别失败时，插件会尝试按番号命中数据源并返回规范化标题。

## 风险与合规提示

- 本插件只做元数据聚合，不下载任何资源，不提供磁力/种子/直链；
- JavLibrary / JavDB 是 HTML 爬取，稳定性受对方站点反爬策略影响，必要时需要配置 cookie/代理；
- 使用各数据源时请遵守对方 Terms of Service；
- 合规性由使用者自行承担。

## 更新日志

- **v1.0.6** 探索页增加 JavDB / JavLibrary 多源列表；FANZA 未配置凭证时自动尝试 fallback 源。
- **v1.0.0** 首版：FANZA/DMM 主源 + JavLibrary/JavDB fallback，Hub API，探索源，名称识别。
