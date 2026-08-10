# M1-02 交付物提取与人工审核切片

状态：`Kickoff contract; implementation pending`

日期：`2026-08-10`

## 1. Job statement

对于独立负责活动、品牌或 B2B 营销项目的市场人员，在已批准方案导入后，帮助其把方案中的交付物、里程碑、约束和关键假设整理成可逐条审核的候选清单，从而减少人工通读和漏项，同时保留每条内容的来源依据。

## 2. 四象限检查

| 象限 | 当前记录 | 执行含义 |
| --- | --- | --- |
| 共同已知 | M1-01 已把原始资料和已批准方案以服务端不可变版本保存；M1-02 的注册表验收要求来源引用和用户批准。 | 只处理已导入且已批准的方案，不读取浏览器本地事实，不生成正式排期。 |
| 我的已知、你的未知 | 真实方案的版式、表格密度、中文/英文混排和“交付物”措辞尚未用获授权客户样本验证。 | 先支持 Markdown、CSV 和基础 DOCX 的结构化文本；对复杂格式保留 `needs_review`，不猜测隐含事项。 |
| 我的未知、你的已知 | 仅凭模型输出不能证明交付物存在、属于哪个阶段或可执行；来源坐标、版本、审核人和修改理由是后续可追溯性的必要条件。 | 候选对象与已确认对象分离；每次接受、修改、拒绝都写入新审核版本和审计事件。 |
| 共同未知 | 用户愿意逐条审核到什么粒度、哪些候选类型最容易被保留，以及提取是否真的减少时间，当前没有真实行为证据。 | 用一个脱敏/获授权方案做最小实验，记录候选保留率、修改率、拒绝率、审核耗时和漏项回报；不把合成 fixture 当作价值证据。 |

## 3. 最小工作流

```text
server-approved proposal
-> parse and cite source spans
-> extract candidate deliverables / milestones / constraints / assumptions
-> classify fact or hypothesis and attach confidence
-> user review: accept / edit / reject / comment
-> save reviewed version and audit event
-> expose approved objects to M1-03
```

必须区分：

- `fact`：可在方案来源中定位的明确陈述。
- `hypothesis`：模型或用户推断、需要后续确认的内容。
- `decision`：用户审核后接受或修改的项目决定。
- `outcome`：M1-02 不产生执行结果，不能在此阶段伪造。

## 4. MVP 产品契约

### 输入

- M1-01 已批准方案版本及其不可变来源元数据。
- 解析器输出的段落、表格单元格和稳定 section/offset 坐标。
- 可选的用户补充说明；补充说明必须标为用户输入，不能伪装成方案事实。

### 输出

- 候选 `Deliverable`、`Milestone`、`Constraint`、`Assumption` 对象。
- 每个候选的 `sourceVersionId`、来源位置、原文摘录、类别、置信度和提取器版本。
- 审核动作 `approve`、`modify`、`reject` 及其理由、操作者和时间；评论是审核事件的可选字段，不改变审核决定。
- 一份不可变审核版本；只有 `approve`/`modify` 的对象可供 M1-03 使用。

### 冻结的 JSON 结构

M1-02 的 API 和持久化实现应以以下结构为最小契约。字段名采用 camelCase；未知字段必须被拒绝，避免把模型自由文本误写入项目事实。

