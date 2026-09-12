# PDF 证据问答：运行与验证

这条链路使用现有 Parser / Canonical IR、Fixed 512/64、BGE-M3 和 exact NumPy cosine。
结构用于命中后的上下文恢复，不参与评分。它已经有可执行代码与离线回归测试；真实答案质量
仍需要服务器上的模型和文档实验，不能由测试通过或引用校验通过推出。

## 1. 服务器准备

以下为 Linux/Bash 示例。提交并推送本次源码后，服务器才能通过 `git pull` 获取改动。
使用服务器已有虚拟环境、Parser 和模型缓存，不需要重新下载 BGE-M3。

```bash
git pull
python -m pip install --no-build-isolation -e '.[retrieval]'
export BGE_M3_MODEL_PATH=/data/rag/models/bge-m3
export BGE_M3_DEVICE=cuda
export SILICONFLOW_MODEL=Qwen/Qwen3.8-27B
read -rs -p 'SiliconFlow API key: ' SILICONFLOW_API_KEY
export SILICONFLOW_API_KEY
```

密钥只从进程环境变量读取，不会写入索引、配置、问答结果或日志。代码不会读取 `.env` 文件。
已经泄露到对话或其他位置的密钥应先更换。模型名按实际账户可用名称配置；代码不会静默更换模型。
硅基流动接口使用 [Chat Completions](https://docs.siliconflow.cn/docs/api/chat-completions-post)，
JSON object 输出、temperature=0、enable_thinking=false。

## 2. 保留可用证据的解析

已有可用 `document.ir.json` 可以直接进入下一步，不需要重跑 Parser。
需要处理结构失败时，显式启用恢复模式；示例使用服务器已安装的 PaddleOCR-VL profile：

```bash
docparser parse-local /data/rag/pdfs/example.pdf \
  --parser paddleocr-vl-1.6 --device cuda --recover-structure \
  --output /data/rag/qa-ir/example
```

也可以使用已有的 `docling-standard`。恢复规则目前有明确边界：

- **缺页**：保留页码占位与缺失标记，不生成不存在的正文；其他页面仍可输出。
- **表格 cell 越界或网格重叠**：不构造错误 Table。保留原始 block text；没有 block text
  时保留 cell 观察序列，不按无效行列索引拼出一个假表。
- **TABLE 缺少结构实体 / UNKNOWN 有文字**：保留 UNKNOWN 类型，以专门标记的 UNRESOLVED
  文字证据参与检索；不会变成已确认的 paragraph 或 table。
- 装饰内容仍不进入检索。坐标、身份和来源归属错误仍会失败，不会裁剪 bbox 或猜页码来通过校验。

`raw/preflight.json` 在 Parser 调用前保存；`raw/parse-result.json` 在成功取得 ParseResult 后、
完整 normalization 前保存。最初 Parser 完全崩溃或不能返回合法 neutral result，不属于这些恢复
规则的处理范围；已有 adapter 的 raw snapshot 仍可用于诊断。

恢复后的 IR 仍通过原有 schema/invariant。恢复不等于质量认证：结构质量门会拒绝把带恢复标记
的文档宣布为完整、可信的结构输出。QA 可以使用其中可追踪的部分，并携带缺页/不确定信息。

不带 `--recover-structure` 的严格解析及现有 A/B 基线仍可复现。对同一有效输入，恢复模式不改变
Fixed embedding text。对实际发生降级的文档，证据集合改变必须作为新的 ingestion 实验报告。

## 3. 建索引一次

```bash
docparser rag-index \
  --ir-root /data/rag/qa-ir \
  --model-path "$BGE_M3_MODEL_PATH" --device "$BGE_M3_DEVICE" \
  --output /data/rag/qa-index-v1
```

递归读取 `document.ir.json`；每个 document_id 只允许一个 revision。
索引保存 Fixed embedding chunks、来源范围、上下文源记录、向量和 manifest。
加载时核对文件摘要、模型摘要和 tokenizer 身份。模型/tokenizer 变更需要重建，不能混用向量。
已有索引目录不会被静默覆盖。源 PDF、模型、IR、索引、运行结果均放在 Git 外部。

## 4. 先检查上下文，再调用模型

```bash
docparser rag-ask '表中 Revenue 的数值是多少？' \
  --index /data/rag/qa-index-v1 \
  --model-path "$BGE_M3_MODEL_PATH" --device "$BGE_M3_DEVICE" \
  --context-only --output /data/rag/context-preview.qa.json

docparser rag-ask '表中 Revenue 的数值是多少？' \
  --index /data/rag/qa-index-v1 \
  --model-path "$BGE_M3_MODEL_PATH" --device "$BGE_M3_DEVICE" \
  --chat-model "$SILICONFLOW_MODEL" \
  --output /data/rag/qa-results/revenue.qa.json
```

请替换为文档实际包含的问题。第二条命令向配置的硅基流动接口发送**问题和选中的上下文**，
不会发送 PDF 文件、完整 IR 或整份索引。

默认 Top-K=5，上下文预算 4096 个 BGE tokenizer tokens，包含引用标签等上下文开销。
这不是 Qwen 完整 prompt 的精确 token 上限；生成模型实际 token 使用量以 API 返回的 usage 为准。
超出服务模型窗口时会报告 API 错误，不会静默截掉证据或切换模型。

默认 M1（不传 `--table-context`）的上下文行为：

1. 按 Fixed source token interval 去重。不同区间不会因为同属一个长 block 就被一起删除。
2. 同一次命中内保留原始 stream 顺序。UNRESOLVED 不获得猜测的相邻 block。
3. 先为命中证据分配预算，再尝试恢复完整短 block/表格片段（默认最多 768 tokens）。
4. 在预算允许时补入显式 caption、已 materialize 的 section heading 和 parser 标记的 header rows。
   没有显式关系时不猜 caption；未知 header role 不改成 column header。
5. 跨页表的不同 segment 保留各自内容和来源页，不能按 table_id 或相同文字把不同行删除。
   第二页片段可以引用第一页已有的显式表头。
6. 放不下的内容记录在 `omitted_source_ids`。M1 超大表的 Fixed 截断片段标记为不完整。
   M2 可选逻辑行恢复已实现，启用方式和真实验证步骤见第 8 节。

返回结果包含检索排名、索引 manifest、上下文参数与来源范围、回答、逐项引用和分阶段耗时。
引用精度为 BLOCK / TABLE_REGION，不能当成字符级或 cell 级定位。
`quote_start/quote_end` 是**已提交 evidence text 内的字符位置**，不是 PDF 字节位置。

- `ANSWERED`：每项 claim 有可解析的 evidence ID，原文摘录确实出现在提交的 evidence 中。
- `INSUFFICIENT_EVIDENCE`：未取得足够证据或模型拒答；不等于原 PDF 没有答案。
- `INVALID_RESPONSE`：JSON/引用 ID/摘录校验失败，结果不发布 claims，CLI 退出码为 3。
  HTTP、模型和文件错误的退出码为 2。
- `semantic_support_verified=false` 始终明确保留：摘录存在不等于摘录支持结论，数值对应关系
  和最终语义正确性仍需独立评估。不能因为这个校验通过就在简历里声称事实正确率达到某个值。

单题也支持重复指定 `--document-id`，例如 `--document-id doc_... --document-id doc_...`。
ID 从 Canonical IR 的 document_id 或 index manifest 的 documents 项取得；不传则搜索全库。
范围在 Top-K 之前生效，未知 ID 报错。范围是用户选择，不是企业权限控制。
远程模型的已分类失败现在保存为 `execution_error`，保留 retrieval/context，CLI 返回 2；
此时 `answer=null` 不代表 context-only 成功。代码/配置错误会停止运行，不会伪装成模型拒答。

## 5. 多个问题复用运行时

CLI 单次问答不重嵌入文档。批量问题用 Python API 复用同一 session，避免每个问题重新加载 BGE：

```python
from pathlib import Path
from docparser.application.qa import ask_document
from docparser.retrieval.answering import SiliconFlowChatModel
from docparser.retrieval.dense import BgeM3Runtime
from docparser.retrieval.index import load_evidence_index

runtime = BgeM3Runtime(Path('/data/rag/models/bge-m3'), device='cuda')
session = load_evidence_index(Path('/data/rag/qa-index-v1')).session(runtime)
model = SiliconFlowChatModel()
output = Path('/data/rag/qa-results')
output.mkdir(parents=True, exist_ok=True)
questions = ['替换为实际问题一', '替换为实际问题二']
for number, question in enumerate(questions):
    result = ask_document(question, session, model=model)
    (output / f'{number:03}.qa.json').write_text(
        result.model_dump_json(indent=2), encoding='utf-8'
    )
```

## 6. 有限实验与独立评估

**正式问答实验使用 `rag-batch` 的固定题目清单。** 每题 JSONL 只含请求信息：

```json
{"query_id":"dev-001","question":"表中 Revenue 的数值是多少？","document_ids":[]}
{"query_id":"dev-002","question":"该数值对应什么统计期间？","document_ids":[]}
```

相同问题文字允许具有不同 query_id。document_ids 非空表示用户限定的文档范围。
上述问题仅演示格式，不是新增 benchmark。对于现有 DEV-21，将已有开发文件投影为
query_id（原 benchmark_query_id）、question、空 document_ids；不要把 answer/gold page 等字段
带入运行输入，也不要读取剩余 79 题做设计。OHR 全库比较始终使用空 document_ids。

```bash
docparser rag-batch \
  --questions /data/rag/dev21.qa-questions.jsonl \
  --index /data/rag/qa-index-v1 \
  --model-path "$BGE_M3_MODEL_PATH" --device "$BGE_M3_DEVICE" \
  --chat-model "$SILICONFLOW_MODEL" \
  --output /data/rag/qa-dev21-current
```

批次复用一个 BGE/session，顺序处理；无自动重试或模型切换。run.json 内保存完整题目清单、
索引/生成/上下文配置、prompt 摘要、逐题文件摘要和 RUNNING/COMPLETE 状态。
每题结果按 `00000.qa.json` 等序号保存，不把外部 query_id 当文件路径。
COMPLETE 表示所有请求均已记录，可能有失败；已分类服务失败记录后继续，最终退出码 2。
模型无效回答为 3；程序错误或进程中断留下未完成批次，不能用于完整实验评分。
输出目录必须为空，重跑写新目录，不能覆盖原始失败记录。

用 `--no-expand-context` 关闭完整源扩展和相关上下文补充，可以与默认配置做**上下文构造实验**。
索引、问题、Top-K、模型、总上下文预算保持一致；这不是新的 chunking A/B。
先分析 DEV-21，再冻结策略。79 条 holdout 不参与设计；最终评估必须使用同一完整候选语料，
分别报告 DEV-21、UNTOUCHED-79、FULL-100，不把已见开发集称为未见测试。

独立检查原始 PDF、答案与引用后，编写 JSONL judgment，query_id 从 QA 输出的
`retrieval.benchmark_query_id` 复制。下面只是格式示例，不是自动评分结果：

```json
{"query_id":"copy-from-qa-result","answerable":true,"answer_correct":true,"citations_support_all_claims":true,"annotator":"reviewer-name"}
```

`answerable` 指**原始检索语料是否足以回答**，不能因为本轮没检索到就标成 false。
拒答/无效回答的 `answer_correct` 和 `citations_support_all_claims` 应为 false。
只有完整的独立判断集合才能评估，不能漏掉失败项。

```bash
docparser rag-evaluate --results /data/rag/qa-dev21-current \
  --judgments /data/rag/qa-judgments.jsonl --output /data/rag/qa-metrics.json
```

有 run.json 时核对完整计划题目、实际文件、摘要、问题/范围与配置；删题、删结果、部分运行都会
拒绝完整评分。失败题也必须有独立判断，answer_correct/citations_support_all_claims 为 false；
answerable 仍由原始请求范围内的语料决定。错误不算成功拒答。

报告包含 denominator_scope=QUESTION_MANIFEST、execution_error_count、回答覆盖率、全问题中的正确且有支持答案比例、
已回答问题中的正确且有支持比例、全问题中的无支持回答比例，以及不可回答题的拒答率。
PageHit/MRR 继续由原有 retrieval evaluator 报告，不能替代这里的答案指标。

没有 run.json 的旧式目录仍可以探索性评分，denominator_scope=RECORDED_RESULTS；
它无法证明有没有漏跑题目，不能当正式完整实验。开发顺序和发布目标见
[复杂文档库问答开发规范](COMPLEX_DOCUMENT_QA_SPEC.md)。

## 7. 验证命令

```bash
ruff check .
mypy
docparser schema check
python -m pytest
```

服务器可选真实 smoke：只发送仓库合成表格，不读取真实 PDF 或 OHR 题目，会调用一次付费 API：

```bash
export DOCPARSER_RUN_QA_SMOKE=1
python -m pytest tests/integration/test_evidence_qa_smoke.py \
  -o addopts='' -m network -q
```

这个 smoke 检查模型接口、实际 BGE 索引及一次带引用回答能否连通，不是复杂 PDF 准确率测试。
更换模型后需重新执行；远程服务的模型别名也不能视为永久固定的权重版本。

## 8. M2：同一检索结果上的表格证据恢复

M1 尚未真实运行不妨碍先部署 M2 代码；两种上下文策略均保留。先在服务器更新源码和安装依赖，
使用同一个新索引做对照。不要因为 M2 离线测试通过就宣称问答准确率已经提高。

**先验证本机实际 BGE tokenizer。** 以下只读本地模型缓存，使用合成中英表格；不调用远程 API，
不读取 PDF/OHR，且不加载 embedding 权重，不要求 GPU：

```bash
export BGE_M3_MODEL_PATH=/data/rag/models/bge-m3
DOCPARSER_RUN_TABLE_CONTEXT_SMOKE=1 python -m pytest \
  tests/integration/test_table_context_offsets.py -o addopts='' -q
```

若失败，保留失败记录；不要把 offset 错误改成“按数值找行”或换 tokenizer 后沿用旧向量。
接着用第 3 节的 `rag-index` 命令把 `--output` 改为 `/data/rag/qa-index-m2`。
这会保存新的 `fixed-evidence-index@1.1.0` 来源映射。无需重跑已有有效 IR 的 Parser。
建索引输出 `table source maps: ALIGNED=N, ...`，同样信息保存在 manifest 的
`table_alignment_counts`。这是源 block 对齐统计；跨页同一表可有多个 source。
`OFFSETS_UNAVAILABLE/TOKEN_MISMATCH/INVALID_OFFSETS/RENDER_MISMATCH` 都需要单独检查。
旧 1.0 索引能读取，但没有 map 时不会自动得到 M2 行恢复。

**先看一次真实问题的 context-only 输出：**

```bash
docparser rag-ask '替换为文档实际包含的表格问题' \
  --index /data/rag/qa-index-m2 \
  --model-path "$BGE_M3_MODEL_PATH" --device "$BGE_M3_DEVICE" \
  --table-context --context-only --output /data/rag/context-m2.qa.json
```

核对原 PDF：命中数值的整行、明确列头、合并单元格 anchor、单位/明确脚注是否同时在 evidence
中；跨页内容是否分别标注正确页区。`covered_cell_ids` 表示这些行覆盖的 cells，不表示系统
已经判定“这个数字属于这个答案”。`row_band_complete` 与语义正确性、整表完整性也不是一回事。

M2 行为：

1. 用 Fixed 命中 token 区间与整段源的真实 offsets 相交定位行；不搜索重复数值。
2. 补齐命中行、rowspan 闭包及明确 COLUMN_HEADER/BOTH 的行；行标题不会触发整表作为表头。
3. 必要行、显式 caption/footnote 一起分配预算，再尝试补全短表和可选 heading。
4. 跨页只使用明确续表关系。无关系/无 header axis/无可靠 offsets 均保留显式不确定性。
5. 超预算恢复失败时保留带警告的原命中片段，仍放不下则记 omission。警告不能当作成功恢复。
6. `char_start/end` 相对于 sources 中原始渲染文本，`quote_start/end` 相对于提交的 evidence.text；
   前者和后者是不同坐标。行/segment/cell IDs 跟随引用输出，bbox 精度仍为 TABLE_REGION。

**一次 M1 基线 + 一次 M2，最多再做一次冻结后的复验：**

```bash
docparser rag-batch \
  --questions /data/rag/dev21.qa-questions.jsonl \
  --index /data/rag/qa-index-m2 \
  --model-path "$BGE_M3_MODEL_PATH" --device "$BGE_M3_DEVICE" \
  --chat-model "$SILICONFLOW_MODEL" --output /data/rag/dev21-m1

docparser rag-batch \
  --questions /data/rag/dev21.qa-questions.jsonl \
  --index /data/rag/qa-index-m2 \
  --model-path "$BGE_M3_MODEL_PATH" --device "$BGE_M3_DEVICE" \
  --chat-model "$SILICONFLOW_MODEL" --table-context --output /data/rag/dev21-m2
```

两条 batch 命令会将选中的问题/上下文发送给已配置的远程模型。模型、问题、索引、Top-K、总预算
和 prompt 均保持一致。确认逐题 retrieval 的 chunk IDs/rank/score 完全相同；检查 M2 实际
恢复数量、不完整/缺失条件数量、context token 数和耗时。新增来源映射不会带来 PageHit/MRR 改善。

分别依据 PDF 独立判断 M1/M2 的答案正确性和引用支持，再用第 6 节 `rag-evaluate` 分别评分。
不能把已验证的 quote 命中率替代答案正确率。保留全部失败/拒答，不靠调预算反复追逐 DEV-21。
没有新增独立数值检查器；算术与条件一致性属于 M3。

如果大多数表仍为未知 header、缺失 caption 或 Parser 读错数字，M2 不能补造这些事实。
记录实际错误类别与来源，再决定是否实施 spec 的条件性派生关联；本轮没有引入这些猜测。
79 条 holdout 继续保持不读取、不用于调优；算法冻结后的最终报告规则仍按主 spec 执行。
