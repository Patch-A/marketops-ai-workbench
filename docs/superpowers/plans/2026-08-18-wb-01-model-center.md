# WB-01 模型中心基础工作包

状态：`in_progress`

## 任务与边界

- 任务 ID：`WB-01`
- 基线提交：`a8672235ae07f93611df90b79790a713ceac2d36`
- 主责任：主集成者
- 目标：为私有部署提供可审计的模型配置元数据、停用和确定性任务匹配入口。
- Job statement：对于需要在不同研究、审核和内容任务间选择模型的工作台操作者，帮助其登记可用模型并看到可解释的匹配/备用建议，从而在模型调用前明确能力和数据边界。

## owned paths

- `apps/api/marketops_models/`
- `apps/api/tests/test_wb_01_model_center.py`
- `apps/api/marketops_import/http.py`
- `apps/api/main.py`
- `apps/api/tests/test_project_import_http.py`
- `project-import.js`
- `model-center.js`
- `index.html`
- `app.js`
- `styles.css`
- `docs/superpowers/plans/2026-08-18-wb-01-model-center.md`

## forbidden paths

- 不写入真实 API Key、Token、客户文件或模型响应。
- 不修改 M2-04 评估结果，不把模型配置验收写成真实模型调用或 ROI 证据。
- 不引入新的运行时依赖，不自动外发数据，不修改顶层 CI。
- `project-status.json`、`docs/PROJECT_STATUS.md` 只由主集成者在验收通过后更新。

## 冻结输入与输出

输入：供应商、显示名称、OpenAI-compatible endpoint、模型名、能力标签、上下文窗口、区域、数据保留说明、服务端环境变量名（只保存引用，不接收明文密钥）。

输出：模型列表、单模型详情、添加/编辑/停用状态、任务类型匹配建议及备用模型理由。响应不得包含明文凭据、环境变量值或外部模型响应。

状态：`unverified -> enabled | disabled | failed`；配置变更采用 `expectedVersion` 乐观冲突。

## 验收命令

```text
python -m unittest apps.api.tests.test_wb_01_model_center -v
python -m compileall -q apps/api/marketops_models apps/api/marketops_import/http.py apps/api/main.py
node --check project-import.js
node --check model-center.js
node --check app.js
node scripts/progress.mjs check
git diff --check
```

## 明确不声称

本工作包不实现真实模型调用、健康探测、费用/延迟观测、自动回退执行、生图、GEO、外部平台连接或生产级密钥管理。上述能力必须在独立工作包中取得运行证据。

## reviewer role

非实现者只读复核 API 字段边界、凭据不泄露、作用域隔离、失败/冲突路径和移动端状态；Harness 只能提供辅助审查，不能替代主集成者验收。
