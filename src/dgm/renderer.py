"""渲染：spec → HTML → 无头浏览器截图 → PIL 裁白边 → PNG。

视觉风格全部在 templates/base.html 里（CSS Grid 排位置，JS 读元素位置画贝塞尔连线），
Python 这边只负责填数据，不掺和样式。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.parse
from importlib import resources
from pathlib import Path

from PIL import Image, ImageChops

from dgm.parser import DgmError

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]
LEGEND_STYLE = {
    "entry": "background:#fbfcfe;border:1px solid #cbd5e1;",
    "key": "background:#eef2ff;border:1px solid #c7d2fe;",
    "end": "background:#f8fbff;border:1px solid #dbeafe;",
    "warn": "background:#fffafb;border:1px solid #fde2e4;",
    "plain": "background:#f8fafc;border:1px solid #e2e8f0;",
}


def load_template() -> str:
    """读打包进来的 HTML 模板（templates/base.html）。"""
    return (resources.files("dgm") / "templates" / "base.html").read_text(encoding="utf-8")


def validate_spec(spec: dict) -> dict:
    """校验 spec 的引用完整性（JSON 输入 / 渲染前的兜底）。"""
    ids = {n["id"] for n in spec.get("nodes", [])}
    for edge in spec.get("edges", []):
        for end in (edge.get("from"), edge.get("to")):
            if end not in ids:
                raise DgmError(
                    f'边引用了不存在的节点 "{end}"',
                    hint="节点名要和节点行完全一致",
                )
    for group in spec.get("groups", []):
        for member in group.get("nodes", []):
            if member not in ids:
                raise DgmError(
                    f'分组 "{group.get("label", "")}" 引用了不存在的节点 "{member}"',
                    hint="成员名字要和节点行完全一致",
                )
    return spec


def build_html(spec: dict) -> tuple[str, int, int]:
    """spec 填进模板 → 完整 HTML；返回 (html, 列数, 行数)。"""
    template = load_template()
    nodes = spec.get("nodes", [])
    ncols = max((n.get("col", 1) for n in nodes), default=1)
    nrows = max((n.get("row", 1) for n in nodes), default=1)

    node_html = []
    for n in nodes:
        desc = f'<div class="d">{n["desc"]}</div>' if n.get("desc") else ""
        node_html.append(
            f'  <div class="node {n.get("type", "plain")}" id="{n["id"]}" '
            f'style="grid-column:{n.get("col", 1)};grid-row:{n.get("row", 1)};">'
            f'<div class="n">{n["label"]}</div>{desc}</div>'
        )

    group_html, group_js = [], []
    for i, g in enumerate(spec.get("groups", [])):
        gid = f"g{i}"
        group_html.append(
            f'  <div class="group" id="{gid}"><div class="group-label">{g["label"]}</div></div>'
        )
        ids = json.dumps(g["nodes"], ensure_ascii=False)
        group_js.append(
            f"  (()=>{{const el=document.getElementById('{gid}');"
            f"const rs={ids}.map(box).filter(Boolean);"
            f"const padX={g.get('pad_x', 16)},padY={g.get('pad_y', 22)};"
            f"el.style.left=(Math.min(...rs.map(r=>r.x))-padX)+'px';"
            f"el.style.top=(Math.min(...rs.map(r=>r.y))-padY)+'px';"
            f"el.style.width=(Math.max(...rs.map(r=>r.r))-Math.min(...rs.map(r=>r.x))+padX*2)+'px';"
            f"el.style.height=(Math.max(...rs.map(r=>r.b))-Math.min(...rs.map(r=>r.y))+padY*2)+'px';}})();"
        )

    legend = spec.get("legend", [])
    legend_html = ""
    if legend:
        items = "".join(
            f'<span><i style="{LEGEND_STYLE.get(item.get("type", "plain"), "")}"></i>'
            f'{item["label"]}</span>'
            for item in legend
        )
        legend_html = f'<div class="legend">{items}</div>'

    # 双向边展开成来回两条，bend 取相反数让两条线分开
    edges = []
    for e in spec.get("edges", []):
        edges.append({
            "from": e["from"], "to": e["to"], "label": e.get("label", ""),
            "bend": e.get("bend", 0), "lx": e.get("lx", 0), "ly": e.get("ly", 0),
            "dashed": e.get("dashed", False),
        })
        if e.get("bidir"):
            edges.append({
                "from": e["to"], "to": e["from"], "label": e.get("label_back", ""),
                "bend": -(e.get("bend", 0)), "lx": 0, "ly": 0,
                "dashed": bool(e.get("label_back")),
            })

    html = template
    html = html.replace("__TITLE__", spec.get("title", ""))
    html = html.replace("__SUB__", spec.get("subtitle", ""))
    html = html.replace("__COLS__", str(ncols)).replace("__ROWS__", str(nrows))
    html = html.replace("__CGAP__", str(spec.get("column_gap", 78)))
    html = html.replace("__RGAP__", str(spec.get("row_gap", 116)))
    html = html.replace("__GROUPS__", "\n".join(group_html))
    html = html.replace("__NODES__", "\n".join(node_html))
    html = html.replace("__LEGEND__", legend_html)
    html = html.replace("__EDGES__", json.dumps(edges, ensure_ascii=False))
    html = html.replace("__GROUPS_JS__", "\n".join(group_js))
    return html, ncols, nrows


def find_browser() -> str:
    """找本机浏览器：DGM_BROWSER 环境变量优先，然后按常见安装路径探测。"""
    env_browser = os.environ.get("DGM_BROWSER")
    if env_browser and os.path.exists(env_browser):
        return env_browser
    for path in EDGE_CANDIDATES:
        if os.path.exists(path):
            return path
    raise DgmError(
        "找不到 Edge / Chrome",
        hint="装 Edge 或 Chrome，或者用 --edge 指定浏览器路径（也可以设 DGM_BROWSER 环境变量）",
    )


def render(spec: dict, out_png: str | os.PathLike, scale: int = 2,
           keep_html: str | os.PathLike | None = None,
           browser: str | None = None) -> tuple[Path, int, int]:
    """渲染 spec → PNG。返回 (输出路径, 宽, 高)。浏览器找不到 / 截图失败抛 DgmError。"""
    validate_spec(spec)
    html, ncols, nrows = build_html(spec)
    browser = browser or find_browser()

    tmpdir = Path(tempfile.mkdtemp(prefix="dgm-"))
    html_path = tmpdir / "diagram.html"
    html_path.write_text(html, encoding="utf-8")
    shot = tmpdir / "shot.png"
    w = 320 * max(ncols, 2) + 220
    h = 270 * max(nrows, 2) + 380
    url = "file:///" + urllib.parse.quote(str(html_path).replace("\\", "/"))
    cmd = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           f"--force-device-scale-factor={scale}", f"--window-size={w},{h}",
           f"--screenshot={shot}", url]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
    except FileNotFoundError:
        raise DgmError(
            f"浏览器启动失败：{browser}",
            hint="用 --edge 指定一个可用的 Edge / Chrome 路径",
        ) from None
    except subprocess.TimeoutExpired as e:
        raise DgmError("浏览器截图超时（120 秒）", hint=str(e)) from None
    if not shot.exists():
        raise DgmError(
            "截图失败：浏览器没有产出 PNG",
            hint="用 --edge 指定浏览器路径；也可以先用 --html 保留 HTML 手动打开排查",
        )

    # 裁白边，四周留 24px 呼吸
    im = Image.open(shot).convert("RGB")
    bbox = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).getbbox()
    if bbox:
        pad = 24 * scale
        im = im.crop((max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                      min(im.width, bbox[2] + pad), min(im.height, bbox[3] + pad)))
    out_path = Path(out_png)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path)
    if keep_html:
        Path(keep_html).write_text(html, encoding="utf-8")
    return out_path, im.width, im.height
