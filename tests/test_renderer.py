"""renderer 测试：不跑真浏览器，mock 掉 subprocess，验证 HTML 内容和裁剪逻辑。"""

import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from dgm.parser import DgmError, parse_dgm
from dgm.renderer import build_html, find_browser, load_template, render, validate_spec

SAMPLE = """\
title: 测试图
subtitle: 副标题
entry a @1,1
key b @2,1 | 描述文字
end c @3,1
a -> b: 走这边
b -> c
group 圈起来: a, b
legend 图例: entry
"""


def test_template_loads_and_has_placeholders():
    from dgm.renderer import load_template

    template = load_template()
    assert "__TITLE__" in template and "__EDGES__" in template and "__NODES__" in template


def test_build_html_contains_nodes_and_edges():
    spec = parse_dgm(SAMPLE)
    html, ncols, nrows = build_html(spec)
    assert '<div class="title">测试图</div>' in html
    assert 'id="a"' in html and 'id="b"' in html and 'id="c"' in html
    assert html.count('class="node ') == 3
    assert "描述文字" in html
    edges = json.loads(html.split("const EDGES = ", 1)[1].split(";", 1)[0])
    assert len(edges) == 2
    assert edges[0]["label"] == "走这边"
    assert (ncols, nrows) == (3, 1)


def test_build_html_group_and_legend():
    spec = parse_dgm(SAMPLE)
    html, _, _ = build_html(spec)
    assert 'class="group-label">圈起来<' in html
    assert "圈起来" in html
    assert 'class="legend"' in html and "图例" in html


def test_bidir_edge_expands_to_two():
    spec = parse_dgm("a @1,1\nb @2,1\na <-> b: 同步\n")
    html, _, _ = build_html(spec)
    edges = json.loads(html.split("const EDGES = ", 1)[1].split(";", 1)[0])
    assert len(edges) == 2
    assert {(e["from"], e["to"]) for e in edges} == {("a", "b"), ("b", "a")}
    assert edges[0]["bend"] == -edges[1]["bend"]


def test_validate_spec_rejects_unknown_edge_node():
    with pytest.raises(DgmError) as ei:
        validate_spec({"nodes": [{"id": "a"}], "edges": [{"from": "a", "to": "鬼"}]})
    assert "鬼" in str(ei.value)


def test_validate_spec_rejects_unknown_group_member():
    with pytest.raises(DgmError) as ei:
        validate_spec({"nodes": [{"id": "a"}], "groups": [{"label": "g", "nodes": ["a", "鬼"]}]})
    assert "鬼" in str(ei.value)


# ---------------- render：mock 掉浏览器 ----------------


def _fake_browser_run(shot: Path):
    """造一个假的 subprocess.run：把截图写成白底 + 一块黑块。"""

    def fake_run(cmd, capture_output=True, timeout=120):
        shot_arg = next(a for a in cmd if a.startswith("--screenshot="))
        shot_path = Path(shot_arg.split("=", 1)[1])
        im = Image.new("RGB", (400, 300), "white")
        for x in range(50, 300):
            for y in range(60, 260):
                im.putpixel((x, y), (0, 0, 0))
        im.save(shot_path)
        return subprocess.CompletedProcess(cmd, 0)

    return fake_run


def test_render_with_mock_browser(tmp_path, monkeypatch):
    shot = tmp_path / "shot.png"
    monkeypatch.setattr(subprocess, "run", _fake_browser_run(shot))
    spec = parse_dgm(SAMPLE)
    out = tmp_path / "out.png"
    html_keep = tmp_path / "keep.html"
    png, w, h = render(spec, out, scale=2, keep_html=html_keep, browser="fake-browser")

    assert png.exists()
    # 黑块 (50,60)-(300,260)，pad=24*scale=48；底边被图片高度 300 截断 → 346x288
    assert (w, h) == (346, 288)
    assert html_keep.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_render_no_screenshot_raises(tmp_path, monkeypatch):
    def fake_run(cmd, capture_output=True, timeout=120):
        return subprocess.CompletedProcess(cmd, 0)  # 什么都不产出

    monkeypatch.setattr(subprocess, "run", fake_run)
    spec = parse_dgm(SAMPLE)
    with pytest.raises(DgmError) as ei:
        render(spec, tmp_path / "out.png", browser="fake-browser")
    assert "截图失败" in str(ei.value)


