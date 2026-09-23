# 分块、证据召回与问答质量：下一阶段开发执行计划

> 状态：第一关的离线执行入口已实现；真实索引的独立证据审核与真实评测仍待执行。
> 当前实现不改变默认分块策略或模型配置；本文不是效果达标声明。

## 1. 决策与基线

本项目的下一步采用“短而可检索的原文片段 + 可追溯的结构关系 + 召回后的证据恢复”。
结构分块仅作为待验证的索引表示；现行问答默认保持 Fixed 512/64。任何新方案都必须
先在相同语料和问题上证明证据召回与正确且有引用支持的回答同时改善，才可以提议改默认值。
无法用设计或离线单测保证准确率达到 85%，也不能把五份已反复调试的 PDF 当作泛化证明。

当前审计基线：`20de9d606134ea4310ff3d35801fe3c9830f50b0`。执行本计划前仍须重新检查
HEAD 和工作区，并以届时真实源码为准。现有 `ir-fixed-token@1.1.0` 是固定控制组，
`ir-structure-aware@2.4.0` 是结构实验组；新索引表示必须有新 chunker 版本、配置摘要和
独立索引目录，不能改写旧索引或冒用旧证据标签。

代码事实与待验证的解释应分开：

| 已确认的行为 | 对检索可能造成的影响；尚待消融确认 |
|---|---|
| `sections.py` 遇到 TITLE/HEADING 就生成平级 Section | 小标题可能把连续正文隔开 |
| 结构普通块仅在超过 `hard_max_tokens=8000` 时拆分，512 是软目标 | 中长解析块可能缺乏检索聚焦度 |
| 未知表头采用 `Row N / Column N` 的逐格表述 | 重复位置词可能挤走数据、增加表格候选 |
| 每个表格行组可重复标题、caption 和显式脚注 | 相同上下文可能稀释不同行组的区别 |
| Fixed 与 Structure 都建立 Canonical 来源区间并可在召回后恢复表格 | 保留结构不要求把完整结构写进每个嵌入文本 |

上一轮五文档、49 题的开发记录中，重排后证据首位命中为 Fixed 42/49、Structure
2.3 39/49、Structure 2.4 38/49；前五位分别为 47/49、49/49、48/49。这些是
**开发集观察**，不是正式未见文档结论。2.4 同时调整标题、表格序列化与脚注表达，
因此不能把变化全部归因于其中任意一项。历史实验中的部分 chunk 标签由旧版本映射而来；
正式晋级前必须按新索引独立复核。此前 `rag-retrieval-ab` 的页面指标是全库 dense
检索，其语料又只含选中查询涉及的文档；不能与限定文档的 Dense Top20→重排 Top5
证据指标放在同一列比较。

## 2. 不变量与改动边界

每一个候选分块策略必须满足：

- 共同输入为同一批最终 Canonical IR revision；`IN_FLOW` 只按已有阅读顺序处理，
  `UNRESOLVED` 独立可检索，装饰块与普通 `UNKNOWN` 不进入检索语料。不得从坐标猜顺序。
- Fixed 512/64 的文本、窗口边界、来源归属和版本完全不变；BGE-M3、本地模型摘要、
  exact cosine、Dense candidate 20、同一 reranker、最终 Top5、Context 4096、
  prompt 和问答评判不因分块实验变化。问题向量可共享。
- 对每份文档，`expected_retrieval_source_block_ids == fixed_covered == candidate_covered`；
  表格块必须在两种表示中有覆盖，存在可渲染表格时结构候选必须实际生成表格 chunk。
  此检查证明“块未漏”，**不证明**某个行、单元格、条件在检索文本中足够自足。
- 不改 Canonical IR、解析器、Section materializer、Quality/Fallback 或 OHR 选择结果。
  不推断未知表头、标题层级、caption 归属或阅读顺序。真实数据、索引、模型和运行结果留在 Git 外。
