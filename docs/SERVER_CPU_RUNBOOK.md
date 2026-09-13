# 当前服务器：从这里开始

核验日期：2026-09-12。代码基线 `6957a7e21fae70c7172aa14c6774d87142caddc7`，加本轮 CPU 准备修改。
这里是实际操作入口；其余架构/spec 文档用于开发查阅，不需要依次实现所有历史规划。

## 1. 先认清目录和运行环境

| 用途 | 已核实路径 / 状态 |
|---|---|
| 仓库 | `/root/RAG` |
| CPU 检查环境 | `/root/autodl-tmp/rag-envs/cpu`，新建，不含 torch / Paddle |
| Parser/后续 GPU 环境 | `/root/RAG/.venv-paddle`，保留已有模型依赖 |
| BGE-M3 完整目录 | `/data/rag/models/bge-m3`，包含 tokenizer、配置和约 2.27 GB PyTorch 权重 |
| 本次使用的 Canonical IR | `/data/rag/canonical-ir/ohr-dev-v1-sectioned`，3 文档 / 20 页 |
| 原始未 sectioned IR | `/data/rag/canonical-ir/ohr-dev-v1`；不要和上一行混合建索引 |
| 已下载 OHR PDF | `/data/rag/datasets/ohr-bench/selected-pdfs`，10 PDF |
| 历史 A/B 输出 | `/data/rag/runs/rag-ab-dev-contract-v1.1` 等目录 |
| 已准备的 question-only DEV-21 | `/root/autodl-tmp/rag-cpu-check.j6fNZiTG/dev21.qa-questions.jsonl` |
| 本轮原始检查日志 | `/root/autodl-tmp/rag-cpu-check.j6fNZiTG` |

该容器实际限额是 **0.5 CPU / 2 GiB**，`free -h` 显示的是宿主机资源，不能据此加载大模型。
本轮没有加载 BGE/Paddle 权重、运行向量推理或调用问答 API。
默认 `/root/miniconda3/bin/python` 没有项目依赖；先激活下面的环境。

## 2. 当前可直接运行的 CPU 检查

```bash
cd /root/RAG
source /root/autodl-tmp/rag-envs/cpu/bin/activate
export CUDA_VISIBLE_DEVICES=''
export USE_TORCH=0 USE_TF=0 USE_FLAX=0
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false

python -m pip check
docparser schema check
ruff check src tests scripts/check_cpu_assets.py
python -m pytest -p no:cacheprovider
python -m mypy --cache-dir /root/autodl-tmp/rag-mypy-cache src tests scripts/check_cpu_assets.py
```

使用项目已安装的 `docparser` console 命令；仓库没有 `python -m docparser` 入口。
已有混合 Parser 环境中的完整 Mypy 曾以 137 退出，缩减 import 的尝试也触发工具内部异常；
干净 CPU 环境的**完整**类型检查已通过，不把缩减检查冒充完整结果。

新机器重建此 CPU 环境时：

```bash
python3.12 -m venv /root/autodl-tmp/rag-envs/cpu
source /root/autodl-tmp/rag-envs/cpu/bin/activate
python -m pip install -r /root/RAG/requirements.lock
python -m pip install --no-build-isolation --no-deps -e /root/RAG
python -m pip install transformers==4.57.6
```

最后一行只用于 tokenizer，不安装 BGE 权重或 torch。底层依赖版本以本次检查目录内的 freeze
记录为准；固定包版本不等于跨硬件位级一致。

## 3. 真实 tokenizer 与资产检查

tokenizer 只把文字映射为 token/offset，不执行 embedding 模型，因此可以在纯 CPU 下运行：

```bash
BGE_M3_MODEL_PATH=/data/rag/models/bge-m3 \
DOCPARSER_RUN_TABLE_CONTEXT_SMOKE=1 \
python -m pytest tests/integration/test_table_context_offsets.py -o addopts='' -q

RUN_DIR=$(mktemp -d /root/autodl-tmp/rag-cpu-check.XXXXXXXX)
python scripts/check_cpu_assets.py \
  --ir-root /data/rag/canonical-ir/ohr-dev-v1-sectioned \
  --pdf-root /data/rag/datasets/ohr-bench/selected-pdfs \
  --tokenizer-path /data/rag/models/bge-m3 \
  --output "$RUN_DIR/assets.json"
```

脚本不接受 query/truth 文件，不计算 Recall、MRR 或答案准确率。它依次检查 PDF preflight、
IR/schema/invariants、Fixed evidence coverage、tokenizer 行对齐、注入已知 chunk 命中后的上下文
原文区间和预算。注入命中不是 dense retrieval；结果中明确标记 `INJECTED_HITS_NOT_RETRIEVAL_EVALUATION`。
单份可预期输入失败计入报告，整体退出码 1；不会把异常文件跳过后返回“全部通过”。

tokenizer 可能报告完整 source stream 超过 8192 tokens：这里是在**分块前分词**，不将整流送入
模型；这条提示不能当成已经执行了 BGE 推理。后续 embedding 输入仍由 Fixed 512/64 产生。

## 4. 本轮实际结果与修复

- 干净 CPU 环境：**481 passed、2 skipped、10 deselected**；真实 BGE tokenizer smoke **1 passed**。
- 完整 Mypy 检查 **156 文件通过**；Ruff、Schema 检查通过。覆盖率门槛为原有模块集合，不能
  将其解释为 QA 语义准确率。
