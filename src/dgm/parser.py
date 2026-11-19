""".dgm 文本 → spec dict。

DSL 语法（一行一个元素，# 之后是注释）：

    title: ...                      标题
    subtitle: ...                   副标题
    gap: 104,112                    列间距,行间距
    entry 节点名 @1,1 | 描述         节点（类型 entry/key/end/warn/plain）
    group 分组名: 节点A, 节点B       虚线分组框
    A -> B: 标签 ~-40               边（~N 控制弯曲，<-> 双向）
    legend 标签: 类型               图例

所有错误抛 DgmError，带行号和原始行，str() 直接是人话报告（CLI / --check 直接打印）。
"""

from __future__ import annotations

import re

NODE_TYPES = {"entry", "key", "end", "warn", "plain"}
DEFAULT_COLUMN_GAP = 78
DEFAULT_ROW_GAP = 116

_POS_RE = re.compile(r"@(\d+)\s*,\s*(\d+)")
_BEND_RE = re.compile(r"~(-?\d+)")


class DgmError(Exception):
    """解析 / 渲染错误：带行号、原始行和提示，str() 输出给人看的报告。"""

    def __init__(self, message: str, line_no: int | None = None,
                 line_text: str = "", hint: str = "") -> None:
        self.message = message
        self.line_no = line_no
        self.line_text = line_text
        self.hint = hint
        super().__init__(message)

    def __str__(self) -> str:
        if self.line_no is None:
            out = [f"[x] {self.message}"]
            if self.hint:
                out.append(f"提示：{self.hint}")
            return "\n".join(out)
        out = [f"第 {self.line_no} 行语法错误：{self.message}"]
        if self.line_text:
            out.append(f"  {self.line_text}")
            # 中文字符按 2 个字符宽算，让 ^ 尽量对齐原行
            width = max(sum(2 if ord(ch) > 127 else 1 for ch in self.line_text), 1)
            out.append("  " + "^" * width)
        if self.hint:
            out.append(f"提示：{self.hint}")
        return "\n".join(out)


def parse_dgm(text: str) -> dict:
    """把 .dgm 文本解析成 spec dict，语法错误抛 DgmError（带行号）。"""
    spec: dict = {
        "nodes": [], "edges": [], "groups": [], "legend": [],
        "title": "", "subtitle": "",
        "column_gap": DEFAULT_COLUMN_GAP, "row_gap": DEFAULT_ROW_GAP,
    }
    edges_raw: list[tuple[dict, int, str]] = []   # (edge, 行号, 原始行)
    groups_raw: list[tuple[dict, int, str]] = []
    node_ids: dict[str, int] = {}                 # 节点名 -> 定义行号
    last_row = 0

    for line_no, raw in enumerate(text.splitlines(), 1):
        content = raw.split("#", 1)[0].strip()
        if not content:
            continue
        if content.startswith("title:"):
            spec["title"] = content[6:].strip()
            continue
        if content.startswith("subtitle:"):
            spec["subtitle"] = content[9:].strip()
            continue
        if content.startswith("gap:"):
            spec["column_gap"], spec["row_gap"] = _parse_gap(content, line_no)
            continue
        if content.startswith("group "):
            groups_raw.append((*_parse_group(content, line_no), content))
            continue
        if content.startswith("legend "):
            spec["legend"].append(_parse_legend(content, line_no))
            continue
        if "->" in content or "<->" in content:
            edges_raw.append((_parse_edge(content, line_no), line_no, content))
            continue
        # 剩下的当节点；但「多个词、又没有 @ 和 |」多半是把边写漏了 ->
        words = content.split()
        if len(words) > 1 and not _looks_like_node(content):
            raise DgmError(
                '边定义缺少 "->"', line_no, content,
                hint=f"应该是  {words[0]} -> {words[-1]}: 标签",
            )
        node = _parse_node(content, line_no, last_row)
        if node["id"] in node_ids:
            raise DgmError(
                f'节点 "{node["id"]}" 重复定义（第 {node_ids[node["id"]]} 行已经定义过）',
                line_no, content,
            )
        node_ids[node["id"]] = line_no
        last_row = node["row"]
        spec["nodes"].append(node)

    # 引用检查：边和分组提到的节点都必须存在（放循环外，允许先写边后写节点）
    for edge, line_no, content in edges_raw:
        for end in (edge["from"], edge["to"]):
            if end not in node_ids:
                raise DgmError(
                    f'边引用了不存在的节点 "{end}"', line_no, content,
                    hint="先定义这个节点，或检查名字是否打错（要和节点行完全一致）",
                )
    for group, line_no, content in groups_raw:
        for member in group["nodes"]:
            if member not in node_ids:
                raise DgmError(
                    f'分组 "{group["label"]}" 引用了不存在的节点 "{member}"',
                    line_no, content,
                    hint="成员名字要和节点行完全一致",
                )
        spec["groups"].append(group)
    spec["edges"].extend(edge for edge, _, _ in edges_raw)
    return spec


