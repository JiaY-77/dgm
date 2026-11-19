"""dgm 命令行入口：报错说人话，出错给行号。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dgm import __version__
from dgm.parser import DgmError, parse_dgm
from dgm.renderer import render, validate_spec


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="dgm",
        description="几行文本 → 一张 PNG（状态机 / 流程图）",
        epilog="示例：dgm flow.dgm -o flow.png --scale 3 --html flow.html",
    )
    ap.add_argument("src", help="输入文件：.dgm 文本（或旧版 .json spec）")
    ap.add_argument("-o", "--out", help="输出 PNG 路径（--check 时可省略）")
    ap.add_argument("--scale", type=int, default=2, help="清晰度倍数（默认 2）")
    ap.add_argument("--html", help="同时保留可交互 HTML 到这个路径")
    ap.add_argument("--edge", help="浏览器路径（默认自动找 Edge / Chrome）")
    ap.add_argument("--check", action="store_true", help="只解析校验语法，不渲染")
    ap.add_argument("--version", action="version", version=f"dgm {__version__}")
    return ap


def _load_spec(path: Path) -> dict:
    if not path.exists():
        raise DgmError(f"找不到输入文件：{path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise DgmError(
            f"文件不是 UTF-8 编码，读不了：{path}", hint=f"先把文件转成 UTF-8（{e}）"
        ) from None
    if path.suffix.lower() == ".json":
        try:
            spec = json.loads(text)
        except json.JSONDecodeError as e:
            raise DgmError(f"JSON 解析失败：{e}", hint="旧版 .json spec 要是合法 JSON") from None
        return validate_spec(spec)
    return parse_dgm(text)


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        spec = _load_spec(Path(args.src))
    except DgmError as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.check:
        print(f"[ok] 语法没问题：{len(spec['nodes'])} 个节点 / "
              f"{len(spec['edges'])} 条边 / {len(spec['groups'])} 个分组")
        return 0
    if not args.out:
        ap.error("缺少输出路径：加 -o out.png（只想校验语法的话用 --check）")
    try:
        png, width, height = render(spec, args.out, args.scale, args.html, args.edge)
    except DgmError as e:
        print(str(e), file=sys.stderr)
        return 1
    size_kb = Path(png).stat().st_size // 1024
    print(f"[ok] {png}  {width}x{height}  {size_kb} KB")
    if args.html:
        print(f"[ok] {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
