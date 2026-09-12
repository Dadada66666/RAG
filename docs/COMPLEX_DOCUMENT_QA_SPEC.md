# 复杂文档库证据问答：开发与验收规范

版本：`complex-document-qa/1.0.0`。日期：2026-09-12。
审计基线：HEAD `b13b3ecb3e79ea12829aaba45e0609e529d25e6e` 加当前工作区 Evidence QA 修改。
本文是接下来开发的执行规范。`MUST` 是验收要求，`DEFER` 不进入本轮实现。
本文不代表以下所有能力已实现；各阶段状态见最后的交付表。

## 1. 用户目标与产品边界

目标：用户选择一份或一组复杂 PDF，或搜索整个文档库，得到条件完整、有原文依据的回答。
系统必须分别展示“有没有答”“是否正确”“引用是否支持”“运行是否失败”，不能用单个分数掩盖问题。

第一版支持目标：

| 类型 | 必须保留的信息 | 验收问题示例（合成示例，不是 OHR truth） |
|---|---|---|
| 正文事实 | 对象、事实、必要限定语、来源 | 某方法使用什么训练集？ |
| 表格查询 | 行键、列键、期间、单位、数值、来源 | 2024 年海外业务收入是多少？ |
| 同文档跨页证据 | 所需片段及各自来源；关系不凭空补全 | 指标如何定义，实验结果是多少？ |
| 有限数值计算 | 两个已引用操作数、单位、操作、结果 | 从 100 增至 120 的增长率是多少？ |
| 缺证据/条件冲突 | 缺少什么、不能确定什么 | 没有披露的地区收入是多少？ |

复杂版式包含扫描、多栏、合并单元格及跨页表；“支持处理”不等于每份文档都准确。
任意图表读数、任意公式推导、开放式跨文档推理、多轮对话记忆为 DEFER。
文档选择是用户提供的范围，不是系统读取 benchmark 答案后推导的范围。

## 2. 当前源码审计与优先级

| 优先级 | 源码证据 | 当前行为及下游后果 | 最小改动 |
|---|---|---|---|
| P1 / M1 | `retrieval/index.py::QASearchSession.retrieve` | 全索引检索；query ID 只哈希问题。多文档同名指标可能混用，重复题文本不能保留独立评估身份 | 文档 ID 预过滤；外部 query ID 原样保留 |
| P1 / M1 | `application/qa.py::ask_document`, `evaluation/qa.py::evaluate_qa` | API 异常无完整 QA 结果；评分只核对现存结果和标注，不核对计划题目 | 固定题目清单、逐题持久化、明确失败状态、完整分母评分 |
| P1 / M2 | `retrieval/context.py::ContextBuilder`, `chunking.py::_render_table_row` | 超大表仍有 token 截断；行渲染只输出 anchor cells；合并单元格关系没有完整带入回答 | 来源位置到逻辑行的精确映射，必要行带与 header/单位上下文 |
| P1 / M3 | `retrieval/answering.py::answer_from_context` | 只检查 quote membership；真实摘录仍能伴随错误年份/指标答案 | 数值来源记录、有限计算；语义正确性继续独立评审 |
| P2 / M1,M4 | `dense.py::_local_directory_digest`, `exact_cosine_retrieval`; `context.py::fits` | 新进程重复模型文件哈希/加载；每题检查整库向量范数并全排序；预算尝试反复 tokenize 已选上下文 | 首先复用 session 并测量冷/热耗时；证实热点后做等价优化 |

保留：Canonical ID/provenance/坐标，严格校验，UNRESOLVED 隔离，两套 chunker 的 evidence parity，
Fixed 512/64、BGE-M3、exact cosine、显式结构恢复、已实现的索引和引用链路。
不把 Structure 2.1 优于 Fixed 当目标，不改变现有 OHR truth，不扩张 Canonical schema 来存 QA 派生状态。

## 3. 解析模型与系统责任

