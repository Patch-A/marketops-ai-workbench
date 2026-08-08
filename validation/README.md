# M0 验证样本集

本目录定义 MarketOps AI Workbench 的技术验证输入。公开仓库只保存合成测试项目、样本契约和脱敏模板；真实历史项目必须放在被 Git 忽略的 `validation/private/` 中。

## 样本目标

M0-01 的完整退出条件是：

- 两份已经获得使用许可的脱敏历史项目。
- 一份明确标注为合成数据的可执行测试项目。
- 每份样本覆盖方案、排期、变更和复盘四类输入。
- 每份样本有用途、保留、发布和个人信息状态记录。
- `node scripts/check-validation-set.mjs full` 通过。

当前已提交的合成项目用于测试数据契约和后续技术验证，不能当作市场需求或产品效果证据。

## 目录

```text
validation/
  manifest.json                         公开样本清单
  fixtures/synthetic-b2b-event-001/    合成测试项目
  templates/historical-project-record.json
  private/                              本地历史项目，始终忽略
```

## 历史项目导入流程

1. 复制 [`templates/historical-project-record.json`](templates/historical-project-record.json) 到 `validation/private/manifest.json`，按其注释字段建立两条项目记录。
2. 只复制经过许可且完成脱敏的项目副本，不复制原始客户文件。
3. 将真实名称、联系人、邮箱、电话、合同号、账号、精确报价、个人日程和不可公开商业信息替换为稳定占位符。
4. 保留任务依赖、相对工期、决策理由、变更关系和复盘因果；否则样本失去技术验证价值。
5. 由资料所有者确认允许用于本地开发和评估。默认禁止公开发布、模型训练和跨客户复用。
6. 运行完整自检：

```powershell
node scripts/check-validation-set.mjs full
```

## 自检层级

- `public`：CI 使用，验证公开清单、合成项目、文件完整性和结构字段。
- `full`：本地里程碑验收使用，在 `public` 基础上要求至少两份获批、脱敏的历史项目。

脚本只能验证记录和文件是否符合契约，不能判断脱敏是否真的彻底，也不能替代资料所有者授权。完成 M0-01 前必须人工抽查历史文件。
