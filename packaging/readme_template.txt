funasr-subtitle - 本地字幕生成工具 (便携版)

使用:
  1. 双击 funasr-subtitle.exe。
  2. 浏览器自动打开 http://127.0.0.1:<端口>。
  3. 首次使用需联网下载模型 (默认 SenseVoice 约 235MB, 存到 %LOCALAPPDATA%\funasr-subtitle); 之后离线可用。
  4. 拖入音频/视频 -> 选模型/语言 -> (可填热词、调每行最大字数) -> 开始转写 -> 导出 SRT/VTT/TXT/JSON。
  5. 导出的 .srt 可直接导入 subtitle-translator 翻译。
  几乎任意含音轨的音视频都能转 (mp4/mkv/mov/ts/mp3/m4a/wav/flac/opus...)。

模型:
  - SenseVoiceSmall: 多语言(中/粤/英/日/韩)、快、默认; 句级时间戳。
  - Paraformer-zh: 中文高精度。
  - Paraformer-zh 热词版: 中文高精度 + 支持热词 (首次下载约 900MB)。

说话人分离(导出 [spk0]/[spk1]...):
  - 只有「完整版」程序 + Paraformer 系模型能用; 轻量版(onnx)与 SenseVoice 做不到, 开关会置灰。

字幕切分:
  - 按自然语句(句末标点)切句; "每行最多几个字"默认 30(中文~30字/英文~60字), 0=不限制; 超长句在标点/词边界再断。
  - "标点"默认"自动": 中文加句末标点, 英文按停顿切、不强加(英文标点常不准); 可改"加标点"/"不加标点"。

停止:
  - 页面右上角"停止服务", 或右下角托盘图标 -> 退出。

首次运行 SmartScreen 提示"Windows 已保护你的电脑": 点"更多信息" -> "仍要运行"。(程序未签名)
排障: 启动异常记录在 %LOCALAPPDATA%\funasr-subtitle\startup.log。

许可: 本程序 MIT; FunASR 模型受各自 Model License; 捆绑 ffmpeg.exe(gyan.dev) 受其 License。
