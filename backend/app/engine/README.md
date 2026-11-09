# Engine — 推理后端实测验证结论

> 本文件记录两条推理后端在本机的**实测结论**：
> - **onnx 后端**（funasr-onnx / ONNX，纯 CPU，CPU 便携包走这条）——第 1–6 节的 RTF /
>   准确率 / DirectML 数据都基于它；
> - **full 后端**（完整 funasr + torch / CUDA，CUDA 便携包走这条）——文中标「完整版」的部分。
>
> 测量环境：Windows 11 x64、Python 3.11。onnx 线的环境**未安装 torch**（这是它能纯 CPU
> 运行的前提）；full 线的环境装了 torch + CUDA。
> 依赖版本下限见 `pyproject.toml`（2026-10 已整体抬高，实时转写又把 onnx 线下限抬到 0.4.9），
> **本文只记验证过的版本**——某条结论若确实只对特定版本成立，会在该条里就地注明。
> ⚠️ 当前默认装的是**纯 CPU 版 onnxruntime**（DirectML 已被移出依赖，见第 6 节）。
> 第 1 节的 RTF / 准确率结论在纯 CPU 版上同样成立——那些本来就是 CPU 测量；
> 只有第 6 节的 DirectML 数据需要额外手动装 `onnxruntime-directml`。
>
> 📌 文中的「§N」编号指作者手头的**原始设计稿**章节（那份稿子不在本仓库里）。仓库内可查的
> 结论以本文为准；编号对不上时以本文的文字描述为准。

## 0. 已验证的依赖版本（2026-10）

两个包各自独立验证过，可作为「升上去没事」的依据：

| 包 | 组件 | 验证版本 | 验证方式 |
| --- | --- | --- | --- |
| CPU / onnx | `funasr-onnx` | **0.4.9**（本机 `.venv` 里这份的 METADATA 列了上游 #3827/#3831/#3838/#3842/#3846/#3847 等 wrapper 修复；`pyproject.toml` 下限 `>=0.4.9`） | 全量测试（本机收集 173 个，含实时 / CUDA 回退 / 打包闸门；CI 只装 `[dev]` 时真实推理用例 skip）+ SenseVoice / Paraformer / 热词版三条离线真实推理 + Paraformer-online 实时真实推理 |
| CUDA / full | `funasr` | **1.4.16** | RTX 3080 Ti 上 SenseVoice / Paraformer / cam++ 三条真实推理路径 |

2026-10-27 复核过周边包：onnxruntime **1.31.1**、onnx **1.23.2**、modelscope **1.40.2**。
两条线分别实跑过：onnx 线 numpy 1.26.4，full 线 numpy 2.4.6 + torch 2.5.1+cu121 上跑通 CUDA 推理。

**2026-11-10：RTX 50 的 CUDA 构建升级**

- 构建固定为正式稳定版 `torch==2.8.0+cu128` + `torchaudio==2.8.0+cu128`，
  保留已验证的 `funasr==1.4.16`；CUDA wheel 的版本与下载源统一放在
  `backend/requirements-cuda.txt`，不使用 nightly / preview / experimental 构建。