def _looks_like_node(content: str) -> bool:
    first = content.split(None, 1)[0]
    return first in NODE_TYPES or "@" in content or "|" in content


def _parse_gap(content: str, line_no: int) -> tuple[int, int]:
    body = content[4:].strip()
    parts = body.split(",")
    if len(parts) != 2:
        raise DgmError("gap 应该是「列间距,行间距」两个整数", line_no, content, hint="gap: 104,112")
    try:
        return int(parts[0].strip()), int(parts[1].strip())
    except ValueError:
        raise DgmError("gap 应该是「列间距,行间距」两个整数", line_no, content,
                       hint="gap: 104,112") from None


def _parse_group(content: str, line_no: int) -> tuple[dict, int]:
    body = content[6:]  # 跳过 "group "
    if ":" not in body:
        raise DgmError('group 行缺少 ":"', line_no, content, hint="group 分组名: 节点A, 节点B")
    label, members = body.split(":", 1)
    label = label.strip()
    nodes = [m.strip() for m in members.split(",") if m.strip()]
    if not label:
        raise DgmError("group 缺少分组名", line_no, content, hint="group 分组名: 节点A, 节点B")
    if not nodes:
        raise DgmError("group 没有列出任何成员节点", line_no, content,
                       hint="group 分组名: 节点A, 节点B")
    return {"label": label, "nodes": nodes}, line_no


def _parse_legend(content: str, line_no: int) -> dict:
    body = content[7:]  # 跳过 "legend "
    if ":" not in body:
        raise DgmError("legend 行缺少 \":\"", line_no, content,
                       hint="legend 标签: 类型（entry/key/end/warn/plain）")
    label, typ = body.rsplit(":", 1)
    label, typ = label.strip(), typ.strip()
    if not label:
        raise DgmError("legend 缺少标签", line_no, content, hint="legend 标签: 类型")
    if typ not in NODE_TYPES:
        raise DgmError(f'图例类型 "{typ}" 不认识', line_no, content,
                       hint="类型是 entry / key / end / warn / plain 之一")
    return {"label": label, "type": typ}


def _parse_edge(content: str, line_no: int) -> dict:
    bidir = "<->" in content
    body = content.replace("<->", "->").strip()
    if ":" in body:
        left, label = body.split(":", 1)
    else:
        left, label = body, ""
    parts = [p.strip() for p in left.split("->")]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise DgmError('边定义缺少 "->" 或两端为空', line_no, content, hint="应该是  A -> B: 标签")
    label = label.strip()
    bend = 0
    mb = _BEND_RE.search(label)
    if mb:
        bend = int(mb.group(1))
        label = label[: mb.start()].strip()
    return {"from": parts[0], "to": parts[1], "label": label, "bend": bend, "bidir": bidir}


def _parse_node(content: str, line_no: int, last_row: int) -> dict:
    typ, body = "plain", content
    first = body.split(None, 1)[0]
    if first in NODE_TYPES:
        typ = first
        body = body[len(first):].strip()
        if not body:
            raise DgmError(f'"{first}" 节点缺少名字', line_no, content,
                           hint=f"{first} 名字 @列,行 | 描述")
    # 描述先摘出来，避免描述里的 @ 干扰位置解析
    desc = ""
    if "|" in body:
        body, desc = body.split("|", 1)
        desc = desc.strip()
        body = body.strip()
    if not body:
        raise DgmError("节点缺少名字", line_no, content, hint="类型 名字 @列,行 | 描述")
    m = _POS_RE.search(body)
    if m:
        col, row = int(m.group(1)), int(m.group(2))
        label = body[: m.start()].strip()
        leftover = body[m.end():].strip()
        if leftover:
            raise DgmError(f'位置 "{m.group(0)}" 后面有多余内容 "{leftover}"', line_no, content,
                           hint="位置写 @列,行 就好，多余文字挪到 | 描述里")
    else:
        if "@" in body:
            raise DgmError('"@" 后面应该是「列,行」两个数字', line_no, content,
                           hint="@2,1（列在前，行在后）")
        label = body.strip()
        col, row = 1, last_row + 1  # 没写位置：堆到第 1 列，自动排到上一节点下一行
    return {"id": label, "label": label, "desc": desc, "col": col, "row": row, "type": typ}
