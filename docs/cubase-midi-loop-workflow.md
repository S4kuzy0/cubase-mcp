# 在 Cubase 14 重建可编辑的采样分轨

从对齐的 WAV 分轨出发。WAV 可以作为参考音轨保留，MIDI 与采样器放在独立的乐器轨。始终在工程副本中导入、试听并保存。

## 先决定每个 MIDI 键的含义

| 目标 | MIDI 记录什么 | 剪辑和改音高时的行为 |
| --- | --- | --- |
| 保留分轨里的具体声音 | 每个 MIDI 键对应一个 WAV 切片，SFZ 按键选择切片 | 音符可以移动、删减和重排。纵向移动音符会选择另一段采样。按切片顺序分配键时，乐段会显示成阶梯。切片里的和弦与效果作为录音内容保留。 |
| 继续写旋律、贝斯或和弦 | 音符按音乐音高排布，SFZ 用根音和键区映射采样 | 在 Key Editor 改音高可以重写旋律或和弦。采样换调后的音色与效果取决于素材和映射。 |

要继续改编时保留一份按音高编排的 MIDI。如果已有工程使用切片触发，可在第 1 小节对齐导入这份 MIDI 作为静音参考。用采样片段重建工程时，直接向 CPR 写入新乐器记录曾出现宿主打开后丢失插件轨。可复用的做法是用 Cubase 自己导出的 MIDI Loop 携带乐器与乐段。

## 制作和导入 MIDI Loop

1. 将各轨的 SFZ 与所需 WAV 放在固定位置，检查 SFZ 内引用的相对路径。准备乐器轨同名的标准 MIDI，例如 Lead、Bass、Pad、Kick、Clap 和 Hats。
2. 在 Cubase LE 14 中新建一个 **sfizz-multi** 乐器轨，载入一个 SFZ。在工程第 1 小节放一个含音符的 MIDI Part。在与目标工程相同的速度、拍号下，选中乐段并用 **File → Export → MIDI Loop** 导出一次，作为原生模板。[Steinberg 导出说明](https://www.steinberg.help/r/cubase-le/14.0/en/cubase_nuendo/topics/vst_instruments/vst_instruments_midi_loops_exporting_t.html?contentId=GSGInTm452AqshYBQuNA1A)
3. 调用 `cubase_inspect_midi_loop` 取得模板 SHA-256，用 `inspect_project` 读取标准 MIDI 的 SHA-256 和轨名。每轨调用一次 `cubase_create_midi_loop`，传入 `template_path`、`expected_template_sha256`、`midi_path`、`expected_midi_sha256`、`track_name`、`sfz_path` 和新的 `output_path`。工具写入音符、轨名和 SFZ 的绝对路径，保留模板的插件实例设置、速度与拍号。标准 MIDI 轨长须落在四分音符边界，音符时间须能用 Cubase 的 480 PPQ 整数 tick 表示。只导入音符事件。
4. 打开并激活目标工程副本，把各个 `.midiloop` 从文件夹或 MediaBay 拖到**轨道区空白处**，对齐同一个第 1 小节。投放到现有轨道时，Cubase 仅将 MIDI 乐段添加到那条轨。采样库的绝对路径改变后，需重新绑定 SFZ 路径。[Steinberg 导入说明](https://www.steinberg.help/r/cubase-le/14.0/en/cubase_nuendo/topics/vst_instruments/vst_instruments_midi_loops_importing_t.html?contentId=hQ_Qd0KT7z_3Y3g5uVPYZQ)

## 逐轨试听和核验

1. 点目标轨轨道头上的 **S** 独奏，播放该段。在 Windows 上按住 **Ctrl** 点击 **S**，可同时清除其他轨的独奏。用 MCP 时，先调用 `cubase_list_tracks`，按实际 ID 和 revision 调用 `cubase_select_track`，再用 `cubase_control` 控制独奏和播放。试听结束记得停止并恢复原来的独奏和静音。[Steinberg 独奏与静音说明](https://www.steinberg.help/r/cubase-le/14.0/en/cubase_nuendo/topics/mixconsole/mixconsole_using_solo_and_mute_c.html)
2. 双击各轨的 MIDI Part 可进入 **Key Editor**。用 Cubase 把工程副本保存成 `.cpr`。随后调用 `cubase_verify_midi_import`，把保存的工程与用于生成 Loop 的 MIDI 按轨比对。默认检查第 1 小节，核验位置、时值、键号、力度和通道。改过轨名时传入 `track_map`。
3. `cubase_list_tracks` 可以确认宿主中的轨道及乐器。生成工具的 `live_state_verified: false` 表示这次调用仅写文件，采样路径是否有效以及发声仍需在宿主试听。

目前 MCP 覆盖制作和检查 MIDI Loop、保存后的 CPR 核验、选轨和独奏。导入 Loop、打开 Key Editor、保存工程以及更换实时插件仍由 Cubase 完成。可用 MIDI 文件工具先改音符，再生成新的 Loop。
