# M1-02 交付物提取与人工审核切片

状态：`completed for the bounded technical acceptance; market-value validation remains deferred`

日期：`2026-08-13`

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

### WP2A-2 验收证据

- 最终受审提交：`b207495e58cc66fd02a00844111a9004f933cff4`；GitHub Actions run [31495136866](https://github.com/Patch-A/marketops-ai-workbench/actions/runs/31495136866) 的 `headSha` 一致，`static-checks` 与 PostgreSQL 18.4 runtime 均通过。
- 数据库新增 5 张项目范围、强制 RLS、append-only 审核表；批准方案与 extraction run 使用真实 `FOR UPDATE`。应用角色只增加 `projects.created_by` 与 `extraction_runs.created_by` 两个列级 `UPDATE` 权限来取得行锁，实际修改仍由不可变触发器拒绝。
- 静态门禁逐表、逐半检查 `USING` 与 `WITH CHECK` 的精确合取结构，并拒绝谓词删除、`AND -> OR`、额外表达式、条件化不可变触发器、缺失 forced RLS 和行锁移除。最终非实现者复审的 30 个只读变异实验全部被拒绝。
- PostgreSQL runtime 对 5 张审核表分别验证正确 scope 可见，以及错误 workspace、client、project 与空 actor 不可见。双候选并发实验只产生版本 2 和一个 `REVIEW_CONFLICT`；完整 4 个 snapshot item 中未操作候选保持 `pending`，没有版本 3、重复决定或孤立 audit。
- 当前 v2 逻辑备份恢复覆盖 12 张业务表、两个 migration hash、非空审核历史、对象、owner、forced RLS 和最小权限。额外的真实 v1 实验导出 7 表 data-only dump，加载 schema version 1 bundle，恢复到已应用 `0001 + 0002` 的隔离库，确认 5 张审核表初始为空，再成功创建 `1 run / 1 candidate / 2 snapshots / 2 items / 1 decision` 和 review version 2。
- 本地完整 `apps/api` 套件通过 272 项，35 项因本机缺少 PostgreSQL、FastAPI、Linux `flock` 或符号链接权限而跳过；同 SHA CI 安装运行依赖后静态套件通过 272 项、跳过 34 项，PostgreSQL runtime 8/8 通过。`compileall`、文档、progress 和 `git diff --check` 均通过。
- 多轮独立审查先后发现并关闭：静态门禁可被谓词/触发器弱化绕过、advisory lock 与行锁契约冲突、单候选并发不能证明完整快照、v1 只解析不恢复、TOC 缺表未覆盖、migration evidence 漂移，以及 `AND -> OR` 假阳性。最终复审对 `b207495` 返回 `CLEAN APPROVE`，未发现 P0/P1/P2。
- WP2A-2 只关闭数据库持久化与恢复工作包。它不提供 HTTP、OpenAPI、认证、浏览器审核 UI 或 M1-03 WBS handoff，也不证明生产容量、需求、ROI、节省时间、重复使用或付费。`M1-02` 继续为 `in_progress`，下一工作包是 WP2B 审核 HTTP API。

## 10. WP2B 与 UI 边界

WP2A 通过后，WP2B 才暴露创建 run、读取候选/历史和逐条审核 HTTP API，并冻结 OpenAPI、认证、错误 envelope、`no-store`、重试和取消语义。浏览器 UI 必须消费服务器事实源，显示来源摘录、事实/假设、pending/approve/modify/reject、冲突刷新和失败恢复；静态 mockup 不构成功能完成证据。

## 11. WP2B：审核 HTTP API 工作包契约

### Job、流程与范围

**Job statement：**面向已经导入并批准方案的认证项目操作者，当其开始方案拆解时，由服务器读取并校验当前已批准方案，创建可追溯审核批次、查看任一审核版本并逐条作出人工决定，使后续 WBS 只消费有来源、可回看且冲突安全的状态。

最小流程为 `authenticate -> create extraction run -> list project runs -> inspect latest or historical snapshot -> submit one decision -> refresh on conflict`。确认事实是批准方案版本/hash、候选原文与来源；确定性提取结果在人工决定前仍是候选；`approve | modify | reject`、理由、评论和替换文本属于人工决定；真实结果与复盘数据不在本包产生。

MVP 只包含以下四个服务器事实源操作：

1. `POST /v1/projects/{projectId}/extraction-runs`：只接收调用方预期的批准方案 version ID/hash；服务器自行读取不可变 proposal 对象、复核 hash、解析并调用 WP1 确定性提取器，再原子创建 run、候选和版本 1。不得接收客户端 candidates、citations、parser blocks 或 scope。
2. `GET /v1/projects/{projectId}/extraction-runs?limit=...`：按创建时间与 ID 稳定倒序列出当前 scope 的 run 摘要及最新审核版本。
3. `GET /v1/projects/{projectId}/extraction-runs/{runId}?reviewVersion=...`：不传版本时读取最新完整快照；传版本时读取该不可变历史版本，并返回完整候选、引用、状态和可用版本号。
4. `POST /v1/projects/{projectId}/extraction-runs/{runId}/decisions`：携带 `expectedReviewVersion`，只改变一个候选并返回新版本；过期版本返回 `REVIEW_CONFLICT`，调用方必须重新读取，禁止盲重试。

WP2B 分为两个顺序包。WP2B-0 先完成 `approved proposal object -> verified parser blocks -> deterministic candidates -> review run` 的服务器端 preparation，以及 list/latest/history 只读模型；WP2B-1 才实现持久幂等创建、HTTP/OpenAPI 和 runtime 装配。任何 fake preparer 或浏览器提交 candidates/blocks 的测试只能验证 adapter 形状，不能关闭 WP2B。

本包不实现模型提取、批量审核、删除/撤销、多人角色矩阵、WBS handoff、浏览器 UI、连接器、通知、市场监控或知识提升。M1 仍冻结为单部署 actor；项目成员共享与角色矩阵必须在后续权限设计中单独验证，不能从当前 RLS 的“非空 actor”推断为已支持多人协作。

### 状态、数据与安全边界

- 所有端点复用 M1-01 的 Bearer/Basic 认证；organization/workspace/client/actor 只从服务器认证状态注入，body、path 和 query 不得覆盖 scope。
- JSON body 限制为单个、UTF-8、无重复 key、无未知字段的对象，并在读取时限制总字节数。reason、comment 和 replacement text 均有明确长度上限；创建 body 不能包含 scope、候选、引用或 parser blocks。
- 所有成功与失败 JSON 响应都带 `Cache-Control: no-store`。错误只返回稳定 `code/message/retryable/requestId`，不得回显 SQL、DSN、token、parser block、候选原文、理由、评论或路径。
- `REVIEW_NOT_FOUND` 同时覆盖不存在、跨 workspace/client/project 和不可见 run，避免存在性差异；`CANDIDATE_NOT_FOUND` 只在已确认可见的 run 内返回。
- 当前 schema 没有 extraction-run 幂等键或提取 manifest，因此 WP2B-0 不公开创建 HTTP。WP2B-1 必须先增加受 RLS、备份恢复和冲突检查约束的持久幂等事实，再允许 `Idempotency-Key`；不得用内存缓存、前端防重复或“先 list 再猜”冒充安全重试。决定请求以 `expectedReviewVersion` 实现一次状态迁移；网络结果不确定时先 GET 对账，`REVIEW_CONFLICT` 的 `retryable` 为 false。
- repository 暂时不可用返回可重试的 `REPOSITORY_FAILURE`；验证错误、冲突和 not-found 不可重试。`asyncio.CancelledError` 必须穿透认证、body 读取、提取、读取和写入，不得转成 JSON 500。
- PostgreSQL 读取必须在事务本地设置 workspace/client/project/actor scope，依赖 forced RLS，并验证 run、candidate、snapshot 和版本完整性。跨 scope 不可见或任何不完整/不一致行都失败关闭。
- OpenAPI 是受审静态契约；运行时 schema 必须与提交文件逐字节语义一致，不由 FastAPI 自动推导替代。

### WP2B-0 工作包与验收

- Task ID：`M1-02`；基线提交：`a43319c97ab274e8ed17d8ba4ed2db73e7f8bedb`。
- Package P：owned paths 为新增正式 parser 模块、服务器 preparation orchestration、对象存储的校验式只读能力、批准 proposal source reader 及其专项测试。输入只有服务器 scope、project ID 与可选 expected proposal identity；输出为已复核 source、零 warning 的 parser result、确定性 candidates 和已提交 review run。禁止 HTTP、OpenAPI、migration、registry、顶层 CI 和前端。
- Package R：owned paths 为 review service/read DTO、PostgreSQL 只读查询及其 unit/runtime tests。读取最新或指定不可变 snapshot，按 ordinal 返回完整 candidate/citation/status/replacement 和截至该版本的 decision；不存在、跨 scope、非创建 actor 和不完整行统一失败关闭。禁止 preparation、HTTP、OpenAPI、migration、registry、顶层 CI 和前端。
- 主集成者 owned paths 为本节文档、跨包导出、冲突修复、最终测试和提交；`project-status.json`、手工编辑 `docs/PROJECT_STATUS.md`、现有 `0001/0002` migration、import 写入/backup/cleanup 行为、根前端、连接器、跨项目检索、真实客户资料、凭据和新依赖仍禁止修改。
- Frozen preparation：对象 key 只能来自 RLS 范围内的当前 approved proposal 行；在共享对象锁内验证 size/hash，parser 输出 hash 必须再次匹配。任何 parser warning、unsupported block、零候选、source 变化或取消都不得创建部分 run。
- Provisional safety limits：运行时文本最多 20,000 行、5,000 个 parser blocks、5,000 个表格单元格、100 个 warnings，单个文本块或表格单元格最多 100,000 字符，单个 DOCX XML part 最多 10 MiB，最终 review candidates 最多 1,000 个。block 与 warning 在追加前限流；DOCX 表格行数受单元格上限约束，零单元格行失败关闭。默认 parser 和 preparation 信任边界都必须在数据库写入前拒绝超限结果。这些是防资源耗尽的工程护栏，尚未由真实方案规模验证；后续只能依据脱敏样本分布和运行指标调整，不能静默放宽。
- Frozen read output：run 摘要、最新 version、可用连续 versions，以及选定完整 snapshot 的 candidates/citations/status/replacement/decision。GET 读取不得加 `FOR UPDATE`，不得产生 audit 或其他写入。
- Acceptance commands：focused parser/preparation/read service/adapter tests；PostgreSQL 18.4 下真实 source/read、跨 scope/actor、latest/history/完整性测试；现有 WP1/WP2A 与备份恢复回归；完整 `apps/api` suite；`compileall`、文档/progress 与 `git diff --check`。同一提交 CI 与非实现者复审通过后只关闭 WP2B-0，不能关闭 WP2B 或 M1-02。
- Reviewer role：非实现者检查对象路径和 hash 不可伪造、parser 失败关闭、候选只由服务器生成、RLS/actor 读取、历史 as-of decision、完整性、取消传播、敏感错误净化和既有导入/恢复无回归。实现者测试不能替代最终审查。

### WP2B-0 验收证据

- 最终受审实现提交为 `dd2bc4f74558cdbb41bdecd2bd9e90be4223b57b`；GitHub Actions run [31593977192](https://github.com/Patch-A/marketops-ai-workbench/actions/runs/31593977192) 的 `headSha` 一致，`static-checks` 与 PostgreSQL 18.4 runtime 均通过。CI 静态环境通过 316 项、跳过 37 项，PostgreSQL runtime 通过 11/11 项。
- 本地完整 `apps/api` 套件通过 316 项、跳过 38 项；focused preparation/parser 通过 26/26 项。M1-02 PostgreSQL 静态契约确认 5 张 forced-RLS append-only 表，并拒绝 7 种弱化变异；`compileall`、文档、progress 和 `git diff --check` 均通过。
- 服务器只从当前 approved proposal 行取得对象 identity，在共享锁内复核对象大小和 SHA-256，再执行受限 Markdown/plain-text/DOCX parser 与确定性提取；客户端不能提交 candidates、citations、parser blocks、对象路径或 scope。读取模型支持 latest/history 完整快照和截至所选版本的 decision。
- 多轮修复关闭 parser 资源上限、DOCX `sectPr` 计数、异常净化、公开/私有异常伪装、异常子类绕过和重复来源坐标覆盖风险。最终独立 reviewer 对重复 outer/table-cell 坐标失败路径、合法引用回归及此前问题逐项复查后返回 `CLEAN APPROVE`，未发现 P0/P1/P2。
- 本证据只关闭 WP2B-0。当前仍没有 HTTP/OpenAPI、持久 Idempotency-Key、runtime pool 装配或浏览器审核 UI；WP2B 和 `M1-02` 均未完成。合成 fixture 只能证明受限工程行为，不能证明真实需求、ROI、节省时间、重复使用、生产容量或付费意愿。

### 风险、未知与最小实验

- 已确认：WP2A 数据库与恢复门禁已通过；现有 FastAPI 有冻结 OpenAPI、认证、`no-store` 和错误 envelope 模式，但 runtime 尚无 proposal 对象读取、正式 parser、review read model 或持久 create idempotency。只加路由不能形成可信审核 API。
- 合理推测：四个操作足以支撑首个审核 UI；尚无真实使用证据证明用户需要完整版本时间线而不是只看最新版本，也没有证据证明同步解析能覆盖真实文件规模。
- 共同未知：parser blocks 的真实规模、审核冲突频率、单次候选数、用户是否理解 fact/hypothesis 与引用坐标。工程测试只固定一个变量：两个请求使用同一 `expectedReviewVersion`；成功信号是一个 201、一个 409，GET 只显示一个新完整版本且无部分写入。真实可用性实验再固定“是否显示引用”并记录审核耗时、保留/修改/拒绝率和漏项报告。
- 失败条件：任何客户端可注入候选/引用、对象 hash 未复核、parser warning 被静默忽略、跨 scope/actor 可见、历史 as-of 状态不完整、错误泄露用户内容、取消被吞，或后续 HTTP blind retry 产生重复 run，都阻止相应工作包通过。

### WP2B-1 工作包与验收

- Task ID：`M1-02`；基线提交：`34795ffe6f71f698a56bc4d508338310efd24da5`。主集成者 owned paths 为新增 `0003` migration、review preparation/service/PostgreSQL/HTTP/runtime、冻结 OpenAPI、备份恢复适配、对应测试和本节文档。`project-status.json`、手工编辑 `docs/PROJECT_STATUS.md`、现有 `0001/0002` migration、前端、连接器、跨项目检索、真实客户资料、凭据和未审查依赖仍禁止修改。
- Job：认证项目操作者以当前 approved proposal identity 和持久 Idempotency-Key 创建或安全重放审核 run，读取 latest/history，并以 `expectedReviewVersion` 逐条决定；人工决定仍由操作者控制，API 不自动批准候选。
- Persistent idempotency：新增 append-only、forced-RLS 的 extraction-run request 事实，唯一键至少包含 workspace/client/project 与规范化 key，并保存 expected proposal version/hash、run ID、actor 和创建时间。认领请求、创建 run/candidates/snapshot/audit 必须同事务提交；失败不得留下占位事实。相同 key 与相同 source 重放原 run；相同 key 与不同 source 返回不可重试 `IDEMPOTENCY_CONFLICT`；并发相同请求只能有一个 run。`0003` 同时前向收紧既有 `audit_events` policy，使审核 run、候选、决定与审计事件均只对创建 actor 可见；这不构成多人角色矩阵。
- HTTP input：创建 body 只允许 `expectedProposalVersionId` 与 `expectedProposalSha256`，并要求单个 `Idempotency-Key`；决定 body 只允许 `expectedReviewVersion`、`candidateId`、`action`、`reason`、可选 `comment`/`replacementText`。所有 JSON 必须是单个 UTF-8 object、无重复 key、无未知字段并受字节/字段长度限制。path/query UUID 与正整数必须 canonical。
- HTTP output：创建返回 run、版本 1 与 `replayed`；list 返回稳定倒序摘要；detail 返回 selected/latest 可用版本、完整 candidate/citation/status 与截至该版本的 decision；决定返回新版本与 decision。成功和失败均 `Cache-Control: no-store`，创建/决定带 `Location`。认证 scope 只来自服务器状态，错误不得回显正文、reason/comment、路径、SQL、DSN 或 token。
- Runtime：import 与 review adapter 共享一个生命周期内的 asyncpg pool；source reader、review repository、preparation service 和 review service 由服务器装配。启动失败与取消必须关闭已创建资源，关闭只执行一次。OpenAPI 是逐字节受审静态契约。
- Acceptance：focused migration/idempotency/service/HTTP/OpenAPI/runtime tests；PostgreSQL 18.4 的同 key 重放、不同 source 冲突、并发单 run、失败原子性、RLS/actor 隔离、四端点与备份恢复；恢复门禁必须覆盖 v1/v2 bundle 升级到当前 schema、非空 v3 request 行集，并在 v3 隔离恢复库通过新 `ReviewService` 重放原 run 的 version 1。完整 API suite、`compileall`、文档/progress、`git diff --check`、同 SHA CI 和非实现者复审全部通过后，只关闭 WP2B-1 API，不关闭 UI 或 `M1-02`。
- Non-goals：浏览器 UI、批量/撤销审核、多人角色矩阵、WBS handoff、模型提取、生产容量和市场价值验证。最小实验固定相同 project/source/key 的两个并发创建；成功信号是一个事实、一个 run、两个等价响应且无孤立行。

### WP2B-1 验收证据

- 最终受审实现提交为 `4466246a2b410c575fef7e621f710a504962e8ab`；GitHub Actions run [31606515703](https://github.com/Patch-A/marketops-ai-workbench/actions/runs/31606515703) 的 `headSha` 一致，`static-checks` 与 `m1-01-runtime` 均通过，包括锁定 FastAPI 运行时、PostgreSQL 18.4、浏览器回归和 v1/v2/v3 备份恢复。
- 本地完整 `apps/api` 套件通过 353 项、跳过 55 项；跳过项来自本机缺少 FastAPI、PostgreSQL、Linux `flock` 或 Windows 符号链接权限，不能单独作为通过证据。同一提交 CI 补齐了 FastAPI/PostgreSQL/Linux 运行路径。
- 两份专项独立复审和最终 SHA 非实现者收口复审均返回 `CLEAN APPROVE`，未发现 P0/P1/P2。该结论只关闭 WP2B-1 API，不关闭浏览器审核 UI 或 `M1-02`。
- 审计随后发现 UI 首次创建 run 需要 approved proposal SHA，而项目详情尚未暴露该服务器事实；后续兼容性修复只补齐项目详情 SHA 读取链和 review API client，不改变 WP2B-1 数据库/HTTP 写入语义。

### WP2B-2 浏览器审核 UI 工作包

- Task ID：`M1-02`；基线提交：`3b1b3252b6bf7263be7382fed150491997442576`。
- Owned paths：根目录 `index.html`、`styles.css`、`app.js`、新增的浏览器审核模块、聚焦的浏览器 UI 契约测试，以及本节文档。
- Forbidden paths：`project-status.json`、手工编辑 `docs/PROJECT_STATUS.md`、顶层 CI、数据库 migration、review domain/database/HTTP 写入语义、连接器、跨项目检索、真实客户文件、凭据和未审查依赖。
- Frozen inputs：服务器恢复的项目详情、approved proposal `versionId`/`sha256`，以及 `project-import.js` 中已验证的四个 review API client 方法。
- Outputs：可创建或安全重放审核 run、浏览完整引用候选、按 `expectedReviewVersion` 接受/修改/拒绝、读取历史版本并在冲突后从 GET 对账的浏览器工作台。浏览器不得提交候选、引用、对象路径、parser blocks 或认证 scope。
- UI boundary：采用用户已确认的黑/白/紫、完整深浅主题和紧凑控制台语言；来源证据、提取候选、人工决定与历史版本必须可区分。动效只表达同步、选中、提交和冲突恢复状态，并尊重 `prefers-reduced-motion`。AI 助手保持次要，不替代人工批准。
- Acceptance：聚焦的确定性 JavaScript 契约测试；既有 M1-01 浏览器 cutover gate；聚焦 M1-02 HTTP/service/read/preparation 测试；Chromium 桌面与移动端的 loading/empty/error/latest/history/modify/reject/conflict 状态检查；无横向溢出、移动触控目标不小于 44px、无控制台错误；`git diff --check`；最后由非实现者复审。
- Reviewer role：非实现者验证客户端只使用服务器 proposal identity、幂等重放不会复制 run、决定严格携带当前版本、409 后读取服务器事实而非盲重试、历史版本不被伪装为最新状态、错误不泄露敏感内容，以及既有导入/刷新恢复不回归。实现者自测不能替代最终审查。
- Completion boundary：本工作包通过也只补齐 M1-02 产品切片。未经授权/脱敏真实方案可用性实验、注册表验收证据、自检和主集成者批准前，`M1-02` 继续保持 `in_progress`。

### WP2B-2 实现者证据与独立复审入口

- 当前候选实现文件为 `index.html`、`styles.css`、`app.js`、`review-workbench.js`；聚焦单元测试为 `tests/review-workbench.test.mjs`。合成 Chromium 门禁位于 `scripts/run_m1_02_review_browser_gate.mjs`，门禁契约测试位于 `apps/api/tests/test_m1_02_review_browser_gate.py`。
- 合成 Chromium 门禁只模拟已冻结的 HTTP 响应契约，不读取客户资料、不上传文件、不使用凭据、不访问外部网络。它验证浏览器状态机和请求边界，不能替代 FastAPI/PostgreSQL runtime、真实客户可用性或价值证据。
- 2026-08-13 最终实现者本地检查通过：`node --test tests/review-workbench.test.mjs` 为 6/6；实际 Chrome 门禁 12/12；浏览器门禁、M1-01 静态资源与聚焦 HTTP 契约在当前默认解释器下 15 项通过、27 项因缺少可选 FastAPI 能力跳过；`node scripts/progress.mjs check`、`git diff --check` 与敏感模式扫描通过。此前完整 `apps/api` 回归为 356 项通过、55 项因本机运行能力跳过。
- Headless Chrome 门禁的 12 个布尔结果全部为 `true`：不确定 create 后同 key 重放且只有一个 run、引用渲染、接受/修改/拒绝、409 后 GET 对账、不确定 decision 后 GET 对账、历史只读、主题切换、375/1440 响应式、无 console failure、无外部请求和请求边界合规。
- `finesse-ui` detector 对 `index.html`、`styles.css`、`app.js` 与 `review-workbench.js` 返回零 finding；该结果是静态辅助证据，不能替代实际渲染和人工视觉判断。
- 最终受审实现提交为 `2bbc0fa11ba814248f4353ec2c01d05f657fabcb`；GitHub Actions run [31712779704](https://github.com/Patch-A/marketops-ai-workbench/actions/runs/31712779704) 的 `headSha` 一致，`static-checks` 与 PostgreSQL 18.4/FastAPI/Chromium runtime job 均通过。
- 首轮独立复审发现两个 P1：浏览器 `localStorage` 持久化幂等键违反冻结边界；新 run 详情 GET 失败时旧 detail 可能被误判为成功对账并清除稳定 key。最终实现改用页面内 `Map` 保存未决 key，并要求详情 GET 成功且返回目标 run ID 才算对账；单元与 Chromium 门禁分别固定无持久浏览器存储和旧 detail 失败路径。
- 同一非实现者对修复 tree `834c23128aecd16b9a059d0c85eb829b0b607230` 增量复审后返回 `CLEAN APPROVE`，未发现新增 P0/P1/P2。确认同一页面内不确定 create 仍以同 key 重放，历史、冲突和不确定 decision 对账没有回归。
- 残余限制：刷新页面后，现有协议不能跨会话精确重放一个仍未决的随机 create key；页面只能从服务器 run 列表恢复已经提交的事实。若以后要求跨刷新精确重放，需要服务端可恢复 operation identity 或按 proposal 查询待决操作，不能用浏览器持久存储补洞。
- WP2B-2 工程门禁在当时已通过；后续授权验证结果见下方最终验收记录。

## 2026-08-15 Final bounded acceptance

- The previous statement that no authorized proposal evidence was available is superseded. Two user-authorized, repository-external derived proposal sets entered the isolated HTTP/PostgreSQL review flow.
- The latest immutable snapshots contain 58 cited candidates and 58 explicit decisions: 20 approve, 22 modify, and 16 reject. No item remains pending; every candidate retains a source location and source quote.
- Existing extraction, persistence, HTTP, browser, isolation, recovery, and failure-path packages had already passed independent non-implementer review. The later authorized run exercised the accepted workflow without adding source files or source text to the repository.
- This closes M1-02's bounded technical acceptance. It does not establish demand, time savings, omission reduction, repeat use, production capacity, ROI, or willingness to pay; those remain later market-validation questions.
