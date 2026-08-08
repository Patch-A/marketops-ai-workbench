# M0-05 依赖与许可证决策

状态：`Decision proposed for independent review`

快照日期：2026-08-08

## 1. 决策范围

本文只决定 MarketOps AI Workbench 从 M1 开始所需的文档解析、词法与向量检索、后台任务编排和项目排期依赖边界。它不引入任何运行时依赖，也不证明候选组件在真实客户文件、中文语义检索或生产负载下可用。

本次判断依据为当前仓库的 M0-02、M0-03、M0-04 技术验证，以及候选项目的 GitHub 官方仓库、许可证文件、发布标签和 README。GitHub Star、安装量和社交热度没有作为采用理由。

## 2. 决策摘要

| 能力 | 决策 | 固定版本或基线 | 许可证 | 集成边界 |
| --- | --- | --- | --- | --- |
| 主数据库与全文索引 | 采用 PostgreSQL | `18.4` | PostgreSQL License | 核心服务；项目状态仍是事实来源 |
| 向量存储与距离计算 | 采用 pgvector | `0.8.6` | PostgreSQL License | PostgreSQL 扩展；M2 初期只在硬过滤后的授权集合上精确搜索 |
| 后台任务队列 | 有条件采用 Procrastinate | `3.9.0` | MIT | 封装在内部 Job Adapter 后；正式任务状态另存业务表 |
| M1 文档解析 | 采用现有确定性解析器契约 | 当前仓库版本 | 仓库 Apache-2.0 | 首先支持已验证的 Markdown、CSV、基础 DOCX |
| 扩展文档解析 | 延后 Docling | `2.118.1` 候选快照 | MIT，模型和传递依赖需另审 | 只能作为隔离 Worker 的可选适配器 |
| 中文与跨语言 embedding | 延后固定模型 | FlagEmbedding `1.4.0`、Sentence Transformers `5.7.0` 为评估快照 | MIT / Apache-2.0 仅覆盖代码；模型权重另计 | 通过可替换 Embedding Adapter 接入，未配置时词法降级 |
| 混合排序 | 自有确定性 SQL/业务逻辑 | RRF 契约待 M2 冻结 | 仓库 Apache-2.0 | 先硬过滤，再分别取词法与向量排名，最后融合 |
| Agent 工作流 | P0 不采用 LangGraph | `1.2.10` 评估快照 | MIT | 复杂恢复、分支和人工中断达到门槛后再评估 |
| 项目排期 | 采用现有确定性引擎 | 当前仓库版本 | 仓库 Apache-2.0 | 日期算术不交给模型或通用 Agent 框架 |
| 复杂资源优化 | 延后 OR-Tools | `9.15` 评估快照 | Apache-2.0 | 只有真实项目出现资源平衡需求后再评估 |

“采用”表示允许后续实现按固定版本和本文边界引入，不表示本次已经安装。“延后”表示它不是当前里程碑阻断项，也不得被产品界面暗示为已支持。

### 2.1 机器审计一致性矩阵

以下矩阵必须与 `validation/results/m0-05-dependency-audit.json` 完全一致，CI 会核对候选 ID、状态和版本。

| Candidate ID | Decision | Version |
| --- | --- | --- |
| `postgresql-fts` | adopted | `18.4` |
| `pgvector` | adopted | `0.8.6` |
| `procrastinate` | adopted | `3.9.0` |
| `docling` | deferred | `2.118.1` |
| `langgraph` | deferred | `1.2.10` |
| `unstructured` | deferred | `0.25.2` |
| `sentence-transformers` | deferred | `5.7.0` |
| `flagembedding` | deferred | `1.4.0` |
| `or-tools` | deferred | `9.15` |
| `apache-tika` | rejected | `3.3.2` |
| `qdrant` | rejected | `1.19.0` |
| `dify` | rejected | `1.16.1` |
| `n8n` | rejected | `n8n@2.33.7` |
| `minio` | rejected | `RELEASE.2025-10-15T17-29-55Z` |
| `rsshub` | rejected | `not-pinned` |

