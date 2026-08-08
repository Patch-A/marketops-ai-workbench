# Contributing

感谢参与 MarketOps AI Workbench。

## 开发原则

1. 先解决一个可验收的营销项目状态，不以增加 Agent 数量作为产品进展。
2. 事实、模型假设、人工决定和实际结果必须在数据和界面上分开。
3. 原始客户资料默认只属于当前项目，跨项目复用必须经过用户确认。
4. 外部发送、正式方案修改、预算和高风险决策默认需要人工批准。
5. 新依赖必须记录许可证、维护状态、用途和可替换方案。

## 变更流程

- 先创建 Issue，说明用户问题、输入、输出和验收方式。
- 开始工作前阅读 [`project-status.json`](project-status.json) 和 [`docs/PROJECT_EXECUTION_PLAN.md`](docs/PROJECT_EXECUTION_PLAN.md)。一个人在同一时间只能推进一个 `in_progress` 任务。
- 只有验收检查通过后，才能把任务标为 `completed`：记录完成日期、可核验证据和 `passed` 自检；随后运行 `node scripts/progress.mjs render` 与 `node scripts/progress.mjs check`。不要手工编辑生成的 [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md)。
- 一个 Pull Request 只处理一个明确问题。
- 行为变更必须补测试或可重复的验收步骤。
- UI 变更必须验证桌面与移动视口、键盘焦点、空状态、加载状态和错误状态。
- 数据结构变更必须说明迁移、删除、权限和审计影响。

## 禁止提交

- API 密钥、Token、账号密码和 `.env` 文件。
- 未脱敏的客户资料、合同、报价和个人信息。
- 未确认允许再分发的论文、PDF、图片、字体和第三方 Skill。
- 许可证不兼容或来源不明的代码。
