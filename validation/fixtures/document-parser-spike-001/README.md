# DOC-PARSER-001

这是 `M0-02` 使用的合成 DOCX 测试件，不含真实品牌、客户或个人资料。

## 文件

- `ai-event-brief.docx`：包含标题层级、正文和两张显式表头表格。
- `ground-truth.json`：定义必须保留的标题、表格数量和坐标检查值。

## 重新生成

生成器需要 `python-docx`，应用运行时和 CI 验收不需要该依赖。生成器会规范化 OOXML ZIP 的文件顺序与时间戳，使相同内容得到稳定哈希。

```powershell
python scripts/generate_document_parser_fixture.py validation/fixtures/document-parser-spike-001/ai-event-brief.docx
python scripts/check_document_parser.py --write
python scripts/check_document_parser.py --check
python scripts/audit_docx_components.py validation/fixtures/document-parser-spike-001/ai-event-brief.docx --output validation/results/m0-02-docx-components.json
python scripts/audit_docx_components.py validation/fixtures/document-parser-spike-001/ai-event-brief.docx --check validation/results/m0-02-docx-components.json
```

测试件的 OOXML 组件审计和 Skill 结构交叉检查已通过。当前环境未完成逐页渲染检查，具体限制见 [`M0_DOCUMENT_PARSING_SPIKE.md`](../../../docs/M0_DOCUMENT_PARSING_SPIKE.md)。
