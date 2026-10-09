"""模型目录。

modelscope 仓库 id 已确认可达且含 .onnx（见 engine/README.md）。
- VAD / punc 是所有 ASR 模型共享的辅助模块。
- 面向用户的「模型」目前是 ASR 模型（默认 SenseVoiceSmall）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    id: str  # 我们对外暴露的稳定 id（= modelscope repo id）
    name: str  # 显示名
    task: str  # "asr" | "vad" | "punc" | "diar"
    size_mb: float  # 粗略下载体积，用于 UI 展示
    repo: str  # modelscope 仓库 id（snapshot_download 用）
    # 主仓库缺、需从别处补的文件：((文件名, 来源仓库), ...)
    # SenseVoiceSmall-onnx 仓库不含 bpe 词表，需从 pt 仓库补一份（见 engine/README.md 1.）。
    aux_files: tuple[tuple[str, str], ...] = ()
    # ASR 输出是否已含标点（含则跳过 ct-punc，SenseVoice withitn 自带标点，见 engine/README.md）
    builtin_punc: bool = False
    # 引擎解码路径：sensevoice(CTC numpy) | paraformer | contextual(支持热词)
    engine: str = "sensevoice"
    # 是否支持热词（仅 contextual paraformer）
    supports_hotwords: bool = False
    # 下载后在模型目录内复制/改名的文件：((源文件名, 目标文件名), ...)
    # contextual 仓库只有 model_eb.onnx，funasr 量化路径要 model_eb_quant.onnx。
    local_copies: tuple[tuple[str, str], ...] = ()
    # 完整 funasr（torch）后端用的模型 id（AutoModel model=...）。
    # 与 onnx repo 不同：完整后端用 pt 模型。空则该模型不支持 full 后端。
    full_model: str = ""


# ---- ASR（面向用户可选） ----
SENSEVOICE = ModelSpec(
    id="iic/SenseVoiceSmall-onnx",
    name="SenseVoiceSmall（多语言·默认·快）",
    task="asr",
    size_mb=235.0,
    repo="iic/SenseVoiceSmall-onnx",
    aux_files=(("chn_jpn_yue_eng_ko_spectok.bpe.model", "iic/SenseVoiceSmall"),),
    builtin_punc=True,
    engine="sensevoice",
    full_model="iic/SenseVoiceSmall",
)
PARAFORMER_ZH = ModelSpec(
    id="iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx",
    name="Paraformer-zh（中文高精度）",
    task="asr",
    size_mb=240.0,
    repo="iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx",
    builtin_punc=False,
    engine="paraformer",
    full_model="paraformer-zh",
)
PARAFORMER_ZH_HOTWORD = ModelSpec(
    id="iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx",
    name="Paraformer-zh 热词版（中文高精度·支持热词）",
    task="asr",
    size_mb=900.0,
    repo="iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx",
    builtin_punc=False,
    engine="contextual",
    supports_hotwords=True,
    local_copies=(("model_eb.onnx", "model_eb_quant.onnx"),),
    full_model="iic/speech_seaco_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
)

# ---- 辅助模块（共享，不在用户「模型」下拉里单列） ----
VAD_MODEL = ModelSpec(
    id="iic/speech_fsmn_vad_zh-cn-16k-common-onnx",
    name="FSMN-VAD",
    task="vad",
    size_mb=2.0,
    repo="iic/speech_fsmn_vad_zh-cn-16k-common-onnx",
)
PUNC_MODEL = ModelSpec(
    id="iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx",
    name="CT-Punc",
    task="punc",
    size_mb=290.0,
    repo="iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx",
)

DEFAULT_MODEL_ID = SENSEVOICE.id

# 面向用户的 ASR 模型目录（UI 下拉）
ASR_MODELS: list[ModelSpec] = [SENSEVOICE, PARAFORMER_ZH, PARAFORMER_ZH_HOTWORD]

# 全部模型（含辅助），按 id 索引
REGISTRY: dict[str, ModelSpec] = {
    m.id: m for m in (SENSEVOICE, PARAFORMER_ZH, PARAFORMER_ZH_HOTWORD,
                      VAD_MODEL, PUNC_MODEL)
}


def get_model(model_id: str) -> ModelSpec:
    if model_id not in REGISTRY:
        raise KeyError(model_id)
    return REGISTRY[model_id]
