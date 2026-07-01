# 处理视频录屏与分析报告

生成时间：2026-07-01  
范围：VID001 测试视频、Cholec80 video12 测试视频  
说明：以下内容基于本地 Electron App 录屏、后端窗口摘要、关键事件节点和抽帧复核整理，不作为临床结论。

## 录屏文件

| 视频 | 会话 ID | 录屏文件 | 录屏参数 |
| --- | --- | --- | --- |
| VID001 | `048cae91` | `/home/user/proj/video_processor/video_stream_app/recordings/final_vid001_capture_20260701_080116.mp4` | 3770x2042, 30fps, 850s |
| Cholec80 video12 | `efc2fe6b` | `/home/user/proj/video_processor/video_stream_app/recordings/final_cholec80_video12_fixed_20260701_093052.mp4` | 3770x2042, 30fps, 1280s |

抽帧复核文件保存在：

- `/home/user/proj/video_processor/video_stream_app/tmp/recording_review/vid001_record_120s.png`
- `/home/user/proj/video_processor/video_stream_app/tmp/recording_review/vid001_record_600s.png`
- `/home/user/proj/video_processor/video_stream_app/tmp/recording_review/vid001_record_835s.png`
- `/home/user/proj/video_processor/video_stream_app/tmp/recording_review/cholec_record_300s.png`
- `/home/user/proj/video_processor/video_stream_app/tmp/recording_review/cholec_record_600s.png`
- `/home/user/proj/video_processor/video_stream_app/tmp/recording_review/cholec_record_1000s.png`
- `/home/user/proj/video_processor/video_stream_app/tmp/recording_review/cholec_record_1250s.png`

## 录屏质量复核

两个最终录屏均能正常播放，录制帧率为 30fps。VID001 末段可以看到“镜头移出体外”关键节点和右侧分析更新；Cholec80 中段与末段可以看到右侧最新窗口随视频推进，且不再误触发 VID001 专属的“镜头移出体外”硬编码规则。

当前仍建议后续继续优化一项 UI 同步细节：视频结束或后端补齐摘要后，底部状态栏的当前窗口号可能比右侧“最新窗口”前进一到数个窗口。录屏文件本身可用，但这个状态同步需要单独收敛。

## VID001 分析摘要

源视频：`/home/user/proj/video_processor/test_data/2024-12-24_225315_VID001.mp4`  
后端窗口摘要：123 个窗口，覆盖 0s-615s。

主要过程：

- 0s-70s：准备阶段和初始暴露，画面以术野建立、牵拉和暴露为主。
- 75s-260s：进入肝胆三角解剖，电凝钩持续分离肝胆三角组织，系统反复进入 CVS 安全视野判断阶段。
- 270s-290s：出现胆囊管相关处理，窗口摘要识别到白色 Hem-o-lok 夹闭胆囊管，剪刀在胆囊管邻近区域操作。
- 405s-465s：识别到 Hem-o-lok 和金属钛夹相关夹闭痕迹，摘要中多次出现已夹闭的胆囊管或胆囊动脉残端。
- 485s-595s：持续识别镜头起雾，关键事件节点以红色视野事件展示；这段对手术视野遮挡有明确提示。
- 595s-615s：镜头移出体外，画面切换到套管口或腹壁外场景，已在右侧分析和底部关键事件节点中展示。

需要注意：

- 事件节点接口本次报告生成时走了 fallback，因为 OpenAI-compatible 事件节点调用 30 秒超时；但窗口摘要本身和录屏 UI 均已覆盖关键节点。
- 部分窗口仍会出现较模板化的 CVS 表述，后续可以进一步合并为更短的过程描述。

## Cholec80 Video12 分析摘要

源视频：`/home/user/proj/video_processor/stream_simulator/media/cholec80_video12.mp4`  
后端窗口摘要：216 个窗口，覆盖 0s-1090s。

主要过程：

- 0s-150s：准备阶段和初始暴露，抓钳牵拉胆囊颈和胆囊体，逐步建立操作区域。
- 155s-360s：进入肝胆三角解剖，摘要中以电凝钩分离肝胆三角组织、剪刀分离胆囊板为主，系统进入 CVS 判断阶段。
- 560s-620s：进入夹闭切断相关窗口，摘要识别到已夹闭的胆囊动脉残端，并提示剪刀切断胆囊动脉。
- 620s-900s：进入胆囊分离，主要为电凝钩分离胆囊床组织和维持术野暴露。
- 915s-920s：进入胆囊取出与装袋阶段。
- 920s-930s：短暂出现套管口/体外过渡画面，摘要已修正为镜头移出体外。
- 930s-960s：标本袋相关操作，摘要已归一为“将胆囊装入标本袋并准备取出”。
- 960s-1070s：进入清洁凝血阶段，系统归纳为清理术野、凝血和确认出血控制；后段可见起雾提示和出血已控制节点。

需要注意：

- Cholec80 的最终摘要数量比录屏中可见窗口数量多，原因是后端分析队列在录屏末尾继续补齐了后续窗口。录屏中右侧分析随视频推进正常，报告采用后端补齐后的完整摘要做时间线。
- 关键事件节点同样走 fallback，仍可用于 UI 事件展示，但医学语义建议以后继续让 LLM 事件合并稳定返回，减少“视野起雾”等状态节点过长覆盖。

## 本轮工程结论

- 两条视频均已完成 Electron App 全屏录屏。
- VID001 的起雾、移出体外、Hem-o-lok/钛夹相关识别已在录屏中可见。
- Cholec80 的肝胆三角解剖、CVS 状态、夹闭切断、胆囊分离、清洁凝血等流程已能连续展示。
- 旧录屏已清理，仅保留两条最终录屏和对应日志。

## 后续建议

- 修复视频结束后底部窗口号和右侧最新窗口号的同步问题。
- 让事件节点 LLM 调用支持更短超时降级或流式返回，避免报告生成时走 fallback。
- 对“胆囊管/胆囊动脉”二选一结果继续做后处理校验，减少模板化或不确定表述。