- 原文内容、页码、行列跨度、续表 segment、source token/char 区间、引用和 provenance
  均保持可解释；不把生成式说明或人工补充写成来源事实。PARENT 仍不参与向量排序。
- 一次提交只检验一个明确假设。任何候选未通过测试或实测门槛，固定默认不变，
  完整保存失败题目与原始运行产物。

## 3. 第一关：先冻结可信的比较协议

这一关先于新的分块修改。它修复**实验执行方式**，不调整 Dense 排名、重排、Context
或问答算法。复用 `QASearchSession` 的索引/上下文契约、现有精确检索与重排函数、
`evaluate_retrieval_gold`、
`rag-batch` 和 `rag-evaluate`。目前 `rag-retrieval-ab` 只产生全库 dense 的
PageHitRate/MRR 与 TableSourceExposure；它不执行 reranker，也不调用正式 chunk-gold
评估。因此不能把该命令的报告改名为“证据召回”或“问答准确率”。

现已在 application/CLI 边界增加一个薄的**离线评测入口**，仅负责：
读取已建好的两个 index 和同一问题清单；用同一 BGE-M3 对问题编码；分别输出 Dense
Top20 和 reranked Top5；读取各自索引绑定的 frozen gold；调用现有正式 evaluator；
记录逐题 rank 与 Context 结果。它复用现有 cosine、rerank、gold 判定与 Context 实现，
不会调用问答生成模型。已实现的命令为 `docparser rag-evidence-ab`，需要用户独立审核的
两份 gold；没有 gold 时不生成正式证据召回结果。

最小真实运行示例（路径均由用户提供；两个索引须用同一批 IR revision 建立）：

```bash
docparser rag-evidence-ab \
  --fixed-index <FIXED_INDEX> --structure-index <STRUCTURE_INDEX> \
  --queries <PREPARED_OHR_QUERIES_JSONL> \
  --fixed-gold <REVIEWED_FIXED_GOLD_JSON> \
  --structure-gold <REVIEWED_STRUCTURE_GOLD_JSON> \
  --model-path <LOCAL_BGE_M3> \
  --reranker-model-path <LOCAL_BGE_RERANKER_V2_M3> \
  --device cuda --reranker-device cuda \
  --scope corpus --output <NEW_EXTERNAL_RUN_DIR>
```

默认 `--scope corpus` 在完整索引上检索。单文档协议必须显式传
`--scope document --document-map <JSON>`，映射文件为
`{"academic/synthetic-report": "doc_..."}` 形式的 OHR document_name → Canonical
document_id；程序核对这些 ID 是否在两个索引中，绝不按 PDF 文件名猜测。真实
document_id 必须从已有索引/IR 读取，示例字符串不是可直接运行的真实 ID。
人工标注时从各索引的 `chunks.jsonl` 核对原文与 `chunk_id`；各自 gold 文件的
`index_identity` 必须由该索引的 `retrieval_gold_index_identity(index.manifest)` 生成，
`queries` 为完整计划问题清单，每题含 `query_id` 和至少一个人工认可的
`acceptable_chunk_ids`。两套索引需要分别审核，不能直接复用旧块 ID 或自动文本匹配结果。
结果目录分别保存两组 `dense.jsonl`、`reranked.jsonl`、`context.jsonl`、
`per-query.jsonl` 和指标 JSON；顶层 `run.json` 包含查询文件、两份 gold、模型和
索引身份，`comparison.json` 保存对照摘要及结构组减固定组的指标差值，
`paired.jsonl` 按问题并排记录两组结果。`per-query.jsonl` 的
`gold_span_overlap_in_context` 只是来源区间相交的诊断，
`evidence_sufficiency=REQUIRES_INDEPENDENT_REVIEW`；不能据此声称答案条件完整。
运行不覆盖非空目录。现有 `rag-retrieval-ab` 仍负责页面诊断，两套报告不能混称。

冻结要求：