- DEV 的 3 份 IR 均可读取并通过 invariants，合计 20 页、276 个可检索 blocks、15 个不可渲染 blocks。
- 14 个 TABLE source maps 全部 `ALIGNED`；16 个含表 chunk 的注入命中检查产生行级恢复，未出现
  row-band 不完整或预算遗漏。这证明执行路径生效，**不证明找到了正确答案对应的表**。
- 这些表仍为 **0 明确列头、0 绑定 caption**。M2 不会补造这些结构事实，数值归属/注释完整性仍有限制。
- OHR 已下载 PDF：**9 个 preflight 通过，1 个明确失败**。失败文件为
  `academic/DUDE_1c6061aa2d4c3167592fff8e35e5100a.pdf`，第 1 页 image resource 存在循环引用。
  本轮把未分类 `LimitReachedError` 修为带页码的 `PreflightError`；没有修复/替换 PDF 本身。
- 仓库内另一组历史解析材料：39 PDF preflight 通过，14 份 IR 文件通过（13 个唯一 document_id）。
  它们不与 DEV 合并计分，也不自动混入新问答索引。
- 修复 PyYAML 约束：项目/lock 使用 6.0.2，与已安装 PaddleX 3.7.1 的要求一致。
  lock 补齐 CPU 测试需要的 NumPy；两个环境最终 `pip check` 均已通过。
- 忽略生成的 `.venv-*`、notebook checkpoints、output、external 下载目录，保留其中已有文件。
  没有删除旧结果、整理移动真实数据或重写历史实验。

### 历史实验不能直接作为当前 QA 基线

真实 tokenizer 的 CPU 比对发现：

| 检查 | 结果 |
|---|---|
| 3 份 IR 的 semantic digest 与历史 manifest | 全部一致 |
| 历史 Fixed embedding chunks | 87 |
| 当前 Fixed 重建 | 88（academic 76、news 10、textbook 2） |
| 相同 chunk IDs / 相同文本 | 35 / 35 |
| 历史 ordered chunks 用整流分词重现文本 | 52 / 52 |

当前 `fixed_token_chunks` 对每个 block 的 `text + "\n\n"` 分词后拼 token；历史 artifact 的
ordered 文本可由先拼全文再分词重现。比如 textbook 是 509 个整流 tokens、525 个逐块拼接 tokens，
因而从 1 个窗口变成 2 个。**tokenize(a + b) 不等于 tokenize(a) + tokenize(b)**。
这说明当前实现与历史 artifact 的表示不完全相同；仅凭相同版本字符串/commit 标签不足以认定
运行输入相同。这里没有证据确定当时未提交工作树或运行入口的具体状态，不能编造历史原因。

CPU 准备没有修改分块算法，也没有使用旧向量填充新 chunks。新 M1/M2 仍能使用同一个新索引公平
对照；但必须把 GPU 上重新生成的当前 baseline 与历史 87-chunk 结果分开报告。
原始比对：本次检查目录中的 `baseline-representation-check.json`。

## 5. 开 GPU 后的下一步

更新：2026-09-12—13 已完成当前 Fixed GPU 基线与 DEV-21 的 M1/M2 问答运行。
实际结果、失败和未通过的验收项见 [SERVER_GPU_VALIDATION.md](SERVER_GPU_VALIDATION.md)。
以下保留环境切换和复现顺序，不表示这些实验仍未运行。

先保留 CPU 检查结果，然后换回模型环境，清除当前 shell 的 CPU-only 限制：

```bash
cd /root/RAG
source .venv-paddle/bin/activate
unset CUDA_VISIBLE_DEVICES USE_TORCH USE_TF USE_FLAX
export BGE_M3_MODEL_PATH=/data/rag/models/bge-m3
export BGE_M3_DEVICE=cuda
python -m pip check
```

接下来由我们核验 GPU 与实际 runtime，再按顺序执行：

1. 先完成 Fixed 输入的历史复现检查；不要用历史的 87 chunks 向量直接拼接当前重建结果。
2. 只使用 `ohr-dev-v1-sectioned` 新建 `rag-index`，输出新目录，例如
   `/root/autodl-tmp/rag-qa/index-m2`。历史 A/B 的 chunks/embeddings/metrics **不是** `rag-index`
   的完整可加载索引，不能只改目录名来使用。
3. 对 DEV 文档跑少量 `rag-ask --context-only --table-context`，先核对真实命中与来源。
4. 使用已有开发题清单做一次 M1、一次 M2，保持同一索引/模型/问题/预算。问答 API 密钥仅从环境变量读取。
5. 根据 PDF 独立检查答案与引用支持。完成这一轮之前，不继续堆新 chunker、推断规则或 M3 功能。

DEV-21 清单从历史 **DEV-only** `rag-ab-dev-contract-v1.1/queries.jsonl` 投影得到，已核对 21 个
ID 与历史 eligible ID 集合一致；输出只包含 query_id、question、document_ids，不含答案或 gold page。
没有打开 100 题源文件来重新选题。当前 SSH 会话没有 `SILICONFLOW_API_KEY`，之后在服务器终端
设置即可；不要将密钥写入配置、Git 或对话。模型接口本轮未测试。

79 条留出题的题目/答案没有用于本轮准备或算法设计。检查原文资产可读取性不代表进行了 holdout
检索/问答实验。最终是否改善稳定性，要分别看输入失败能否定位、运行能否复现、引用能否追踪、
答案是否正确，不能用一个“测试通过”替代全部结论。

本轮修复与手册同时保存在本地和服务器工作树，尚未提交。后续同步代码前先核对本轮 diff，
保留服务器原有未跟踪文件与实验结果。
