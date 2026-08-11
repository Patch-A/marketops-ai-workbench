# M1-02 交付物提取与人工审核切片

状态：`WP1 passed; persistence and review workflow pending`

日期：`2026-08-11`

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

### WP1 验收证据

- 受审提交：`10737d0e688ad41ef5192f369101d2f8a76c9b7c`；tree：`66550d9e978b9cb595dd54406ea156d38226470f`。
- GitHub Actions run [31484373207](https://github.com/Patch-A/marketops-ai-workbench/actions/runs/31484373207) 的 `headSha` 与受审提交一致；`static-checks` 和 `m1-01-runtime` 均通过。当前顶层 CI 尚未单独列出 WP1 专项命令，因此同一提交的专项测试仍作为独立证据保留。
- 本地专项套件通过 `27/27`；完整 `apps/api` 套件通过 `238` 项，`32` 项因本机缺少 PostgreSQL、FastAPI 运行依赖、Linux `flock` 或符号链接权限而跳过；`compileall` 与 `git diff --check` 通过。
- 合成方案回归稳定产生 10 个候选：6 个交付物、3 个假设、1 个约束；该结果验证确定性工程行为，不验证真实方案召回率、误报率或业务价值。
- 多轮非实现者审查发现并推动关闭：交错坐标回退、范围重叠、表格越界及身份不一致、重复语义列、`sectionPath` 丢失、未知字段敏感信息回显、normalized DOCX cell 回归、固定进位坐标碰撞和跨 DOCX part 混排。最终独立 `codex review --commit 10737d0e688ad41ef5192f369101d2f8a76c9b7c` 退出码为 0，未输出 P0/P1/P2 finding，且未修改工作区。
- Git HTTPS push 连续三次连接 GitHub 443 失败后，使用 GitHub Git Data API 按原始字节创建 blob 和 tree，并更新分支。远端与本地最终对齐到同一提交、同一 tree 和同一父提交；该降级路径不改变代码内容。

### WP1 明确限制

- WP1 只有候选契约和确定性提取器，没有数据库持久化、审核版本、审计事件、HTTP API、UI、权限门禁、并发冲突处理或 M1-03 handoff。
- 公开资料和合成 fixture 只能证明工程行为；不能证明需求、ROI、节省时间、重复使用、真实项目质量或付费意愿。
- `M1-02` 继续保持 `in_progress`。下一工作包必须先实现项目范围内的候选持久化、不可变审核版本、逐条审核审计和权限/冲突失败路径，再接 UI。

## 9. WP2A：项目范围持久化与不可变审核版本

### 四象限复检

| 象限 | 当前结论 | 执行约束 |
| --- | --- | --- |
| 共同已知 | 候选来自同项目已批准方案；候选不是人工决定；每次批准、修改或拒绝必须可追溯。 | 候选、审核版本和决定分离；原候选与旧审核版本只追加不覆盖。 |
| 用户已知、实现未知 | 真实用户更偏好逐条还是批量审核、是否需要多人同时审核，尚无行为数据。 | P0 先实现单候选原子操作和完整新快照；批量操作与多人工作流留到真实使用验证后。 |
| 用户未知、实现已知 | 新增业务表会改变备份/恢复 allowlist、应用角色权限、RLS 证明和运行时 gate。 | WP2A 不只加 migration；必须同步扩展备份、恢复、权限和隔离验证，否则审核数据可能在恢复后丢失。 |
| 共同未知 | 并发审核冲突频率、完整快照的存储增长和真实方案候选规模尚无证据。 | 用两个并发 reviewer 争用同一 `expectedReviewVersion` 的最小实验；记录候选数、版本数、冲突数和事务耗时，不把合成结果外推为生产容量。 |

### 工作包契约

- Task ID：`M1-02`；基线提交：`a1669172465468e3d5286ad5eff601c02ae78890`。
- Owned paths：新增 `apps/api/marketops_review/**`、`apps/api/migrations/0002_extraction_review.sql`、对应 `apps/api/tests/test_m1_02_review_*.py` 与 `apps/api/tests/postgres/test_m1_02_review_runtime.py`。备份、恢复、权限 gate 和顶层 CI 的适配由主集成者单独审查后修改。
- Forbidden paths：`project-status.json`、`docs/PROJECT_STATUS.md`、M1-01 的 `0001` migration、对象存储/import/browser/cleanup 行为、前端、连接器、跨项目检索、真实客户资料和新依赖。
- Frozen input：服务器注入的 organization/workspace/client/actor scope、项目 ID、已批准 proposal artifact/version/hash、WP1 的完整候选批次，以及审核请求的 `expectedReviewVersion`、单个 candidate ID、`approve | modify | reject`、原因、可选评论和修改后文本。
- Frozen output：一个项目范围 extraction run、不可变候选集合、从版本 1 开始的不可变完整审核快照、逐条决定和 append-only audit event。候选 ID、原文、来源引用与旧版本不得被更新或删除。
- Review semantics：版本 1 为全部 `pending`；每次操作复制上一版完整快照并只改变一个候选，生成 `version + 1`。`modify` 必须有非空 replacement text；三种人工动作都必须有 reason。只有最新完整版本可供后续读取，旧版本仍可查看。
- Conflict semantics：请求必须携带 `expectedReviewVersion`；与数据库最新版本不一致时返回稳定 `REVIEW_CONFLICT`，且不得写入 review version、decision 或 audit event。禁止 last-write-wins。
- Source gate：run 的 artifact/version/hash 必须与项目当前已批准 proposal 完全一致；候选 citation 必须仍匹配该 source version/hash。失败返回 `INVALID_PROPOSAL_STATE` 或 `SOURCE_CITATION_INVALID`，不得部分保存。
- Scope gate：所有新表启用并强制 RLS；读取和写入必须同时匹配 workspace、client、project 与非空 actor。跨项目或跨客户对象在应用角色视角下不可见，不返回存在性差异。
- Atomicity gate：创建 run、候选、初始版本、完整 pending snapshot 和 audit 必须同事务提交；每次审核的新版本、完整 snapshot 和 audit 也必须同事务提交。任一步失败全部回滚。
- Recovery gate：新增表必须进入应用级逻辑备份/恢复 allowlist；隔离恢复后 run、候选、所有审核版本、决定、审计、RLS 和应用角色权限必须与源快照一致。旧的项目导入恢复证据不得因扩展而失效。
- Acceptance commands：focused unit tests、migration/static contract tests、PostgreSQL 18.4 RLS/concurrency/rollback runtime tests、backup/restore gate、完整 `apps/api` 测试、`compileall`、`check-docs`、progress check 和 `git diff --check`。同一提交 CI 与非实现者复审均通过后，WP2A 才可关闭。

### WP2A 最小并发实验

固定一个含两个候选的合成 run，两个 reviewer 同时基于 `expectedReviewVersion = 1` 修改同一候选。唯一可变因素是提交先后；成功信号是恰好一个事务生成版本 2，另一个得到 `REVIEW_CONFLICT`，数据库中没有版本 3、重复决定或孤立 audit。失败信号是 last-write-wins、两个事务都成功、部分 snapshot、跨 scope 可见或恢复后审核历史缺失。

### WP2A-1 领域服务证据

- 受审提交：`e3d9a0884e486b646327da77056e0d5e99cbb7c2`；GitHub Actions run [31488499868](https://github.com/Patch-A/marketops-ai-workbench/actions/runs/31488499868) 的 `headSha` 一致，两个现有 job 均通过。当前顶层 CI 尚未单列 WP2A-1 专项测试。
- 新增不可变 run、候选批次、完整 review snapshot、单候选 decision、audit event 和 async repository/transaction 协议。创建 run 锁定当前批准方案；每次审核要求 project ID 与 `expectedReviewVersion`，同版本并发只允许一个 winner。
- 专项测试 `18/18` 通过；本地完整 `apps/api` 测试 `258` 项通过，`32` 项因环境能力跳过；`compileall`、文档/progress 检查和 `git diff --check` 通过。
- 首轮独立 commit review 发现批准方案读取未声明锁定、UUID 大写输入被接受但未统一归一化两项 P2；修复后最终 review 未发现可执行 correctness regression。reviewer 的只读环境没有 Python launcher，因此它没有重复执行 Python 测试，该限制由同一工作区的上述可复现测试证据补充，但不能描述成 reviewer 亲自复跑。
- WP2A-1 仅关闭领域服务包。`apps/api/migrations/0002_extraction_review.sql`、PostgreSQL adapter、RLS/ACL、真实并发事务、备份恢复和 HTTP/UI 均未实现；WP2A 与 M1-02 继续为 `in_progress`。

### WP2A-2 数据库与恢复工作包契约

- Task ID：`M1-02`；基线提交：`bd97b40011dc1dc6d28ff8cf74e498bdaaed1ecb`。
- Owned paths：`apps/api/migrations/0002_extraction_review.sql`、`apps/api/marketops_review/postgres.py`、`apps/api/marketops_review/__init__.py`、对应 unit/PostgreSQL runtime tests，以及为新增业务表所必需的 migration、ACL、备份恢复 gate 和 CI 适配。主集成者负责所有修改和最终提交；探索 Agent 只返回只读分析。
- Forbidden paths：`project-status.json`、`docs/PROJECT_STATUS.md`、`0001_project_import.sql`、HTTP/UI、对象存储和 cleanup 行为、跨项目知识检索、真实客户资料、凭据和新依赖。
- Frozen input/output：沿用 WP2A-1 的领域对象与错误码；adapter 只能把服务器 scope 和 project ID 写入事务本地配置，并以不可变行保存 run、候选、完整 snapshot、snapshot item、decision 和现有 audit event。数据库错误不得回显 SQL、DSN 或输入内容。
- Schema decision：审核数据使用独立的 extraction run、candidate、snapshot、snapshot item 和 decision 表；review audit 复用 `audit_events`，避免第二套审计事实源。所有新表保存完整 workspace/client/project scope，启用并强制 RLS，禁止更新、删除和截断。
- Acceptance：静态契约和 adapter unit tests 先通过；随后 PostgreSQL 18.4 实测跨 scope 不可见、批准方案 `FOR UPDATE`、同版本并发一胜一冲突、任一步失败无部分行、应用角色无越权；最后扩展逻辑备份恢复，证明新增审核历史、RLS、owner 和权限在隔离恢复后保持。HTTP/UI 不属于本包完成证据。
- Reviewer role：非实现者复查 SQL 约束、RLS/ACL、事务锁顺序、错误翻译、备份 allowlist 和恢复证明。实现者的本地测试或同一模型判断不能替代最终审查。

## 10. WP2B 与 UI 边界

WP2A 通过后，WP2B 才暴露创建 run、读取候选/历史和逐条审核 HTTP API，并冻结 OpenAPI、认证、错误 envelope、`no-store`、重试和取消语义。浏览器 UI 必须消费服务器事实源，显示来源摘录、事实/假设、pending/approve/modify/reject、冲突刷新和失败恢复；静态 mockup 不构成功能完成证据。
