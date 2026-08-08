# Reference Landscape

检查日期：2026-08-08。Star 数是 GitHub API 当时返回的快照，不代表产品适配性、活跃用户数或代码质量。

## 可借鉴的产品结构

| 项目 | GitHub 快照 | 适合借鉴 | 不直接照搬的原因 |
|---|---:|---|---|
| [Open WebUI](https://github.com/open-webui/open-webui) | 148,151 stars；许可证 API 返回 `NOASSERTION` | 模型、知识、工具、语音和会话状态的统一工作区 | 许可证需要单独核实；产品范围明显大于个人营销工作台 |
| [LobeHub](https://github.com/lobehub/lobehub) | 81,390 stars；`NOASSERTION` | Agent 组织、个人记忆、模型切换和工作区导航 | 不把 Agent 数量当作产品价值；许可证状态先核实 |
| [AnythingLLM](https://github.com/Mintplex-Labs/anything-llm) | 64,462 stars；MIT | 本地知识库、workspace 隔离、文档问答 | RAG 不是营销策略闭环，仍需证据到交付物的业务状态 |
| [Cherry Studio](https://github.com/CherryHQ/cherry-studio) | 50,010 stars；AGPL-3.0 | 桌面多模型、文件和个人生产力工作流 | AGPL 对商业二改有合规影响，不作为默认 fork |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) | 38,788 stars；AGPL-3.0 | 多渠道、人格、语音和消息平台接入 | 面向 IM/机器人场景，和营销项目交付不是同一核心流程 |

## Skill 取舍

- 本地安装并适配了 `finesse-ui`。上游仓库 [mouse-lin/finesse-skill](https://github.com/mouse-lin/finesse-skill) 在检查时为 403 stars、MIT。它适合做视觉注册、AI 控制台状态、移动端和 anti-slop 审计；不负责产品范围和营销逻辑。因本地副本包含大量示例和第三方库，完成逐项 NOTICE 审查前不随公开仓库分发。
- 未安装：[mouse-lin/finesse-brief](https://github.com/mouse-lin/finesse-brief)。仓库在检查时为 8 stars、MIT，且核心提示约 7 万字符，与本项目的 `workbench-requirements` 重叠明显。保留为可选参考，不把上下文成本带进默认工作流。
- 项目自建：[workbench-requirements](../.agents/skills/workbench-requirements/SKILL.md) 负责范围、证据、状态和验收；[workbench-ui](../.agents/skills/workbench-ui/SKILL.md) 负责把已确认流程转成界面。两者是本项目的主 Skill。

## 不能从 Star 数推出的结论

Star 数不能证明某个项目适合个人营销工作、适合商业二改、支持粤语或中文语音，也不能证明其数据隐私模型符合客户资料要求。采用前必须分别核对许可证、部署方式、模型成本、数据流和目标用户的真实任务。
