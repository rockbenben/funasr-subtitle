"""CLI 参数 -> JobOptions 的单测（不触发推理，只查参数映射）。

界面上的设置与命令行开关是一一对应的：默认值必须与 JobOptions 的默认值一致，
否则「命令行跑出来的结果」和「界面上跑出来的结果」会不一样。
"""
from app.engine.cli import _build_parser, _options_from_args
from app.schemas import JobOptions


def _opts(*argv: str):
    return _options_from_args(_build_parser().parse_args(list(argv)))


def test_defaults_match_job_options_defaults():
    opts = _opts("a.wav")
    assert opts.model_id == ""
    assert opts.language == "auto"
    assert opts.max_chars == JobOptions().max_chars
    assert opts.pause_ms == 0
    assert opts.itn is True
    assert opts.punctuation == "auto"
    assert opts.hotwords == ""


def test_split_options_are_forwarded():
    opts = _opts("a.wav", "--max-chars", "12", "--pause-ms", "300")
    assert opts.max_chars == 12
    assert opts.pause_ms == 300


def test_no_itn_turns_off_number_normalization():
    assert _opts("a.wav").itn is True
    assert _opts("a.wav", "--no-itn").itn is False


def test_punctuation_and_hotwords_are_forwarded():
    opts = _opts("a.wav", "--punctuation", "off", "--hotwords", "字幕 导出")
    assert opts.punctuation == "off"
    assert opts.hotwords == "字幕 导出"


def test_out_of_range_values_are_clamped():
    opts = _opts("a.wav", "--max-chars", "9999", "--pause-ms", "-5")
    assert opts.max_chars == 100
    assert opts.pause_ms == 0
