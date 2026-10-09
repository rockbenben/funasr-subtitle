# Engine — onnx 后端实测验证结论

> 本文件记录 funasr-onnx / ONNX 各模块在本机的**实测结论**（torch-free 的 onnx 后端），
> 供后续模式复用。测量环境：Windows 11 x64、Python 3.11、**未安装 torch**。
> 依赖一律取当前最新（`pip install -e "backend[onnx]"`），**本文不钉依赖版本号**——
> 某条结论若确实只对特定版本成立，会在该条里就地注明。
> ⚠️ 当前默认装的是**纯 CPU 版 onnxruntime**（DirectML 已被移出依赖，见第 6 节）。
> 第 1 节的 RTF / 准确率结论在纯 CPU 版上同样成立——那些本来就是 CPU 测量；
> 只有第 6 节的 DirectML 数据需要额外手动装 `onnxruntime-directml`。

## 1. SenseVoiceSmall（默认 ASR）

- **ONNX 来源**：官方 `iic/SenseVoiceSmall-onnx`（94 万+下载），含 `model_quant.onnx`（≈230MB）。
  - ⚠️ 原始 `iic/SenseVoiceSmall` 仓库**只有 `model.pt`（893MB），没有 onnx**；
    funasr-onnx 遇到缺 onnx 会尝试 `from funasr import AutoModel` 在线导出（需 torch+funasr）——我们不走这条路。
  - ⚠️ onnx 仓库**缺 `chn_jpn_yue_eng_ko_spectok.bpe.model`**（分词器需要），
    需从 pt 仓库用 `allow_file_pattern=[bpe]` 单独补一份（见 `models/manager.py:_fetch_aux_files`）。
- **torch 依赖**：`sensevoice_bin.py` 顶部 `import torch`，但 torch 仅在其 `__call__` 的
  CTC 贪心解码用到。我们**绕过 `__call__`**，直接用其 `extract_feat / read_tags(_get_lid/_get_tnid) / infer`，
  CTC 解码改用**纯 numpy**（argmax → 去连续重复 → 去 blank=0）。运行时无需 torch。
- **时间戳**：funasr-onnx 的 SenseVoice `__call__` **不直接给时间戳**，但 CTC logits 的
  **帧位置可推出 token 时间**：实测去掉前 4 个特殊前缀帧后，语音帧 ≈ **60ms/帧**且稳定。
  我们在 numpy CTC 解码时保留每个 token 的首现帧号（`_ctc_greedy_with_frames`），
  线性映射到 VAD 段的 [start,end]，得到逐 token 时间戳，再在标点处切自然语句
  （`subtitle.segment_timed`）。这比「整段用 VAD 边界」精确：能裁掉前导静音、
  并把同一 VAD 段内的多句各自定位（解决「切分太粗糙」）。
  Paraformer 路径无逐 token 时间 → 退回按字数比例分配（`segment_span`）。
  （这是 onnx 后端；完整版 torch 后端用 funasr 的 `sentence_info` 给 Paraformer 句级时间戳。）
- **特殊标记**：输出文本前缀带 `<|lang|><|emo|><|event|><|itn|>`（如 `<|zh|><|NEUTRAL|><|Speech|><|withitn|>`），
  用正则 `<\|[^|>]*\|>` 剥离。
- **标点**：`withitn` 模式输出**已自带标点**（逗号/句号/问号）。→ 默认 ASR **跳过 ct-punc**，
  否则会出现 `？？？` 这类重复标点。（`ModelSpec.builtin_punc=True`）
- **实测**（10.3s 中文样本，CPU，模型已加载）：ASR 推理本身 ~0.2s（RTF≈0.02）；
  整条管线（VAD + ASR + 切句）~1.0s（RTF≈0.10），文本与时间戳正确。
  （旧记录的 RTF≈0.41 为早期/冷态测量，已重测更正。）

## 2. VAD — fsmn-vad