def test_find_browser_error_message(monkeypatch, tmp_path):
    monkeypatch.delenv("DGM_BROWSER", raising=False)
    monkeypatch.setattr("dgm.renderer.EDGE_CANDIDATES", [str(tmp_path / "不存在的浏览器.exe")])
    with pytest.raises(DgmError) as ei:
        find_browser()
    assert "--edge" in str(ei.value)


def test_find_browser_env_var(monkeypatch, tmp_path):
    fake = tmp_path / "my-browser.exe"
    fake.write_text("")
    monkeypatch.setenv("DGM_BROWSER", str(fake))
    assert find_browser() == str(fake)


# ---------------- anchors：抽模板里的真 JS 跑 node，验证锚点贴边 ----------------


def _extract_anchor_js() -> str:
    """从 base.html 抽出 anchors()/diagAnchors() 函数源码——测真代码，不是 Python 复刻品。"""
    template = load_template()
    start = template.index("function anchors(")
    end = template.index("const NS=")
    js = template[start:end]
    assert "diagAnchors" in js, "抽出失败：模板里找不到 diagAnchors"
    return js


def _run_anchors_js(cases: list):
    """把 [(a,b) 或 (a,b,boxes), ...] 喂给模板里的 anchors()，返回对应返回值列表。"""
    node = shutil.which("node")
    if node is None:
        pytest.skip("本机没有 node，跳过 anchors JS 单测")
    script = (
        _extract_anchor_js()
        + "\nconst CASES="
        + json.dumps(cases)
        + ";\nconsole.log(JSON.stringify(CASES.map(c=>anchors(...c))));"
    )
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, f"node 执行失败：{out.stderr}"
    return json.loads(out.stdout)


_BOX_SEQ = itertools.count()


def _box(x: float, y: float, w: float = 200, h: float = 64, id: str | None = None) -> dict:
    """造一个 box 对象；id 必须唯一——segHitsBox 靠 id 跳过起终点卡片。"""
    return {"id": id or f"n{next(_BOX_SEQ)}", "x": x, "y": y, "w": w, "h": h,
            "cx": x + w / 2, "cy": y + h / 2, "r": x + w, "b": y + h}


def _on_border(p: dict, box: dict) -> bool:
    """点是否落在 box 的四条边之一上，且在该边段范围内。"""
    on_side = (p["x"] == box["x"] or p["x"] == box["r"]) and box["y"] <= p["y"] <= box["b"]
    on_top_bottom = (p["y"] == box["y"] or p["y"] == box["b"]) and box["x"] <= p["x"] <= box["r"]
    return on_side or on_top_bottom


def test_anchors_diagonal_endpoints_on_node_border():
    """斜对角（x/y 范围都不重叠）锚点必须贴边——回归 02/03 曲线断头的 bug。

    旧实现用「x 范围中点」当锚点，两卡片 x 范围不相交时中点落在空隙里，箭头悬空。
    """
    # 复刻 @1,1 → @2,3 的典型布局：卡片 172+ 宽，列间距 96 / 行间距 112
    a = _box(0, 0)                      # x∈[0,200] y∈[0,64]
    b = _box(200 + 96, 64 + 112)        # x∈[296,496] y∈[176,240]——两个范围都不重叠
    assert min(a["r"], b["r"]) <= max(a["x"], b["x"])   # x 不重叠（触发旧 bug 的前提）
    assert min(a["b"], b["b"]) <= max(a["y"], b["y"])   # y 不重叠

    [p1, p2, direction] = _run_anchors_js([(a, b)])[0]
    assert direction in ("dh", "dv")
    assert _on_border(p1, a), f"起点 {p1} 不在 a 的边框上"
    assert _on_border(p2, b), f"终点 {p2} 不在 b 的边框上"
    # 旧 bug 的悬空点：x 取两卡片 x 范围的中点 (200+296)/2=248，谁都不沾
    assert p1["x"] != 248 and p2["x"] != 248
    # 主方向横向（|dx|=296 > |dy|=176）→ a 右边中点出、b 左边中点入
    assert (p1["x"], p1["y"]) == (a["r"], a["cy"])
    assert (p2["x"], p2["y"]) == (b["x"], b["cy"])