## 3. 已确认事实

### 3.1 当前仓库已验证的事实

- M0-02 已在合成样本上验证 Markdown、CSV、基础 DOCX 的稳定顺序、来源坐标、版本哈希和失败码；PDF、OCR、复杂版式和视觉渲染尚未验证。
- M0-03 已验证完成到开始依赖、工作日、显式缓冲、锁定日期、截止日期和关键路径的确定性行为；资源平衡和复杂依赖类型尚未支持。
- M0-04 已在 16 个合成查询上验证先做 workspace/client/project/visibility 硬过滤，再评分和返回可校验引用；字符 n-gram 只是语义代理，不是生产 embedding。

### 3.2 上游仓库可核对的事实

| 项目 | 官方仓库快照 | 许可证核对 | 维护信号 | 事实边界 |
| --- | --- | --- | --- | --- |
| PostgreSQL | 标签 `REL_18_4`，标签提交日期 2026-05-11 | 官方 `COPYRIGHT` 为 PostgreSQL License 文本 | 默认分支在 2026-08-07 仍有提交 | 本文没有验证容器镜像摘要或本项目迁移脚本 |
| pgvector | 标签 `v0.8.6`，标签提交日期 2026-07-29 | 官方 `LICENSE` 为 PostgreSQL License 文本；GitHub API 的 SPDX 字段为 `NOASSERTION`，因此以许可证原文为准 | 仓库未归档，默认分支在 2026-08-08 仍有提交 | README 说明 HNSW/IVFFlat 在近似索引下可能先扫描再应用过滤；这不能直接满足本项目“未授权块不得进入评分”的严格契约 |
| Procrastinate | 发布 `3.9.0`，2026-06-20 | 官方 `LICENSE.md` 为 MIT | 仓库未归档，2026-07-31 仍有提交；README 同时公开征求更多维护者 | README 声明基于 PostgreSQL 13+，支持周期任务、重试和任务锁；本文尚未完成与 PostgreSQL 18.4 的集成测试 |
| Docling | 发布 `v2.118.1`，2026-08-07 | 官方仓库为 MIT | 仓库未归档，2026-08-08 仍有提交 | README 声明支持 PDF、DOCX、PPTX、XLSX、HTML、图像和 OCR；模型包有各自许可证，MIT 不能自动覆盖模型权重和所有传递依赖 |
| Unstructured | 发布 `0.25.2`，2026-08-03 | 官方仓库为 Apache-2.0 | 仓库未归档，2026-08-04 仍有提交 | README 说明 PDF/图像本地处理可能需要 Poppler、Tesseract 和格式 extras，会扩大容器与许可证审查面 |
| Apache Tika | 稳定标签 `3.3.2` | Apache-2.0；官方 README 明确另有子组件 NOTICE 和许可证 | 仓库未归档，2026-08-08 仍有提交 | 它提供统一的检测、文本和元数据提取，但需要 JVM 服务或进程边界，且本文未验证营销文档的结构坐标质量 |
| Qdrant | 发布 `v1.19.0`，2026-08-05 | Apache-2.0 | 仓库未归档，2026-08-08 仍有提交 | README 声明支持 payload 过滤、稠密/稀疏向量和混合融合；采用它会新增独立存储服务和备份恢复面 |
| Sentence Transformers | 发布 `v5.7.0`，2026-08-06 | 代码仓库为 Apache-2.0 | 仓库未归档，2026-08-07 仍有提交 | 代码许可证不等于所下载模型权重的许可证 |
| FlagEmbedding | 发布 `v1.4.0`，2026-04-22 | 代码仓库为 MIT | 仓库未归档，2026-04-22 仍有提交 | README 将 BGE-M3 描述为支持 100+ 语言、最长 8192 token 和多种检索模式；本项目没有独立验证这些质量声明，模型权重仍需单独核对 |
| LangGraph | 核心标签 `1.2.10`，标签提交日期 2026-07-28 | MIT | 仓库未归档，2026-08-08 仍有提交 | README 声明支持持久执行和 human-in-the-loop；当前 P0 的受约束步骤尚不足以证明需要引入该框架 |
| OR-Tools | 发布 `v9.15`，2026-01-12 | Apache-2.0 | 仓库未归档，2026-08-07 仍有提交 | 提供约束求解能力，但 M0-03 的当前排期问题不需要通用求解器 |

