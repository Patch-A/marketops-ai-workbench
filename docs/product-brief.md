# Product Brief

> 本文件保留为简要入口。正式范围以 [PRODUCT_SPEC.md](PRODUCT_SPEC.md)、[ARCHITECTURE.md](ARCHITECTURE.md) 和 [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md) 为准。

## 定位

MarketOps AI Workbench 是一个独立开源、可私有部署的 AI 营销项目运营工作台。它面向独立负责活动、品牌和 B2B 营销项目的市场人员，把已确认方案转成可执行排期，持续管理任务、风险和外部变化，并在项目结束后把经用户确认的经验沉淀为下一项目可引用的知识。

## 核心边界

- 产品本体是 Web 工作台，不依赖飞书、OpenClaw 或特定模型。
- Agent 是内部执行能力，Skill 是可复用方法，连接器是可选集成。
- 第一版单人优先、团队就绪，Docker 私有部署并支持 BYOK。
- 飞书是首个计划连接器，企微随后，个人微信非官方自动化暂不支持。
- 项目资料默认只属于当前项目，跨项目知识必须经过用户确认。

## P0 闭环

```text
确认方案 -> 提取交付物 -> WBS/倒排计划 -> 执行更新
-> 市场变化影响 -> 周报 -> 复盘 -> 候选知识 -> 下个项目引用
```

## 非目标

- 通用聊天或陪伴 Agent。
- 全功能 SEO、媒体分发、CRM 或舆情平台。
- 无来源的市场结论、ROI 预测或自动外发。
- 未经确认的跨客户学习和模型微调。
