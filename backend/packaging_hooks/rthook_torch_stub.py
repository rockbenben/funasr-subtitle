"""PyInstaller 运行时钩子：在冻结程序启动最早期注入 torch-stub。

确保任何 `import torch`（funasr_onnx 顶层）在无真 torch 时也能成功。
与 app.engine.funasr_engine._ensure_funasr 同源，二者幂等。
"""
import importlib.machinery
import sys
import types

if "torch" not in sys.modules:
    _stub = types.ModuleType("torch")
    _stub.__spec__ = importlib.machinery.ModuleSpec("torch", loader=None)
    _stub.__version__ = "0.0.0-stub-fs"
    _stub.Tensor = type("Tensor", (), {})
    sys.modules["torch"] = _stub
