"""Reject stale/CPU-only build environments without requiring a GPU on the builder."""
import importlib
import re
import sys
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parents[1] / "backend/requirements-cuda.txt"


def verify() -> None:
    expected = dict(re.findall(
        r"^(torch|torchaudio)==(\S+)$", REQUIREMENTS.read_text(encoding="utf-8"), re.MULTILINE,
    ))
    for name in ("torch", "torchaudio"):
        module = importlib.import_module(name)
        actual = module.__version__
        if actual != expected[name]:
            raise RuntimeError(f"{name}: expected {expected[name]}, found {actual}")
    torch = importlib.import_module("torch")
    cuda = expected["torch"].split("+cu")[1]
    runtime = f"{cuda[:-1]}.{cuda[-1]}"
    if torch.version.cuda != runtime:
        raise RuntimeError(f"expected CUDA {runtime}, found {torch.version.cuda}")
    # Importing both also loads their native DLLs; a matching metadata version alone
    # cannot catch a broken/mixed installation. No is_available() gate: CI has no GPU.
    print(f"CUDA stack OK: torch {torch.__version__}, torchaudio {expected['torchaudio']}")


if __name__ == "__main__":
    try:
        verify()
    except (ImportError, OSError, RuntimeError) as exc:
        print(f"CUDA preflight failed: {exc}\nInstall with: python -m pip install -r {REQUIREMENTS}",
              file=sys.stderr)
        sys.exit(1)
