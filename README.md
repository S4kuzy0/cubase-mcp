# Cubase MCP

在 Windows 上通过 MCP 控制 Cubase，并离线检查、创建和校验 Cubase 工程素材。Piapro 乐谱与歌声编辑由 [Piapro MCP](https://github.com/S4kuzy0/cubase-piapro-mcp) 提供。

## 安装与连接

在仓库根目录用 Python 3.12 或更新版本创建环境。安装服务需要本机已有 Cubase。创建文件和读取标准 MIDI 时，Cubase 可以关闭。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
codex mcp add cubase -- (Resolve-Path .\.venv\Scripts\python.exe).Path -I -B -X utf8 -m cubase_mcp.server
```

已有相同 MIDI Remote 驱动及端口的安装可直接沿用。首次配置时，在 loopMIDI 创建 `CP MCP To Cubase` 和 `CP MCP From Cubase`，运行下列脚本，然后在 Cubase 中执行 **Reload Scripts**。两个端口专用于控制，项目乐器轨应使用自己的 MIDI 输入。

```powershell
.\.venv\Scripts\python.exe scripts\install_midi_driver.py
```

新开 Codex 聊天后先调用 `daw_get_status`。有多个 Cubase 版本或需指定 VST3 缓存时，可在注册项设置 `CUBASE_PIAPRO_CUBASE_EXE` 和 `CUBASE_PIAPRO_CUBASE_PREFS`。已有工作流可继续用 `CUBASE_PIAPRO_STATE_DIR` 共享模板登记、备份和跨进程锁。

## 工具范围

| 工作流 | 入口 |
| --- | --- |
| 走带与当前通道 | `daw_get_status`、`cubase_control`，校验播放、停止或当前通道状态回传 |
| 实况轨道 | `cubase_list_tracks`、`cubase_select_track`、`cubase_add_track`，写入使用最新轨道清单版本 |
| 标准 MIDI | `create_midi`、`edit_notes`、`transpose_notes`、`arrange_midi_notes`、`set_midi_track_patch`、`read_midi_controller_points`、`edit_midi_controller_points` |
| 工程与素材 | `cubase_import_midi_native`、`cubase_save_project_as`、`cubase_export_project_wav`、`inspect_project`、模板与 MIDI Loop 工具 |
| 混音 | `cubase_mix_channels` 批量读写音量、声像、静音、独奏 |
| 宿主参数 | `cubase_inspect_channel` 和 `cubase_set_channel_parameter`，用于 DirectAccess 暴露的 EQ、发送及插件参数 |
| 宿主插件槽与电平 | `cubase_list_slot_plugins`、`cubase_load_slot_plugin` 和 `cubase_read_channel_meter`，以实际通道能力及回传为准 |
| 插件扫描记录 | `cubase_list_vst3_instruments`，读取本机 Cubase 缓存中的乐器类 |

原生导入与另存已在英文 Cubase LE 16 工程副本实测。先检查 MIDI 的 SHA-256，再以新的 CPR 路径调用原生导入。工具回读保存后的起点、长度、音高、力度及通道。轨道清单上限为 64 个受支持的通道。CPR 回读只支持已识别的 RIF2 布局。

WAV 工具以项目起点和全选事件的右定位点为范围。当前导出设置须显示 Stereo Out、WAV、工程目录下的 Mixdown 及 Always Ask，并显式允许有界对话框回退。工具在对话框填写独立文件名，核对 WAV 文件头和帧数，再交付到指定路径。提供预期秒数时会额外核对时长。

DirectAccess 工具先读取通道对象和插件槽，写入时重新检查轨道、对象身份及旧值。精确支持范围由宿主版本和插件决定。电平返回最近一次宿主回传及采样时间。MIDI CC 点编辑按绝对 tick 操作标准 MIDI 文件中的控制器轨道。Cubase 工程中 VST 参数自动化轨的时间点没有公开的 MIDI Remote 编辑接口。

冷启动后如果收到 `NO_CUBASE_FEEDBACK`，这一安装曾通过 **Reload Scripts** 恢复通信。播放与停止均已复验，冷启动自动恢复仍待验证。

详见 [同类项目对比](docs/comparison.md)、[MIDI Loop 工作流](docs/cubase-midi-loop-workflow.md)和[项目记录](docs/project-memory.md)。
