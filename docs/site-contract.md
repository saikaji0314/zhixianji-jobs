# 站点契约（实测记录）

观察日期：**2026-10-15**。观察方式：直接请求站点，未使用浏览器。
站点页面结构或接口一旦变化，本文件与解析代码都需要重新核对。

## 1. 搜索页与查询参数

搜索页 `https://jobs.ultraai.site/discover?job_type=social&status=active&q=agent`。
页面的 `currentFilters()` 用 `URLSearchParams` 拼出 `/api/jobs` 的查询串，
因此页面的查询参数与接口参数是同一套。

已确认可用／已确认存在的参数：

| 参数 | 含义 |
| --- | --- |
| `q` | 关键词 |
| `job_type` | `internship` / `campus` / `social` |
| `status` | `active` / `inactive` / 全部 |
| `location`、`source`、`organization`、`role`、`topic` | 其它页面筛选项 |
| `today`、`unviewed`、`favorites` | 布尔开关，值为 `1` |
| `new_since`、`offline_since` | 增量时间点 |
| `page` | 页码，从 1 开始 |

## 2. 列表接口

```
GET /api/jobs?job_type=social&status=active&q=agent&page=1
```

响应 `application/json`：

```json
{
  "total": 4779,
  "page": 1,
  "pages": 80,
  "page_size": 60,
  "jobs": [ { ... }, ... ]
}
```

实测要点：

- **每页固定 60 条。** 传 `page_size=5` 或 `page_size=200` 都不生效，返回仍是 60 条，
  因此本工具不发送该参数，分页以响应中的 `page_size` 为准。
- `page` 生效，`total` 与 `jobs.length` 自洽（80 × 60 = 4800 ≥ 4779，末页 39 条）。
- 列表顺序**不是**按 `first_seen_at` 倒序。实测一次真实读取中，
  窗口内记录出现在第 4773 位，窗口外记录出现在第 91 位，
  所以必须读完全部页面再判定收录时间窗口。
- 同一批读取内 `(source, source_job_id)` 未出现重复；跨页仍按该主键去重以防万一。

### 单条岗位字段

| 字段 | 示例 | 说明 |
| --- | --- | --- |
| `source` | `baidu` | 招聘来源标识 |
| `source_job_id` | `9e66747d-…` | 来源站内的岗位 ID |
| `job_type` | `social` | 实习／校招／社招 |
| `title` | `数字人 AI Coding Agent 产品经理（J106620）` | 岗位标题 |
| `organization` | `MEG` | 招聘组织／公司 |
| `category` | `产品` | 类别，可为空 |
| `location` | `北京市` | 地点，可为空 |
| `updated_at` / `posted_at` | `2026-10-15` | 站点记录的职位时间 |
| `first_seen_at` | `2026-10-15T18:06:10+08:00` | **本站首次收录时间** |
| `last_seen_at` | `2026-10-15T18:06:10+08:00` | 最近一次核验时间 |
| `deactivated_at` | `""` | 下线时间，在招时为空 |
| `is_active` | `true` | 是否在招 |
| `published_at` | `2026-10-15` | 官网发布时间或收录时间，可为空 |
| `published_at_source` | `official` / `first_seen` | 上者的来源 |
| `query_score` | `110` | 命中位置打分，见下节 |
| `has_description` | `true` | 站点是否已抓到正文 |
| `description_excerpt` | `…` | **摘要**，不是完整正文 |
| `public_path` | `/jobs/baidu/9e66747d-…` | 站点详情页路径 |
| `detail_url` | `https://talent.baidu.com/…` | 招聘官网原始链接 |
| `source_label` | `百度招聘` | 来源中文名 |
| `viewed` / `favorited` | `false` | 与登录账号相关的个人状态 |

## 3. `q` 的匹配范围：标题**或**岗位详情

这一条最容易误解，单独说明。

搜索页自己写明「**标题命中优先，其次匹配岗位详情**」。也就是说 `q` 不是标题过滤，
而是「标题命中 ∪ 岗位详情命中」的并集，站点把两者排在同一个结果列表里，标题命中排在前面。

`query_score` 就是这个位置的机器可读标记。实测（2026-10-15，`q=agent`，
`job_type=social`、`status=active`，共 4779 条）**只有三档**：

| `query_score` | 条数 | 标题是否含 `agent` |
| --- | --- | --- |
| `110` | 964 | 是 |
| `100` | 37 | 是 |
| `10` | 3778 | 否，只靠岗位详情命中 |

两个方向都验证过，没有例外：

- 标题含 `agent` 的 1001 条 = 964 + 37，`query_score` 只取 110 与 100；
- 标题不含的 3778 条，`query_score` 全部是 10。

因此 **`query_score >= 100` 等价于「标题命中关键词」**（110 与 100 的区别未查明，
推测与关键词在标题中的位置有关）。这个阈值属于站点实现细节，可能变化；
`src/zhixianji/export.py` 里的 `TITLE_HIT_MIN_SCORE` 集中定义了它，并有对应的测试。

**实际影响（收录时间窗口内）**：