这些维护信号只说明仓库近期存在公开活动，不能证明安全性、兼容性、长期维护能力或适合本产品。

## 4. 采用决策

### 4.1 PostgreSQL 18.4 与 pgvector 0.8.6

采用理由：

- 项目状态、审计、权限、全文检索和向量数据可以保留在同一事务与备份边界，符合私有部署和“小团队先行”的范围。
- pgvector 官方 README 给出了 PostgreSQL 全文检索配合 RRF 或 cross-encoder 的混合检索方向，也明确给出了 tenant isolation 的分区建议。
- 两者许可证文本允许与 Apache-2.0 项目组合分发；发布时仍需在第三方声明中保留版权和许可证文本。

强制限制：

1. M2 初期禁止在共享跨客户 HNSW/IVFFlat 索引上直接执行带租户过滤的近似查询。pgvector 官方文档说明近似索引可能在扫描后才应用过滤，这与 M0-04 的严格顺序不等价。
2. 首版使用数据库 RLS 加应用层范围校验，并通过 `MATERIALIZED` 授权候选集或物理分区形成边界，再在授权集合内执行精确向量距离计算。任何查询计划优化都不能替代跨客户泄漏测试。
3. 词法索引使用 PostgreSQL FTS，但中文 token 由应用层确定性生成并版本化；不能假定内置英文式词法处理能解决中文检索。
4. 混合排名优先使用独立排名后的 RRF，避免直接相加不可比较的全文分值和向量距离。RRF 常数、候选数、并列规则和查询配置必须进入结果 hash。
5. embedding 必须绑定 provider、模型 ID、模型或 API 版本、向量维度和生成时间。模型变化必须重建索引，不允许新旧向量静默混用。

回退：pgvector 扩展不可用、模型不可用或索引需要重建时，继续提供经过范围过滤的确定性词法检索，并明确显示 `lexical_only`，不能把降级结果标成完整混合检索。

### 4.2 Procrastinate 3.9.0

有条件采用理由：

- 它直接使用 PostgreSQL，不要求为 M1 再增加 Redis、Valkey 或 RabbitMQ 服务。
- 官方 README 明确列出周期任务、重试和任务锁，覆盖解析、索引和导出 Worker 的基础需求。
- MIT 许可证与核心仓库许可证兼容。

采用条件与边界：

- 只能通过内部 `JobQueue` 适配器使用，业务代码不得直接依赖其装饰器或数据库表结构。
- `queued/running/partial/needs_review/succeeded/failed/cancelled` 是 MarketOps 业务状态，不以队列内部状态代替。
- 作业参数只保存不可变 artifact/version ID 和范围 ID，不把原始客户正文复制到队列载荷或日志。
- M1 合并前必须验证崩溃恢复、幂等、最大重试、取消、超时、孤儿锁回收和 PostgreSQL 18.4 兼容性。
- README 的“征求更多维护者”是实际维护风险。升级采用逐版本锁定，不跟随无界版本范围。

回退：保留业务 Job 表和可重放输入；若上游停更或集成测试不通过，可替换为本项目基于 PostgreSQL 锁的最小 Worker，或迁移到 Celery + 独立 broker。更换队列不能改变业务任务 ID、状态或审计事件。

### 4.3 现有文档解析器与项目排期引擎

M1 继续使用已经通过 M0 检查的确定性解析器和排期语义，原因不是它们功能更强，而是当前纵向切片只要求一份已确认方案进入可编辑排期，已验证格式足以完成该门槛。

