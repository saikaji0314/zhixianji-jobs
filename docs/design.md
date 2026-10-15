# 设计与数据约定

确认日期：2026-10-15。首版按本设计实现，实际验证范围见 [validation.md](validation.md)。

## 1. 边界与技术选择

把「按搜索条件找到窗口中新收录的岗位」和「取得完整 JD」这两件事做成可重复执行的本地命令，
结果落盘供后续分析。

只使用 Python 3.11+ 标准库：

- HTTP 用 `urllib.request`，避免为一次 GET 引入第三方库；
- HTML 解析用 `html.parser` 自建轻量 DOM，够用且不依赖 lxml／BeautifulSoup；
- 存储用 `sqlite3`，导出用 `csv`／`json`。

站点不需要浏览器（见 [site-contract.md](site-contract.md)），因此不引入 Playwright／Patchright、
不维护浏览器 Profile、不需要登录。

初期不做 Web 界面与常驻服务。程序是可中断、可继续的批处理命令。

## 2. 模块职责

| 模块 | 职责 | 位置 |
| --- | --- | --- |
| CLI | 解析命令、编排阶段、输出进度 | `src/zhixianji/cli.py` |
| 配置 | 加载与校验输入条件 | `src/zhixianji/config.py` |
| HTTP | 只读 GET、超时、有限重试、退避 | `src/zhixianji/http_client.py` |
| 列表发现 | 翻页读取 `/api/jobs`、去重、快照、上限提示 | `src/zhixianji/discover.py` |
| 时间窗口 | 收录时间解析与窗口判定 | `src/zhixianji/window.py` |
| 详情读取 | 抓取详情页、解析、岗位身份核对 | `src/zhixianji/detail.py` |
| HTML 解析 | 轻量 DOM 与 `JobPosting`／正文区块提取 | `src/zhixianji/htmlparse.py` |
| 存储 | 批次、列表快照、详情快照 | `src/zhixianji/store.py` |
| 导出 | CSV／JSONL／Markdown／摘要 | `src/zhixianji/export.py` |
| 离线验证 | 虚构样例与关键行为测试 | `tests/` |

## 3. 阶段划分

`discover` 与 `details` 分开，理由是两者的成本与失败模式不同：
读列表便宜且必须一次读全，读详情贵（每岗一次请求）且允许中断后续跑。
分开之后，列表证据可以先落盘，详情可以分小批验证再扩量。

`run` 只是把两者串起来，不引入额外语义。

## 4. 数据约定

### 岗位身份

岗位以 `(source, source_job_id)` 唯一确定，与站点 `public_path` 的 `/jobs/<source>/<id>` 一致。
同一岗位被多个关键词命中时按该主键去重。

### 时间字段

| 字段 | 来源 | 说明 |
| --- | --- | --- |
| `first_seen_at` | 列表接口 | 本站首次收录时间，等于详情页「收录时间」 |
| `last_seen_at` | 列表接口 | 最近一次核验时间 |
| `published_at` / `published_at_source` | 列表接口 | 官网发布时间及其来源；`official` 表示来自官网，`first_seen` 表示站点无官网日期而回落到收录时间 |
| `posted_at` / `updated_at` | 列表接口 | 站点记录的职位时间，原样保存 |
| `date_posted` | 详情页 `JobPosting.datePosted` | 结构化发布时间 |
| `site_collected_at` | 详情页 `<time datetime>` | 页面显示收录时间对应的机器可读值 |

窗口判定只读取配置指定的那一个字段，其它时间原样保留但不参与判定。

### 窗口语义

窗口是 `[asOf - days, asOf]` 的闭区间，按具体时刻比较，不做自然日近似。
`asOf` 缺省为运行时刻，可以固定以便复现。

边界行为：正好等于下界算窗口内；早一秒算窗口外；缺失或无法解析记 `missing_date` 并排除；
晚于 `asOf` 记 `after_as_of` 并排除（可能是时钟差异，不强行纳入）。

**必须读完全部列表页再判定窗口。** 实测列表顺序不等价于收录时间倒序：
一次真实采集中，窗口内记录出现在第 4773 位，而窗口外记录出现在第 91 位。
因此任何「读到旧记录就提前停止」的优化都会漏数据。

### 详情正文

正文有两路证据，优先结构化：

1. `application/ld+json` 的 `JobPosting`：`responsibilities` 与 `qualifications` 直接分列；
2. 页面正文 `section.dossier-section`：按 `<h2>` 标题取「岗位职责」「职位要求」。

两路都记录 `parse_source`，便于统计某一批数据走了哪条路径。
正文区块不假定只有两节：所有 `dossier-section` 都按标题保存到 `sections`，
多出来的「加分项」等不会被丢弃。

列表页的 `description_excerpt` 是摘要，永远不当作完整正文。
相似岗位推荐区（`recommendations`）的内容不参与 JD 提取。

### 快照与覆盖

列表接口的逐页原始响应保存到 `raw/list/page-NNN.json`；
详情页原始 HTML 保存到 `raw/html/<source>__<id>.html`，并在库中记录 SHA-256。
重新抓取会更新该岗位的详情记录，但不会删除已导出的历史批次目录。

## 5. 失败处理

- 列表 4xx 直接失败并报告；5xx 与网络错误按 `maxRetries` 退避重试。
- 详情 404 记 `missing`（可能已下线），不重试。
- 详情身份核对不通过记 `mismatch`／`unverified`，**清空正文**，不入库错误内容。
- 正文缺一侧记 `incomplete`，保留已有的那一侧。
- 每抓一条立刻写库，中断后按 `pending_details` 继续；`--retry-failed` 才会重试失败项。
- 达到 `maxPages`／`maxJobs` 预算时明确警告并写入摘要，不静默截断。

## 6. 导出约定

- `jobs.csv`／`jobs.jsonl` 只含窗口内记录；窗口外记录留在数据库供审计。
- CSV 使用 UTF-8 BOM、正确转义多行正文，并对 `=`、`+`、`-`、`@` 开头的单元格做公式注入防护。
- 未抓详情的记录也会出现在导出中，`jd_status` 为 `not_fetched`，不会被当作成功。
- 摘要记录生效条件、窗口边界、列表读取告警与失败原因分布，使结果可解释、可复现。
- 导出只读数据库，不访问站点。
