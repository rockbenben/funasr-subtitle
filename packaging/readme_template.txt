funasr-subtitle - 本地字幕生成工具 (便携版)

使用:
  1. 双击 funasr-subtitle.exe。
  2. 浏览器自动打开 http://127.0.0.1:<端口>。
  3. 第一次使用需联网下载模型 (默认 SenseVoice; CPU 版约 235MB, CUDA 版约 0.9GB);
     等提示"模型已就绪", 之后完全离线可用。
  4. 拖入音频/视频 -> 选模型/语言 -> (可填热词、调每行最大字数、设停顿阈值、选标点模式)
     -> 开始转写 -> 导出 SRT/VTT/TXT/JSON。
  5. 导出的 .srt 可直接导入 subtitle-translator 翻译。
  几乎任意含音轨的音视频都能转 (mp4/mkv/mov/ts/mp3/m4a/wav/flac/opus...)。

两个版本 (界面与操作完全一样, 只差推理后端):
  - CPU 版 (funasr-subtitle-win-x64.zip): funasr-onnx 纯 CPU, 无需显卡, 最快上手。
  - CUDA 版 (funasr-subtitle-cuda-win-x64.zip): 完整 funasr + torch, 有 N 卡自动用 GPU、
    无卡自动回退 CPU; 额外支持说话人分离 (cam++)。
  两版用的模型不是同一批 (CPU 版下 onnx 权重, CUDA 版下 pt 权重), 所以"已下载"状态各算各的。
  同一台机器别同时开两个版本: 双击第二个只会打开已在跑的那个页面。

模型:
  - SenseVoiceSmall: 多语言(中/粤/英/日/韩)、快、默认。
  - Paraformer-zh: 中文高精度。
  - Paraformer-zh 热词版: 中文高精度 + 支持热词 (CPU 版首次下载约 900MB)。
  模型默认存到 %LOCALAPPDATA%\funasr-subtitle\models
  (设了环境变量 MODELSCOPE_CACHE 就改存到那里, funasr 也读同一个变量)。

说话人分离(导出 [spk0]/[spk1]...):
  - 只有「CUDA 版」+ Paraformer 系模型能用; CPU 版与 SenseVoice 做不到, 开关会置灰。
  - cam++ 模型不在首次下载清单里, 勾上这个开关后第一次转写时会现下, 多等一会儿是正常的。

字幕切分:
  - 按自然语句(句末标点)切句; "每行最多几个字"默认 30(中文~30字/英文~60字), 0=不限制;
    超长句在标点/词边界再断。
  - "标点"默认"自动": 中文加句末标点, 英文按停顿切、不强加(英文标点常不准);
    可改"加标点"/"不加标点"。
  - 可选"停顿超过几毫秒就断": 说话停顿超过该长度时在这里断句(默认 0=不启用)。
    例: 一句话 12 个字, 前 8 个字说完一停, 每行上限 10 字 -> 按字数会切成 10+2,
    按停顿切成 8+4。
    用的是上游语音检测(VAD)的静音阈值, 所有模型都一样; 注意断成两段后每段单独转写、
    单独加标点, 段末的逗号可能变成句号。
  - "数字转阿拉伯数字"默认开: 说话里的"二零二五年"写成"2025年"、"十五台"写成"15台",
    带量词的("三个苹果")不动; 关掉就保留中文数字原样。

停止:
  - 页面右上角"停止服务", 或右下角托盘图标 -> 退出。退出会清掉当前字幕, 先导出再退。

首次运行 SmartScreen 提示"Windows 已保护你的电脑": 点"更多信息" -> "仍要运行"。(程序未签名)
排障: 启动异常记录在 %LOCALAPPDATA%\funasr-subtitle\startup.log。

许可: 本程序 MIT; FunASR 模型受各自 Model License; 捆绑 ffmpeg.exe(gyan.dev) 受其 License。