限制必须保留在界面和导入校验中：

- 不支持 PDF、扫描件、OCR、复杂 DOCX 版式时应明确拒绝或标成 `partial/needs_review`。
- 当前排期不支持资源平衡、复杂依赖和概率模拟。
- 模型只能建议任务、工期和关系，日期计算与冲突判定仍由确定性引擎负责。

回退即保留原始文件和失败原因，让用户换用已支持格式或人工继续；不得静默抽取不完整内容。

## 5. 延后与拒绝项

### 5.1 Docling 2.118.1：延后，优先扩展候选

Docling 的格式范围和本地 OCR 方向与产品相符，但目前有三个未解决的质量问题：真实中文营销文件覆盖、容器资源占用、模型及传递依赖许可证。它只能在隔离 Worker 中通过 `DocumentParser` 接口试运行，默认禁止自动下载模型和向外发送文件。

准入门槛：

- 对经授权且去标识的中文 DOCX/PDF 样本完成标题、表格、阅读顺序、OCR、来源坐标和失败可观察性测试。
- 生成锁文件、SBOM、完整传递许可证清单，并逐项核对模型权重许可证与 NOTICE。
- 记录 CPU、内存、冷启动、单页耗时和镜像增量；设定资源上限和超时。
- 在 Word 或 LibreOffice 可用环境补做逐页视觉回归。

Unstructured 0.25.2 是同一接口下的备选，不与 Docling 同时进入默认镜像。它的格式 extras、Poppler 和 Tesseract 必须作为单独供应链审查。Apache Tika 3.3.2 在 P0 拒绝，原因是新增 JVM/服务复杂度且未显示优于两个 Python 候选的可引用结构质量；这不是对 Tika 通用质量的否定。

### 5.2 Embedding 与重排模型：延后固定实现

当前不能采用“某代码仓库是 MIT/Apache-2.0，因此其所有模型都可安全商用”的推导。模型权重、训练数据声明、下载源和服务条款是单独的审查对象。

M1 使用词法检索即可；M2 开始前必须在真实但获授权的中文/中英混合查询集上比较至少一个本地候选和一个 BYOK 服务候选。候选需固定模型修订 SHA、许可证文件 SHA、向量维度、最大输入、截断策略、硬件资源和离线可用性。FlagEmbedding/BGE-M3 与 Sentence Transformers 只是评估入口，不是已采用依赖，也不能据上游 README 的能力描述宣称本项目已获得对应召回质量。

### 5.3 Qdrant 1.19.0：P0 拒绝

Qdrant 的官方能力覆盖过滤和混合检索，许可证也无明显核心冲突；拒绝原因是当前规模下新增独立数据库、备份、恢复、迁移和租户配置的成本没有证据支持。若 PostgreSQL 精确搜索超过经测量的延迟或容量门槛，再用同一隔离矩阵对 Qdrant 做替代测试。

### 5.4 LangGraph 1.2.10：延后

当前工作流是少量有 Schema 的单职责步骤、确定性计算和显式人工审批。引入通用 Agent 图框架会增加状态来源和升级面，却没有已经观测到的复杂分支需求。只有出现跨进程恢复、多分支补偿和人工中断使现有 Job + 业务状态机明显难以维护时，才按固定版本评估 LangGraph。聊天记录或 Agent 内部 checkpoint 不能成为项目事实来源。

### 5.5 OR-Tools 9.15：延后

M0-03 已能处理当前完成到开始依赖和锁定冲突。没有真实证据证明首批用户需要多人资源优化或约束求解，因此不引入 OR-Tools。若真实项目出现资源容量、轮班、多技能负责人或成本优化需求，再用明确的最优性、可解释性和运行时间门槛评估。

### 5.6 大型平台、强 copyleft 与已归档服务：P0 拒绝

