# 数据模型

## 1. 设计原则

- 项目数据库是事实来源，向量索引只是派生数据。
- 原始文件、解析文本、引用、向量和学习规则必须能追溯到同一来源版本。
- 客户资料默认不能跨客户检索。
- AI 输出不能覆盖人工确认内容，只能创建新版本或候选变更。

## 2. 核心实体

| 实体 | 关键字段 | 说明 |
|---|---|---|
| Organization | id、name、settings | 团队边界，单人模式也保留 |
| Workspace | id、organization_id、type | 个人或工作空间 |
| Client | id、workspace_id、name、policy | 客户和品牌边界 |
| Project | id、client_id、type、stage、status | 一次营销项目 |
| Artifact | id、project_id、kind、current_version | Brief、方案、报价、周报等 |
| ArtifactVersion | id、artifact_id、storage_key、hash、author | 不可变文件版本 |
| SourceChunk | id、version_id、text、location、embedding | 可引用的检索单元 |
| Assumption | id、project_id、statement、status、confidence | 需要验证的项目判断 |
| Decision | id、project_id、decision、approver、reason | 人工确认的决定 |
| Deliverable | id、project_id、name、status、due_at | 方案中确认的交付物 |
| Task | id、deliverable_id、owner、duration、dates、status | 可执行任务 |
| Dependency | predecessor_id、successor_id、type、lag | 任务依赖 |
| Signal | id、source、observed_at、summary、confidence | 外部市场变化 |
| Impact | signal_id、target_type、target_id、severity、status | 信号与项目对象的影响关系 |
| Feedback | id、target、actor、action、reason | 接受、修改、驳回和评论 |
| Outcome | id、project_id、metric、planned、actual、source | 实际结果 |
| Retrospective | id、project_id、finding、evidence | 复盘结论 |
| KnowledgeItem | id、scope、type、status、content、confidence | 可复用知识 |
| KnowledgeEvidence | knowledge_id、source_type、source_id | 知识的证据链 |
| AuditEvent | actor、action、target、before、after、time | 关键操作审计 |

## 3. 信息分类

每条关键内容必须标记为：

- `fact`：来自用户输入或可引用来源。
- `hypothesis`：模型或人员提出、尚待验证。
- `decision`：由有权限的人确认。
- `outcome`：执行后产生的实际结果。

分类是业务字段，不只是在界面上显示一个标签。

## 4. 知识范围

| Scope | 可检索范围 | 默认策略 |
|---|---|---|
| project | 当前项目 | 原始资料默认范围 |
| client | 同一客户的项目 | 用户明确提升后可用 |
| workspace | 当前个人或团队 | 只保存通用方法和已批准模板 |
| global | 所有部署用户 | P0 不支持 |

## 5. 版本与删除

- 原始文件版本不可被 AI 静默覆盖。
- 删除源文件时，解析文本、缩略图、向量、缓存和引用状态必须同步处理。
- 若知识条目引用被删除来源，条目进入 `needs_review`，不能继续作为已确认事实使用。
- 项目归档不等于删除；归档项目仍受权限和保留期限控制。
