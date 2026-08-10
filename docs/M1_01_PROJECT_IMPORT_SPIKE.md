# M1-01 项目与已批准方案导入切片

状态：`Completed for the bounded M1-01 acceptance contract`

日期：`2026-08-10`

## 目的

验证第一条用户路径：用户创建项目，选择项目原始资料和已批准方案版本，服务器计算 SHA-256、保留两个不可变文件版本，并在浏览器刷新后从 PostgreSQL 恢复项目摘要。

M1-01 的受限验收已完成：提交 `cd3ff09d3b84fbd71342c5e3728cc45898918d25` 通过真实 PostgreSQL 18.4、FastAPI 与 Chromium 联合门禁，GitHub Actions run `31397741577` 的 `static-checks` 与 `m1-01-runtime` 均成功，非实现者最终复审为 `CLEAN APPROVE`，未发现 P0/P1/P2。

## 已实现

- `project-import.js` 是严格的同源 Server API client，规范化浏览器 MIME、校验响应，并为不确定网络结果保留同一 idempotency key。
- 项目事实、文件元数据和批准信息只从 `GET /v1/projects` 与 `GET /v1/projects/{projectId}` 读取；localStorage 和 IndexedDB 已从项目事实路径移除。
- 导入对话框要求项目名称、原始资料、方案文件、正整数方案版本和人工批准确认。
- 支持 Markdown、CSV、基础 DOCX；不支持 PDF、扫描件、PPTX 等格式时保持表单并显示失败原因。
- POST 成功后页面仍要完成一次项目详情 GET 才能显示“服务器已保留”；刷新不会重复 POST。
- FastAPI 白名单提供四个静态资产和首页；浏览器原生 Basic 与程序化 Bearer 都由服务器解析为固定部署 scope，凭据不进入页面、URL 或浏览器存储。

## 契约检查

```powershell
node --check app.js
node --check project-import.js
node scripts/check_project_import.mjs
```

结果：本地契约检查通过，覆盖严格 POST/GET、MIME 规范化、刷新恢复、稳定重试、错误映射、本地事实源独立和响应字段漂移。它不替代真实浏览器或数据库门禁。

## 历史浏览器检查

在 `http://127.0.0.1:4173/` 使用公开合成 fixture 完成：

1. `change-log.md` 作为项目原始资料，`proposal.md` 作为已批准方案 v3。
2. 成功状态显示项目名、两个文件名和“已保留”。
3. 刷新后同一摘要恢复，控制台没有 error/warn。
4. `ground-truth.json` 作为原始资料时，表单显示“不支持格式”，未创建项目。
5. 375px 视口无横向溢出；对话框宽度 322px，页面 scrollWidth 等于 clientWidth。默认桌面视口 1280px 同样无横向溢出。

这些结果属于已被替换的浏览器本地原型，只保留为历史交互证据，不能证明当前 Server API cutover。当前真实浏览器门禁见 runtime integration plan 的 WP5D。

## 最终门槛证据

- WP1-WP5C 的 PostgreSQL、RLS、幂等、重启、连接丢失回滚、应用级逻辑备份恢复和 orphan cleanup 证据均保留在 runtime integration plan 中。
- WP5D 在 Linux CI 中使用真实 PostgreSQL 18.4、哈希锁 Python runtime、FastAPI 和 Chromium 完成上传、POST 后 GET、刷新、根路径恢复、本地存储污染/清除、网络失败与稳定重试、多视口及凭据暴露检查。
- 浏览器门禁的 12 项聚焦单测通过；OpenAPI 通过 23 项守卫和 37 项变异；前端 Server API 契约检查通过。
- 非实现者对 `cd3ff09` 和同 SHA run `31397741577` 的最终复审为 `CLEAN APPROVE`，确认 `BaseExceptionGroup` 清理边界和服务器启动失败后的 scope cleanup 均由可执行失败路径覆盖。
- 即使 M1-01 完成，也只证明合成数据上的单用户私有部署导入闭环，不证明生产认证、跨浏览器、需求、ROI、节省时间、复用或付费。

## 下一任务

进入 M1-02：从已批准方案提取交付物、里程碑、约束和假设，保留来源引用，并要求用户逐条确认、修改或拒绝。M1-02 不负责自动生成完整排期；可编辑 WBS 与确定性排期属于 M1-03。

### Explicit prototype limits

- Browser extension checks and MIME normalization are early feedback only; server parsing and structural validation remain authoritative.
- Basic authentication is a replaceable single-deployment boundary and requires TLS. It is not actor membership, a session system, or multi-user authorization.
- The local immutable-object adapter and cleanup protocol retain their documented Linux/same-filesystem/cooperative-lock limits.
- Synthetic fixtures establish engineering behavior only. Real customer material must not be used without consented handling, and no demand or value claim follows from this gate.

## Server contract progress (2026-08-09)

- `apps/api/migrations/0001_project_import.sql` freezes the PostgreSQL tenant scope, immutable artifact versions, approved-proposal pointer, audit table, deferred checks, and forced RLS policies.
- `scripts/check_m1_01_postgres_contract.py` passes 23 static guards and weakening mutations, including rejection of open project/artifact/audit policies, mutable artifact identity, and unapproved proposal selection. This is not proof that PostgreSQL 18.4 executes the migration correctly.
- `apps/api/marketops_import/service.py` defines the dependency-neutral transaction order, path-streamed SHA-256, 25 MiB limits, full UTF-8 traversal for Markdown/CSV, basic DOCX validation, scoped manifest idempotency, explicit authenticated approval, object integrity checks, UUID output, server-only scope, and stable failure codes. Twenty-five unit tests pass with public/synthetic files and fake adapters.
- `apps/api/openapi/project-import.openapi.yaml` freezes the authenticated multipart POST plus scoped list/detail reads, Basic/Bearer choices, `no-store`, Location, stable errors and display-only project metadata. Twenty-three contract guards and thirty-seven weakening mutations pass locally.
- The asynchronous HTTP, asyncpg, local-object, recovery, backup/restore, orphan-cleanup, and real Chromium packages have separately bounded evidence. Commit `cd3ff09` and GitHub Actions run `31397741577` passed the final non-implementer review with no P0/P1/P2 findings.