1. 一份问题清单和原始 PDF/IR revision 清单，记载 SHA-256、模型摘要、环境与源码提交。
   当前五份已见文档/49 题仅为 DEV；未参与过本轮调试的文档或文档家族另列 HOLDOUT。
   同一原始 PDF 的页、题目或改名副本不得同时出现在两组。若当前无合格未见文档，
   先补评测材料，不得公布泛化提升。
2. 固定语料范围有两份独立协议：正式 OHR 类型的**全库检索**覆盖所选完整语料，
   不把 truth 的 document_name 当过滤条件；明确面向单文档使用场景的**文档限定检索**
   另行报告。两个协议的分母、候选集、索引摘要和结果文件分开。
3. 在 Canonical 来源上记录人工审核的支持片段、必要表格行/列/条件以及原文页区，
   作为标注依据；为**每一个实际索引**分别审核 `acceptable_chunk_ids` 并绑定
   `RetrievalGoldIndexIdentity`。可用文本匹配或旧块映射提出候选，但审核员必须核实：
   新 chunk 是否包含足以支持该问题的实际内容，尤其核实表格行组与条件；不得自动批准。
4. 同一问题在两种表示中的标注标准必须一致。标注歧义、来源本身缺失、一个 chunk
   无法独立支持答案的情况单独记录并裁决；不能生成虚假的可接受 ID，也不能只从
   某一组删掉困难题。两个 gold 文件与其 index identity 不匹配时直接拒绝比较。
5. 已计划的题目全部保留。缺失运行、解析失败、索引失败、provider 错误和不完整结果
   有明确逐题状态；不得靠排除失败题提高分数。正式证据级评测需要完整、同源的可评
   集合；无法完成共同标注时阻断晋级，同时报告全部计划题目的故障分母。

当前离线输出包含每题两种表示的 Dense 最早正确证据 rank、重排后 rank、Top20 候选
缺失、重排 Top5 丢失和最终 Context 来源区间相交诊断；“必要证据是否完整”及最终逐题
失败层仍须独立审核，不能从区间相交自动推断。正式指标为
按 frozen gold 的 EvidenceRecall@1/@5/@20 与 MRR；@20 只评 Dense 候选，
@5 评最终重排结果。现有正式 evaluator 已实现最早正确 chunk rank、@1/@5/@10
与 MRR；@20 须基于**同一个最早 rank**补最小聚合及测试，不另造判断算法。
`PageHitRate`、`TableSourceExposure` 与按页面匹配的结果保留为
**诊断**，不能替代正式证据指标。现有 gold evaluator 只计算单个可接受 chunk 的
最早 rank；多来源问题的“所有必要来源均命中”必须另作独立、人工标注的诊断，
不得把单块命中冒充多来源完整召回。

验收：同一输入重复运行 chunk ID、Dense 顺序、重排顺序、证据 rank 和 Context
来源顺序一致；浮点分数只允许既定数值容差；Fixed 旧索引/旧问答输出在不改变输入时
保持原样。不存在可用正式 gold 时只报告可追踪诊断，不报告 EvidenceRecall 提升。

## 4. 第二关：结构策略的单变量实验

每一轮都以同一个 Fixed 索引作控制组，候选建立**新索引**。先在 DEV 找原因，
仅把一个通过合成契约测试的变更送入真实 A/B；记录相对旧结构版本及 Fixed 的
逐题改善和退步。某一变更被否决就回到此前胜出的结构候选，再试下一项；
不能把多个未验证的变更累加后解释总分。

### 4.1 假设 A：未知表头的紧凑表格表达

范围：仅改 `chunking.py` 中未知表头的结构检索文本渲染。
对同一完整行带，比较现行逐格 `Row N / Column N` 与保留原始单元格文字、
列位置和空位的紧凑逻辑行表达。明确的 `COLUMN_HEADER/BOTH` 继续使用已有
列名逻辑；`UNKNOWN/NONE` 不升级为列头。实际 `rowspan/colspan`、跨页 segment、
caption 绑定、row-band 边界及原文 source map 不得变化。

