# Cubase MCP 项目记录

本项目从 [Cubase/Piapro MCP 0.5.6](https://github.com/S4kuzy0/cubase-piapro-mcp/commit/811b57f17b1d673c501854ffed5a541abd5735b7) 拆出。Cubase MCP 负责宿主控制、标准 MIDI、CPR 读取与工程副本生成、MIDI Loop 和 VST3 扫描目录。Piapro 导入、曲谱和歌声编辑由原仓库的独立 Piapro 服务维护。

继续使用原 MIDI Remote 驱动、虚拟端口名称和状态目录配置。保留原有 Cubase 14 文件结构和有界窗口回退的版本限制。Cubase LE 16 在手动重载脚本后实测播放、停止均有回传，冷启动自动连接仍需进一步复验。

本次拆分以现有代码为底座。[外部项目对比](comparison.md)用于确定后续功能优先级。

0.6.0 独立 wheel 通过 40 项回归。在 Windows 上通过新服务的 stdio 入口连接 LE 16，读到 24 个工具、测试工程的 Piapro 轨和 5 个 VST3 乐器条目，实际播放与停止均收到回传，测试工程文件哈希未变。

0.7.0 在 LE 16 原生导入并另存工程副本，从新 CPR 回读验证两个音符的起点、长度、音高、力度和通道。另一个实测用例将临时声像由 0.5 调到约 0.65 后复原。原生 WAV 对话框的自动命名、导出和头部核验已实测生成双声道 44.1 kHz、32 位浮点 WAV。

DirectAccess 插件装载、参数写入、EQ、发送和电平接入经过驱动契约及服务测试，现场能力仍需 MIDI Remote 恢复后逐项验证。MIDI CC 点支持标准 MIDI 文件读写。Cubase 工程内的 VST 自动化轨时间点尚无公开 MIDI Remote 编辑接口。此次冷启动后，LE 16 能列出虚拟端口和驱动，但设备实例为零。重载脚本与切换 MIDI 后端均未恢复回传。
