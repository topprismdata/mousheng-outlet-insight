# 官方 InsightRequest 与 InsightPackage 契约示例

本目录存储被文档、CLI 与测试套件共同引用的**权威标准契约示例**。

## 文件清单

| 文件 | 用途 | 引用方 |
|---|---|---|
| [`examples/insight-request.example.json`](./insight-request.example.json)  | 标准 InsightRequest 输入契约示例 | `SKILL.md`, `mousheng-outlet-insight_PRD.md`, `scripts/test_doc_examples.py`, `scripts/insight_cli.py` |
| [`examples/insight-package.example.json`](./insight-package.example.json) | 标准 InsightPackage 输出契约示例 (由 request 确定性生成) | `mousheng-outlet-insight_PRD.md`, `scripts/test_doc_examples.py` |

## 防漂移保证

1. **唯一权威源**：示例文件仅在本目录维护，避免双副本漂移。
2. **自动校验**：`scripts/test_doc_examples.py` 直接读取并验证这些 JSON 文件 100% 通过 Schema，并验证二者精确配对。
3. **单向链接**：所有文档必须链接这些文件，不得在文档中重写示例。

## 运行时使用

### 1. 从项目根目录 (`/Users/ghb/outlet-insight/`) 运行 CLI：
```bash
python skill/scripts/insight_cli.py \
    --request examples/insight-request.example.json \
    --out /tmp/package.json \
    --validate \
    --pretty
```

### 2. 从组件脚本目录 (`skill/scripts/`) 运行 CLI：
```bash
cd skill/scripts
python insight_cli.py \
    --request ../../examples/insight-request.example.json \
    --out /tmp/package.json \
    --validate \
    --pretty
```

### 3. 通过 Python SDK 调用：
```python
import json
import sys
sys.path.insert(0, "skill/scripts")
from insight import synthesize_from_request

with open("examples/insight-request.example.json") as f:
    request = json.load(f)

# 传入并执行确定性合成
package = synthesize_from_request(
    request,
    analysis_id="mousheng-prd-example-001",
    generated_at="2026-08-25T12:00:00Z"
)
```