验证：比较每个表格的 chunk 数、每块数据文字比例、各行组词元数、行/单元格覆盖、
Table 查询的 Dense@20、重排 EvidenceRecall@1/@5 和错题配对。特别测试“同一长表
的最后数据行被单独切出”“首行看起来像表头但 role=NONE”“多列合并单元格”。
若只是 token 减少而证据指标未改善，记录结果，不称为检索优化。

### 4.2 假设 B：减少重复上下文对表格排名的干扰

分两次实验，分别只调整**显式脚注全文重复**和**冗长标题前缀**，不要同时改。
保持 caption/FOOTNOTE_OF/heading 关系在 Canonical 和索引来源记录中完整；
检索文本可保留有证据的简短主题线索，长条件由现有 Context Builder 按预算恢复。
不可依据问题词汇决定某条说明是否“重要”。

关键约束：当前 `structure_aware_chunks` 会压制已绑定脚注的独立候选。
若从所有表格块中移除脚注全文，必须为脚注保留可检索候选，或证明它仍以足够的
真实文本存在于其他 embedding chunk；否则 evidence parity 会下降。Context
可以沿显式关系补充脚注，但 Context 的预算有限，不能把“可补充”视为已被召回。
同样要避免 caption 失去独立可检索路径。验收同时看脚注/caption 问题、证据覆盖、
表格排名与最终 Context 缺失，不能只看平均 token 数。

### 4.3 假设 C：普通长块的检索粒度

当前超过 512、未超过 8000 词元的普通 block 会保持为一个候选；该行为已有单测，
因此变化需要新的结构 chunker 版本与相应回归测试。只对可分割的普通文本试验
确定性、真实 tokenizer 边界的适中片段，并优先选已有段落/句子边界；不能用字符数、
语言模型摘要或特定 PDF 规则代替。应避免制造大量极短候选，且不得将不同
`UNRESOLVED` 块或相邻但无可信顺序的块打包。

若拆分一个 Canonical block，必须给每个 chunk 记录**准确的**
`metadata.source_token_ranges`，使 `context.py::_structure_source_spans` 能返回对应
原文区间；只保留 `source_block_ids` 而回退到整个源，会让检索和 Context 的证据粒度
不一致。验证多字节字符、重复文本、首尾窗口、跨页和省略条件；检索块的文字、
来源区间、字符/token 坐标和引用要往返一致。表格整行带、合并单元格、公式与图形
继续遵循其已有保护规则；单一行带大于合理检索预算属于单独报告的边界问题，
本步不得悄悄把不完整行标成完整。

### 4.4 假设 D：平级章节的边界强度

仅在 A/B/C 的逐题诊断仍显示“相关连续正文被平级小标题拆散”时启动。
真实 `Section.level=1` 表示确定性的平级归属，不证明 PDF 的 H1 层级。
可在**已确认 IN_FLOW** 的同页或连续流中，实验把小标题作为提示而非一律硬切分；
跨不确定阅读顺序、表格行带或明确独立实体仍须保持边界。候选 metadata 要记录
原 Section 和被合并的来源，不改 Section graph、heading level 或 Canonical IR。
验证跨主题误合并、同标题不同段、正文与表格条件、来源页区及对问答的影响。

## 5. 测试与运行顺序

每个改动先增加小型合成契约测试，再运行本地离线检查：

```text
ruff check .
mypy
docparser schema check
python -m pytest
```

测试要证明：Fixed 窗口与版本不变；结构输出可重复；完整行带与合并单元格不被伪造；
显式表头只在有证据时启用；表格/脚注/caption 及 UNRESOLVED 证据不漏；
`source_token_ranges`、页、segment、bbox 与 Context 引用对应；不同索引 gold 身份不混用；
问答批次缺题/失败仍在完整分母。默认 CI 不要求网络、PDF 下载、GPU 或模型权重。

