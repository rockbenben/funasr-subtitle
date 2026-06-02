# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec —— 「完整 funasr」变体（torch + CUDA，onedir，windowed）。

与 onnx 变体的区别：
- 真 torch 在场 -> **不注入 torch-stub、不 exclude torch**、无 rthook。
- 收集 funasr / torch / torchaudio（含 CUDA DLL，包会很大，数 GB）。
- 入口、前端静态资源、windowed 设置同 onnx 变体。
- 由 build.ps1 -Variant full 用 .venv-full 调用，--distpath dist-full。

注意：torch+CUDA 的 collect_all 体积巨大且耗时，属预期。
"""
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []


def _grab(pkg):
    try:
        d, b, h = collect_all(pkg)
        return d, b, h
    except Exception as e:
        print(f"[full.spec] collect_all({pkg}) partial: {e}")
        return [], [], []


for pkg in ["funasr", "torch", "torchaudio", "modelscope", "librosa", "scipy",
            "sklearn", "soundfile", "kaldi_native_fbank", "sentencepiece", "jieba"]:
    d, b, h = _grab(pkg)
    datas += d; binaries += b; hiddenimports += h

hiddenimports += collect_submodules("uvicorn")
# 显式收集 app 全部子模块（含 funasr_full_engine），不依赖 modulegraph 追延迟 import
hiddenimports += collect_submodules("app")
hiddenimports += ["pystray", "PIL"]

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
    runtime_hooks=[],            # 无 torch-stub：真 torch 在场
    excludes=["tkinter", "matplotlib", "tensorflow"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="funasr-subtitle",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False, icon=None,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, upx_exclude=[], name="funasr-subtitle",
)
