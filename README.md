# 职先机岗位采集（zhixianji-jobs）

按搜索条件读取 [职先机](https://jobs.ultraai.site) 的岗位列表，把**收录时间在指定窗口内的岗位**逐条抓成完整 JD
（岗位职责 + 职位要求），存到本地 SQLite 并导出 CSV／JSONL／可读 Markdown，供后续分析。

只做读取：搜索列表、读取详情、本地存储、导出。没有投递、打招呼、发简历或任何写入站点的能力。

## 当前状态

0.1.0 首版已实现：列表读取与收录时间窗口判定、完整 JD 抓取、SQLite 存储、
CSV／JSONL／可读 Markdown 导出，以及 104 项离线测试。
真实站点的采集范围、成功率、覆盖来源与人工抽查结论见 [验证记录](docs/validation.md)。

## 为什么不需要浏览器

职先机的列表和详情都不是必须靠浏览器渲染的：

- 列表来自公开 JSON 接口 `GET /api/jobs?...`，与 `/discover` 页面的查询串一一对应；
- 详情页 `/jobs/<source>/<id>` 是服务端渲染的 HTML，完整 JD 就在页面里，另有
  `application/ld+json` 的 `JobPosting` 结构化数据。

因此本工具只用 HTTP 请求和标准库解析，不启动浏览器、不需要登录、不需要 Node。
接口与页面的实测细节见 [站点契约](docs/site-contract.md)。

## 收录时间口径

职先机对每个岗位保存了两个时间，含义不同：

| 字段 | 含义 | 页面上的位置 |
| --- | --- | --- |
| `first_seen_at` | **本站首次收录**该岗位的时间 | 详情页「收录时间」 |
| `published_at` | 招聘**官网发布**时间 | 列表卡片「官网发布／首次收录」 |

本工具默认按 `first_seen_at` 筛选，窗口是「截至运行时刻的最近 N 天」，边界按具体时刻比较。
窗口字段、天数、`asOf` 都由配置决定；按 `published_at` 筛选只需改一个字段：

```json
"window": { "field": "published_at", "days": 30 }
```

两个时间都会原样保存，所以换口径重新筛选不需要重新访问站点。

## ⚠️ 关键词匹配的是「标题**或**岗位详情」

这是使用本工具最容易误解的一点，请先看这里。

职先机搜索页自己写明「**标题命中优先，其次匹配岗位详情**」。`q` 不是标题过滤，
返回的是「标题命中 ∪ 岗位详情命中」的并集，只是把标题命中排在前面。
所以 `q=agent` 会出现标题里完全没有 agent 的岗位——因为它的**岗位要求里提到过** Agent。

用 `q=agent`（社招 + 在招）实测的整体分布：

| 口径 | 条数 |
| --- | --- |
| 站点搜索返回的全部结果 | 4779 |
| 其中标题含 `agent` | 1001 |
| 其中仅靠岗位详情命中 | 3778 |

落到「最近 30 天收录」窗口内：

| 口径 | 条数 | 对应文件 |
| --- | --- | --- |
| 站点搜索返回的全部结果 | 635 | `jobs.csv` |
| 其中标题命中 `agent` | **104** | `jobs-title-hit.csv` |
| 其中仅靠岗位详情命中 | 531 | — |

只在详情里命中的 531 条，529 条确实在正文中出现了 `agent`，但很多只是要求里顺带一句
「有 Agent 设计和实现经验者优先」。例如 `Java研发专家` 的标题与 Agent 无关，
命中来自它要求的第 7 条。**做「Agent 岗位」分析时建议用 `jobs-title-hit.csv`。**

判定依据是站点自己的 `query_score` 字段：`>= 100` 表示标题命中，`10` 表示只靠详情命中。
两个方向都逐条验证过、没有例外，细节见 [站点契约](docs/site-contract.md) 的「`q` 的匹配范围」一节。
导出会同时给出两种口径，所以不需要为了换口径重新采集。

**顺带一提**：站点的搜索界面并不使用 `query_score`，也不做筛选，页面计数显示的是完整
`total`。所以只翻前几页会看到清一色的标题命中结果，容易以为结果集只有一百多条。

## 安装与运行

当前实现只用 Python 标准库，需要 Python 3.11+，无需安装任何第三方包。

```powershell
Copy-Item config/search.example.json config/search.local.json
python run.py validate --config config/search.local.json
python run.py run      --config config/search.local.json
```

`run` 依次执行：读取列表并判定窗口 → 抓取窗口内岗位的完整 JD → 导出。
程序串行请求，列表与详情之间默认间隔 400／700 毫秒；实际耗时主要取决于站点响应速度。

常用命令：

| 命令 | 作用 |
| --- | --- |
| `validate` | 只校验配置，不访问网络 |
| `discover` | 只读列表并判定窗口，写入数据库与列表快照 |
| `details --batch <ID>` | 为窗口内岗位抓取完整 JD；`--limit N` 小批验证，`--retry-failed` 重试失败项 |
| `run` | 列表 + 详情 + 导出 |
| `export --batch <ID>` | 从本地数据库重新导出，不再访问站点 |
| `batches` | 列出本地批次及计数；`--limit N` / `--offset M` 分页 |

`batches` 分页只读本地 SQLite，不访问站点：

```powershell
python run.py batches                      # 全部批次（原有文本表格，含每批次计数与中文 search）
python run.py batches --limit 20           # 最新 20 个批次
python run.py batches --limit 20 --offset 20  # 下一页
python run.py batches --offset 40          # 跳过前 40 个，返回其余全部
```

`--limit` 只接受 1—1000 的整数，`--offset` 只接受 0—1000000 的整数（默认 0）。
排序固定为创建时间（`created_at`）降序，同一秒内创建时按 `batch_id` 降序，因此逐页拼接
与全量列表完全一致，不会重复或遗漏。空页正常退出 0；非法参数在打开或创建数据库之前
以退出码 2 拒绝，错误只写 stderr。

中断后继续：再跑一次 `details --batch <ID>` 即可，已成功的正文不会重复访问。

## 配置

| 配置项 | 语义 |
| --- | --- |
| `baseUrl` | 站点根地址 |
| `search` | 搜索条件，与 `/discover` 页面的查询串对应；`q` 必填，参数名写错会被拒绝而不是静默忽略 |
| `window.field` | 按哪个时间字段筛选，默认 `first_seen_at` |
| `window.days` | 窗口天数，默认 30 |
| `window.asOf` | 固定窗口结束时刻，便于复现历史批次；留空表示用运行时刻 |
| `collection.maxPages` / `maxJobs` | 预算上限。达到上限时程序会明确警告并记入摘要，不会假装抓全 |
| `collection.listDelayMs` / `detailDelayMs` | 请求间隔，默认 400 / 700 毫秒 |
| `collection.timeoutMs` / `maxRetries` | 超时与重试次数 |

搜索条件全部来自输入，程序不内置城市、薪资、学历或岗位方向偏好。
`config/search.local.json` 被 Git 忽略，示例模板中不含任何个人条件。

## 输出

导出目录 `output/<批次ID>/`：

| 文件 | 内容 |
| --- | --- |
| `jobs.csv` | 窗口内全部岗位（站点搜索原样），一行一条，含职责与要求全文（UTF-8 BOM，Excel 可直接打开） |
| `jobs-title-hit.csv` | **上表的一个子集：标题命中关键词的岗位**，做「Agent 岗位」分析时用这个 |
| `jobs.jsonl`、`jobs-title-hit.jsonl` | 同样的记录，JSON Lines，便于程序处理 |
| `summary.json` | 生效条件、窗口边界、各项计数、命中位置分布、失败原因分布、列表读取告警 |
| `jd/<source>__<id>.md` | 每条完整 JD 的可读版本，适合人工浏览 |
| `jd-title-hit/` | 标题命中子集的可读版本 |
| `raw/html/` | 详情页原始 HTML 快照，便于复核与重新解析 |
| `raw/list/` | 列表接口的逐页原始响应快照 |

CSV 中含 `query_score` 与 `match_scope`（`title`／`description`）两列，便于自行调整口径。
数据库默认 `data/zhixianji.db`，可用 `--db`、`--output` 改路径。

## 岗位状态与失败记录

详情抓取结果按状态保存在数据库中，导出时以 `jd_status` 体现：

| 状态 | 含义 |
| --- | --- |
| `ok` | 取得完整正文，且岗位身份核对通过 |
| `incomplete` | 页面存在，但职责或要求有一侧为空 |
| `mismatch` / `unverified` | 详情页身份与目标岗位不一致或无法确认；**此时不保存正文**，避免串岗 |
| `missing` | 详情页 404，可能已下线 |
| `error` | 网络或解析失败 |

身份核对以详情页的 `data-job-source` 与 `data-source-job-id` 为准。核对不通过时不写正文，
只记录失败状态——宁可缺一条，也不把别的岗位内容写成这个岗位的 JD。

## 测试与核对

```powershell
python -m unittest discover -s tests -t .
```

测试全部离线，不访问站点。样例数据为虚构内容，结构按真实页面观察构造。
测试临时目录使用 `data/_test-tmp`，原因见 `tests/__init__.py` 中的说明。

对一个已完成批次做独立核对（默认离线，只读数据库与归档 HTML）：

```powershell
python tools/spot_check.py --batch 20261015-1910-agent
python tools/spot_check.py --batch 20261015-1910-agent --live --sample 5
```

它复核入库正文能否在归档页面里溯源、完整 JD 是否确实长于列表摘要，并在 `--live` 时
抽样重新访问站点逐字比对。

## 文档

- [协作约定](AGENTS.md)
- [设计与数据约定](docs/design.md)
- [站点契约（实测）](docs/site-contract.md)
- [验证记录](docs/validation.md)

## 使用边界

- 只采集公开招聘信息，请求串行且带间隔，不绕过任何访问控制。
- 站点的接口或页面结构变化会影响解析；`docs/site-contract.md` 记录了观察日期，改动后需重新核对。
- 采集结果不纳入 Git；是否对外分享岗位数据由使用者自行判断。