| 口径 | 条数 |
| --- | --- |
| 站点搜索返回的全部结果 | 635 |
| 其中标题命中 `agent` | **104** |
| 其中仅靠岗位详情命中 | 531 |

只在详情里命中的 531 条，绝大多数确实在正文里出现了 `agent`（529/531），
但很多只是要求里顺带一句「有 Agent 设计和实现经验者优先」——
例如 `xiaomi/7696728516373793066`「Java研发专家」，标题与 Agent 无关，
命中来自其要求第 7 条。这类岗位对「找 Agent 岗」的分析是噪声，
所以导出时会把两种口径分开成 `jobs.csv` 与 `jobs-title-hit.csv`。

**注意**：站点的搜索 UI 并不使用 `query_score`（源码里只把它透传），
也不做任何筛选，页面计数显示的是完整 `total`（4,780），
所以只浏览前几页会看到清一色的标题命中结果，容易误判结果集很小。

## 4. 详情页

```
GET /jobs/<source>/<source_job_id>
```

服务端渲染的 HTML（约 12–32 KB），无需 JavaScript 即可取得完整 JD。

### 4.1 岗位身份（用于核对）

```html
<section class="opportunity-hero job-hero"
         data-job-status="active"
         data-job-source="baidu"
         data-source-job-id="9e66747d-0aee-4282-88cc-4fafdaadae15"
         data-job-type="social"
         data-job-title="数字人 AI Coding Agent 产品经理（J106620）">
```

`data-job-source` + `data-source-job-id` 是入库前的身份判据。
注意站点上可能存在同名不同 ID 的岗位，标题一致不足以证明身份，必须比对 ID。

### 4.2 收录时间

```html
<p class="job-collected"><span>收录时间</span><time datetime="2026-10-15T18:06:10+08:00">2026-10-15 18:06</time></p>
```

`datetime` 与列表接口的 `first_seen_at` 逐条一致（抽查 8 个不同来源均吻合）。
这就是本工具默认的窗口字段。

### 4.3 结构化正文（首选）

页面含 `application/ld+json`，其中 `@graph` 里有 `JobPosting`：

```json
{
  "@type": "JobPosting",
  "title": "…",
  "description": "<p>…</p>",
  "datePosted": "2026-10-15",
  "hiringOrganization": { "name": "MEG" },
  "identifier": { "name": "百度招聘", "value": "baidu:9e66747d-…" },
  "jobLocation": [{ "address": { "addressLocality": "北京市" } }],
  "responsibilities": "…",
  "qualifications": "…"
}
```

`responsibilities` / `qualifications` 天然把「岗位职责」「职位要求」分开。
同一 `@graph` 里的 `WebPage.dateModified` 等于收录时间，可作为兜底。

**注意：`JobPosting` 并非每个岗位都有。** 实测美团来源的详情页没有 `ld+json`，
因此必须保留正文区块的回退路径。

### 4.4 正文区块（回退）

```html
<section class="dossier-section" id="job-responsibilities">
  <header class="dossier-section__heading"><h2>岗位职责</h2></header>
  <div class="dossier-section__body"><div class="job-prose"><p>…</p></div></div>
</section>
<section class="dossier-section" id="job-requirements"> … <h2>职位要求</h2> … </section>
```

- 区块数量不固定，可能还有「加分项」等；按 `<h2>` 标题全部保留。
- 多处条目被塞进同一个 `<p>`，用空格或 `-` 分隔，**没有可靠的机器可读分隔符**。
  本工具保持原文，不猜分隔符去切分。
- 页面还有 `section.recommendations`（相似岗位）、`section.opportunity-thesis`、
  `section.job-mini-program` 等区块，这些不属于 JD，解析时排除。

### 4.5 其它

- 详情页返回 404 表示岗位不存在或已下线。
- 页面上的「相似岗位」链接是 `/jobs/<source>/<id>`，可作为发现更多岗位的入口，本工具未使用。
- 站点为 Cloudflare 前置；请求带普通浏览器 User-Agent，串行请求、间隔数百毫秒，未遇到限流。

## 5. 未验证的部分

以下内容本工具没有依赖，也未验证，做扩展前需要重新实测：

- 登录后接口（`/api/auth/me`、`/api/job-favorites`、`/api/home-delta`）的返回结构；
- `new_since` / `offline_since` 增量接口的边界语义；
- `role`、`topic`、`similar_to`、`also_viewed` 等参数的行为；
- `/jobs/archive` 下线岗位档案页的结构；
- 站点是否有速率限制，以及长期高频请求的后果。

## 6. 复核方法

```powershell
# 列表字段与分页
curl "https://jobs.ultraai.site/api/jobs?job_type=social&status=active&q=agent&page=1"

# 详情页结构化数据是否还在
curl "https://jobs.ultraai.site/jobs/baidu/9e66747d-0aee-4282-88cc-4fafdaadae15" | Select-String "application/ld+json"
```

若 `page_size` 仍是 60、`first_seen_at` 仍在列表字段中、详情页仍有
`data-source-job-id` 与 `dossier-section`，则本工具的前提成立。
