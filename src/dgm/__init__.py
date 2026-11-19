"""dgm：几行文本 → 一张 PNG 的图表工具（状态机 / 流程图）。"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from dgm.parser import DgmError, parse_dgm
from dgm.renderer import build_html, load_template, render, validate_spec

try:
    __version__ = _pkg_version("dgm-diagram")
except PackageNotFoundError:  # 源码直跑（没 pip install）时的兜底
    __version__ = "0.0.0.dev0"

__all__ = [
    "DgmError",
    "__version__",
    "build_html",
    "load_template",
    "parse_dgm",
    "render",
    "validate_spec",
]