- Dify `1.16.1` 使用带多租户商业触发与前端标识限制的修改版 Apache 许可，不作为 Apache-2.0 核心、前端或内嵌平台。
- n8n `2.33.7` 使用 Sustainable Use License，不作为核心自动化引擎或随项目捆绑分发；未来只能在单独条款审查后作为用户自行配置的外部连接器。
- MinIO Community Edition 仓库已归档且使用 AGPL-3.0，不作为默认对象存储；P0 使用本地文件适配器，未来只依赖通用 S3 接口。
- RSSHub 使用 AGPL-3.0，P0 不捆绑代码或服务；M3 优先使用标准 RSS/Atom 和官方 API，用户自有 RSSHub 端点只能作为可选外部来源。

这些拒绝结论针对本项目的分发、维护和范围约束，不等于对相应项目通用能力的否定。

## 6. 合理推测与未知

### 合理推测

- 单一 PostgreSQL 边界可能降低个人部署的运维成本，但尚未通过实际安装和恢复演练确认。
- M1 的小语料更适合先做精确向量扫描；只有基准数据证明性能不足，近似索引的复杂度才有依据。
- 通过适配器隔离 Docling、embedding 和队列，可降低替换成本；这仍需接口契约测试验证。

### 暂时无法验证

- 真实中文方案、合同、复盘和扫描件上的解析完整率。
- embedding 对活动、品牌和 B2B 市场术语的召回率、误报率及跨语言表现。
- PostgreSQL 18.4、pgvector 0.8.6 和 Procrastinate 3.9.0 的组合在目标 Docker 环境中的性能与恢复行为。
- 上游未来维护、漏洞响应和许可证是否变化。
- 用户是否愿意为这些能力付费；依赖选择与合成技术测试不能证明商业价值。

这些未知不阻断 M1 的已验证 DOCX/Markdown/CSV 到确定性排期纵向切片。它们分别是 M1 集成测试和 M2 检索准入门槛，不能被写成已经完成。

## 7. 许可证与供应链要求

1. 采用的第三方版本必须在锁文件或容器摘要中固定，不允许 `latest`、无上限范围或仅固定主版本。
2. 发布镜像前生成 SBOM，并扫描直接与传递依赖许可证；`NOASSERTION` 不能自动视为不兼容，也不能自动放行，必须读取许可证原文。
3. 在 `THIRD_PARTY_NOTICES.md` 保留 PostgreSQL、pgvector、Procrastinate 及未来实际分发依赖的版权、许可证链接和修改说明。
4. 模型代码、模型权重、数据集和托管 API 条款分别审查。下载脚本必须固定来源与 SHA-256，并允许部署方禁用网络下载。
5. AGPL、SSPL、BUSL、Fair-code、自定义许可证或来源不明组件不得进入 Apache-2.0 核心；若未来作为外部服务使用，必须由单独适配器和部署说明隔离。
6. 每次依赖升级必须重跑解析失败路径、检索隔离矩阵、引用新鲜度、任务恢复和排期确定性测试。

## 8. M1/M2 验收触发器

| 阶段 | 必须通过后才能启用 |
| --- | --- |
| M1 数据库 | PostgreSQL 迁移、备份恢复、RLS 越权测试、固定镜像摘要 |
| M1 队列 | 崩溃恢复、幂等、重试上限、取消、孤儿任务回收、敏感日志检查 |
| M1 文档 | 已支持格式回归、失败可观察、原件与来源版本绑定 |
| M2 向量 | 固定 embedding 模型/服务版本、权重许可证、删除传播、重建和混用拒绝 |
| M2 检索 | 授权集合先于评分、中文真实样本评估、词法降级、RRF 确定性和引用新鲜度 |
| 后续解析扩展 | Docling/Unstructured 二选一的质量、资源、SBOM、模型许可证和视觉回归 |

## 9. 官方来源