| 问题 | 代码证据/判定方法 | 系统责任与边界 |
|---|---|---|
| `<th>` 没有轴语义 | Paddle `mapping.py::table_cells_from_html` 将显式 header 保留为 UNKNOWN | 保留原始 header；不能把所有 `<th>` 宣布为列头 |
| Docling 已给出表头轴 | Docling `mapping.py::_table` 使用 row_header/column_header | 保留并利用明确角色，不重做已有模型能力 |
| caption/续表关系为空 | 两个 mapping 只传递可识别的显式关系 | 先对照 raw payload 判断上游有没有；映射丢失才修 adapter |
| OCR 数字错误、错列但 grid 合法 | `recovery.py` 只识别缺页/网格越界或重叠等已知失败 | schema 无法证明内容正确；回放原始 PDF/区域，记录真实错误，按切片评估 Parser |
| 读取顺序未知或错误 | `sections.py` 只沿已确认 IN_FLOW 构造平级 section | 不给 unresolved 排序；IN_FLOW 也不等于人工验证正确 |
| 解析器整体异常 | Paddle adapter 在形成完整 ParseResult 前异常会结束调用 | 原始证据可获得时保留；M4 批量导入记录每份文档成功/失败，不能把失败当空文档 |
| 只有图，没有可用文字 | `_render_block` 的 Figure 路径只有已有 text | 当前不能可靠读图；不暗中启用新视觉模型 |

模型限制不能通过把 unknown 填成“合理值”解决。原生 PDF text 也是证据，不是自动 ground truth。
修 Parser 的必要条件：真实 raw/PDF 证明信息已经存在但映射丢失，或有可重现的通用正确性错误。
如果只有模型识别能力不足，报告切片限制；可用现有第二 Parser 做离线对比，但不凭空自动选优。
不为一张坏表更换整套 IR，不把降级为文字称为恢复了行列语义。

## 4. 架构与控制变量

```text
离线：PDF → 现有 Parser → neutral evidence → Canonical IR（严格/显式恢复）
     → Fixed embedding chunks + 来源映射 → 一次 BGE embedding → 本地持久化索引

在线：用户问题 + 用户指定 document_ids
     → 同一索引内预过滤 → exact dense Top-K
     → 有预算的来源/表格上下文 → 已配置生成模型
     → 摘录检查、后续有限数值检查 → 答案/拒答/失败 + 引用

评估：固定问题清单 → 每题结果（包括错误）→ 独立判断 → 完整分母指标
```

产品范围过滤与 OHR 全库检索是两个协议。OHR MUST 使用固定全库，不把 truth.document_name
送给产品预过滤器。新增上下文算法 MUST 在相同 IR、query、embedding、排名、Top-K、prompt、
生成配置、上下文预算下比较。修改 prompt、数值协议或解析结果属于另外的实验。
Parent 继续不参与 dense ranking；不实现父子分数融合。

## 5. M1：可信运行基线与文档范围（第一开发增量）

### 5.1 请求与范围

`QAQuestion = {query_id, question, document_ids[]}`。ID 由输入清单提供，不能用题目文本替换。
空 document_ids 表示全库；非空集合在评分前筛选候选。未知文档 ID 报请求错误，不回退全库。
重复的题目文本可以有不同 query_id 和范围。范围保存到 QAResult，引用必须落在所选文档内。
范围过滤不是权限系统；未来企业权限仍需独立的授权入口。

### 5.2 批量运行与产物

新入口 `rag-batch` 读取无答案的 questions.jsonl，单个 BGE runtime/session 顺序运行。
新输出目录包含 `run.json`、原样结构化题目清单、按序号命名的 `*.qa.json`；不将 query_id 当路径。
manifest 保存题目、索引、上下文配置、生成配置、prompt 版本/摘要；密钥不入产物。
生成器传输/服务协议失败作为 `execution_error` 保存，保留已经完成的 retrieval/context。
模型输出无效 JSON/引用仍是 INVALID_RESPONSE；失败和拒答不可混为一谈。
程序缺陷、损坏索引、错误运行配置 MUST 报错；不能用 `except Exception: continue` 伪造成功运行。
进程中断留下未完成 run；评分必须拒绝把部分产物当完整实验。
M1 不实现自动重试、并发、调度、复杂断点恢复；新一轮运行写新目录，避免覆盖付费结果。

### 5.3 评估

批量评估 MUST 核对完整题目 ID、问题文本、范围、输出状态及 run 配置。
`计划题目 = 已记录结果 = 独立判断`，缺一项即无完整报告。
错误也进入 question_count、answer_coverage 和正确且支持率分母，并单列 execution_error_count。
answerable 是原始语料在请求范围内是否能回答，不能根据本次检索结果反向标注。
已有无清单的单次评分只保留为探索用途，标注其不保证计划题目覆盖。
运行输入不包含 answer、gold page、gold table/cell；评分资料不进入生成 prompt。

M1 验收：多文档同名指标不越界；重复题文本保留独立 ID；中间一次 API 超时后后续题可运行；
超时题有上下文且留在分母；漏跑/删结果拒绝完整评分；只嵌入问题，不重新嵌入文档。

