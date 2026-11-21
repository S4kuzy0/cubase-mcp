# Cubase MCP 同类项目对比

北京时间 2026-11-21 核对 GitHub 仓库、源代码和作者验收说明。外部项目的实机结论来自各项目公开记录，本机只验收自己的服务。星数为当日快照，提交取核心源码目录的最近提交。

| 项目 | 星数、源码提交与许可 | 部署与可借鉴能力 |
| --- | --- | --- |
| [Cubase Complete MCP](https://github.com/muhwagwa0112/cubase-complete-mcp) | 2 星，2026-10-16 `895b582`，MIT，[v1.0.0](https://github.com/muhwagwa0112/cubase-complete-mcp/releases/tag/v1.0.0) | Windows、.NET 8、loopMIDI 与 Cubase 15。作者在 15.0.30 验证了工程打开和保存、原生 MIDI 导入并回读、插件类 UID 核验、定位器范围 WAV 导出。导出依赖预设格式和 OCR 校验。[能力矩阵](https://github.com/muhwagwa0112/cubase-complete-mcp/blob/main/docs/capability-matrix.md) |
| [Asakitan/CubaseMCP](https://github.com/Asakitan/CubaseMCP) | 1 星，2026-11-14 `14b04a3`，MIT | Windows、Node.js 与项目自己的宿主管理方式。仓库报告支持新建空白 CPR、自动化点、音频路由、插件插槽和无导出窗口的 WAV／AIFF／FLAC 混缩。直接采用需要移植宿主生命周期和兼容性处理。[源码及验收入口](https://github.com/Asakitan/CubaseMCP/tree/main/src) |
| [Yoka Cubase MCP](https://github.com/yokadeeds-dev/yoka-cubase-mcp) | 8 星，核心运行代码 2026-06-15 `199fbc2`，测试入口 2026-10-16 有更新，AGPL-3.0，[v0.2.0](https://github.com/yokadeeds-dev/yoka-cubase-mcp/releases/tag/v0.2.0) | Python、loopMIDI，部分命令需要 AutoHotkey。MCU 可提供银行混音、电平镜像、实时 MIDI 录入，另有 VST 参数映射、音频分析和母带建议。Windows 已验证，macOS 是占位实现。适合参考交互方式，复用代码需评估 AGPL 条件。[实时回传验证脚本](https://github.com/yokadeeds-dev/yoka-cubase-mcp/blob/main/tests/selftests/closedloop_smoketest.py) |
| [PIXELHIZE/CubaseMCP](https://github.com/PIXELHIZE/CubaseMCP) | 3 星，2026-07-29 `78067cf`，未找到可核实的仓库许可证 | Windows、TypeScript、loopMIDI。适合看 DirectAccess、命令可执行性与按能力报告失败的设计。原生项目、完整 MIDI 编辑和批量混缩等多项依赖额外桥接。工具清单和 mock 测试不能替代实际宿主验收。[工具能力审计](https://github.com/PIXELHIZE/CubaseMCP/blob/main/docs/full-capability-audit.md) |
| [ieee0824/cubase_mcp](https://github.com/ieee0824/cubase_mcp) | 2 星，2026-09-19 `eb3bf31`，MIT | Rust 与 MIDI Remote SysEx。作者在 macOS 的 Cubase 13.0.30 验证走带回传，可使用内部 MIDI 环回。完整轨道、标记、VST 和音频分析仍处在规划或探测阶段。[MCP 工具源码](https://github.com/ieee0824/cubase_mcp/blob/main/src/tools.rs) |

这些项目是本地运行的服务，依赖各自匹配的 Cubase 版本和 MIDI 桥。完整复用可能同时涉及不同的 Cubase 授权版本、运行时和端口配置。此次拆分使用原代码，没有引入上述项目作为依赖。

## 我们已经有的优势

| 能力 | 已实现的工作流 |
| --- | --- |
| 原生工程素材处理 | 离线读取部分 CPR，利用本机模板创建独立工程，生成配套 Audio 目录并校验替换 WAV。与必须打开宿主的工程操作互补。 |
| MIDI 内容验证 | 离线生成、编辑、移调和设置音色，校验已保存 CPR 中的开始位置、音高、时值、力度及通道。能从支持的 sfizz 模板生成带乐器状态的原生 MIDI Loop。 |
| 当前 LE 实机证据 | Windows 上的 LE 16 播放、停止均完成闭环。通过轨道 UID 和版本锁定目标，文件替换有 SHA-256 和可恢复备份。 |
| Piapro 配套能力 | 独立的 Piapro MCP 提供原生歌手表、VSQX 曲线、PPSF 表情、EVEC、快照与回读验证。对照仓库未找到同等范围的 Piapro 原生数据编辑。 |

## 还缺什么

1. **优先补标准 MIDI 原生导入并回读，其次补项目另存为和整曲 WAV 导出。** 现有工具能生成 MIDI、复制本地模板和核对导入结果，文件进入 Cubase 以及原生保存、整曲出音频仍需要额外操作。Complete MCP 是最相关的工作流参考。需验证 LE 16 的宿主命令、定位器范围和输出文件，预计中到高成本。
2. **增加插件装入及目标参数控制。** 现有 VST3 扫描缓存只能回答发现了什么插件。需要宿主报告插件插槽、目标类 UID 和参数写入后的真实值。可对照 Complete MCP、Asakitan 及 Steinberg 的 MIDI Remote API，成本较高。
3. **扩展走带和混音。** 当前以播放、停止、录音及所选通道的音量、声像、静音和独奏为主。其他仓库还展示了速度、拍号、定位器、循环、节拍器、银行混音、电平、发送、EQ 和自动化。可按宿主实际能力逐项加，基础走带成本较低，通用路由和自动化成本较高。
4. **持续复验冷启动和 LE 16 专属适配。** 当前手动重载后通过了播放与停止，旧版对话框和 CPR 文件适配仍有版本范围。优先在 LE 16 验证原生路径和返回状态，保留现有文件工作流。
