# M0-02 文档解析技术验证

状态：`Completed with documented limitations`
验证日期：2026-08-08

## 1. 验证问题

本技术验证回答一个窄问题：MarketOps 能否把当前验证集中的 Markdown、CSV 和 DOCX 转为顺序稳定、可引用、可回到源文件位置的结构化块，并在无法解析时给出明确失败。

它不证明生产级办公文档兼容性，也不证明知识库、RAG、OCR 或模型提取效果。

## 2. 输出契约

每次解析输出包含：

- `source.path`、格式、字节数和 SHA-256。来源坐标只对该哈希版本有效。
- `blocks`，按源文件正文顺序保存标题、段落和表格。
- `sectionPath`，保存块所在的标题层级。
- `location`，按格式保存行号、CSV 行列或 DOCX OOXML 部件及段落/表格/单元格序号。
- `warnings`，保存已发现但没有静默猜测的复杂结构。

解析结果是派生数据，不是原件的替代品。原文件变化后必须重新解析，不能把旧坐标继续附在新版本上。

## 3. 已确认结果

以下是本仓库自动测试确认的事实，不是推测：

| 输入 | 已验证能力 | 证据 |
| --- | --- | --- |
| 合成活动方案 Markdown | 标题层级、段落、4 张表格、行号和章节路径 | `validation/results/m0-02-document-parser.json` |
| Dreamforce 2024 公开案例重构 | 公开事实仍位于“已确认的公开描述”章节，并保留行坐标 | 同上 |
| 合成活动排期 CSV | 15 个任务、表头、行号、列名和单元格坐标 | 同上 |
| 合成活动简报 DOCX | 4 个标题、7 个段落、2 张表格、显式表头和 OOXML 位置 | 同上 |
| 失败路径 | 空文件、不支持格式和损坏 DOCX 返回稳定错误码 | 同上 |

合成 DOCX 由脚本生成，并在连续两次构建中得到相同 SHA-256。表格的 `tblW`、`tblInd`、`tblGrid` 和每个 `tcW` 已通过结构审计。

在无法渲染的环境中，独立组件审计确认 20 类结构全部通过：OOXML ZIP 与 XML 完整性、必需部件、内容类型、内部与外部关系、活动内容、页面尺寸与页边距、精确样式参数、标题层级、显式表头与行设置、表格几何、直接格式化、图片替代文本、动态字段、批注与修订、正文编码、核心元数据和自定义属性。Skill 交叉检查另确认无障碍高/中/低风险项均为 0，样式直接格式化 Run 和段落均为 0。

## 4. 暂不支持

以下能力没有通过，不能在 M1 中暗示为已支持：

- PDF、扫描件和 OCR。
- 图片、图表、SmartArt、浮动形状和文本框正文。
- 页眉、页脚、脚注、尾注、批注和完整修订语义。
- 真实页码、版面坐标和跨页表格位置。
- 复杂合并单元格的业务语义恢复。基础解析器只暴露合并标记。
- XLSX、PPTX、网页归档和邮件附件。

遇到修订或文本框时，解析器只发出警告；它不会把当前提取结果声明为完整。

## 5. 视觉验证限制

`documents` Skill 要求新建 DOCX 渲染成逐页 PNG 后检查。此次环境没有 LibreOffice `soffice`，已安装的 Microsoft Word 又因本机 Office 类型库错误无法导出 PDF。因此没有完成 DOCX 逐页视觉检查。

组件审计结果为 `structuralStatus: passed`、`visualStatus: not_run_renderer_unavailable`。这不影响 XML 顺序、表格几何、来源坐标和失败路径的工程验收，但不能发现字体替换、实际换行、截字、重叠和分页问题，也不能宣称通过视觉渲染门槛。引入生产解析器或交付用户可见 DOCX 前，必须在可用的 Word/LibreOffice 环境补做渲染回归。

## 6. 技术决策

当前保留标准库基线解析器，理由是它能够验证数据契约，又没有新增运行时许可证和供应链风险。它不是生产方案。

在 `M0-05` 比较 Docling、Unstructured、Apache Tika 或格式专用解析器时，至少评估：

1. 中文 DOCX/PDF、表格、扫描件和复杂版式覆盖。
2. 来源坐标是否稳定并能绑定原文件版本。
3. 失败是否可观察，是否会静默丢内容。
4. 许可证、容器体积、CPU/内存、维护活跃度和安全更新。
5. 本地/私有部署及敏感文件不外传的可行性。

## 7. 复现

使用 Python 3.11 以上版本执行：

```powershell
python scripts/check_document_parser.py --check
python scripts/audit_docx_components.py validation/fixtures/document-parser-spike-001/ai-event-brief.docx --check validation/results/m0-02-docx-components.json
python scripts/document_parser_spike.py validation/fixtures/document-parser-spike-001/ai-event-brief.docx --pretty
```

只有自动检查、技术报告和进度登记一致时，`M0-02` 才能保持完成状态。