M1 当前产物形式：题目清单内嵌在 run.json，包含完整问题、范围和 ID；逐题保存文件摘要。
COMPLETE 表示所有请求都已记录，可能包含执行错误，不代表所有问题都回答成功。
CLI 遇到已记录的服务失败返回退出码 2；INVALID_RESPONSE 返回 3；不把进程退出码当题目分母。

## 6. M2：表格证据恢复（不改 Fixed embedding）

2026-09-12 执行说明：用户明确要求在尚未运行 M1 的情况下先开发 M2。
因此先交付代码与离线契约验证，真实 M1 基线及 M2 效果验证仍待服务器完成。
M2 通过 `--table-context` 显式启用；默认继续 M1。两条路径使用同一个新建索引、同一检索结果。

建立 retrieval-derived source map：同一源渲染文本的 char/token interval → table_id、segment_id、
logical row band、cell IDs。用实际冻结 tokenizer 的 offset 对齐，不逐行重新 tokenize 再拼接，
不在文档中用数字字符串反查 cell。无法可靠对齐时保留不完整状态，不声称命中了正确 cell。

算法：

1. 合并命中来源区间，但保留同一长 block 的不相邻区间与不同跨页 segment。
2. 短表/片段能放入预算则完整保留。长表取命中相交的逻辑行。
3. 沿 rowspan 扩成最小完整 row band；必要 anchor/合并表头保留同一个 cell ID，不能复制成新事实。
4. 加入明确的列头链、行键、单位与显式 caption/footnote；row-header 不能使整张表被当成 header rows。
5. 只沿已确认的续表关系扩展；所有行段分别保留页区，不能给跨页内容一个虚构 bbox。
6. 预算优先给直接证据及解释它必需的条件，再给可选 section/caption；不能静默只留下裸数值。
7. 大于整个预算的 row band 标记不完整；缺少关系时保持 UNKNOWN。

派生 caption association 只在真实未绑定 caption 阻碍问答后启用：同页、明确 caption 类型和
Table/Tab. 标记、几何邻近、一对一、无歧义；规则/阈值/依据写入派生记录，单独测关联 precision。
禁止在 Canonical 中冒充 Parser fact。多栏竞争、上下都有表、跨页 caption 均可保留未绑定。
header-axis 推断同理：HTML `<th>` 是 header 证据，不足以独立证明 COLUMN_HEADER。

先实现显式结构路径，后续派生关联为条件性工作，不是 M2 所有输入的强制前置。
预处理按表建立 row/cell 索引，避免每行扫描全部 cells；查询只访问命中源和关系，不能扫描全 IR。
记录 char/token 两类位置及 tokenizer identity；bbox 只有 table region 时诚实标 TABLE_REGION。

M2 验收：中英混排、重复数值、多级表头、跨页续表、rowspan 超过目标预算、未知 header，
均用合成契约测试和独立真实 PDF 证据检查；合成通过不代替真实 BGE offset 验证。

### 6.1 M2 实现契约与实际边界

实现位于 `retrieval/table_context.py`、`context.py`；Canonical IR、Fixed chunker version、
embedding text、retriever、Top-K 与生成 system prompt 均不因开关改变。

- `fixed-evidence-index@1.1.0` 把派生 map 存在 `sources.jsonl`，由已有文件摘要保护；manifest
  的 `table_alignment_counts` 统计 **TABLE source block**，不是 table entity 数或准确率。
  同一个新索引可运行 M1/M2。旧 1.0 索引仍可加载，但没有 map 时 M2 明确回到不完整源片段；
  正式 M2 实验须在新目录建索引。
- char 位置来自确定性 row rendering 的累计长度，允许 cell 内含换行；token 位置来自**整段源**
  的 tokenizer offsets，包括 IN_FLOW 编码所用的尾部换行。必须与 Fixed 的 token IDs 完全一致。
  fast tokenizer 不能提供有效 offsets、offsets 非单调/越界/零长或 token IDs 不同，均不推算行号。
- 先恢复直接命中的最小 row bands 和条件，再尝试完整短源扩展。列头只接受 Canonical 的
  COLUMN_HEADER/BOTH 对应 `header_row_indices`；ROW_HEADER 通过命中整行及 rowspan 保留。
  单位只来自原始行、明确 caption 或 `FOOTNOTE_OF`，没有独立单位推断。
- 仅通过 `continued_from_segment_id` / `continues_to_segment_id` 的显式连通关系跨 source
  恢复行与表头。相同 table ID 不独立授权跨片段补行。保留每个 source 的原始字符片段和页区。