- 本机 FunASR 1.4.16 的 METADATA 没有直接约束 torch / torchaudio 版本；上游要求先单独安装
  [匹配驱动的 PyTorch 与 torchaudio](https://pypi.org/project/funasr/1.4.16/)。旧版 2.5.1+cu121
  不包含 sm_120 内核，CPU 回退只保证能用，不能补出新架构的 GPU 支持。
- 选择依据是 Windows 包的新旧显卡覆盖。本机分别安装官方 wheel 后，`torch.cuda.get_arch_list()` 为：

  | Windows wheel | 实际包含的 CUDA 架构 |
  | --- | --- |
  | 2.8.0+cu128 | sm_61、sm_70、sm_75、sm_80、sm_86、sm_90、sm_100、sm_120 |
  | 2.15.0+cu132 | sm_75、sm_80、sm_86、sm_90、sm_100、sm_120 |

  2.8.0 是正式稳定版，2.15.0 是当日最新稳定版；本项目选择前者，以保留 sm_61 / sm_70。
  这份结论限于实际检查的 Windows wheel，不外推到 Linux，也不承诺覆盖所有旧卡。
- [CUDA 12.8 Update 1 对应 Windows 驱动 572.61 或更新](https://docs.nvidia.com/cuda/archive/13.2.0/cuda-toolkit-release-notes/index.html#cuda-driver)，
  反馈里的 573.24 满足此版本要求；仍建议使用适配显卡的正式驱动。系统 CUDA Toolkit 无需单独安装。
- `packaging/verify-cuda.py` 在冻结前实际导入 torch / torchaudio，校验版本与 CUDA runtime，
  不要求构建机有 GPU；无卡 CI 只验证依赖与冻结，不能代替 GPU 推理测试。
- 本机 2.8.0+cu128 复测：onnx 环境 178 通过 / 5 跳过，full 环境 175 通过 / 8 跳过；
  RTX 3080 Ti 上 SenseVoice、Paraformer、Paraformer + CAM++、Paraformer 实时路径均跑通。
  前端构建、13 项对比度、10 项实时音频 / 小窗合同、ruff 与打包工作流 actionlint 检查通过。
- 本地完整 CUDA 打包通过，zip 约 3692 MiB（2 卷）；清空 PATH 后，冻结 exe 的 API、模型面板、
  页面、真实文件转写及 SRT 导出通过。full venv 和未压缩程序各约 7.7 GiB，CI 因而预检 32 GiB
  可用空间，必要时换到系统盘构建，上传前删除中间副本；远端 CI 尚未运行。
- 自动发布补验：CPU / CUDA 冻结包均在空模型目录中经 API 下载模型，完成样本转写与 SRT / VTT
  导出；CUDA 包隐藏 GPU 后用 CPU 推理。实际附件通过 ZIP 结构 / CRC、分卷与 SHA-256 检查。
  发布校验新增 24 项测试，包含部分上传失败后继续发布的合同；GitHub 发布调用由测试替身验证，
  未执行远端发布。触发方式与自动检查入口见 [仓库约定](../../../AGENTS.md#提交与发版)。
- RTX 50 / sm_120 的支持来自官方 wheel；本机只有 RTX 3080 Ti，RTX 50 的整条识别路径仍需对应硬件实测。

⚠️ 实时转写把 `funasr-onnx` 下限从 0.4.3 抬到 **0.4.9**（见第 9 节）：短包、空尾包、
流式缓存、空 punc chunk 都是上游 0.4.4–0.4.9 陆续修的，**降回 0.4.3 会崩**。本文 1./3./6. 节
的结论在 0.4.9 上复测仍成立（CTC 解码仍 numpy、ct-punc 仍按 20 token 分块）；0.4.3 是
升 0.4.9 之前的旧验证版本，下面带版本号的条目按「0.4.3 起、0.4.9 仍成立」理解。

**早期记录的两处 workaround：一处按当前 0.4.9 源码已不再必需（保留作保险），另一处仍是硬性必需**：

- `ContextualParaformer.__init__` 在 0.4.9 里**会**读 `config["lang"]` 设 `self.language`（本仓库 config 没有 `lang`，所以是 `None`）。
  `__call__` 只把 `"en-bpe"` 当英文、其余都走中文后处理，所以手动 `asr.language="zh"` 目前在功能上是 no-op；
  保留它是防 config 将来带上 `lang: en-bpe` 的保险（§6）。
- contextual 仓库仍然只有 `model_eb.onnx`，量化路径要的 `model_eb_quant.onnx` 仍需下载后本地复制（§6）——这条不能删。

升 `funasr-onnx` 时注意它声明 `numpy<=1.26.4`，**不要连带升 numpy 2**——这条已写成 `pyproject.toml`
里 `numpy>=1.26,<2` 的上界，pip 不会再往 onnx 线装 numpy 2。torch 线（`requirements-full.txt`）不受此限。
升级时顺手确认 `ContextualParaformer` 的 `model_eb_quant.onnx` 是否仍缺、`__init__` 是否仍按 `config["lang"]`
设 `self.language`（0.4.9 里：前者仍缺、后者已设）。复制逻辑别只因为版本号变了就删。


## 1. SenseVoiceSmall（默认 ASR）

- **ONNX 来源**：官方 `iic/SenseVoiceSmall-onnx`（94 万+下载），含 `model_quant.onnx`（≈230MB）。
  - ⚠️ 原始 `iic/SenseVoiceSmall` 仓库**只有 `model.pt`（893MB），没有 onnx**；
    funasr-onnx 遇到缺 onnx 会尝试 `from funasr import AutoModel` 在线导出（需 torch+funasr）——我们不走这条路。
  - ⚠️ onnx 仓库**缺 `chn_jpn_yue_eng_ko_spectok.bpe.model`**（分词器需要），
    需从 pt 仓库用 `allow_file_pattern=[bpe]` 单独补一份（见 `models/manager.py:_fetch_aux_files`）。
- **torch 依赖**：**当前 funasr-onnx（0.4.3 起，0.4.9 仍如此）已经不 import torch 了**——它的 CTC 贪心解码
  改成了 numpy（`np.diff`/`np.where`，见 `sensevoice_bin.py:191-198`；0.4.9 仍是这套），整个包可以在没有 torch 的
  环境里 import（我们 .venv 里就没装 torch，实测 `import funasr_onnx` 直接成功）。
  我们仍然**绕过 `__call__`**：它只返回文本，不给帧位，我们要的是**逐 token 时间**。
  自己用 numpy 解 CTC（argmax → 去连续重复 → 去 blank=0），并额外记下每个 token 的首现帧号
  （`_ctc_greedy_with_frames`）。
  ⚠️ `app/engine/funasr_engine.py:_ensure_funasr()` 里的 **torch-stub 仍然保留**：
  它防的是「用户 venv 里装了真 torch」时 modelscope/scipy 探测到 torch 后崩掉那条老路
  （见本文 §4），与 funasr-onnx 本身要不要 torch 无关。
- **时间戳（onnx 路径）**：funasr-onnx 的 `__call__` 只给文本不给时间戳，但 CTC logits 的
  **帧位置能推出 token 时间**。换算用**上游同一套公式**（`funasr/models/sense_voice/model.py:1067`）：
  `毫秒 = 帧号 * 60 - 30`（半帧居中；帧长 60ms 是上游写死的常数，实测本仓库这批权重也一样）。
  ⚠️ 不要把整段语音**线性拉伸**到 VAD 段长——那样静音空档的时长会被缩放，
  「按停顿断句」的毫秒阈值就不准了（实测同一段音频，空档从被缩放的 461ms 变回真实的 670ms）。
  逐 token 时间戳拿到后在标点处切自然语句（`subtitle.segment_timed`），
  这比「整段用 VAD 边界」精确：能裁掉前导静音、并把同一 VAD 段内的多句各自定位。
  Paraformer 路径无逐 token 时间 → 退回按字数比例分配（`segment_span`）。
- **时间戳（完整版 torch 路径）**：上游 `generate(output_timestamp=True)` 直接给逐字起止毫秒
  （SenseVoice 也能给，`funasr/models/sense_voice/model.py:1036-1075`，同样是 60ms/帧）。
  我们用它重建「带时间的 token」再切句（`funasr_full_engine._per_token_pieces`），
  时间不再按字数比例摊到整段音频上。
  ⚠️ 开了 `output_timestamp` 之后，上游对「没有 punc 模型」的句子切分会跳过（它要求 punc_model），
  所以 sentence_info 会变空——完整版 SenseVoice 走的是我们自己的逐字路径，不是 sentence_info。
- **停顿断句（issue #5）用上游 VAD 的静音阈值实现，不在字幕层自造规则**：
  说话停下来的地方本来就会被 VAD 切成新的语音段，我们只是把「静默多久算一段」调小。
  - onnx：`Fsmn_vad(..., max_end_sil=pause_ms)`（上游参数，默认取 config 的 800ms）；
    阈值不同就建不同实例并缓存（模型只有 2MB）。
  - 完整版：`generate(..., max_end_silence_time=pause_ms)`（上游 runtime kwarg，
    `fsmn_vad_streaming/model.py:925`），**传显式值会关掉上游的动态静音调度**。
  - 代价（已知取舍）：断成两段后每段单独转写、单独加标点，段末的逗号会变成句号。
  - 字幕层（`subtitle/segmentation.py`）的断句依据只有「标点」和「行宽」两种。
- **数字规范化（ITN）**：SenseVoice 的第 4 个前缀 token 是 ITN 标签（`withitn`=14 / `woitn`=15，
  见 `funasr_onnx/sensevoice_bin.py:textnorm_dict`），完整版对应 `generate(use_itn=...)`。
  实测（`tests/fixtures/sample_zh_numbers.wav`，TTS 朗读「我有三个苹果，二零二五年要买十五台电脑。」）：
  - `withitn`：「我有三个苹果，**2025**年要买**15**台电脑。」
  - `woitn`：「我有三个苹果，**二〇二五**年要买**十五**台电脑。」

  → 方向是**中文数字 → 阿拉伯数字**（不是反过来）；**带量词的「三个」两种模式都不转**。
  界面上的「数字转阿拉伯数字」开关就是它（`JobOptions.itn`，默认开 = 从前的行为）。
- **特殊标记**：输出文本前缀带 `<|lang|><|emo|><|event|><|itn|>`（如 `<|zh|><|NEUTRAL|><|Speech|><|withitn|>`），
  用正则 `<\|[^|>]*\|>` 剥离。
- **逐字时间戳（导出用）**：拿得到逐字时间的路径（onnx SenseVoice / 完整版）会把每个字的起止毫秒
  挂到字幕条上（`Segment.words`），JSON 导出里就有 `words` 字段 —— 卡拉OK / 逐词高亮可以直接用。
  onnx Paraformer 没有字级时间（ONNX 导出只有 `logits`+`token_num`），那条路的 `words` 为空。
- **标点**：`withitn` 模式输出**已自带标点**（逗号/句号/问号）。→ 默认 ASR **跳过 ct-punc**，
  否则会出现 `？？？` 这类重复标点。（`ModelSpec.builtin_punc=True`）
- **实测**（10.3s 中文样本，CPU，模型已加载）：ASR 推理本身 ~0.2s（RTF≈0.02）；
  整条管线（VAD + ASR + 切句）~1.0s（RTF≈0.10），文本与时间戳正确。
  （旧记录的 RTF≈0.41 为早期/冷态测量，已重测更正。）

## 2. VAD — fsmn-vad

- **ONNX 来源**：`iic/speech_fsmn_vad_zh-cn-16k-common-onnx`，**只含 `model_quant.onnx`** → 必须 `quantize=True`。
- **API**：`Fsmn_vad(dir, quantize=True)(wav_float32_16k)` → `[[[start_ms,end_ms], ...]]`
  （最外层按 batch；无语音时为 `[[]]`，我们兜底为整段一片）。
- **静音阈值（停顿断句用它）**：
  - onnx 版（funasr-onnx 的 e2e-VAD 副本）：只有**固定**阈值，构造参数 `max_end_sil`
    （默认取 config 的 `max_end_silence_time=800`）。
  - 完整版（torch `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch`）：默认走**动态静音调度**
    `DEFAULT_SILENCE_SCHEDULE`（段越长越耐静音：前 10s 容忍 2000ms、30s 容忍 800ms、60s 后 200ms，
    注释写的是「audio already complete, cut by natural pauses」）；**显式传
    `max_end_silence_time` 会关掉这个调度**，改成固定阈值
    （`fsmn_vad_streaming/model.py:925`、`1010-1013`）。
  - 实测（10.3s 样本，句中有 ~500ms 与 ~1040ms 两处停顿）：默认调度 → 1 段；
    `max_end_silence_time=500` → 2 段；`=300` → 3 段（逗号处也断开）。

## 2.1 完整版（torch 后端）怎么接上游

CUDA 选择共用 `app/engine/torch_device.py`（文件引擎、实时引擎、`compute_label()` 都调它）：驱动可见后，实际运行一次小张量运算并同步，避免
`is_available()` 为真、实际报 `no kernel image is available for execution on the device`。
该错误或 `invalid device function` 会在本次进程禁用 CUDA；文件模型加载或整段推理
遇到它时用新 CPU 模型重试，推理 cache 也重建。实时模型加载使用相同检查和加载回退。
强制 CPU 时跳过探测，算力标签跟随实际选择。其他错误（如显存不足）继续上报。
合同测试模拟驱动可见但内核不支持、异步错误、加载失败和推理失败；不代表已在报错用户的
显卡上实测。当前随包 torch 版本见第 0 节，不能由 CUDA 可见推断所有 NVIDIA 显卡都受支持。

完整版直接用上游 `funasr.AutoModel`（一条管线：VAD → ASR → 标点 → 切句 → 说话人），
我们只做三件事：

- **逐字时间戳**：`generate(output_timestamp=True)` → 上游给 `words`（与正文同序的词/字）
  和 `timestamp`（每个词的起止毫秒，60ms/帧）。SenseVoice 也能给（它没有 timestamp 头，
  但上游能按帧反推）。我们把它还原成「带时间的 token」再切句
  （`funasr_full_engine._per_token_pieces`），时间不再按字数比例摊到整段音频上。
- **句级时间戳**：`generate(sentence_timestamp=True)`。带时间戳头的模型（Paraformer）按标点切句
  并给每句真实起止；SenseVoice 没有 timestamp 头，开了这个开关上游会退化成
  「**VAD 段 = 一句**」（`auto_model._vad_segment_sentences`）——实测 funasr 1.4.16 不会报错。
  ⚠️ 但开了 `output_timestamp` 之后，上游对「没有 punc 模型」的句子切分会跳过（它要求
  punc_model），sentence_info 会变空——完整版 SenseVoice 因此走我们自己的逐字路径。
  带说话人时仍以 sentence_info 为准（speaker 只有它有）。
- **每行字数上限**：上游不管行长（那是字幕需求），所以我们再按 `max_chars` 拆一刀，
  有逐字时间时，文字和对应的词一起拆，每行起止取首末词的真实时间；只有句级时间时才按比例分配。
  **别把上游的句子直接当最终 cue**——
  这样「每行最多几个字」在完整版上才是摆设。
  SenseVoice 的末条字幕和末词都取上游的结束时间，不延伸到音频结尾；在测试音频后加 15 秒
  静音的回归样本用于检查这点。

## 3. 标点 — ct-punc

- **ONNX 来源**：`iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx`，**只含 `model_quant.onnx`** → `quantize=True`。
- **API**：`CT_Transformer(dir, quantize=True)("无标点文本")` → `("有标点文本", [punc_id...])`。实测中文标点正确。
- **长文本**：上游 0.4.3 起（0.4.9 仍如此）的 `__call__` **内部按 20 token 分块**逐块加标点（`split_size=20`），
  整段都会标上（实测 268 字的长句前 3/4 里有 15 个逗号）。我们**不用再自己分块**
  ——早先「ct-punc 只在结尾加标点」的问题已经没有了，自己按 110 字硬切反而会把句子切碎。
- 默认 SenseVoice 路径用不到（builtin_punc）；保留给 Paraformer 等不自带标点的 ASR。

## 4. torch-stub 与 modelscope/scipy 的共存陷阱（重要）

**现在的 funasr-onnx 自己已经不需要 torch**（见 §1），但我们仍然注入一个 **stub**——
它防的不是 funasr-onnx，而是**用户 venv 里装了真 torch** 时 modelscope/scipy 探测到 torch
后崩掉那条老路：
- stub 必须有 `__spec__`（否则 `importlib.util.find_spec('torch')` 抛 `ValueError: __spec__ is None`），
  还需 `__version__` 和一个 `Tensor` 占位类（scipy 的 array_api_compat 会 `getattr(torch,'Tensor')`）。
- **modelscope 与 stub 不能共存**：modelscope 的 logger 用 `find_spec('torch')`，一旦发现 torch 就会
  `import torch_utils`（调 `torch.distributed`）→ 崩。
  → 解决：**先在无 stub 时用 modelscope 完成下载/初始化，再装 stub 导入 funasr**。
- **scipy/librosa 在 import 期探测 torch**：必须**先 torch-free 导入 scipy.signal/librosa**，再装 stub。

安全导入顺序固化在 `funasr_engine._ensure_funasr()`：
`import modelscope.hub.snapshot_download` → `import scipy.signal, librosa` → 装 stub → `import funasr_onnx`。

## 5. 模型下载

两条后端**用的不是同一批仓库**：onnx 后端下 onnx 权重（几百 MB），完整版下 pt 权重
（0.9–2.1 GB）。仓库 id 的对应关系写在 `app/models/registry.py`（`repo` vs `full_repo`）。

- 用 `modelscope.hub.snapshot_download(repo, cache_dir=…, local_dir=<缓存根>\<repo>)`，可达性好，**torch-free**。
  缓存根默认 `<数据目录>\models`；用户设了 `MODELSCOPE_CACHE` 就跟随它（funasr 也读同一个变量）。
- **必须显式传 `local_dir`**：modelscope ≥1.38 默认落到
  `<cache>/models/<owner>--<name>/snapshots/<rev>/`，应用按平铺 `<cache>/<owner>/<name>` 找；
  不传就会「下载成功但找不到」。读取侧两种布局都认，已按新布局下过的模型不必重下。
- 完整版（torch）的 pt 模型也预置到同样的平铺目录：funasr 的 `snapshot_download`
  会把 `<cache>/<owner>/<name>` 当 legacy cache 直接复用，下载面板和引擎不会各存一份。
- 用 `ignore_file_pattern=["*.pt","*.pth",...]` 跳过 PyTorch 权重（SenseVoice pt 仓库 893MB）
  —— **仅 onnx 链路**，完整版必须下 pt。
- 默认链路（SenseVoice）总下载量 ≈ VAD(0.5MB) + SenseVoice-onnx(230MB) + bpe(0.4MB) ≈ **231MB**
  （registry 的 `size_mb` 取整估值 **235MB**，用于 UI 进度显示）。
  完整版 SenseVoice ≈ ASR(897MB) + VAD(4MB)；Paraformer-zh 还要加 ct-punc(1132MB) ≈ 2.1GB。

### 5.1 两条后端的「模型已就位」判据不同

onnx 下载按钮、就绪判断和文件引擎加载共用 `ModelManager.onnx_parts` 清单。已有 ASR 缓存
而缺 VAD/punc 时只补缺项；实时模型独立，不带文件转写的辅助模型。SenseVoice 的可选
ct-punc 兜底只使用已缓存权重，缺失时保留原文，不在转写中触发联网下载。

`/api/models` 的 `downloaded` 与下载面板的完成判定**随当前后端变**：

| 后端 | 判据 | 预置清单 |
| --- | --- | --- |
| onnx | 预置清单里每个仓库的主权重 `model_quant.onnx` + 辅助文件（bpe 词表等）+ 目录内改名产物都在 | ASR(onnx) + VAD(onnx) +（不自带标点时）ct-punc(onnx) |
| full | 预置清单里每个仓库都含 `model.pt` + `config.yaml` | ASR(pt) + VAD(pt) +（不自带标点时）ct-punc(pt) |

实时模型是上表的例外：`streaming=True` 时 `onnx_parts` / `_full_parts` 都**只返回本体**（不带 VAD / punc），
且 onnx 侧还要 `required_files` 里的 `decoder_quant.onnx` / `config.yaml` / `am.mvn` / `tokens.json`
全在才算就位——它走 online 前端，不挂离线 VAD/标点。

- **cam++ 不在预置清单里**：只有勾了「区分说话人」才用得上，量也不大，让 funasr 在加载
  引擎时自己按需下载。代价是那一刻没有进度可报。
- 完整版**不点「下载模型」也能转写**：引擎加载时 funasr 会自己下 pt 权重（走 ModelScope
  的新布局 `<cache>/models/<owner>--<name>/snapshots/<rev>/`），但没有进度，界面会长时间
  停在「读音频」。走下载面板预置的好处是**有进度**、且落成平铺布局后 funasr 会直接复用，
  两边不会各存一份。

## 6. 实测结论（Paraformer / DirectML / diarization）

- **Paraformer-zh（已实现）**：两个 ONNX 仓库都验证可用，引擎按 `ModelSpec.engine` 分派：
  - 普通版 `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx`（quant 238MB）→
    `funasr_onnx.Paraformer`。中文精度高于 SenseVoice。⚠️ 该路径**不输出时间戳**
    （走 `{"preds":...}` 分支），时间戳仍由 VAD 提供；`preds` 是 `(整句, [逐字])` 元组，取 [0]。
    无标点 → 走 ct-punc。
  - 热词版 `...-contextual...onnx`（quant 871MB + model_eb 25MB）→ `funasr_onnx.ContextualParaformer`，
    `__call__(wav, hotwords)` 支持热词。两个历史注意点：① quant 路径要 `model_eb_quant.onnx`，
    仓库只有 `model_eb.onnx` → 下载后在目录内复制一份（`ModelSpec.local_copies`）；
    ② `ContextualParaformer.__init__` 在 0.4.9 里已按 `config["lang"]` 设 `self.language`（本仓库 config 无 `lang` → `None`），
    `__call__` 只对 `"en-bpe"` 走英文后处理，`None` 与 `"zh"` 同走中文分支。加载后仍显式设 `asr.language="zh"`
    是防 config 将来带 `lang: en-bpe` 的保险，不是当前必需的补丁。
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
- **diarization（cam++）**：只有完整版（torch）支持；onnx 线没有可用的说话人分离类。完整版内部——
  - **完整版（full）**：`AutoModel(spk_model="cam++")`，实测可用（RTX 3080 Ti，见第 0 节）。
    只有 Paraformer 系模型能出带 `spk` 的 `sentence_info`，所以 SenseVoice + cam++ 没有意义，
    面板上的开关只对 Paraformer 系亮。cam++ 模型不在预置下载清单里，勾了才现下（见 5.1）。
  - **onnx 后端**：funasr-onnx **至今仍无任何说话人分离类** → 降级不支持，
    前端开关置灰，后端安全忽略。

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

## 8. 上游已知问题与本地缓解

### funasr 丢弃无标点句尾（modelscope/FunASR#3754，截至 1.4.16 未发版）

`funasr.utils.timestamp_tools.timestamp_sentence()` 只在遇到终止标点时才 flush 一句，
于是**末尾那段没有终止标点的文本会被整段丢弃**——说话人说一半被打断、录音到点结束都
很常见，表现为字幕尾部凭空少一截，而 `r0["text"]` 里其实还在。

上游测试用例很直白：

```python
timestamp_sentence([1, 3, 1], [[0,100],[100,200],[200,300]], "你 好 世界")
# 修复前: ["你好。"]          ← "世界" 整段丢失
# 修复后: ["你好。", "世界"]
```

**本地缓解**：`FunasrFullEngine._recover_dropped_tail()` 比对「sentence_info 拼接出的
文本」与「整段文本」的**实义字符**（必须忽略标点——ct-punc 会给 `sentence_info` 补句号，
直接比原字符串会把标点差异误判成「多了内容」），确认确实是**前缀关系**（丢的是尾巴而不是
中间）后，把剩下那截按「上一条结束 → 音频结束」的时间范围补回去，沿用上一条的说话人标签。

两个刻意的取舍：

- **时间戳不从 `timestamp` 对齐**。实测该数组按「非标点 token」给（本机 10.3s 样本：
  32 条时间戳 vs 36 个非空白字符），和字符数对不上，硬对齐更容易错。宁可时间范围略宽，
  也不能把字丢了。
- **对不上就原样返回**。中间缺段、非前缀关系、时间上没地方放——一律不猜，交给原有兜底路径。

回归见 `tests/test_full_tail_recovery.py`（含「不误伤」路径：覆盖完整时不得补、纯标点
差异不得补、中间缺段不得猜、时间不够不得硬塞）。

⚠️ **上游发版后应删掉这段缓解**（届时 `_recover_dropped_tail` 会恒等返回，可直接移除调用点）。

## 9. 实时转写

> 本节是 2026-11 新增的模式；结论来自合同测试 + 本地真实推理 + 浏览器实测，
> 各条对应代码见 `app/engine/live.py`、`app/api/live.py`、`app/jobs/queue.py`。

实时模式直接接上游的专用 Paraformer-online，不将离线 SenseVoice 按固定秒数反复识别：

- CPU 包：`funasr_onnx.paraformer_online_bin.Paraformer`，`chunk_size=[5,10,5]`，每次传 9600 个 16k 单声道采样（600ms），会话内复用 `param_dict.cache`，停止时显式 `is_final=True`。[上游示例](https://github.com/modelscope/FunASR/blob/main/runtime/python/onnxruntime/demo_paraformer_online.py)。
- 完整版：`AutoModel(model="paraformer-zh-streaming")`，`generate(input=..., cache=..., is_final=..., chunk_size=[0,10,5], encoder_chunk_look_back=4, decoder_chunk_look_back=1)`。[上游示例](https://github.com/modelscope/FunASR/blob/main/examples/industrial_data_pretraining/paraformer_streaming/demo.py)。
- ONNX 依赖下限升至 **0.4.9**（`pyproject.toml` 的 `[onnx]` extra）：使用上游已修复的短包、空尾包和流式缓存处理，不在本地复制上游推理代码。本机 `.venv` 里这份 0.4.9 的 METADATA 逐条列了 0.4.4–0.4.9 修的 #3827/#3831/#3838/#3842/#3846/#3847；**降回 0.4.3 会让实时路径崩**。
- ONNX 仓库为 `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online-onnx`。实查包含 `model_quant.onnx`（166,350,528 字节）、`decoder_quant.onnx`（71,867,274 字节），无需在 CPU 包中导出模型。就绪判定同时检查 decoder、config、词表、CMVN。
- full 别名对应 `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online`，本体及词表约 849 MiB；这条接口不挂离线 VAD、标点或 cam++。

浏览器 `public/pcm-capture.js`（AudioWorklet）将麦克风／共享音频混成 16k mono int16 PCM，每 9600 采样（600ms）发一包，经 `/ws/live` 发送；立体声取各声道均值、超范围样本夹到 ±1。每包等上包确认后再发（`chunk` 回包才继续），积压 50 包（约 30 秒）时停止收音并补完；停止时先发不足 600ms 的尾包，再请求上游 final flush。麦克风不接回扬声器（worklet 输出静音），共享画面不发送给后端。共享是否包含音轨由浏览器和来源决定，没有音轨时前端直接报错让用户重选。

流式任务和文件任务共用唯一 worker：`/ws/live` 每个 PCM 包作为一个 `LiveChunk` 入同一队列串行推理，实时会话期间拒绝新文件任务（`409`），有文件任务在跑时也拒绝开实时。开始实时会清空常驻文件引擎缓存以控峰值内存；每次收音独享在线模型前端和 cache，退出后释放。页面断开时 ASGI 协程可能立刻被取消，所以收尾也投一个 `final=True` 的 `LiveChunk`，由 worker 完成 final flush 再释放会话，已识别文字仍保留为可导出的 Job。行宽仍走 `segment_span`；当前行按增量结果替换（`replace_from`），超过 `MAX_CUE_MS`（7s）或 final 才固定旧行，推理 cache 始终连续。在线模型不返回逐字时间：行级时间按收音块估算，`words=None`，不用于精确对轴；Job 会把 `diarization / itn / punctuation / pause_ms / use_gpu` 强制成「未接入」的值，只输出中文文本。

实时字幕阅读视图独立在 `frontend/src/LiveCaptions.tsx`，主页面和小窗通过 React portal 共用同一份字幕状态，不新建录音或推理会话。点击「悬浮显示」直接调用浏览器 Document Picture-in-Picture 置顶，缺失或被禁用时回退普通窗口，弹窗不可用时保留页面内小窗；关闭小窗只移除阅读视图，停止收音仍调用主页面的同一控制器。原生画中画的持续同步、关闭后继续转写、小窗内停止，以及普通窗口回退和离开实时模式时的回收均已在浏览器验证。

本地验证：`sample_zh_numbers.wav` 的 ONNX 与 full 路径均在 final 前持续输出文字，最终含「苹果」「电脑」；浏览器使用该样本作为虚拟麦克风，验证持续出字、停止补尾、导出。合同测试覆盖两个声音来源、独占队列、断线回收、错误保留、缓存与 final 参数；CI 不下载模型，真实推理在本地模型和对应推理栈均已就绪时才执行（`tests/test_live*.py`）。