def test_anchors_diagonal_all_four_directions():
    """四个斜对角方向，起终点都必须落在各自卡片边框上。"""
    a = _box(0, 0)
    right_down = _box(296, 176)      # b 在右下
    right_up = _box(296, -176 - 64)  # b 在右上
    left_down = _box(-296 - 200, 176)
    left_up = _box(-296 - 200, -176 - 64)
    pairs = [(a, right_down), (a, right_up), (a, left_down), (a, left_up),
             (right_down, a), (right_up, a), (left_down, a), (left_up, a)]
    for (p1, p2, direction), (aa, bb) in zip(_run_anchors_js(pairs), pairs):
        assert direction in ("dh", "dv")
        assert _on_border(p1, aa), f"起点 {p1} 不在卡片边框上"
        assert _on_border(p2, bb), f"终点 {p2} 不在卡片边框上"


def test_anchors_diagonal_vertical_dominant_uses_top_bottom_edges():
    """主方向纵向的斜对角 → 从上/下边出入，锚点同样贴边。"""
    a = _box(0, 0)
    b = _box(296, 64 + 400)  # 远在下方 → |dy| > |dx|
    [p1, p2, direction] = _run_anchors_js([(a, b)])[0]
    assert direction == "dv"
    assert (p1["x"], p1["y"]) == (a["cx"], a["b"])
    assert (p2["x"], p2["y"]) == (b["cx"], b["y"])
    assert _on_border(p1, a) and _on_border(p2, b)


def test_anchors_overlap_cases_still_use_overlap_midpoint():
    """同行/同列（范围重叠）的锚点仍取重叠范围中点——确认没把正常路径改坏。"""
    row_a, row_b = _box(0, 0), _box(296, 20)      # y 重叠 [20,64]
    col_a, col_b = _box(0, 0), _box(50, 176)      # x 重叠 [50,200]
    (h1, h2, hdir), (v1, v2, vdir) = _run_anchors_js([(row_a, row_b), (col_a, col_b)])
    assert hdir == "h" and vdir == "v"
    hy = (max(row_a["y"], row_b["y"]) + min(row_a["b"], row_b["b"])) / 2  # 42
    vx = (max(col_a["x"], col_b["x"]) + min(col_a["r"], col_b["r"])) / 2  # 125
    assert h1["y"] == h2["y"] == hy
    assert v1["x"] == v2["x"] == vx
    assert _on_border(h1, row_a) and _on_border(h2, row_b)
    assert _on_border(v1, col_a) and _on_border(v2, col_b)


def test_anchors_diagonal_avoids_crossing_middle_card():
    """斜对角路由避开夹在中间的卡片——回归 03「已驳回->草稿」水平段横穿中间列的走位。

    复刻 03 布局：草稿@1,1、提交申请@2,1（挡路卡）、已驳回@3,3。横向折线（dh）
    的最后一段在 y=已驳回 的行高上穿过提交申请；竖向折线（dv）中段走在空行隙里。
    传了 boxes 时应选 dv；不传 boxes（老接口）退回主方向 dh。
    """
    draft = _box(0, 0)            # 草稿 col1 row1
    middle = _box(296, 20)        # 提交申请 col2 row1（挡路）
    rejected = _box(200 * 2 + 96 * 2, 64 + 112)  # 已驳回 col3 row3
    boxes = [draft, middle, rejected]
    (p1, p2, direction), = _run_anchors_js([(rejected, draft, boxes)])
    assert direction == "dv"
    assert _on_border(p1, rejected) and _on_border(p2, draft)
    # 不传 boxes 时保持旧行为：按主方向（|dx|>|dy| → dh）
    (p1, p2, direction), = _run_anchors_js([(rejected, draft)])
    assert direction == "dh"
    assert _on_border(p1, rejected) and _on_border(p2, draft)


def test_anchors_diagonal_prefers_dominant_when_both_routes_clear():
    """两条候选都不穿卡时，仍按主方向选——确认避卡逻辑没有喧宾夺主。"""
    a = _box(0, 0)
    b = _box(296, 176)            # 右下斜对角，中间无卡
    boxes = [a, b]
    (p1, p2, direction), = _run_anchors_js([(a, b, boxes)])
    assert direction == "dh"      # |dx| 296 > |dy| 176
    assert (p1["x"], p1["y"]) == (a["r"], a["cy"])
    assert (p2["x"], p2["y"]) == (b["x"], b["cy"])
