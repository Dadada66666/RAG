# 2026-09-12—13 GPU 实验记录

## 范围与复现

代码 HEAD：`6957a7e21fae70c7172aa14c6774d87142caddc7`，包含工作树中尚未提交的 CPU 准备修改。
服务器：RTX 3090 24GB；Python 3.12；模型环境 `/root/RAG/.venv-paddle`。
BGE-M3：`/data/rag/models/bge-m3`，CUDA，batch size 8。

完整实验目录：`/root/autodl-tmp/rag-gpu-check.dUGmtT2z`。
其中 `execution.json`、`source-file-digests.json`、`runtime.json` 和 `index/manifest.json`
记录代码、输入、模型和索引摘要。`run.py`、`evaluate.py`、`qa.py` 保存实际执行脚本。
未修改 chunker、检索器、提示词、Canonical IR 或 OHR 真值；没有读取 HOLDOUT-79 题目/答案。

只使用 `ohr-dev-v1-sectioned` 的 3 文档、20 页、DEV-21。当前 Fixed 512/64 生成 **88 chunks**，
不可与历史 87-chunk artifact 混用。新索引已经落盘、重新加载并通过摘要验证。

## 当前 Fixed 检索基线

| Slice | 题数 | PageHit@1 | PageHit@5 | PageHit@10 | MRR@10 |
|---|---:|---:|---:|---:|---:|
| ALL | 21 | 17/21 = 0.809524 | 21/21 | 21/21 | 0.873016 |
| TEXT | 2 | 2/2 | 2/2 | 2/2 | 1.000000 |
| TABLE | 11 | 7/11 = 0.636364 | 11/11 | 11/11 | 0.757576 |
| READING_ORDER | 8 | 8/8 | 8/8 | 8/8 | 1.000000 |

这是页级命中，不是答案正确率或正确表格实体召回率。

对接时发现 QA 索引使用 PDF 文件名，OHR 使用带目录的 document_name。
直接计分得到的零命中无效，保存在 `metrics.unmapped-invalid.json`。
修正仅通过 `chunk_id → document_id → IR 相对目录` 显式映射名称；不改变分数、排序或真值。
映射见 `evaluation-name-map.json`，正式结果为 `metrics.json` 和 `retrieval.ohr-names.jsonl`。

## M1/M2 上下文检查

两组使用相同 Top-5 和 4096-token 预算；M1 为 SOURCE_SPANS，M2 为 LOGICAL_ROWS。

| 检查 | M1 | M2 |
|---|---:|---:|
| 平均 context tokens | 2921.10 | 2933.76 |
| 表格 evidence 片段出现次数 | 16 | 16 |
| 显式完整 row-band 片段次数 | 0 | 16 |
| omitted source 次数 | 1 | 1 |

8/21 题的 context text 发生变化。所有 context 均未超预算；有字符区间的片段均与原 source
对应区间逐字一致。M1 的 row-band 数为 0 表示它不提供此标注，不能解释成其表格内容都不完整。
16 是跨题出现次数，不是 16 张不同表，也不是语义正确计数。

建索引、21 题检索和两套 context 构造共约 24.24 秒，包含首次模型加载与落盘。
这是单次诊断耗时，不是吞吐保证。后续 QA 期间并行运行过解析测试，不能作为隔离性能实验。

## 真实解析检查

现有 Paddle GPU smoke：**2 passed / 2 failed**。

- single、bilingual 用例通过；这仅验证非空输出，不代表中英文识别准确率。
- scanned fixture 是 10×10 纯白图，却要求非空 blocks，测试断言与材料不相符。
- merged-table fixture 没有实际合并单元格，只重复绘制已有线段；无法验证合并单元格能力。
  此次该输入未识别出 table，保留失败，不将其改写成通过。

另取已用 DEV 文档的原第 7 页，单独生成测试 PDF，真实执行 Paddle → ParseResult → IR。
结果：1 页、7 blocks、1 table、186 cells，IR validation 通过，约 51.49 秒。
原始 parser、neutral result、IR 和诊断均保存在 `real-table-page/`，没有替换检索语料。
该派生 PDF 的 IR page 1 对应原文 page 7，仅用于解析检查。