- `ContextEvidence` 保存 `char_start/end`、`token_start/end`、`row_indices`、`segment_ids`、
  `covered_cell_ids` 和 `row_band_complete`。char 为渲染源坐标，token 为相交 token 包络；
  token 跨行时两行的包络可重叠。`covered_cell_ids` 是所含行覆盖的 cell 集合，**不是 quote
  精确指向某个 cell 的语义断言**。引用仍仅承诺 TABLE_REGION/BLOCK 几何精度。
- `row_band_complete=true` 只说明所需行带/表头行在显式连通来源中完整，不能推出列轴正确、
  注释齐全、OCR 数值正确或整表完整。UNKNOWN header、缺失条件、缺失续表行均有独立标记。
- 预算决策包含标签与警告。行/表头/明确 caption/footnote 一起尝试装入；失败则提交带
  `TABLE_RESTORATION_BUDGET_EXCEEDED` 的原命中片段，连片段也放不下则记录 omission。
  不做字符串数值查找、caption 邻近猜测或 `<th>` 轴语义推断。
- `--no-expand-context --table-context` 仍执行必要行带与条件恢复；它只关闭可选整源、heading
  等扩展。移除 `--table-context` 才回到 M1 对照路径。

新增预处理按 cells 排序/合并 span 区间，成本约为 `O(C log C + sum(row_span) + R×columns)`，
另加源编码、逐行 offset 二分查找与 segment/source 映射成本；这些都在建索引时完成。
session 缓存源 token/行索引。查询按命中 interval 二分找行，然后访问明确相连的 source，
不扫描 DocumentIR。当前上下文候选合并和整段预算 tokenize 随候选数重复执行；不是线性复杂度
的通用大表引擎。实际延迟待 M4 测量，再决定是否需要增量预算或更细的 interval 索引。

真实模型边界：Parser 没有输出的数字/单位/列轴/续表关系不会被此算法恢复；未检索到的证据
也不会凭空出现。M2 的待验证目标是**给定相同 hits 时答案与引用支持的改善**，不是 PageHit/MRR
提升，更不是保证任意复杂文档的问答正确性。服务器有限验证步骤见运行指南第 8 节。

## 7. M3：条件与数值答案

派生回答记录包含对象、指标、期间、数值、单位及每项来源。只对当前问题必要条件要求记录，
不生成万能事实图谱。来源检查核验引用和已定位 cell 的一致性；实体/年份语义映射可能仍由模型出错。
明确区分 QUOTE_MEMBERSHIP、SOURCE_VALUE_CHECK、ARITHMETIC_CHECK、人工语义支持判断。
不得用数值检查通过把 semantic_support_verified 自动设为 true。

有限计算仅支持差值、比率、增长率及显式单位换算；操作数必须指向已提交证据中的具体值。
程序计算，不执行模型代码；零分母、币种/期间不匹配、单位未知时不给伪精确结果。
允许模型结合多处证据；不确定或冲突应说明缺什么，不把没检索到当原文不存在。
这一增量单独冻结 prompt/answer contract，并报告模型格式失败及拒答变化。

## 8. M4：运行效率、导入稳定性与演示

首先量出 cold start（模型加载/哈希/源 tokenize）、warm retrieval、context、generation 的耗时、
文档数/页数/块数/向量数、峰值内存和 API usage。延迟含错误请求，不能只报告成功题。
M1 复用 runtime 后，再根据热点决定：预计算 row lookup、减少相同 context 的重复 tokenize、
加载时验证不可变向量并复用验证结果、保持分数和 tie-break 完全一致的 Top-K 选择。
未经测量不替换 exact cosine。模型文件哈希缓存不能仅依赖目录名字或模型别名。

另一个已见开销是 QAResult.context.sources 保留命中 source 的完整 text，超长来源可能重复序列化。
M4 测量后可把不参与 prompt 的完整源留在索引，以 digest/source ID 引用；不得丢掉实际提交的 evidence。

批量导入复用现有 parser 实例，逐文档保存成功/部分/失败状态及原始产物路径；不丢失失败文件。
索引一次、问题多次复用；文档修订需要新的来源映射。embedding 复用只针对确切相同输入和模型摘要。
删除旧文档/权限隔离/增量更新需要明确版本与授权语义，不能用“已有 document_id”宣称企业能力。

问答验收后再完成轻量演示：选择文档 → 问题 → 回答/拒答 → 点击引用定位本地 PDF 页区。
文件定位使用 source digest/受控目录映射；引用只能指向实际产物。图像显示不等于图像理解。
企业扩展顺序：真实小规模用户试用 → 文档生命周期与权限 → 已测瓶颈处并发/存储。
Kafka/Kubernetes/Ray/微服务/S3/调度/自动多 Parser 路由/GraphRAG 为 DEFER。