- **ONNX 来源**：`iic/speech_fsmn_vad_zh-cn-16k-common-onnx`，**只含 `model_quant.onnx`** → 必须 `quantize=True`。
- **API**：`Fsmn_vad(dir, quantize=True)(wav_float32_16k)` → `[[[start_ms,end_ms], ...]]`
  （最外层按 batch；无语音时为 `[[]]`，我们兜底为整段一片）。

## 3. 标点 — ct-punc

- **ONNX 来源**：`iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx`，**只含 `model_quant.onnx`** → `quantize=True`。
- **API**：`CT_Transformer(dir, quantize=True)("无标点文本")` → `("有标点文本", [punc_id...])`。实测中文标点正确。
- 默认 SenseVoice 路径用不到（builtin_punc）；保留给 Paraformer 等不自带标点的 ASR。

## 4. torch-stub 与 modelscope/scipy 的共存陷阱（重要）

funasr-onnx 必须能 `import torch` 才能 import（`__init__` 会导入 sensevoice_bin）。我们注入一个**stub**：
- stub 必须有 `__spec__`（否则 `importlib.util.find_spec('torch')` 抛 `ValueError: __spec__ is None`），
  还需 `__version__` 和一个 `Tensor` 占位类（scipy 的 array_api_compat 会 `getattr(torch,'Tensor')`）。
- **modelscope 与 stub 不能共存**：modelscope 的 logger 用 `find_spec('torch')`，一旦发现 torch 就会
  `import torch_utils`（调 `torch.distributed`）→ 崩。
  → 解决：**先在无 stub 时用 modelscope 完成下载/初始化，再装 stub 导入 funasr**。
- **scipy/librosa 在 import 期探测 torch**：必须**先 torch-free 导入 scipy.signal/librosa**，再装 stub。

安全导入顺序固化在 `funasr_engine._ensure_funasr()`：
`import modelscope.hub.snapshot_download` → `import scipy.signal, librosa` → 装 stub → `import funasr_onnx`。

## 5. 模型下载

- 用 `modelscope.hub.snapshot_download(repo, cache_dir=%LOCALAPPDATA%\funasr-subtitle\models)`，可达性好，**torch-free**。
- 用 `ignore_file_pattern=["*.pt","*.pth",...]` 跳过 PyTorch 权重（SenseVoice pt 仓库 893MB）。
- 默认链路（SenseVoice）总下载量 ≈ VAD(0.5MB) + SenseVoice-onnx(230MB) + bpe(0.4MB) ≈ **231MB**
  （registry 的 `size_mb` 取整估值 **235MB**，用于 UI 进度显示）。

## 6. 实测结论（Paraformer / DirectML / diarization）

- **Paraformer-zh（已实现）**：两个 ONNX 仓库都验证可用，引擎按 `ModelSpec.engine` 分派：
  - 普通版 `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx`（quant 238MB）→
    `funasr_onnx.Paraformer`。中文精度高于 SenseVoice。⚠️ 该路径**不输出时间戳**
    （走 `{"preds":...}` 分支），时间戳仍由 VAD 提供；`preds` 是 `(整句, [逐字])` 元组，取 [0]。
    无标点 → 走 ct-punc。
  - 热词版 `...-contextual...onnx`（quant 871MB + model_eb 25MB）→ `funasr_onnx.ContextualParaformer`，
    `__call__(wav, hotwords)` 支持热词。两个坑：① quant 路径要 `model_eb_quant.onnx`，
    仓库只有 `model_eb.onnx` → 下载后在目录内复制一份（`ModelSpec.local_copies`）；
    ② funasr-onnx 的 `ContextualParaformer.__init__` 漏设 `self.language`，`__call__` 会读它
    → 加载后手动 `asr.language="zh"`。（若上游哪天修好了，删掉这行即可。）