限制：reading order 仍 unresolved，186 cells 均无 exact bbox，不能宣称单元格级精确定位。
数值诊断报告 6 组差异：`1.84` 等与 `$1.84` 等；抽查对应 Canonical cell 中数值仍存在。
不能把这 6 组直接当作漏识别数值，需区分数学标记与货币单位；本次未增加清洗规则。

## 远程问答

硅基流动 `Qwen/Qwen3.8-27B` 合成请求已通过。密钥仅传入进程环境，不写入实验产物。
DEV-21 的 M1/M2 各一次真实 batch；最终状态以 `qa-summary.json` 为准。
9 月 13 日重连发现 M2 仅保存 5 条，原进程已退出。核验已有文件与源码摘要后，由
`resume-m2.py` 继续剩余 16 条，没有重跑已有失败；详情见 `resume.json`。
中断时可能有已发出但未落盘的请求，因此不能凭结果数断言服务商只计费 42 次。
两组跨时段运行，超时率或延迟差异不能解释为 M2 的因果收益。

| 状态 | M1 | M2 |
|---|---:|---:|
| ANSWERED（输出及逐字引用校验通过） | 13 | 14 |
| INSUFFICIENT_EVIDENCE | 3 | 3 |
| INVALID_RESPONSE | 2 | 1 |
| TRANSPORT_ERROR | 3 | 3 |

M1 生成阶段总耗时约 495.93 秒，包含失败请求。超时仍保留在 21 题分母中，不自动重试。
M2 已记录生成阶段总耗时约 305.87 秒，不含中断的未落盘请求及两次运行间隔。
两份 batch 均为 COMPLETE，全部 42 份结果通过 manifest/摘要/身份校验，检索结果逐题完全相同。
模型列表接口在超时期间正常响应，约 0.20 秒；不能因此断言生成服务没有负载或排队问题。
答案的引用逐字校验不等于语义支持验证。独立 PDF 判读尚未完成，不报告 supported-correct 指标。

已定位的具体失败链路：

- YOLOv10-X 的大目标 AP：gold page 17；Top-1 为 caption，Top-10 无 TABLE 来源。
  PageHit 成功，但模型缺少表格数据并拒答。M2 行恢复只能处理已命中的 table，无法凭空找回它。
- YOLOv10-N forward latency：gold page 7；Top-10 没有该页 TABLE 来源，只有段落/caption
  和其他页表格。不能把这种失败归因于表格行切断。
- Table 3 的 baseline/o2o 题：Top-5 已有 gold-page tables，但缺少可靠表号/条件绑定。
  M1 拒答；需要检查对应 caption/row/column 关系，不能只提高 Top-K。

`answer-review.md` 汇总所有已完成请求的参考答案、回答及引用，供独立复核。
它不是人工判分文件，不能直接充当 `rag-evaluate --judgments` 输入。

## 配对结论与下一步

完整生成输入（问题、context text、warnings）相同的 13 题仍出现了不同输出状态。
真正改变输入的 8 题：5 题两组均 ANSWERED，2 题两组均拒答，1 题从引用校验失败变成超时。
详见 `paired-statuses.json`。因此 **当前没有证据证明 M2 提高了正确且有支持的回答比例**。
14 对 13 的输出覆盖差异不能作为算法收益；也不能从一次小样本实验断言 M2 永远无价值。

当前可确认：GPU 解析、建索引、检索、构造 context、调用生成模型、引用追踪已真实贯通。
当前不可确认：复杂文档问答达到稳定发布要求、单元格精确定位、未见文档泛化、企业 SLA。

下一步只处理已见问题：

1. 修正 scanned/merged-table 测试材料，使其实际包含文字和合并单元格，保留原失败记录。
2. 对三条持续拒答做 PDF→IR→hits→context 的逐题核验：区分目标表未命中和表号/列条件未绑定。
   不用重新调 chunk 大小代替证据关系修复，不为指定题目硬编码关系。
3. 对 INVALID_RESPONSE 保留受控的模型原始响应诊断，再确定 JSON/quote 失败原因；当前产物只保留
   失败类别，不能据此猜测究竟哪个字段或哪段引用错误。不能放宽引用校验来制造通过率。

先独立核验答案与引用支持，再决定最小实现修复。服务超时单独按运行可靠性处理；如果需要复验，
新建运行目录并保留本轮完整分母，不能覆盖失败或把重试成功替换成第一次成功。
HOLDOUT-79 保持未使用。本轮没有进行算法调优或更改生产源码。
