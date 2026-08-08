# M1-01 项目与已批准方案导入切片

状态：`Client slice implemented; server-backed acceptance pending`

日期：`2026-08-09`

## 目的

验证第一条用户路径：用户创建项目，选择项目原始资料和已批准方案版本，系统计算源文件 SHA-256、保留两个文件，并在刷新后恢复项目摘要。

这不是 M1-01 的最终完成证据。当前仓库仍是静态原型，项目事实来源尚未迁移到 PostgreSQL；本切片用于验证前端交互、数据契约和浏览器失败路径。

## 已实现

- `project-import.js` 定义版本化项目记录、源文件元数据、已批准方案元数据、格式门禁、SHA-256 校验和 localStorage 记录适配器。
- 文件内容通过 IndexedDB `marketops-files-v1/files` 保存，项目记录只保存不可变文件 ID、哈希和批准时间。
- 导入对话框要求项目名称、原始资料、方案文件、正整数方案版本和人工批准确认。
- 支持 Markdown、CSV、基础 DOCX；不支持 PDF、扫描件、PPTX 等格式时保持表单并显示失败原因。
- 刷新时同时检查项目索引和两个 IndexedDB 文件 ID，缺少任一文件不会显示“已保留”。

## 契约检查

```powershell
node --check app.js
node --check project-import.js
node scripts/check_project_import.mjs
```

结果：契约测试通过，覆盖有效记录、批准版本门禁、文件保留门禁、哈希、格式门禁、localStorage upsert 和文件存在性失败路径。

## 浏览器检查

在 `http://127.0.0.1:4173/` 使用公开合成 fixture 完成：

1. `change-log.md` 作为项目原始资料，`proposal.md` 作为已批准方案 v3。
2. 成功状态显示项目名、两个文件名和“已保留”。
3. 刷新后同一摘要恢复，控制台没有 error/warn。
4. `ground-truth.json` 作为原始资料时，表单显示“不支持格式”，未创建项目。
5. 375px 视口无横向溢出；对话框宽度 322px，页面 scrollWidth 等于 clientWidth。默认桌面视口 1280px 同样无横向溢出。

这些浏览器结果验证交互行为，不证明服务器端持久化、跨浏览器访问、权限隔离、备份恢复或真实客户资料质量。

## 尚未通过的最终门槛

- PostgreSQL `Project`、`Artifact`、`ArtifactVersion` 表与迁移尚未实现。
- API 尚未提供创建项目、上传/保留源文件、选择批准版本和审计事件端点。
- 浏览器 localStorage/IndexedDB 只能作为临时原型适配器，不能替代产品的服务器事实来源。
- 未完成独立审查，因此 `M1-01` 必须保持 `in_progress`。

## 下一工作包

实现服务器端导入接口和 PostgreSQL 迁移，复用本切片的记录字段与错误语义；在服务器端验收通过前，不得把浏览器本地记录当作生产项目状态。
### Explicit prototype limits

- Retention verification now reads both IndexedDB records and recomputes SHA-256 against the project metadata.
- Browser format checks are filename-extension gates only. Parser and structural validation remain a server-backed acceptance requirement.
- The browser prototype has no workspace/client authorization boundary and must not receive real customer files.
- Two file writes and the local project index are not one atomic transaction; partial failures can leave orphaned local blobs until server cleanup exists.
