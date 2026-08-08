# M0 验证样本集

本目录定义 MarketOps AI Workbench 的技术验证输入。M0 使用公开案例重构和合成项目，不要求个人用户拥有完整历史项目档案。真实项目资料如未来可获得，只能放在被 Git 忽略的 `validation/private/` 中。

## 样本目标

M0-01 的完整退出条件是：

- 两份来源可核对、明确区分事实/重构/未知项的 AI 时代公开案例重构。
- 一份明确标注为合成数据的可执行测试项目。
- 合成项目覆盖方案、排期、变更和复盘四类输入；公开案例共同覆盖后期变更、数据采集、人工批准和持续迭代。
- `node scripts/check-validation-set.mjs public` 通过。

这些样本用于测试数据契约和后续技术验证，不能当作市场需求、真实用户行为或产品效果证据。商业 Go 仍需要实时任务、重复使用和付款行为。

## 目录

```text
validation/
  manifest.json                         公开样本清单
  fixtures/synthetic-b2b-event-001/    合成测试项目
  fixtures/document-parser-spike-001/  可复现 DOCX 解析测试件
  public-cases/                         来源可核对的公开案例重构
  results/                              技术探针的可重复结果摘要
  templates/historical-project-record.json
  private/                              可选的本地真实资料，始终忽略
```

## 可选的真实项目导入

1. 复制 [`templates/historical-project-record.json`](templates/historical-project-record.json) 到 `validation/private/manifest.json`，按其注释字段建立两条项目记录。
2. 只复制经过许可且完成脱敏的项目副本，不复制原始客户文件。
3. 将真实名称、联系人、邮箱、电话、合同号、账号、精确报价、个人日程和不可公开商业信息替换为稳定占位符。
4. 保留任务依赖、相对工期、决策理由、变更关系和复盘因果；否则样本失去技术验证价值。
5. 由资料所有者确认允许用于本地开发和评估。默认禁止公开发布、模型训练和跨客户复用。
6. 运行可选的完整自检：

```powershell
node scripts/check-validation-set.mjs full
```

## 自检层级

- `public`：M0 和 CI 使用，验证合成项目、两份公开案例重构、来源、已知/未知项和 AI 时代维度。
- `full`：可选的本地增强检查，在 `public` 基础上要求至少两份获批、脱敏的真实历史项目；不再是 M0 退出条件。

脚本只能验证记录和文件是否符合契约，不能判断网页陈述是否独立真实，也不能替代资料所有者授权。公开案例用于技术基准，实时 Brief/项目用于价值验证，两者不能混为一谈。

M0-02 的文档解析结果、边界和复现方法见 [`M0_DOCUMENT_PARSING_SPIKE.md`](../docs/M0_DOCUMENT_PARSING_SPIKE.md)。
