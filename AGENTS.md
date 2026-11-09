# AGENTS.md

改这个仓库前先读这份。它是工作约定的**正本**——同一条规矩写两遍，迟早只改一遍，所以别往别的文件（README / 代码注释）复制内容，只留链接。

- 面向用户：[README.md](README.md)
- 技术正本（各模块的实测结论）：[backend/app/engine/README.md](backend/app/engine/README.md)

> `.qoder-credits/` 里那些 `*.canvas.tsx` 是 Qoder IDE 生成的**会话 token 报告**，不是本项目的文档或设计稿（该目录已 gitignore）。别把它当参考资料读。

## 这是什么

本地离线字幕生成：拖进音视频 → FunASR 转写 → 导出 SRT / VTT / TXT / JSON；另有麦克风／共享音频的实时转写。Windows 便携包，浏览器当界面。

**两条推理后端，两套模型**——这是理解本仓库一切的前提：

| | CPU 包（onnx） | CUDA 包（full） |
| --- | --- | --- |
| 推理栈 | `funasr-onnx`（纯 CPU，torch-free） | 完整 `funasr` + torch（CUDA 可选） |
| 模型权重 | onnx，几百 MB | pt，0.9–2.1 GB |
| 说话人分离 | 不支持 | 支持（cam++，仅 Paraformer 系） |
| 文件转写入口 | `app/engine/funasr_engine.py` | `app/engine/funasr_full_engine.py` |
| 实时转写入口 | `app/engine/live.py` → `funasr_onnx.paraformer_online_bin` | `app/engine/live.py` → 上游 `paraformer-zh-streaming` |

后端由构建期烘焙的 `app/_build.py` 决定（不提交）；开发态用环境变量 `FUNASR_SUBTITLE_BACKEND=onnx|full` 覆盖，默认 onnx。

## Health Stack

- **测试**：`cd backend; .\.venv\Scripts\python -m pytest`（CI 跑 `python -m pytest -q --tb=short`，Python 3.11，Ubuntu + Windows 双平台）
  CI 只装 `[dev]`，**不装 `[onnx]`**：需要真实推理的用例在 CI 里是 skip 的（本地有模型缓存时会真跑）。所以「本地绿」不等于「CI 绿」，CI 只是合同检查。
  解码层测试需要 PATH 上有 ffmpeg / ffprobe（CI 用 choco / apt 装）；`tests/test_packaging_media.py` 只在 Windows 跑。
- **lint**：`cd backend; .\.venv\Scripts\python -m ruff check .`（配置在 `backend/pyproject.toml`，line-length 110）
- **前端**：`cd frontend; npm run build`（`tsc -b && vite build`，类型错直接失败）+ `npm run check:contrast`（WCAG AA 对比度守护，纯 node 脚本读 studio.css）+ `npm run test:live`（`node --test` 跑 AudioWorklet 分块／小窗合同，CI 也跑；脚本直接 import `.ts`，要 Node ≥22.6，CI 用 24）
- **起后端**：`cd backend; ..\backend\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8765`；前端 `cd frontend; npm run dev`
  完整版另建 `backend\.venv-full`（见 `backend/requirements-full.txt` 头部），加 `FUNASR_SUBTITLE_BACKEND=full`。
- **打包**：`powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-Variant full]`
- **提交**：Conventional Commits，message **整体用英文**（标题与 body 都是）。

## 不可破坏的约定

**torch-stub 的导入顺序**（`funasr_engine._ensure_funasr()` 固化了它，删任何一步都会崩）

`modelscope` → `scipy/librosa` → 装 torch-stub → `funasr_onnx`

- modelscope 的 logger 用 `find_spec('torch')`，发现 torch 就会 import 真 torch_utils → 崩。
- scipy / librosa 在 import 期探测 torch。
- ⚠️ funasr-onnx 0.4.x **自己已经不 import torch** 了（CTC 解码改成 numpy），stub 现在只为
  「用户 venv 里装了真 torch」那条老路兜底；别因为「上游不用 torch 了」就顺手删掉它。
- stub 必须有 `__spec__`（否则 `find_spec` 抛 ValueError）、`__version__`、一个 `Tensor` 占位类。
- `app/models/manager.py` 是**下载层，必须保持 torch-free**——它只 import modelscope。
- 实时转写也走 `_ensure_funasr()` 再 `import funasr_onnx.paraformer_online_bin`，仍受上面这条顺序约束；
  `funasr-subtitle.spec` 的 hiddenimports 必须带上该模块，否则冻结包里只下了模型却加载不到。

**模型缓存布局**

