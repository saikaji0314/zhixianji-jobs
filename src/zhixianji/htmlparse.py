"""轻量 HTML 解析：把岗位详情页转成结构化记录。

详情页有两种可用的证据来源，本模块都支持并如实记录用了哪一种：

1. ``application/ld+json`` 里的 ``JobPosting``（结构化，职责与要求分列）；
2. 页面正文里的 ``section.dossier-section``（人读版，标题为「岗位职责」「职位要求」）。

两者都不写死岗位内容，字段名来自当次真实页面观察，见 docs/site-contract.md。
只用标准库实现，避免为解析引入依赖。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

SKIP_TAGS = {"script", "style", "noscript", "template"}

# 这些标签在取文本时视为块级：前后插入换行，避免不同条目粘成一行。
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "body", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "html", "li", "main", "nav", "ol", "p", "pre",
    "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}

WHITESPACE_RE = re.compile(r"[ \t\u00a0\u3000]+")
BLANKLINE_RE = re.compile(r"\n{2,}")


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)
    parent: "Node | None" = None

    def class_list(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def has_class(self, name: str) -> bool:
        return name in self.class_list()

    def walk(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.walk()

    def find_all(
        self,
        tag: str | None = None,
        cls: str | None = None,
        node_id: str | None = None,
    ) -> list["Node"]:
        out = []
        for node in self.walk():
            if tag is not None and node.tag != tag:
                continue
            if cls is not None and not node.has_class(cls):
                continue
            if node_id is not None and node.attrs.get("id") != node_id:
                continue
            out.append(node)
        return out

    def find(self, tag: str | None = None, cls: str | None = None, node_id: str | None = None) -> "Node | None":
        found = self.find_all(tag=tag, cls=cls, node_id=node_id)
        return found[0] if found else None

    def text(self, block_breaks: bool = True) -> str:
        """取子树文本。block_breaks=True 时块级标签前后断行。"""
        pieces: list[str] = []
        _collect_text(self, pieces, block_breaks)
        return normalize_text("".join(pieces))


def _collect_text(node: Node, out: list[str], block_breaks: bool) -> None:
    for child in node.children:
        if isinstance(child, str):
            out.append(child)
            continue
        if child.tag in SKIP_TAGS:
            continue
        if block_breaks and child.tag in BLOCK_TAGS:
            out.append("\n")
        _collect_text(child, out, block_breaks)
        if block_breaks and child.tag in BLOCK_TAGS:
            out.append("\n")


def normalize_text(raw: str) -> str:
    """折叠行内空白、去掉空行，保留条目换行。"""
    if not raw:
        return ""
    lines = []
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cleaned = WHITESPACE_RE.sub(" ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return BLANKLINE_RE.sub("\n", "\n".join(lines)).strip()


class _DomBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node(tag="#document")
        self._stack: list[Node] = [self.root]
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k: (v if v is not None else "") for k, v in attrs}
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        node = Node(tag=tag, attrs=attr_map, parent=self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k: (v if v is not None else "") for k, v in attrs}
        node = Node(tag=tag, attrs=attr_map, parent=self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return
        # 没有匹配的开始标签：忽略，保持容错。

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        self._stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    builder = _DomBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def extract_json_ld_job(html: str) -> dict[str, Any] | None:
    """从页面里取 schema.org JobPosting。找不到时返回 None。"""
    for match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    ):
        payload = match.group(1).strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        graph = data.get("@graph") if isinstance(data, dict) else None
        candidates = graph if isinstance(graph, list) else [data]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") == "JobPosting":
                return candidate
    return None


def json_ld_webpage_date_modified(html: str) -> str | None:
    """取 WebPage 上的 dateModified（等于站点的收录时间）。"""
    for match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue
        graph = data.get("@graph") if isinstance(data, dict) else None
        for candidate in graph if isinstance(graph, list) else [data]:
            if isinstance(candidate, dict) and candidate.get("@type") == "WebPage":
                modified = candidate.get("dateModified")
                if isinstance(modified, str) and modified:
                    return modified
    return None


def _strip_html(raw: str) -> str:
    """把 JSON-LD 里带标签的 description 转成纯文本。"""
    if not raw:
        return ""
    if "<" not in raw:
        return normalize_text(raw)
    return parse_html(raw).text()


def _first_location(job: dict[str, Any]) -> tuple[str, str]:
    entries = job.get("jobLocation")
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return "", ""
    localities: list[str] = []
    regions: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        address = entry.get("address")
        if isinstance(address, dict):
            locality = str(address.get("addressLocality") or "").strip()
            region = str(address.get("addressRegion") or "").strip()
            if locality:
                localities.append(locality)
            if region and region != locality:
                regions.append(region)
    location = "、".join(dict.fromkeys(localities))
    region = "、".join(dict.fromkeys(regions))
    return location, region


def parse_detail(html: str) -> dict[str, Any]:
    """解析详情页，返回与来源无关的规范化字段。

    结构化数据优先；缺失时回退到正文区块。两种情况都记录 parse_source，
    以便之后判断某一批数据的解析路径占比。
    """
    root = parse_html(html)
    warnings: list[str] = []

    hero = root.find("section", cls="opportunity-hero")
    hero_attrs: dict[str, str] = {}
    if hero is not None:
        # 页面用 data-* 属性携带岗位身份；这里映射成与其它模块一致的字段名。
        for attr, name in (
            ("data-job-status", "status"),
            ("data-job-source", "source"),
            ("data-source-job-id", "source_job_id"),
            ("data-job-type", "job_type"),
            ("data-job-title", "title"),
        ):
            hero_attrs[name] = hero.attrs.get(attr, "")

    title_node = root.find(node_id="job-title")
    html_title = title_node.text(block_breaks=False) if title_node is not None else ""

    company_node = root.find("p", cls="job-company")
    html_company = company_node.text(block_breaks=False) if company_node is not None else ""

    meta_node = root.find("ul", cls="job-meta")
    html_meta = [normalize_text(li.text(block_breaks=False)) for li in meta_node.find_all("li")] if meta_node else []
    html_meta = [item for item in html_meta if item]

    collected_node = root.find("p", cls="job-collected")
    site_collected_at = ""
    site_collected_text = ""
    if collected_node is not None:
        time_node = collected_node.find("time")
        if time_node is not None:
            site_collected_at = time_node.attrs.get("datetime", "")
            site_collected_text = normalize_text(time_node.text(block_breaks=False))
        if not site_collected_at:
            warnings.append("收录时间元素存在但缺少 datetime")

    # 正文区块人读版：标题 -> 文本，可覆盖任意数量的 section（不假定只有两节）。
    sections: list[dict[str, str]] = []
    for section in root.find_all("section", cls="dossier-section"):
        heading_node = section.find("h2")
        heading = normalize_text(heading_node.text(block_breaks=False)) if heading_node is not None else ""
        body_node = section.find("div", cls="job-prose") or section.find("div", cls="dossier-section__body")
        text = body_node.text() if body_node is not None else ""
        if not text:
            warnings.append(f"正文区块 {section.attrs.get('id') or heading or '?'} 内容为空")
        sections.append({"id": section.attrs.get("id", ""), "heading": heading, "text": text})

    def section_by_id(section_id: str, *fallback_keywords: str) -> str:
        for entry in sections:
            if entry["id"] == section_id:
                return entry["text"]
        for entry in sections:
            if any(keyword in entry["heading"] for keyword in fallback_keywords):
                return entry["text"]
        return ""

    html_responsibilities = section_by_id("job-responsibilities", "职责", "工作内容")
    html_requirements = section_by_id("job-requirements", "要求", "资格", "任职")

    record: dict[str, Any] = {
        "parse_source": "html",
        "hero": hero_attrs,
        "html_title": html_title,
        "html_company": html_company,
        "html_meta": html_meta,
        "html_location": html_meta[0] if html_meta else "",
        "html_category": html_meta[1] if len(html_meta) > 1 else "",
        "site_collected_at": site_collected_at,
        "site_collected_text": site_collected_text,
        "date_posted": "",
        "identifier_name": "",
        "identifier_value": "",
        "json_ld_location": "",
        "json_ld_region": "",
        "sections": sections,
        "responsibilities": html_responsibilities,
        "qualifications": html_requirements,
        "warnings": warnings,
    }

    job = extract_json_ld_job(html)
    if job:
        record["parse_source"] = "json-ld"
        json_location, json_region = _first_location(job)
        identifier = job.get("identifier")
        identifier_name = identifier_value = ""
        if isinstance(identifier, dict):
            identifier_name = str(identifier.get("name") or "")
            identifier_value = str(identifier.get("value") or "")
        elif isinstance(identifier, str):
            identifier_value = identifier
        organization = job.get("hiringOrganization")
        json_company = ""
        if isinstance(organization, dict):
            json_company = str(organization.get("name") or "")
        elif isinstance(organization, str):
            json_company = organization

        record.update(
            {
                "json_ld_title": str(job.get("title") or ""),
                "json_ld_company": json_company,
                "json_ld_location": json_location,
                "json_ld_region": json_region,
                "date_posted": str(job.get("datePosted") or ""),
                "identifier_name": identifier_name,
                "identifier_value": identifier_value,
                "json_ld_description": _strip_html(str(job.get("description") or "")),
                "responsibilities": normalize_text(str(job.get("responsibilities") or "")) or html_responsibilities,
                "qualifications": normalize_text(str(job.get("qualifications") or "")) or html_requirements,
            }
        )
        if not record["site_collected_at"]:
            record["site_collected_at"] = json_ld_webpage_date_modified(html) or ""

    record["title"] = record.get("json_ld_title") or html_title or hero_attrs.get("title", "")
    record["company"] = record.get("json_ld_company") or html_company
    record["location"] = record.get("json_ld_location") or record["html_location"]
    record["category"] = record["html_category"]
    record["jd_text"] = _join_jd(record["responsibilities"], record["qualifications"])
    return record


def _join_jd(responsibilities: str, qualifications: str) -> str:
    parts = []
    if responsibilities:
        parts.append(f"岗位职责\n{responsibilities}")
    if qualifications:
        parts.append(f"职位要求\n{qualifications}")
    return "\n\n".join(parts)
