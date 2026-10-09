"""Pydantic 数据模型（§8）。

JobStatus 同时充当管线 stage 名（§7 WS progress.stage）。
这些类型是所有未来模式（会议纪要/转图文/实时）的公共数据契约（§16）。
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from .config import DEFAULT_MAX_CHARS


# 保持 (str, Enum) 而不是 StrEnum：StrEnum 会把 str() 变成取值本身，
# 而这里的 JobStatus 会经 job.model_dump() 序列化成 WS/REST 载荷，语义不能变。
class JobStatus(str, Enum):  # noqa: UP042
    queued = "queued"
    decoding = "decoding"
    vad = "vad"
    asr = "asr"
    punc = "punc"
    diar = "diar"
    assembling = "assembling"
    done = "done"
    error = "error"


Language = Literal["auto", "zh", "yue", "en", "ja", "ko"]


class Segment(BaseModel):
    start_ms: int
    end_ms: int
    text: str
    speaker: str | None = None  # diarization 开启时存在，如 "spk0"


Punctuation = Literal["auto", "on", "off"]


class JobOptions(BaseModel):
    model_id: str = ""  # 空 -> 用 default
    language: Language = "auto"
    diarization: bool = False
    # 标点模式：auto=中文等自动加、英文按停顿不强加（默认）；on=都加；off=都不加句末标点
    punctuation: Punctuation = "auto"
    hotwords: str = ""  # 空格分隔，可空
    use_gpu: bool = False  # DirectML 显卡加速（§10/M8），不可用则回退 CPU
    # 每行最大「显示宽度」：中日韩字符=1，拉丁/数字=0.5（默认 30 ≈ 中文 30 字 / 英文 ~60 字）。
    # 0=不限制（只按句末标点切）。超长在安全边界（标点>词边界>CJK 字）断，不拆英文词。
    # 默认值可用环境变量 FUNASR_SUBTITLE_MAX_CHARS 调整。
    max_chars: int = Field(default=DEFAULT_MAX_CHARS, ge=0, le=100)


class JobError(BaseModel):
    code: str
    message: str
    # 原始技术细节（ffmpeg / 异常）：界面折叠进「查看详情」，message 只放人话。
    detail: str | None = None


class Job(BaseModel):
    id: str
    filename: str
    status: JobStatus = JobStatus.queued
    percent: float = 0.0  # 0..100
    options: JobOptions = Field(default_factory=JobOptions)
    segments: list[Segment] = Field(default_factory=list)
    error: JobError | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


# ---- API 辅助响应 ----

class ModelInfo(BaseModel):
    id: str
    name: str
    task: str
    size_mb: float
    downloaded: bool


class ModelsResponse(BaseModel):
    models: list[ModelInfo]
    default_id: str


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str


class CreateJobResponse(BaseModel):
    job_id: str


class OkResponse(BaseModel):
    ok: bool = True


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