真实运行顺序固定为：

1. 保留旧 Fixed 和 Structure 2.4 索引及原报告；从**同一批已经保存的 IR** 各建新候选
   索引，不重跑 Parser；记录每个索引的 manifest、文件 digest、候选数和长度分布。
2. 对同一全库、同一问题清单运行 Dense Top20、同一个 reranker→Top5；另行执行显式
   文档限定协议。比较逐题证据，不用标题相似或同页命中代替证据。
3. 对 Top5 建生产 Context 4096，分别统计：正确直接证据已召回、存在于最终 Context、
   表格必要行/条件完整、预算遗漏、引用是否仍指向原文。任一候选的 Context 不能因为
   扩展而挤走本可容纳的直接证据。
4. 只对 DEV 上经选择的少量候选执行 QA；使用现有 `rag-batch` 的同一题目、生成模型、
   prompt、密钥来源和配置。每个候选写独立输出目录，保留 provider error、无效回答、
   拒答和引用。独立阅卷后调用现有 `rag-evaluate`；基于完整问题清单计算
   `correct_and_supported_rate`，并单列回答覆盖、无支持答案、错误与不可回答问题。
   阅卷时隐藏方案名称，争议题按同一事先确定的标准复核。
5. 策略定稿后只在未见文档家族上运行一次 HOLDOUT。研究者不能再依据 HOLDOUT
   错题继续改同一版本并把它称为未见集；新增修复需新版本与新留出集。

每个阶段产物应有配置/索引身份与逐题记录，至少能追溯“原始来源 → chunk →
Dense rank → 重排 rank → Context evidence → 答案/引用”。不要覆盖已有 run；
失败保持在原始分母。若无模型或未见文档，只完成离线契约验证并标记真实阶段未运行。

## 6. 判定、晋级和回退

预先声明主指标：正式人工审核的 EvidenceRecall@5（最终重排候选）与完整计划题目的
`correct_and_supported_rate`。同时报告 Dense EvidenceRecall@20、最终 EvidenceRecall@1、
MRR、TEXT/TABLE/READING_ORDER 切片、Context 必要证据保留率、provider 错误、
候选数和延迟。若某切片没有题目，标为“不适用”，不能写成 100%。

只有同时满足以下条件，才允许提议将结构候选改为默认：

1. DEV 上标注与身份检查完整、通过所有不变量、没有来源/行区间/Context 的新缺失；
   开发集用于选择候选，不能单独证明产品收益。
2. HOLDOUT 中与 Fixed 在**相同问题集合**上的 EvidenceRecall@5 和
   `correct_and_supported_rate` 均有正向的逐题净改善；Dense@20 候选缺失不增加。
   对 TABLE、TEXT、READING_ORDER 和不可回答题分别报告分子/分母及负面个案；
   任一业务关键切片出现明确退步、无支持答案增加或执行失败增加，均阻止切换。
3. 使用按文档家族配对的差值和不确定性区间；样本太少、区间含零或标注有争议时，
   只能写“观察到改进迹象”，不声称稳定泛化。复测保存同一索引与问题的确定性结果。

没有通过时：Fixed 512/64 继续作默认；新策略保持显式实验选项，记录它在哪些证据
类型有效、在哪些题目退步。若正式证据指标提升但问答指标未提升，或相反，
只报告实际改善的层，不能称为“整体准确率和召回率都提高”。任何“达到 85%”的
说法都必须说明独立真实问题集、完整分母、支持性引用判定和不确定性；本计划不预支结果。

## 7. 执行指针

**NEXT：在真实已保存索引上独立审核两套 gold，执行第 3 节的冻结比较协议。**
协议可复现后，按第 4 节依次验证 A、B、C；D 只由真实错误触发。每个阶段形成
独立可回退的改动，不捆绑上线。最终以未见文档的证据召回、Context 保留和正确且
有支持的回答共同决定默认策略。
