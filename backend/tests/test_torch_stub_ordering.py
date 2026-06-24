"""torch-stub 安装时机的回归测试（修复「No module named 'torch.multiprocessing'」）。

背景（§17 / engine/README.md）：
- modelscope 的 get_logger() 用 `importlib.util.find_spec('torch')` 探测 torch；
  一旦发现 torch（哪怕是我们注入的 stub），就会 `from modelscope.utils.torch_utils
  import is_master`，而 torch_utils 顶部 `import torch.multiprocessing as mp` —— stub 是
  扁平 module（非 package），子模块导入抛
  `ModuleNotFoundError: No module named 'torch.multiprocessing'; 'torch' is not a package`。
- 因此**必须**让 modelscope 在「无 torch」时先完成 logger 初始化，之后再装 stub。
  app.engine.funasr_engine._ensure_funasr 已按此顺序处理。

历史 bug：funasr-subtitle.spec 曾用 runtime_hook `rthook_torch_stub.py` 在冻结程序
**启动最早期**注入 stub，先于 modelscope 初始化 —— 导致纯 CPU 包
（funasr-subtitle-win-x64）一启动/首次用 modelscope 即崩。开发态（uvicorn，无
runtime hook）复现不了，故长期未被发现。

本测试用独立子进程（import 状态是全局的，必须隔离）锁定两条不变量：
1. stub-first 必然复现该崩溃（说明 runtime hook 为何被禁止）。
2. modelscope-first → 再装 stub 不崩（_ensure_funasr 的正确顺序）。
并静态断言 spec 不再注册「启动期装 stub」的 runtime hook。
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

_STUB = textwrap.dedent(
    """
    import sys, types, importlib.machinery
    stub = types.ModuleType("torch")
    stub.__spec__ = importlib.machinery.ModuleSpec("torch", loader=None)
    stub.__version__ = "0.0.0-stub-fs"
    stub.Tensor = type("Tensor", (), {})
    sys.modules["torch"] = stub
    """
)


def _run(*fragments: str) -> subprocess.CompletedProcess:
    # 每个片段单独 dedent 再拼接：拼接后整体 dedent 会因公共缩进为 0 而无法去缩进。
    code = "\n".join(textwrap.dedent(f) for f in fragments)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(BACKEND), capture_output=True, text=True,
    )


def _modelscope_available() -> bool:
    return _run("import modelscope.hub.snapshot_download").returncode == 0


pytestmark = pytest.mark.skipif(
    not _modelscope_available(), reason="modelscope 未安装（onnx 后端依赖），跳过"
)


def test_stub_before_modelscope_crashes():
    """复现历史 bug：先装 stub 再 import modelscope -> torch.multiprocessing 崩溃。"""
    proc = _run(
        _STUB,
        """
        import modelscope.hub.snapshot_download
        print("NO_CRASH")
        """,
    )
    assert "torch.multiprocessing" in proc.stderr, proc.stderr
    assert "NO_CRASH" not in proc.stdout


def test_modelscope_before_stub_is_safe():
    """正确顺序：modelscope 先 torch-free 初始化，再装 stub，不崩。"""
    proc = _run(
        """
        import modelscope.hub.snapshot_download  # torch-free init first
        """,
        _STUB,
        """
        # 装 stub 后任何 modelscope 操作都不应再触发 torch_utils 崩溃
        from modelscope.utils import logger as L
        assert L.init_loggers.get("modelscope") is True
        print("OK")
        """,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_spec_has_no_startup_torch_stub_hook():
    """静态护栏：spec 不得用 runtime hook 在启动期注入 torch-stub（即本 bug 的根因）。"""
    spec = (BACKEND / "funasr-subtitle.spec").read_text(encoding="utf-8")
    assert "rthook_torch_stub" not in spec, (
        "funasr-subtitle.spec 不应注册 rthook_torch_stub —— 它会在 modelscope 初始化前"
        "装 stub，导致冻结的 CPU 包崩 'torch.multiprocessing'。stub 由 _ensure_funasr "
        "在 modelscope 初始化之后安装。"
    )