```json
{
  "schemaVersion": 1,
  "projectId": "uuid",
  "proposal": {
    "artifactId": "uuid",
    "sourceVersionId": "uuid",
    "sha256": "hex",
    "mediaType": "text/markdown",
    "parserVersion": "parser-version",
    "approvalStatus": "approved"
  },
  "run": {
    "runId": "uuid",
    "status": "needs_review",
    "extractorVersion": "extractor-version",
    "createdAt": "2026-08-10T00:00:00Z"
  },
  "candidates": [
    {
      "candidateId": "uuid",
      "kind": "deliverable",
      "text": "Candidate statement",
      "classification": "fact",
      "confidence": 0.92,
      "sourceCitation": {
        "sourceVersionId": "uuid",
        "location": { "sectionPath": ["Scope"], "startOffset": 120, "endOffset": 168 },
        "quote": "Source excerpt copied from the approved proposal",
        "sourceSha256": "hex"
      },
      "review": {
        "status": "approve",
        "actorId": "uuid",
        "reason": "Matches the proposal",
        "comment": null,
        "reviewedAt": "2026-08-10T00:01:00Z"
      }
    }
  ],
  "audit": {
    "reviewVersion": 1,
    "events": [
      {
        "eventId": "uuid",
        "candidateId": "uuid",
        "action": "approve",
        "before": "needs_review",
        "after": "approved",
        "actorId": "uuid",
        "reason": "Matches the proposal",
        "occurredAt": "2026-08-10T00:01:00Z"
      }
    ]
  }
}
```

Allowed values are deliberately narrow: `kind` is `deliverable | milestone | constraint | assumption`; candidate `classification` is only `fact | hypothesis`; candidate review status is `pending | approve | modify | reject`; run status is `queued | parsing | extracted | needs_review | partially_approved | approved | failed | cancelled`. `sourceCitation` is required for a `fact`; a `hypothesis` must still carry the source or user-input citation that motivated it, or remain `needs_review` and be excluded from approval. Human `decision` is created by the review event, while `outcome` belongs to later execution milestones and cannot be emitted by M1-02. A `modify` event must include the replacement text and preserve the prior candidate version.

### 状态与失败

`queued -> parsing -> extracted -> needs_review -> partially_approved -> approved`；解析失败、模型超时、来源坐标失效和保存失败进入 `failed` 并保留原项目状态，同时显示可重试或人工继续入口。用户取消进入 `cancelled`，不得产生可供 M1-03 消费的批准对象。

| Failure code | Trigger | Retry / recovery | Must not happen |
| --- | --- | --- | --- |
| `INVALID_PROPOSAL_STATE` | Proposal version is missing or not approved | Ask user to select an approved immutable version | Extract from an unapproved draft |
| `UNSUPPORTED_FORMAT` / `INVALID_DOCUMENT` | Parser cannot safely read the input | Keep source; offer supported-format replacement or manual entry | Silently drop the original |
| `SOURCE_CITATION_INVALID` | Hash, offsets, section path, or quote no longer match | Mark candidate `needs_review`; reparse the current version | Treat stale text as a fact |
| `EXTRACTOR_TIMEOUT` / `EXTRACTOR_UNAVAILABLE` | Optional model or parser exceeds budget | Retry with bounded backoff or deterministic extractor; allow manual review | Block project access or mutate approved data |
| `REVIEW_CONFLICT` | Review version is stale or actor is out of scope | Reload latest version and require an explicit new decision | Last-write-wins overwrite |
| `PERSISTENCE_FAILED` | Candidate or audit write fails | Roll back the pending review version; retry safely | Publish partial approval |

### 安全与知识边界

- 原始文件和候选结果保持项目范围；M1-02 不提升客户资料为 workspace/global 知识。
- 任何跨项目检索必须显式授权并保留引用；本切片默认不做跨项目检索。
- 用户拒绝或修改不得覆盖原始方案；删除来源后相关候选进入 `needs_review`。
- API 响应和日志不得泄露对象存储 key、凭据、原始文件全文或 DSN。

## 5. 实现顺序与工作包

1. **数据契约**：冻结候选对象、来源坐标、审核版本、审计事件和稳定错误码；先写静态契约与负向测试。Owned paths: `apps/api/marketops_extract/**`, `apps/api/openapi/**`, and their focused tests.
2. **确定性提取薄切片**：用规则/结构化解析从合成方案提取可复现候选；模型适配器只能产生候选，不能直接写已确认对象。
3. **审核 API 与 UI**：实现逐条接受、修改、拒绝、评论、重试和取消状态；保存新版本，不原地覆盖。
4. **来源完整性**：校验来源版本 hash、坐标和引用摘录；坐标失效时 fail closed 并转人工审核。
5. **M1-03 handoff**：仅输出已批准候选及其来源，供 WBS/排期切片消费。