- 下载**必须显式传 `local_dir=<根>/<owner>/<name>`**：modelscope ≥1.38 默认落到 `<根>/models/<owner>--<name>/snapshots/<rev>/`，不传就是「下载成功但找不到」。读取侧两种布局都认（老用户不必重下）。
- 完整版的 pt 模型也预置到**平铺**目录，funasr 的 legacy cache 探测会复用，下载面板与引擎不会各存一份。
- 缓存根 = `config.model_cache_dir()`：默认 `%LOCALAPPDATA%\funasr-subtitle\models`，设了 `MODELSCOPE_CACHE` 就跟随它（funasr 读同一个变量）。**测试要显式清掉它**（`tests/test_api_smoke.py` 已这么做），别让 CI 碰到用户真实缓存。
- `full_repo` 必须是**真实仓库 id**，不能写 funasr 别名（如 `paraformer-zh`）——别名只在 `AutoModel(model=...)` 里认。`tests/test_model_registry.py` 会拿 `name_maps_ms` 对这条做校验。
- onnx 的下载按钮、`/api/models` 的就位判定、文件引擎加载共用 `ModelManager.onnx_parts` 清单（ASR + VAD + 不自带标点时的 ct-punc）；三条路别各写一份。实时模型 `streaming=True`，清单里**只有它自己**，不带文件转写的辅助模型，且 `required_files`（decoder / config / 词表 / CMVN）缺一不算就位。完整版同理走 `_full_parts`。
- SenseVoice 的 ct-punc 兜底只认**已缓存**权重：缺了保留原文，不能在转写中途偷偷联网下载（`_punctuate`）。

**依赖边界**