- PostgreSQL：[仓库](https://github.com/postgres/postgres)、[18.4 标签](https://github.com/postgres/postgres/tree/REL_18_4)、[COPYRIGHT](https://github.com/postgres/postgres/blob/master/COPYRIGHT)
- pgvector：[仓库](https://github.com/pgvector/pgvector)、[0.8.6 标签](https://github.com/pgvector/pgvector/tree/v0.8.6)、[LICENSE](https://github.com/pgvector/pgvector/blob/master/LICENSE)、[过滤与混合检索说明](https://github.com/pgvector/pgvector/blob/master/README.md#filtering)
- Procrastinate：[仓库](https://github.com/procrastinate-org/procrastinate)、[3.9.0 发布](https://github.com/procrastinate-org/procrastinate/releases/tag/3.9.0)、[LICENSE](https://github.com/procrastinate-org/procrastinate/blob/main/LICENSE.md)
- Docling：[仓库](https://github.com/docling-project/docling)、[2.118.1 发布](https://github.com/docling-project/docling/releases/tag/v2.118.1)、[LICENSE](https://github.com/docling-project/docling/blob/main/LICENSE)
- Unstructured：[仓库](https://github.com/Unstructured-IO/unstructured)、[0.25.2 发布](https://github.com/Unstructured-IO/unstructured/releases/tag/0.25.2)、[LICENSE](https://github.com/Unstructured-IO/unstructured/blob/main/LICENSE.md)
- Apache Tika：[仓库](https://github.com/apache/tika)、[3.3.2 标签](https://github.com/apache/tika/tree/3.3.2)、[LICENSE](https://github.com/apache/tika/blob/main/LICENSE.txt)
- Qdrant：[仓库](https://github.com/qdrant/qdrant)、[1.19.0 发布](https://github.com/qdrant/qdrant/releases/tag/v1.19.0)、[LICENSE](https://github.com/qdrant/qdrant/blob/master/LICENSE)
- Sentence Transformers：[仓库](https://github.com/huggingface/sentence-transformers)、[5.7.0 发布](https://github.com/huggingface/sentence-transformers/releases/tag/v5.7.0)、[LICENSE](https://github.com/huggingface/sentence-transformers/blob/main/LICENSE)
- FlagEmbedding：[仓库](https://github.com/FlagOpen/FlagEmbedding)、[1.4.0 发布](https://github.com/FlagOpen/FlagEmbedding/releases/tag/v1.4.0)、[LICENSE](https://github.com/FlagOpen/FlagEmbedding/blob/master/LICENSE)
- LangGraph：[仓库](https://github.com/langchain-ai/langgraph)、[1.2.10 标签](https://github.com/langchain-ai/langgraph/tree/1.2.10)、[LICENSE](https://github.com/langchain-ai/langgraph/blob/main/LICENSE)
- OR-Tools：[仓库](https://github.com/google/or-tools)、[9.15 发布](https://github.com/google/or-tools/releases/tag/v9.15)、[LICENSE](https://github.com/google/or-tools/blob/stable/LICENSE)

## 10. 复现方法

以下命令只读取官方仓库元数据和标签；GitHub API 需要可用网络，受匿名或账户配额限制。

```powershell
gh api repos/docling-project/docling
gh api repos/docling-project/docling/releases/latest
gh api repos/pgvector/pgvector
git -c versionsort.suffix=- ls-remote --sort=-version:refname --tags https://github.com/pgvector/pgvector.git
gh api repos/procrastinate-org/procrastinate/releases/latest
gh api repos/qdrant/qdrant/releases/latest
gh api repos/google/or-tools/releases/latest
```

许可证必须读取固定标签中的完整文件，不能只依赖 GitHub API 的 SPDX 字段：

```powershell
Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/pgvector/pgvector/v0.8.6/LICENSE
Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/procrastinate-org/procrastinate/3.9.0/LICENSE.md
Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/docling-project/docling/v2.118.1/LICENSE
```

本决策通过独立审查后，主集成者还应运行仓库进度检查和 M0-05 依赖审计；本文作者不是最终审查者，也不据此标记任务完成。
