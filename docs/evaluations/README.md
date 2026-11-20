# 评测证据目录

这个目录只用于保存可复核的评测报告，不承载运行时代码。

## 当前证据

| 文件 | 说明 |
| --- | --- |
| [rule-equivalence-v3-103.json](rule-equivalence-v3-103.json) | 当前 103 条规则链路结果等价报告；绑定 `git_head`、`app_dirty=false` 和数据库指纹 |
| [large-schema-v1.json](large-schema-v1.json) | 40 张 synthetic 表的检索消融；闭包 91.7%，接近相同 token 上限的纯 Top-K 也 91.7%；不衡量 SQL 准确率 |

## 历史归档

[`archive/`](archive/) 只保留核心里程碑报告（v3-100、holdout、冻结口语等）；过程性大规模 JSON/JSONL 已清理，不再把仓库当作产物网盘。历史文件保留原样用于追溯，**不应当作当前代码的分数**。

## 如何阅读

1. 先看报告中的 `runtime.git_head` 和 `app_dirty`，确认它对应哪次提交、是否来自脏工作树。
2. 历史模型报告可能没有 `git_head`；它们只记录了当时的 `source_sha256`，不能单独映射到 commit。
3. 严格模型分数是带日期、模型和网络条件的快照，不是固定 CI 门槛。
4. 规则等价、范围守卫、自然语言和检索消融是确定性检查，不代表模型能力。

归档门禁：

```bash
python -m app.evaluation.check_archives docs/evaluations/rule-equivalence-v3-103.json --require-clean
```