## 9. 有限实验、发布门槛与停止条件

M1 先跑 DEV-21 当前端到端基线，逐题对照原 PDF、IR、检索和 context，归因 ingestion/retrieval/
context/generation/transport。21 题已经是开发集；不得重复称为独立检验。
M2 只进行一次基线、一次主要改进、一次复验；必要正确性修复需记录原因，不无限调 token 大小。
排名一致的 context A/B 必须复用检索结果或核对排名/分数完全一致。缺失关系的派生算法单独做消融。

OHR 未覆盖或样本极少的跨页计算、不可回答问题，需要另建少量人工核对的专项病例，明确标 DEV/功能测试，
不能混进 OHR 分母，也不能据此宣称未见文档泛化。没有此类独立测试就不发布对应质量宣称。

冻结后一次评估 UNTOUCHED-79；全部文档准备允许仅用 source manifest，不读留出问题/答案做设计。
所有系统使用相同完整候选语料。FULL-100 和 DEV-21 从同一冻结协议重新报告，不拼接历史小库结果。
分别报告每题 paired win/tie/loss、原始分子/分母和文档切片；21/79 小样本不能宣称普遍领先。
TABLE slice 不等于 canonical table ground truth；PageHit 不等于 Recall；不追求把 11 个 TABLE 标签
都强制命中实体表。企业财务文档能力要另有未见文档测试，不能靠 academic OHR 外推。

发布必须满足：

- 所有请求有终态；来源引用不越所选范围；失败不丢失；引用不存在时不发布 claims。
- 报告 supported-correct/all、supported-correct/answered、coverage、unsupported、error、拒答及耗时。
- v1 目标（尚未达到的验收目标）：在声明支持的、独立标注可回答题中，正确且有支持比例 >=80%；
  已回答题中正确且有支持比例 >=90%。同时公布覆盖率和原始计数，不能靠删题/拒答达标。
- 表格、跨页、计算分别报告；未测切片不标稳定。目标不根据留出结果下调；未达标保留实验状态。
- 企业 SLA、生产正确率及大规模吞吐没有测量就不承诺。演示可用、实验有效、企业可用是不同结论。

## 10. 开发纪律、状态与最小测试

不写为了通过 schema 的猜测，不对每个函数包异常，不增加没有使用者的接口层。
只在外部服务边界分类可预期运行错误；内部程序错误暴露。校验只服务于来源正确、范围正确和实验完整。
每个增量：先更新本 spec 的实际状态，再实现、定向测试、现有离线回归、类型/lint/schema 检查。
文档修改不重跑模型；真实验证单列，不能把 mock 结果写成性能或准确率提升。

| 增量 | 修改位置 | 当前状态 |
|---|---|---|
| 原始 QA 基础 | recovery/index/context/answering、独立评估 | 已离线验证；452 passed 的历史记录见 EVIDENCE_QA_IMPLEMENTATION |
| M1 范围＋完整运行 | index.py, application/qa.py, 新 qa_batch.py, evaluation/qa.py, CLI | 已实现并离线验证；真实模型基线待服务器运行 |
| M2 表格证据恢复 | context.py、table_context.py、索引/CLI/引用字段、相关测试 | 已实现并离线验证；真实 tokenizer、M1 基线与 M2 效果验证待服务器运行 |
| M3 条件与计算 | answering.py、有限算术模块、独立评估 | 待实现，不能称语义已验证 |
| M4 效率/导入/演示 | 已测热点、导入编排、轻量界面 | 待真实测量后实现 |

服务器 BGE/PDF 资产不在本机；本机只运行离线契约测试。密钥仅用环境变量，不入 Git/日志。
M1 历史回归：463 passed、1 skipped、10 deselected；Mypy 152 文件通过。
M2 本轮回归：480 passed、2 skipped、10 deselected；Mypy 155 文件通过，Ruff 与 schema check
通过，新增/修改的 9 个 M2 Python 文件通过格式检查。默认配置覆盖门槛通过（86.41%，仅指原有
IR/quality/fallback/robust 模块集合，并非 QA 或 M2 模块覆盖率）。
这些是软件契约结果，不是复杂 PDF 准确率或吞吐结果。
对照文档：[EVIDENCE_QA_GUIDE.md](EVIDENCE_QA_GUIDE.md)、[RAG_CHUNK_SPEC.md](RAG_CHUNK_SPEC.md)、
[RAG_EVALUATION_SPEC.md](RAG_EVALUATION_SPEC.md)。历史的 hybrid/平台阶段不构成当前必做需求。
