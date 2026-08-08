# MarketOps AI Workbench

一个面向活动、品牌与 B2B 营销项目的开源 AI 项目运营工作台。

> 当前状态：M1 纵向切片进行中。根目录页面已提供浏览器端的项目/原文件/已批准方案导入交互，用于验证数据契约和失败路径；它不代表 PostgreSQL 事实来源、后端 API、知识库、连接器或自动化已经完成。

## 产品目标

MarketOps 将营销项目的 Brief、证据、方案、排期、执行、外部变化、复盘和可复用知识放进同一套项目状态中。它不是通用聊天 Agent，也不依赖飞书、OpenClaw 或特定模型平台。

核心任务：

> 帮助独立负责项目的市场人员，在方案确认后把交付物转成可执行、可监控的计划，并在进度或外部依据变化时定位受影响的任务，最终把实际结果沉淀为下个项目可验证复用的知识。

## 产品边界

- 产品本体：独立 Web 工作台。
- Agent：排期、研究、风险、汇报和复盘等内置执行能力。
- Skill：Agent 使用的业务方法和开发工作流。
- 连接器：飞书、企微、邮箱、日历、RSS 和第三方监测服务。
- 模型：用户通过 BYOK 选择兼容的模型服务。

## 首个完整闭环

```text
已确认方案
-> 交付物识别
-> WBS 与倒排计划
-> 执行状态与风险
-> 市场变化影响分析
-> 周报
-> 复盘
-> 候选知识
-> 用户确认写入 Playbook
```

长期产品将把入口向前扩展到 Brief、市场研究和方案评审，但不会用无来源的模型判断冒充市场事实或 ROI。

## 方案文档

- [产品规格](docs/PRODUCT_SPEC.md)
- [技术架构](docs/ARCHITECTURE.md)
- [数据模型](docs/DATA_MODEL.md)
- [知识库与自进化](docs/KNOWLEDGE_AND_LEARNING.md)
- [路线图](docs/ROADMAP.md)
- [项目执行方案](docs/PROJECT_EXECUTION_PLAN.md)
- [项目进度与完成纪要](docs/PROJECT_STATUS.md)
- [M0 技术退出评审](docs/M0_REVIEW_GATE.md)
- [M1-01 项目导入切片](docs/M1_01_PROJECT_IMPORT_SPIKE.md)
- [M0 验证样本集](validation/README.md)
- [公开活动案例参考库](docs/PUBLIC_CASE_REFERENCES.md)
- [验收标准](docs/ACCEPTANCE_CRITERIA.md)
- [开源组件与 Skill 审查](docs/OPEN_SOURCE_REVIEW.md)
- [市场验证执行包](docs/market-validation-playbook.md)
- [安全政策](SECURITY.md)

## 本地查看探索原型

当前原型是浏览器本地的交互切片，可在本地服务中打开 `index.html`。它用于验证导入交互、文件哈希和失败状态，不是可用于真实客户资料的服务器系统。M1-01 的 PostgreSQL、API 和权限验收仍未完成。

## 开源与数据

本项目采用 [Apache License 2.0](LICENSE)。第三方组件仍遵循各自许可证，候选组件在正式引入前必须完成许可证和维护状态审查。

真实客户资料、研究 PDF、API 密钥、模型 Token、上传文件和本地数据库不得提交到公开仓库。

## 贡献

项目仍在规格阶段。提交功能代码前请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和对应里程碑的验收标准。