Forbidden paths for this package: `project-status.json`, `docs/PROJECT_STATUS.md`, top-level CI workflows, M1-01 migrations/backup/cleanup/browser-gate code, and any connector or cross-project retrieval implementation. The primary integrator alone changes the registry and generated status page.

### 工作包契约

- Task ID：`M1-02`；基线提交：`d26a616c677f038a5d71461648441930a2d04286`。
- 主集成者负责：`project-status.json`、`docs/PROJECT_STATUS.md`、顶层 CI、M1-02 计划和最终提交。
- 实现包应明确拥有的路径；不得修改 M1-01 迁移、备份、清理和浏览器门禁实现，不得加入真实客户资料或未审依赖。
- 完成条件：静态契约、确定性合成验证、失败路径、审计/权限检查、独立审查和同一提交 CI 全部通过，并在注册表写入证据与 `selfCheck.status: "passed"`。

## 6. 最小验证实验

固定一个公开/合成方案，只改变“是否显示来源摘录与坐标”这一变量。比较无引用候选与有引用候选的审核耗时、保留率、修改率、拒绝率和用户报告漏项数。成功信号是审核者能逐条定位依据且审核时间不高于当前人工基线；失败信号是坐标不可用、候选误报过多或用户不愿逐条审核。该实验只能验证可用性假设，不能证明付费意愿或 ROI。

## 7. 明确非目标

- 不在 M1-02 生成完整 WBS、倒排排期、甘特图或关键路径。
- 不做通用聊天、语音、飞书/企微连接器、市场监控或自动知识提升。
- 不把模型建议、公开案例或合成数据写成已确认项目事实。

## 8. WP1：确定性候选契约与提取器

- Task ID：`M1-02`；基线提交：`7487aad525d9fd3cfbef25c1c174df81d644ca25`。
- Owned paths：`apps/api/marketops_extract/__init__.py`、`apps/api/marketops_extract/contract.py`、`apps/api/marketops_extract/deterministic.py`、`apps/api/tests/test_m1_02_extraction_contract.py`、`apps/api/tests/test_m1_02_deterministic_extractor.py`。
- Forbidden paths：`project-status.json`、`docs/PROJECT_STATUS.md`、顶层 CI、数据库迁移、HTTP/OpenAPI、M1-01 实现与测试、真实客户资料、模型 SDK 和新依赖。
- Frozen input：一个已批准方案版本的 UUID 与 SHA-256，以及来自受审解析器的 `heading`、`paragraph`、`table` blocks；每个可提取内容必须有稳定 section path 和行/表格单元格坐标。
- Frozen output：仅包含 `deliverable | milestone | constraint | assumption` 的不可变候选；候选分类仅为 `fact | hypothesis`；候选 ID 对相同来源版本、类别、文本和坐标稳定；所有候选默认 `pending`，不得输出人工批准决定。
- Failure contract：拒绝非法 UUID/SHA-256、未知 block/location 字段、空文本、越界或非单调坐标、重复来源位置、未知候选类别、缺少来源引用，以及不能无歧义映射的表格。失败不得返回部分候选。
- Extraction boundary：只从明确的章节和列名识别合成 fixture 中的交付物、里程碑、约束和假设；不推断负责人、日期、依赖、ROI 或未写明事项。模型适配器不在 WP1 范围。
- Acceptance commands：`python -m unittest apps.api.tests.test_m1_02_extraction_contract apps.api.tests.test_m1_02_deterministic_extractor -v`；`python -m compileall -q apps/api/marketops_extract`；`git diff --check`。
- Reviewer role：非实现者检查候选/决定分离、稳定 ID、来源完整性、重复与未知字段失败、合成 fixture 期望和无部分结果行为。WP1 通过不等于 M1-02 完成，也不证明真实方案质量、节省时间或付费价值。
