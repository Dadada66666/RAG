# 2026-09-13 真实问答与解析损失取证

## 实验边界

服务器：`/root/autodl-tmp/evidence-audit.QES19nQq`。
本地副本：`.tmp/evidence-audit/evidence-audit.QES19nQq/`。
使用上轮已保存的 DEV-21 Top-5、相同 Qwen/Qwen3.8-27B、相同 prompt、4096-token 预算，
只使用冻结的 caption-context 回放输入。没有重新检索、调参、修改真值或读取 HOLDOUT-79。
原始 completion 诊断已启用；仅保存至问答产物，不含密钥。
该运行使用隔离代码副本，不覆盖服务器工作树；生成发生在不同时间，服务波动不能归因于算法。

## 实际结果

| 终态 | 旧 M2 | 本次 caption-context |
|---|---:|---:|
| ANSWERED（引用/格式通过，不等于语义正确） | 14 | 18 |
| INSUFFICIENT_EVIDENCE | 3 | 1 |
| INVALID_RESPONSE | 1 | 2 |
| TRANSPORT_ERROR | 3 | 0 |

全部 21 题有记录且摘要验证通过，检索结果逐题完全相同。完整生成输入改变 8 题。
状态变化：14 题 ANSWERED→ANSWERED；2 题超时→ANSWERED；1 题超时→INVALID_RESPONSE；
2 题拒答→ANSWERED；1 题持续拒答；1 题持续 INVALID_RESPONSE。
不能将 18/21 写成答案准确率，也不能将新增 4 个 ANSWERED 都归因于关联算法。

两项具体证据改善：

- ordinal 7，YOLOv10-X 大目标 AP：旧 context 缺 Table 15；新 context 包含完整表。
  模型回答 70.9%，引用 `| YOLOv10-X | 54.4 | 71.3 | 59.3 | 37.0 | 59.8 | 70.9 |`。
- ordinal 11，YOLOv10-N forward latency：旧 context 缺 Table 1；新 context 包含表与 caption。
  模型回答 1.79 ms，同时引用数值行和 caption 对 Latency^f 的定义。

已查看原 PDF 第 7、17 页渲染，两个数值及对应指标与原表一致。这是两个病例的来源核验，
不是全部 18 个 ANSWERED 的独立盲评。

引用仍以表格区域为定位单位，不能把全表 row_indices 当作精确命中单元格。
全部已回答题仍需独立判定正确性与完整引用支持，当前没有正式 supported-correct 总分。

## Table 3：首次损失位置已定位

`page8.png` 是原 PDF 第 8 页的渲染。原图有分开的 Table 3（Dual assign.）、
Table 4（Matching metric.）、Table 5。

保存的 `raw-page8.json` 中：

- layout_det_res 把 Table 3 与 Table 4 放在一个 table bbox `[204,433,627,543]`；
  该检测 score 约 0.916，但它仍然是错误的对象边界，不能把分数当结构正确率。
- 标题同样被合成 `Table 3: Dual assign. Table 4: Matching metric.`。
- 原始识别 HTML 已是一个 5×8 表，首列标题 `o2m o2o`、第二列 `AP Latency`，
  后面混入 Table 4 的 alpha/beta/AP 列；勾选条件也丢失了独立列位置。
- neutral → Canonical 的 40 个 cells 在行、列、span、text 上全部相同。
  证据见 `neutral-page8.json`、`canonical-table3-4.json`、`table3-chain.json`。

结论：本例不是 Canonical normalization 把正确表格变坏；首次可见错误已经在 parser layout
输出中，后续识别又弱化了列对应。重构 IR 或增加 header-role 猜测不能修复这个具体错误。
当前依据不能进一步区分 layout 模型本身与上游布局后处理，应保留这一边界。

### 参考答案疑点：不修改官方真值

ordinal 10 问 only-o2o，保存参考答案为 44.9 AP / 7.07 ms。
原页 Table 3 可见：only-o2m 为 44.9 / 7.07；only-o2o 为 43.4 / 2.44。
应作为独立人工复核事项；不能为了匹配参考答案让系统强答 44.9 / 7.07。
没有删除该题、修改真值或报告重新计算的“更高准确率”。

## 输出失败：现在有原始证据

1. ordinal 13：返回合法 JSON、回答及引用，但没有 `reason` 字段。
   diagnostic 为 `error_locations=["reason"]`, `error_types=["missing"]`。
   最小可行修改：ANSWERED 允许省略 reason，解析成 None；INSUFFICIENT_EVIDENCE 仍必须有非空原因。
   不改变 claims/citations 要求，不把缺引用的答案视为通过。
2. ordinal 2：模型采用 Table 7 的 44.4 / 2.36（保存参考答案为 44.5 / 2.31），
   且引用内主动插入 `...`，触发 QUOTE_NOT_IN_SUBMITTED_EVIDENCE。
   这里不能用模糊匹配/删除省略号自动修复：引用检查恰好阻止了一份错误表格归属的回答发布。
   下一步应检查目标表 caption 与正文中的表号引用如何进入上下文，而不是放宽引用检查。

进一步检查 `page9.png`：Table 8（Results of CIB）的 ours 行确为 44.5 / 2.31；
Table 9 才叫 Rank-guided，只有 stages/AP 列，没有 ours 行或 latency 列。
该题措辞提到 Rank-guided，但参考数值来自邻近 Table 8，存在第二处题目/来源歧义。
不能在未经人工确认时把参考答案当成题目唯一无歧义的目标，更不能用题目字符串硬绑 Table 8。

## 下一次修改顺序

1. 修正 answer schema 中 reason 的可省略语义，补齐 ANSWERED/拒答的对照测试。
2. 将多表号 caption/多表合区作为明确的“不可信表边界”诊断。先验证现有 parser 的区域识别
   能否在正确分离区域后保留列；这只是离线可行性验证，不能把人工框坐标写入生产算法。
   在没有通用分离依据前，不自动拆表或填表头。
3. 对错表答案做 caption/表号关联审计；保留原始证据，只有关系明确时扩展上下文。
4. 独立核验全部回答，分别报告数据问题、解析问题、证据获取问题与生成问题。

本轮没有修改生产逻辑、没有新推断规则、没有重新优化 chunker，也没有新增 parser。
