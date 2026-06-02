"""英文标点来源选择。

Root cause（实测）：英文路径原本剥离 SenseVoice 自带标点、改用 zh-cn ct-punc 重标点，
而 zh 模型会在错误位置插句号（把一句切成几段）。修复：原生标点优先，仅当原生无句末
标点时才退回 ct-punc。纯逻辑，无需加载模型。
"""
from app.engine.funasr_engine import _latin_punct, _normalize_latin_punct, _has_terminal


def test_latin_prefers_native_punct_over_ctpunc():
    # SenseVoice 原生已带正确句末标点：直接采用，绝不调用 ct-punc。
    native = "So the thing is they learn from data. And once you have enough data, you can train."
    bare = "".join(c for c in native if c not in ".,?!;:")
    called = {"ct": False}

    def fake_ctpunc(_s):
        called["ct"] = True
        return "wrong. all. over."

    out = _latin_punct(native, bare, fake_ctpunc)
    assert out == _normalize_latin_punct(native)
    assert called["ct"] is False  # 原生有句末标点 -> 不退回 ct-punc


def test_latin_falls_back_to_ctpunc_when_native_has_no_terminal():
    # 原生完全没有句末标点时，才退回 ct-punc 补。
    native = "hello world how are you today"  # 无终止标点
    out = _latin_punct(native, native, lambda _s: "hello world. how are you today?")
    assert _has_terminal(out)
