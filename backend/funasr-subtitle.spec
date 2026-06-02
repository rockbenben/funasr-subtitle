# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec（onedir，windowed）— §12 / §17.7。

§17.7 实测要点：
- collect_all('funasr_onnx') 在无 torch 时**无法 import 而枚举失败**。
  解决：在 spec 里先收集 modelscope（无 stub），再注入 torch-stub，再收集 funasr_onnx /
  scipy / sklearn —— 与运行时的安全顺序一致。
- 额外加运行时钩子 rthook_torch_stub.py，保证冻结程序启动最早期就有 torch-stub。
- 显式列出 funasr_onnx 子模块作为 hiddenimports，双保险。
- excludes torch / funasr（运行时用 numpy + torch-stub）。模型不进包，首启下载。
"""
import sys
import types
import importlib.machinery
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = []


def _grab(pkg):
    try:
        d, b, h = collect_all(pkg)
        return d, b, h
    except Exception as e:
        print(f"[funasr-subtitle.spec] collect_all({pkg}) partial: {e}")
        return [], [], []


# 1) 先收集「无 torch 时可正常 import」的包（modelscope 必须在装 stub 前收集）
for pkg in ["modelscope", "onnxruntime", "soundfile", "kaldi_native_fbank",
            "sentencepiece", "jieba", "sympy"]:
    d, b, h = _grab(pkg)
    datas += d; binaries += b; hiddenimports += h

# 2) 注入 torch-stub（与运行时同源），让 funasr_onnx 可被 import/枚举
if "torch" not in sys.modules:
    _stub = types.ModuleType("torch")
    _stub.__spec__ = importlib.machinery.ModuleSpec("torch", loader=None)
    _stub.__version__ = "0.0.0-stub-fs"
    _stub.Tensor = type("Tensor", (), {})
    sys.modules["torch"] = _stub

# 3) 现在收集需要 torch 才能 import 的包
for pkg in ["funasr_onnx", "librosa", "scipy", "sklearn"]:
    d, b, h = _grab(pkg)
    datas += d; binaries += b; hiddenimports += h

# funasr_onnx 子模块双保险
hiddenimports += [
    "funasr_onnx",
    "funasr_onnx.sensevoice_bin",
    "funasr_onnx.vad_bin",
    "funasr_onnx.punc_bin",
    "funasr_onnx.paraformer_bin",
    "funasr_onnx.utils.utils",
    "funasr_onnx.utils.frontend",
    "funasr_onnx.utils.sentencepiece_tokenizer",
]
hiddenimports += collect_submodules("uvicorn")
# 显式收集 app 全部子模块：不依赖 modulegraph 追函数体里的延迟 import，冻结后不丢模块
hiddenimports += collect_submodules("app")
hiddenimports += ["pystray", "PIL"]

# 前端静态产物（build.ps1 在打包前拷到 backend/frontend_dist）
import os
_dist = os.path.join(os.getcwd(), "frontend_dist")
if os.path.isdir(_dist):
    datas += [(_dist, "frontend_dist")]


a = Analysis(
    ["funasr_subtitle.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=["packaging_hooks/rthook_torch_stub.py"],
    excludes=["torch", "funasr", "tkinter", "matplotlib", "tensorflow"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="funasr-subtitle",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # windowed（无控制台）
    disable_windowed_traceback=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="funasr-subtitle",
)
