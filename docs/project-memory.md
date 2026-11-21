# Cubase MCP 项目记录

本项目从 [Cubase/Piapro MCP 0.5.6](https://github.com/S4kuzy0/cubase-piapro-mcp/commit/811b57f17b1d673c501854ffed5a541abd5735b7) 拆出。Cubase MCP 负责宿主控制、标准 MIDI、CPR 读取与工程副本生成、MIDI Loop 和 VST3 扫描目录。Piapro 导入、曲谱和歌声编辑由原仓库的独立 Piapro 服务维护。

继续使用原 MIDI Remote 驱动、虚拟端口名称和状态目录配置。保留原有 Cubase 14 文件结构和有界窗口回退的版本限制。Cubase LE 16 在手动重载脚本后实测播放、停止均有回传，冷启动自动连接仍需进一步复验。

本次拆分以现有代码为底座。[外部项目对比](comparison.md)用于确定后续功能优先级。