- **DirectML（已实现，但默认关闭）**：用 **onnxruntime-directml**（同时提供 CPU+DML）。
  funasr-onnx 的 `OrtInferSession` 只会挂 CUDA/CPU，故用 monkeypatch 把
  `funasr_onnx.utils.utils.InferenceSession` 重定向为 `providers=[Dml, CPU]`
  （见 `_patch_directml`），对 VAD/ASR/punc 全部生效。实测三个 session 的
  `get_providers()` 均为 `['DmlExecutionProvider','CPUExecutionProvider']`，转写正确。
  通过 `use_gpu` 启用（后端能力保留：`/api/models` 仍返回 `compute` 算力标签，前端据此显示
  「用 CPU 跑 / 用显卡跑」）。
  **实测结论（重要）**：对默认的**量化(int8) ONNX** 模型，DML 虽然真的在 GPU 上跑
  （~78% 算子核落在 DmlExecutionProvider，22% 回退 CPU），但**比纯 CPU 慢 ~2.8×**
  （10.3s 样本，**仅 ASR 推理**：CPU 0.21s vs DML 0.59s；整条管线另见本文 1.）。原因：模型小、音频短、量化算子部分回退导致
  GPU↔CPU 反复拷贝，固定开销盖过算力收益。→ **前端已隐藏「显卡加速」开关**，默认 CPU。
  真正要 GPU 提速得用完整 funasr+torch+CUDA（体积数 GB，违背本工具定位），不做。
  ⚠️ **依赖现状**：`onnxruntime-directml` **已不在 `pyproject.toml` 依赖里**。原因有二——
  ① 它和 `funasr-onnx` 依赖的纯 CPU 版 `onnxruntime` 提供同名包、会互相覆盖，
  pip 保证不了 DirectML 胜出（单次 `pip install` 实测得到的是 CPU 版）；
  ② 既然开关已隐藏且实测更慢，没必要默认装它。
  想手动复现本节实验：`pip install --force-reinstall --no-deps onnxruntime-directml`，
  然后确认 `ort.get_available_providers()` 里有 `DmlExecutionProvider`。
- **diarization（cam++）**：funasr-onnx **至今仍无任何说话人分离类** → onnx 后端降级不支持，
  本期不支持，前端开关置灰，后端安全忽略。

## 7. 内存占用（长视频 / 反复切模型）

本工具是常驻的本地应用，长视频和反复切模型最容易把内存顶爆。实测（Windows 11，
峰值工作集 peak_wset，样本为 16k 单声道正弦波）：

- **解码峰值**：PCM 直接流式读进按 ffprobe 时长预分配的 numpy 缓冲区（`media/decode.py:_PcmSink`），
  不再是「攒 `list[bytes]` 再 `b"".join()`」造成的 2 份拷贝。

  | 音频长度 | 旧实现 | 现实现 | 降幅 |
  | --- | --- | --- | --- |
  | 30 分钟 | 254.9 MB | 145.4 MB | −43% |
  | 2 小时 | 915.6 MB | 474.9 MB | −48% |

  时长估准时缓冲区**不会重新分配**（只有 ffprobe 报不准时才按倍数扩容）。
- **引擎常驻**：缓存键是 `(model_id, use_gpu, diarization)`，来回切模型/开关说话人分离
  会不断产生新引擎，各自持有一组 ONNX session。改为 LRU、上限 `ENGINE_CACHE_SIZE`（默认 2）
  后，走同样的 4 键切换：旧 1857 MB（4 个引擎一路上涨）→ 现 1027 MB（稳定在 2 个、走平）。
  安全前提：任务由单个 worker 线程串行执行，不存在「另一个任务正在用被淘汰的引擎」。
- **已完成任务**：每个 Job 常驻全部 segments，改为按 `MAX_JOBS`（默认 50）从最旧的
  **终态**任务开始丢弃；排队中/执行中的任务永不丢（否则 UI 会误报「任务不存在」）。
