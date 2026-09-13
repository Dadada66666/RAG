# Evidence reliability increment — 2026-09-13

本轮目标：在不改变 Fixed/BGE/排名的前提下，让表格标题命中能够带回有依据的表格来源，
并让真实问答失败可诊断、中断可恢复。不是修复任意错误表格，也不承诺准确率已提升。

## 范围与规则

1. 在 retrieval-derived 层记录双向 caption/table links，不修改 Canonical IR。
   显式绑定优先。派生绑定只接受 caption 类型且以 Table/Tab./表 + 编号开头的文本；
   同页、水平重叠至少较窄区域一半、垂直间距不超过 3 个 caption 高度且不超过页高 5%。
   这些是预先固定的候选门槛，不是置信概率。caption 和 table 都只有唯一候选才接受；
   显式绑定冲突、多个候选、跨页、普通正文中的表号引用均不猜测。
   保存算法版本、依据、距离与重叠比例，原文/坐标/provenance 继续引用原 source。
2. `--caption-context` 显式启用；默认实验协议不变。使用已有 logical-row pipeline，
   将关联表格/标题作为受预算限制的 evidence bundle；超过预算明确记 omission，
   不把整表任意截成开头若干 tokens。不会创建 embedding chunk 或改变排序。
3. INVALID_RESPONSE 保存原始 completion 和结构化错误位置（不含密钥），留在 QA 产物，
   不打印到普通 CLI 日志。继续严格引用校验，不进行模糊匹配或自动改写引用。
4. `rag-batch --resume` 只续跑未持久化题目。先核验问题、配置、索引、prompt 和全部已存结果；
   已有成功、拒答及失败不重试。完整 batch 重入无请求。保留中断运行标识；可能已发送而未落盘
   的请求不可保证 exactly-once，说明该限制，不引入调度系统。

## 验证与边界

独立合成病例覆盖双栏、上下方标题、竞争候选、显式关系优先、普通段落、预算不足、
caption 命中后表格来源可追踪；测试不使用 OHR 题目/答案。恢复测试覆盖中断、损坏结果、
配置漂移、已有失败不重试。诊断测试覆盖坏 JSON、schema 字段及引用不存在。

不得把 column header UNKNOWN 改为猜测的角色；错列和相邻表合并需另有 raw/PDF 证据。
本轮实现完成后才允许一次 DEV 复验，不能利用 HOLDOUT-79。正式答案质量仍需独立判读。

## 实现状态

已实现：caption 双向关联与独立上下文开关、索引 1.2 版本门槛、INVALID_RESPONSE 诊断、
校验后续跑及完整运行无请求重入。Canonical schema、Fixed/BGE/排名及默认 context 不变。
离线（含回答契约修复）：501 passed / 2 skipped / 10 deselected；Ruff 通过，Mypy 157 文件通过。
未宣称：真实准确率提高、自动修复错列/并表、自动恢复跨页 caption、任意图表理解。

## 真实结构与上下文回放（未调用生成模型）

服务器隔离验证目录：`/root/autodl-tmp/caption-association-0luketse`，包含实际模块副本、
`activation.json`、`replay.py`、`replay.json` 和逐题 context。没有覆盖服务器工作树或旧实验。
使用既有 DEV 的 3 份 IR 与已保存 Top-5，真实 BGE tokenizer 在 CPU 上回放。
88 个 embedding chunk ID/text 与旧索引逐一相同；规则阈值没有按回放结果调整。

14 张表中，Table 1、14、15 产生唯一几何关联，其余未强制关联。
21 题均在 4096 tokens 内；6 题增加了 table 来源。其中此前缺表的两题（0-based ordinal 7、11）
现在分别带回 Table 15、Table 1，context 为 3856、4073 tokens。
另外 4 题也增加了表格，因此额外 context 可能有成本，不能只报告成功病例。
这证明关联与预算链路实际激活，不证明匹配全部正确或最终答案正确。
下一步是独立核验关联和答案支持，固定配置做一次完整 DEV 对照；不读取 HOLDOUT-79 调整规则。

## 回答校验契约修复（已实现）

成功回答允许省略 `reason`，与显式 `null` 等价；拒答仍必须提供非空原因，成功回答仍必须有
claims 和逐字引用。未知 evidence ID、改写或带虚构省略号的引用仍拒绝，不能以宽松引用匹配
换取成功率。prompt 不变。新批次记录 `answer_validation_version=answer-validation@1.1.0`；
缺失该字段的历史批次按 1.0.0 读取，可以评估，但不能用新规则续跑。重新实验使用新目录。

对保存的两份失败 completion 做 CPU 校验重放：`00013.qa.json` 从缺少 reason 的
INVALID_RESPONSE 变为 ANSWERED；`00002.qa.json` 的非逐字引用仍被拒绝。
这是同一原始输出的契约验证，不是新模型运行、语义正确性判定或更新后的端到端准确率。
旧实验文件保持原样。

## 下一步：并排表格区域解析验证（待实施、待 GPU 验证）

依据：真实原始 PDF 的两张并排表，在保存的 layout 输出中已经合并为一个 bbox；HTML 随后
混合两表列，neutral 到 Canonical 的映射没有引入该错误。不能通过问答 prompt 或 IR 改列
恢复丢失的结构。当前不实现自动拆表，也不把高 detector score 当作表拓扑正确。

最小实验协议：

1. 固定现有 parser、模型版本、分辨率及参数，对同一页比较整页解析与各独立表区域解析。
   区域先由仅看 PDF、看不到问题答案的人标注，作为定位上限诊断；这些坐标不得进入产品规则。
2. 独立测试集至少覆盖并排两表、正常单表、多级表头/合并单元格、无表正文。不要只复跑
   失败的那一页；不使用 HOLDOUT-79，也不以 OHR reference 修正解析结果。
3. 每次保存 PDF digest、页号、原图尺寸、裁剪框、缩放与回映射坐标、parser 配置、原始输出
   和耗时。对照原图核验表数量、行列归属、合并跨度及逐格值；不能只比较非空率或 GriTS。
4. 若正确区域仍稳定错列，停止添加定位规则，明确是当前表识别能力限制；若区域解析明显改善，
   才研究通用区域提议，要求独立样本验证并计入误拆单表和额外耗时，再决定是否进入产品。
5. 验收前不自动替换 Canonical 表。若以后接入替换，必须保留原始 evidence 与 derived 标识，
   同时验证裁剪坐标回到原页后的 citation bbox，不能只保证答案字符串正确。

该实验是下一次开启 GPU 的目的；本轮不需要重新 embedding、不需要远程问答调用。