- onnx 线的 numpy 必须 `<2`（`funasr-onnx` 声明 `numpy<=1.26.4`）；torch 线不限。**两条线别装进同一个 venv**。
- CUDA 构建只用正式稳定版，版本与源以 `backend/requirements-cuda.txt` 为准；兼容性与实测记录见 [技术正本 §0](backend/app/engine/README.md#0-已验证的依赖版本2026-10)。
- `funasr-onnx` 下限是 **0.4.9**：实时 Paraformer 依赖上游修过的短包、空尾包和流式缓存，降回 0.4.3 会让 `/ws/live` 崩。本机 `.venv` 里这份 0.4.9 的 METADATA 逐条列了 #3827/#3831/#3838/#3842/#3846/#3847 等 wrapper 修复，是带上游 hotfix 的版本，别按「官方 0.4.3」去理解。
- `onnxruntime-directml` **故意不在依赖里**：它和纯 CPU 版 `onnxruntime` 同名互相覆盖，且实测比 CPU 慢 ~2.8×。前端 `useGpu = false` 也是因此——别「顺手加回来」。
- Python 锁 `>=3.11,<3.12`（numpy 1.x 只出到 3.12）。

**接口与状态机**

- FastAPI 的 `File()` / `Form()` 必须写在参数默认值里（依赖注入靠读签名），挪进函数体表单就失效。
- 引擎缓存键是 `(model_id, use_gpu, diarization)`，LRU 上限 `ENGINE_CACHE_SIZE`；**淘汰安全的前提是任务由单个 worker 线程串行执行**。加并发 worker 会让「淘汰掉正在用的引擎」变成真 bug。
- **文件任务和实时任务共用同一个 worker 线程**：`/ws/live` 的每个 PCM 包作为 `LiveChunk` 入同一队列串行推理；实时会话期间拒绝新文件任务（`409`），有文件任务在跑时拒绝开实时。每次收音独享在线模型前端与 cache，开始实时会清空文件引擎缓存以控峰值内存。
- **实时会话必须由 worker 收尾**：断线 / 停止都往队列投一个 `final=True` 的 `LiveChunk`，由 worker 完成上游 final flush 后再 `finish_live`；不能在 ASGI 协程里直接释放会话（协程可能立刻被取消），否则已识别文字会丢、`live_active` 卡住。
- **模型按来源互斥**：`streaming=True` 的模型只能走 `/ws/live`，文件任务选它返回 `live_only`；实时选了离线模型直接报错。实时 Job 会把 `diarization / itn / punctuation / pause_ms / use_gpu` 强制成「未接入」的值，只输出中文文本，别把它写成这些选项已生效。
- 实时在线模型不返回逐字时间：`words=None`、时间按收音块估算，只用于看字、不用于精确对轴；当前行按增量结果替换，超过 `MAX_CUE_MS` 或 final 才固定成旧行。
- 单条字幕 ≤ `MAX_CUE_MS`（7s）只在有逐 token 时间时才真正卡得住；比例分配时间的路径没有这个能力。
- 切句参数 `max_chars` / `pause_ms` 走 `JobOptions`。`pause_ms` 默认 **0（关闭）**，改成非 0 会
  改变所有人的断行结果，别顺手改默认值；它的实现是**上游 VAD 的静音阈值**（静默超过这个长度就
  断成新语音段），不是字幕层自造的规则——各后端怎么接见
  [backend/app/engine/README.md](backend/app/engine/README.md)。
- `itn`（数字转阿拉伯数字，上游 `use_itn`）默认 **开**；关掉会把「2025年」变回「二〇二五年」，
  同样是会改变所有人输出的默认值，别随手翻。
- 完整版（torch）走上游 `AutoModel`：**「每行最多几个字」由我们自己补上**（上游不管行长）；
  别把上游的句子直接当最终 cue。时间戳优先用上游的逐字时间戳
  （`output_timestamp=True`，SenseVoice 也有），其次才是 `sentence_info`（句级、带说话人）。
- 导出文件名走 RFC 5987：中文名只进 `filename*`，另给 ASCII 回退。
- `Segment.words`（逐字时间戳）：拿得到逐字时间的路径才填（onnx SenseVoice / 完整版），
  拿不到就留 `None`（onnx Paraformer）——**别填猜的**，卡拉OK 靠它。
- 引擎的 `use_gpu` 语义**随后端变**：onnx 是 DirectML（前端开关恒 false）；full **完全不看这个值**，由 `app/engine/torch_device.py:torch_device()` 决定——`torch.cuda.is_available()` 只证明驱动可见，还要真跑一次小张量并 `synchronize()`，否则「有卡却报 no kernel image is available」会在推理时才炸。
  探测到 `no kernel image` / `invalid device function` 就在本次进程禁用 CUDA，加载失败或整段 `generate` 失败时用**新 CPU 模型 + 空 cache** 重试；实时加载同理回退；其他错误（如显存不足）照常上报。`FUNASR_SUBTITLE_FORCE_CPU=1` 跳过探测，算力标签跟随实际选择。

**打包契约**

- `build.ps1` **不装推理栈依赖**，只冻结你指给它的 venv；onnx 变体会在冻结前检查 `funasr_onnx` 能否导入，缺了直接抛错。它会检查 `npm ci` / `npm run build` / PyInstaller 的退出码，失败即抛错。
- `ffmpeg.exe` / `ffprobe.exe` 找不到不再只是警告，而是直接抛错；捆绑完还会调 `packaging/verify-media.ps1` 用**包内绝对路径**的这两个工具实测 ffprobe + 解码 `sample_zh.wav`，不过就中止打包。`tests/test_packaging_media.py` 另在清空 PATH 的环境里复测这道闸门，改了它记得同步测试。
- `app/_build.py` 是构建期烘焙的，**不提交**（已在 .gitignore）；build.ps1 结束后会删掉它（PyInstaller 的 `finally` 和脚本末尾各删一次）。
- CUDA 包超 GitHub 单文件 2 GB 上限，build.ps1 调用 `packaging/split-archive.ps1` 自动切分卷并按实际卷数生成 `…-merge.bat`；改动后跑 `tests/test_packaging_volumes.py`。
- `packaging/readme_template.txt` **原样进包**，两个变体共用同一份——写内容时要同时覆盖 onnx 与 full 两种情况。
- 改了 `docs/social-card.html` 就要重渲染 `docs/social-preview.png`（两者是源文件与交付物的关系，只改 HTML 会让预览图说旧话）。

**行尾与文风**

- 仓库 `* text=auto eol=lf`，且 `core.autocrlf false`。别改回 `autocrlf=true`——历史上因此把 `.py` 压成过单行。
- 中文全角标点是本项目既定文风，ruff 的 RUF001/002/003 已关，别去「修」。

## 改动范围

- 面向用户的文案用普通用户的话；原始异常进 `detail`，界面折叠成「查看详情」，别把 traceback 弹到主界面。
- 别擅自新增设置项 / 开关 / 步骤——这工具的定位是「解压即用」。
  （目前界面上的设置是：声音来源、语言、模型、热词、每行字数、停顿阈值、标点、区分说话人、数字转阿拉伯数字；文件转写时全显示，实时模式只留模型 / 每行字数——都是用户明确要的。）
- 判断「值不值得改」的依据是实测，不是推测；注释写**为什么**（含踩过的坑），不是复述代码在做什么。
- 代码注释里的「§N」指作者手头的原始设计稿章节（**那份稿子不在仓库里**）；编号对不上时以 `backend/app/engine/README.md` 的文字为准。

## 提交与发版

- 按功能域合并提交，一条提交说清一件事；message 写实质改动，不写过程词、不加署名。
- **Agent 不自动 push、不打 tag、不手动发 Release**——这些等人明确点头。
- 已授权的 CI 发布流程：`.github/workflows/package.yml` 在版本标签推送后自动测试、构建并发布，
  无人工审批；也允许手动填写已有 `release_tag` 重试。未填标签的手动运行只构建产物。
  `packaging/release.py` 校验版本、完整包与分卷、校验和、上传后的摘要，两个变体全部通过才公开发布；
  改动后跑 `tests/test_release.py` 和 `actionlint .github/workflows/package.yml`。
