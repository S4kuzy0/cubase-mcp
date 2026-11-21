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
| 标准 MIDI | `create_midi`、`edit_notes`、`transpose_notes`、`arrange_midi_notes`、`set_midi_track_patch` |
| 工程与素材 | `inspect_project`、`cubase_create_project`、`cubase_create_audio_project`、`cubase_verify_midi_import` 与 MIDI Loop 工具 |
| 插件扫描记录 | `cubase_list_vst3_instruments`，读取本机 Cubase 缓存中的乐器类 |

Cubase LE 16 已实测播放与停止回传。轨道清单上限为 64 个受支持的通道。旧 CPR 解析及 MIDI 导入校验适用于支持的 Cubase 14 RIF2 结构，MIDI 轨创建和路由的有界界面回退适用于已验证的 Cubase 14 英文布局。模板工具在新路径写副本。

冷启动后如果收到 `NO_CUBASE_FEEDBACK`，这一安装曾通过 **Reload Scripts** 恢复通信。播放与停止均已复验，冷启动自动恢复仍待验证。

详见 [同类项目对比](docs/comparison.md)、[MIDI Loop 工作流](docs/cubase-midi-loop-workflow.md)和[项目记录](docs/project-memory.md)。
