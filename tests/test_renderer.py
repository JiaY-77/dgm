"""renderer 测试：不跑真浏览器，mock 掉 subprocess，验证 HTML 内容和裁剪逻辑。"""

import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from dgm.parser import DgmError, parse_dgm
from dgm.renderer import build_html, find_browser, render, validate_spec

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
