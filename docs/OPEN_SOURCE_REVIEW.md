# 开源组件与 Skill 审查

快照日期：2026-08-08。Star 数来自当日 GitHub API，只反映关注度。许可证字段为初筛，不构成法律意见；正式引入时必须读取仓库中的完整许可证、NOTICE 和依赖许可证。

## 1. 产品运行候选组件

| 项目 | Star 快照 | 许可证快照 | 可能用途 | 当前决策 |
|---|---:|---|---|---|
| [Docling](https://github.com/docling-project/docling) | 64,411 | MIT | PDF、Word 等文档解析 | 优先技术验证 |
| [AnythingLLM](https://github.com/Mintplex-Labs/anything-llm) | 64,483 | MIT | 本地知识库和 Workspace 参考 | 借鉴，不整体 Fork |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 39,185 | MIT | 可恢复 Agent 工作流 | P0 后按复杂度评估 |
| [changedetection.io](https://github.com/dgtlmoon/changedetection.io) | 32,986 | Apache-2.0 | 网页变化监测 | 评估 API 集成或实现参考 |
| [Frappe Gantt](https://github.com/frappe/gantt) | 6,078 | MIT | 甘特图 UI | 优先评估集成 |
| [Plane](https://github.com/makeplane/plane) | 55,711 | AGPL-3.0 | 项目、任务和文档结构 | 仅作产品参考 |
| [RSSHub](https://github.com/DIYgod/RSSHub) | 45,648 | AGPL-3.0 | RSS 数据源 | 可选外部服务，需隔离许可证影响 |
| [Dify](https://github.com/langgenius/dify) | 151,758 | GitHub API `NOASSERTION` | Agent、RAG 和工作流参考 | 完整许可证审查前不嵌入 |
| [Flowise](https://github.com/FlowiseAI/Flowise) | 55,247 | GitHub API `NOASSERTION` | 可视化 Agent 流程 | 完整许可证审查前不嵌入 |
| [n8n](https://github.com/n8n-io/n8n) | 199,785 | Fair-code/需核对 | 外部自动化 | 不作为 Apache 核心依赖 |
| [pgvector](https://github.com/pgvector/pgvector) | 22,535 | API 未识别 | Postgres 向量检索 | 核对许可证后技术验证 |
| [MinIO](https://github.com/minio/minio) | 61,387 | AGPL-3.0 | S3 对象存储 | 上游仓库已归档，不默认采用 |

## 2. 选择原则

- 优先使用维护活跃、接口稳定、许可证明确的窄组件。
- 不 Fork 大型 RAG、Agent 或项目管理平台作为产品底座。
- 能通过标准协议替换的能力使用适配器，例如模型 API、S3、RSS 和连接器。
- 自进化、项目知识范围和影响关系属于本产品业务层，不外包给通用 RAG 框架。
- 引入 AGPL、SSPL、BSL、Fair-code 或自定义许可证组件前必须评估分发和 SaaS 义务。

## 3. 项目开发 Skills

### 已保留在公开仓库

- `workbench-requirements`：产品范围、状态、数据边界和验收。
- `workbench-ui`：把已确认流程转成可用工作台界面。

### 本地使用但不随仓库分发

- `finesse-ui`：来自 `mouse-lin/finesse-skill` 的本地适配版。上游标注 MIT，但本地副本包含大量示例和第三方前端库；完成逐项 NOTICE 审查前通过 `.gitignore` 排除。

### 待技术栈确定后评估

| Skill | 公开信号 | 用途 | 判断 |
|---|---|---|---|
| `vercel-labs/agent-skills@vercel-react-best-practices` | skills.sh 约 615.8K 安装 | React/Next.js 性能与架构 | 前端启动时评估 |
| `firecrawl/firecrawl-workflows@firecrawl-knowledge-base` | 约 30.3K 安装 | 网页采集和知识摄取 | 只用于采集参考，不替代项目知识设计 |
| 飞书官方 `lark-doc`、`lark-approval`、`lark-apps` | skills.sh 官方来源 | 飞书连接器开发 | M4 再启用 |
| Playwright 测试类 Skill | 多个来源，约 1K-3K 安装 | E2E 和视觉验收 | 核对维护者后选择一个 |

RAG 搜索结果中多数 Skill 安装量低于 500，且来源分散。项目知识与学习机制应创建项目专属 Skill，并由真实测试样本驱动更新。

## 4. 视频带来的开发流程约束

三个视频提供的是方法线索，不是市场证据：

1. 需求分析、UI Skill 调度和代码理解应分开，避免直接从一句话生成整站。
2. 工作台 UI 的高级感不能替代数据模型、失败状态和真实业务闭环。
3. AI 已进入日常工作和企业报销讨论，但街采样本不足以证明付费规模。

因此开发流程固定为：需求与验收 -> 数据与架构 -> 纵向闭环 -> 自动化与连接器 -> 视觉完善。
