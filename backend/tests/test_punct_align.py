"""_apply_punct_to_timed 对齐：大小写/增删字符不应让时间戳塌缩。"""
from app.engine.funasr_engine import _apply_punct_to_timed


def test_case_change_does_not_collapse_timestamps():
    # bare 逐字带真实时间；punct 把首字母大写（ct-punc 兜底常见）。
    timed = [(c, 100 + i * 10) for i, c in enumerate("hello world")]
    out = _apply_punct_to_timed(timed, "Hello world.")
    # 收集非空白字符的时间，必须仍随位置递增（不能全塌到第一个时间）
    times = [t for ch, t in out if not ch.isspace()]
    assert times[0] == 100
    assert times[-1] >= 190  # 末字符 'd' 的真实时间附近，而非塌到 100
    assert times == sorted(times)


def test_inserted_char_resyncs():
    timed = [(c, 10 + i * 10) for i, c in enumerate("dont")]
    out = _apply_punct_to_timed(timed, "don't")  # 插入了 '
    times = [t for ch, t in out if ch.isalpha()]
    # 'dont' 四个字母时间应保留递增，不因插入的 ' 而塌缩
    assert times == [10, 20, 30, 40]
