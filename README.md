# Speech Hotkey Tool

> 双击 `Shift` 开始离线听写，停顿后自动识别并粘贴；再双击 `Shift` 停止。

**Speech Hotkey Tool** 是一个 Windows 本地语音输入小工具。它打开后不会显示主窗口，只会常驻在系统托盘中。语音识别完全在本机运行，不上传录音。

## 核心特性

- **托盘运行**：双击 `SpeechHotkeyTool.exe` 后，程序只会出现在 Windows 右下角系统托盘。
- **双击 Shift 听写**：双击 `Shift` 开始录音；每次检测到说话停顿，就自动识别当前句子并粘贴到光标所在位置；再双击 `Shift` 停止录音。
- **本地离线识别**：使用 `sherpa-onnx` 加载本地 FunASR 模型，不依赖云端接口。
- **自带模型**：`models` 文件夹内已带 `sherpa-onnx-funasr-nano-int8-2025-12-30`，默认 CPU 运行，比较适合不同配置的 Windows 机器。后续会继续加入更多模型。
- **自动粘贴**：识别完成后通过剪贴板和 `Ctrl+V` 自动输入到当前应用。
- **日志反馈**：如果遇到问题，右键托盘图标，点击 `Open Log`，把日志内容反馈给开发者。

## 快速开始

下载并解压发布包后，目录应类似这样：

```text
SpeechHotkeyTool.exe
_internal\
models\
```

直接双击：

```text
SpeechHotkeyTool.exe
```

启动后不会弹出主窗口，只会进入系统托盘。

## 使用方式
（第一次进入软件时会需要先加载模型，可以将鼠标放在托盘图标上查询状态）
1. 打开任意可以输入文字的地方，例如记事本、浏览器、聊天窗口、PowerPoint。
2. 把光标放到要输入的位置。
3. 双击 `Shift`，听到提示音后开始说话。
4. 每说完一句稍微停顿一下，程序会自动识别并粘贴。
5. 再双击 `Shift`，停止听写。

## 托盘菜单
鼠标放在托盘图标上可以查询状态：
右键系统托盘里的图标，可以看到：

- `Start / Stop Dictation`：开始或停止听写。
- `Open Log`：打开日志文件，用于排查问题。
- `Exit`：退出程序。

## 模型说明

当前默认模型：

```text
models\sherpa-onnx-funasr-nano-int8-2025-12-30\
```

这是一个 sherpa-onnx FunASR Nano int8 模型包，默认使用 CPU，线程数为 4。它不是最高精度模型，但体积、速度和兼容性比较均衡，适合拿来作为默认随包模型。

后续可以继续加入更多模型，例如更高精度模型、更快模型或不同语言模型。

## 常见问题

**Q: 双击 exe 后为什么没有窗口？**  
A: 这是正常的。程序默认是托盘 App，只会出现在 Windows 右下角系统托盘。

**Q: 双击 Shift 没反应怎么办？**  
A: 先确认托盘里有程序图标。如果目标程序是管理员权限运行的，可能需要用管理员权限运行本工具。某些安全软件、游戏或受保护窗口也可能拦截全局键盘监听。

**Q: 为什么没有自动粘贴？**  
A: 请确认光标已经放在可输入文字的位置。部分管理员权限窗口、游戏、远程桌面或受保护软件可能会拦截模拟粘贴。

**Q: 识别为空怎么办？**  
A: 请检查麦克风权限、默认输入设备、说话音量和环境噪声。也可以右键托盘图标打开日志，把日志内容反馈回来。

**Q: 日志会不会一直变大？**  
A: 不会。日志文件会自动轮转，单个日志最大约 1 MB，最多保留 3 个旧日志。

**Q: 为什么我电脑上识别速度这么慢？**  
A: 速度会随电脑 CPU、内存、磁盘和杀毒软件扫描而变化；当前默认是 CPU、4 线程，不会默认用 GPU。首次启动/首次识别会慢一点，因为要加载 1GB 左右的模型，之后会快很多。

## 调试命令

如果你不是双击运行，而是在 PowerShell 里调试，可以用：

```powershell
.\run.ps1 --check-model --no-tray --no-preload
```

测试模型自带音频：

```powershell
.\run.ps1 --transcribe-file models\sherpa-onnx-funasr-nano-int8-2025-12-30\test_wavs\dia_hunan.wav --no-tray --no-preload
```

## 致谢

本项目基于以下开源项目：

- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
- [FunASR](https://github.com/modelscope/FunASR)
