# Speech Hotkey Tool v1.0

## 下载

下载 `SpeechHotkeyToolPortable.zip`，解压后双击 `SpeechHotkeyTool.exe` 即可运行。

## 主要功能

- Windows 托盘 App，启动后不显示主窗口。
- 双击 `Shift` 开始离线听写，再双击 `Shift` 停止。
- 说话停顿后自动分句识别，并粘贴到当前光标位置。
- 使用本地 `sherpa-onnx` + FunASR Nano int8 模型，默认 CPU 运行。
- 右键托盘图标可打开日志，便于反馈问题。

## 随包内容

```text
SpeechHotkeyTool.exe
_internal\
models\
README.md
```

## 已知说明

- 首次启动或首次识别需要加载本地模型，会比后续识别更慢。
- 当前默认 CPU 4 线程，不默认使用 GPU。
- 如果出现模型加载错误，请完整解压 zip，并确认 `models` 目录没有被截断或损坏。
- 日志会按大小轮转，单个日志约 1 MB，最多保留 3 个旧日志。
