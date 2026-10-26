# Atlas BI · 企业级电商 NL2SQL 数据分析平台

**把自然语言经营问题转化为可执行、可审计、可复现的只读 SQL 查询。**

Atlas BI 面向电商运营、商业分析与数据团队，覆盖订单、商品、退款、投放和客户画像。平台将 Schema 召回、意图规划、SQL 生成、安全校验、只读执行、失败修复和结果解释纳入统一编排，并以检索证据、SQL 和 trace 将每次查询完整串联。SQL 结果、SQL 与证据不做模型二次改写；模型只读取确定性结果摘要，生成可读的中文分析结论并通过 SSE 增量推送，模型不可用时自动回退为可复现摘要。分析工作台支持会话级短期记忆，追问可以继承时间、指标与筛选条件。

[![CI](https://github.com/sun-sh/atlas-bi-nl2sql/actions/workflows/ci.yml/badge.svg)](https://github.com/sun-sh/atlas-bi-nl2sql/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/Orchestration-LangGraph-1f6feb)
![License](https://img.shields.io/badge/License-MIT-yellow)

![Atlas BI 流式分析工作台](docs/atlas-stream-final.png)

## 业务定位

| 维度 | 平台能力 |
| --- | --- |
| 使用对象 | 电商运营、商业分析、数据支持与业务管理团队 |
| 分析域 | 销售额、品类、退款率、ROI、客单价、趋势与渠道结构 |
| 数据层 | 订单、商品行、退款、渠道投放和客户画像五张业务表 |
| 查询链路 | 双路 Schema 召回 → 意图规划 → SQL 生成 → AST 门禁 → 只读执行 → 有界修复 → 结果解释 |
| 接入方式 | Web 工作台、FastAPI 查询接口、OpenAPI 文档与 OpenAI 兼容模型网关 |
| 会话能力 | 会话级短期记忆：最多 20 轮、7 天过期；显式条件优先，缺失条件按需继承 |

## 系统架构

### 系统全景

```mermaid
flowchart LR
    U[运营 / 商分用户] --> W[Web 分析工作台]
    W --> A[FastAPI API]
    A --> G[LangGraph 查询编排]
    G --> R[混合 Schema 召回]
    R --> Q[(Qdrant + BGE)]
    R --> E[(BM25 / 本地 IDF)]
    G --> P[意图与筛选规划]
    P --> S[规则 / LLM SQL 生成]
    S <--> M[OpenAI 兼容模型网关]
    S --> V[SQL AST 安全门禁]
    V --> D[(只读数据库)]
    D --> G
    G --> T[结果 / 证据 / trace]
    T --> W
```

### Agent 编排架构

这里所说的 Agent 是一个**节点固定、工具固定、修复次数有上限**的 LangGraph 工作流，而不是可自主挑选工具或无限循环的开放 Agent。模型只参与 SQL 生成、SQL 修复和结果解读，所有 SQL 仍必须经过同一道 AST 安全门禁。

```mermaid
flowchart TD
    START(["用户问题"]) --> RETRIEVE["retrieve<br/>双路 Schema 召回"]
    RETRIEVE --> PLAN["plan<br/>意图、时间、指标、筛选与会话继承"]
    PLAN --> GENERATE["generate<br/>模型 SQL；失败回退规则 SQL"]
    GENERATE --> EXECUTE["execute<br/>AST 安全门禁 + 只读执行"]
    EXECUTE --> CHECK{"执行成功?"}
    CHECK -->|"是"| FINALIZE["finalize<br/>结果摘要与最终状态"]
    CHECK -->|"否，仍有修复额度"| REPAIR["repair<br/>原 SQL + 错误 + Schema 反馈"]
    REPAIR --> EXECUTE
    CHECK -->|"否，修复额度耗尽"| FINALIZE
    FINALIZE --> MODE{"调用方式"}
    MODE -->|"/api/query"| SYNC["同步结果解读<br/>模型 / 确定性摘要"]
    SYNC --> SYNC_MEMORY[("可选：保存会话快照")]
    SYNC_MEMORY --> RESPONSE(["HTTP 响应"])
    MODE -->|"/api/query/stream"| RESULT["result 事件<br/>rows + SQL + evidence + digest"]
    RESULT --> STREAM["模型原生 token 流<br/>stream_result_narrative"]
    STREAM -->|"成功"| NARRATIVE["narrative 事件<br/>最终文本 + mode"]
    STREAM -->|"失败 / 空响应"| FALLBACK["reset / 单次 digest<br/>确定性回退"]
    NARRATIVE --> DONE["done 事件<br/>trace + 可选会话快照"]
    FALLBACK --> DONE
    DONE --> SSE_END(["SSE 完成"])
```

编排边界：

| 维度 | 当前实现 |
| --- | --- |
| 控制方式 | LangGraph 固定 DAG；只有执行失败会进入 `execute → repair → execute` 的有界回路 |
| 可用工具 | Schema 检索、模型网关、SQL AST 门禁、只读数据库、会话存储；模型不能自行新增工具 |
| 模型职责 | 生成候选 SQL、结合错误修复 SQL、基于确定性摘要写分析结论；不改写结果行、SQL 或证据 |
| 记忆范围 | 会话级短期上下文；最多 20 轮、7 天过期，不保存跨会话用户画像 |
| 停止条件 | SQL 成功，或校验/执行失败且修复额度耗尽；返回结果另受 `MAX_RESULT_ROWS` 截断边界约束 |
| 可观测性 | `GraphState` 保存计划、Schema 命中、SQL、尝试记录、告警和 `trace_id` |

## 核心能力

| 模块 | 工程实现 | 价值 |
| --- | --- | --- |
| 混合召回 | BGE 语义向量与关键词召回融合；Qdrant 默认内存模式，Elasticsearch BM25 可选 | 兼顾自然语言近义表达与业务字段精确匹配 |
| 查询规划 | 识别销售、品类、退款、投放和趋势意图，解析时间、地区、渠道、品类条件 | 将开放问题收敛为可验证的查询计划 |
| SQL 生成 | 离线规则生成器与 OpenAI 兼容模型生成共用同一执行链路 | 无模型可运行，接入模型后覆盖更自然的问法 |
| 安全执行 | sqlglot AST 校验、单条 SELECT、表/函数白名单、敏感字段拦截、只读连接、行数限制 | 在数据库执行前落实数据访问边界 |
| 有界修复 | LangGraph 捕获执行错误，结合原 SQL 与 Schema 反馈给模型，默认最多 2 次 | 失败可追踪，修复成本可控 |
| 模型网关 | 在线拉取模型、测试连接、保存服务端配置、密钥脱敏 | 模型接入与业务编排解耦 |
| 结果解读 | 先返回 SQL 结果，再以 SSE 流式输出模型分析结论；模型失败、超时或返回空内容时回退为确定性摘要 | 缩短首屏等待，结论仍可追溯和复现 |
| 可观测性 | 召回分数、执行 SQL、尝试记录、耗时、告警和 trace ID | 从问题到结果全过程可追问 |
| 会话记忆 | 保存近期问题、结构化查询上下文和结果；追问显示实际继承项 | 支持连续分析，同时明确条件来源 |
| 质量门禁 | 确定性数据、6 条黄金用例、39 项自动化测试、CI | 每次变更均可回归验证 |

## 安全与治理

模型输出被视为不可信输入。规则、模型与修复生成的 SQL 都必须经过同一套门禁：只允许单条 `SELECT`；校验授权表和函数；拒绝 `SELECT *`；拦截敏感字段；使用只读数据库连接；限制结果集与修复次数。模型密钥只保存在服务端，读取接口不返回明文；模型调用或修复失败会进入响应告警和查询轨迹。

详见 [SECURITY.md](SECURITY.md)。

## 模型接入

打开 `/models` 即可完成模型接入：配置 OpenAI 兼容 Base URL 与 API Key、在线拉取模型、运行连通性测试、保存并启用。配置写入被 Git 忽略的 `data/model_config.json`，读取接口仅返回脱敏状态。

| 接口 | 用途 |
| --- | --- |
| `GET /api/model/config` | 获取脱敏配置 |
| `PUT /api/model/config` | 保存服务端配置 |
| `POST /api/model/models` | 拉取可用模型 |
| `POST /api/model/test` | 验证端点、密钥与模型 |

![模型配置](docs/atlas-model-config.png)

## 可观测性与 API

| 接口 | 说明 |
| --- | --- |
| `GET /api/health` | 数据库、检索后端、模型激活状态与修复配置 |
| `GET /api/overview` | 经营总览指标 |
| `POST /api/query` | 同步执行完整 NL2SQL 查询链路，返回结构化结果与最终分析结论 |
| `POST /api/query/stream` | 先返回 SQL 结果，再通过 SSE 流式推送分析结论 |
| `GET/POST /api/sessions` | 列出或创建分析会话 |
| `GET/DELETE /api/sessions/{id}` | 恢复或删除会话 |
| `GET /api/evaluation` | 运行黄金评测集 |
| `GET /docs` | OpenAPI 交互文档 |

同步响应包含 `mode`、`plan`、`retrieval`、`sql`、`rows`、`attempts`、`elapsed_ms`、`warnings`、`trace_id`、`narrative` 和 `answer`；`answer` 是最终结论文本，`narrative.mode` 标识 `model` / `digest`。流式接口的 `result` 事件先携带完整结构化结果与首屏确定性摘要，最终结论在后续 `narrative` 事件的 `text` 字段中返回。会话查询还返回 `session_id` 与更新后的会话快照；`plan.inherited` 明确列出本轮继承的时间、指标或筛选条件。

### 流式分析

`POST /api/query/stream` 使用 Server-Sent Events (SSE)。标准事件顺序为：

```text
result → [thinking] → (delta* | reset?) → narrative → done
```

- `result`：SQL 已执行完成，包含完整结果、SQL、证据和首屏确定性摘要，前端可立即展示结果表。
- `thinking`：可选，模型正在组织答案。
- `delta`：追加模型原生流式内容；模型未启用时，确定性摘要作为单个 `delta` 推送。
- `reset`：模型流中途失败时，用确定性摘要替换已收到的草稿。
- `narrative`：最终结论文本及来源模式（`model` / `digest`），客户端应以该事件为准。
- `done`：本轮完成；存在 `session_id` 时返回已持久化的会话快照。

```bash
curl -N -X POST http://127.0.0.1:8000/api/query/stream \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"question":"上个月各渠道ROI","session_id":null}'
```

浏览器端应使用 `fetch` + `ReadableStream` 读取 POST 响应；原生 `EventSource` 只支持 GET，不适用于该接口。SSE 只覆盖结果解读阶段，SQL 生成与执行在 `result` 事件前完成。

## 数据域

内置确定性数据集覆盖 2025-10 至 2026-02，包含 `orders`、`order_items`、`refunds`、`marketing_spend` 和 `customers` 五张业务表，支持销售、品类、退款、投放效率、客单价和趋势分析。

## 质量门禁

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m app.evaluation.run
```

当前快照：**39 passed**、**6/6 黄金用例命中**、SQL 执行率 **100%**、结果命中率 **100%**。评测固定使用规则生成器，避免外部模型服务影响可复现性；CI 在每个 `main` 提交上执行测试与评测。

![评测中心](docs/atlas-evaluation-latest.png)

详见 [docs/EVALUATION.md](docs/EVALUATION.md)。

## 快速启动

要求 Python 3.10+，已在 Python 3.12 和 3.13 验证。

```bash
python -m pip install -r requirements.txt
python -m app.nl2sql.seed
python -m uvicorn app.api.main:app --reload --port 8000
```

Windows 可运行 `run.ps1`，macOS / Linux 可运行 `./run.sh`。

| 页面 | 地址 |
| --- | --- |
| 分析会话 | http://127.0.0.1:8000/workspace |
| 经营总览 | http://127.0.0.1:8000/overview |
| 检索与证据 | http://127.0.0.1:8000/evidence |
| 评测中心 | http://127.0.0.1:8000/evaluation |
| 模型配置 | http://127.0.0.1:8000/models |
| OpenAPI 文档 | http://127.0.0.1:8000/docs |

默认 `USE_BGE=true`，首次初始化检索器时会下载本地 BGE 模型；Qdrant 使用内存模式并重建 5 张表的向量索引。设置 `QDRANT_URL` 可连接远端 Qdrant，设置 `ELASTICSEARCH_URL` 可启用 BM25。

## 配置

复制 `.env.example` 为 `.env`：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_ENABLED` | `false` | 启用模型 SQL 生成与结果解读 |
| `LLM_PROVIDER` | `openai` | 服务商标识，也可在 `/models` 中配置 |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容接口 |
| `OPENAI_API_KEY` | 空 | 模型密钥 |
| `LLM_MODEL` | `gpt-4.1-mini` | 默认模型 |
| `USE_BGE` | `true` | 启用本地语义检索 |
| `EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | 本地向量模型 |
| `QDRANT_URL` | 空 | 远端 Qdrant；空值使用内存模式 |
| `ELASTICSEARCH_URL` | 空 | Elasticsearch；空值使用本地 IDF |
| `MAX_REPAIR_ATTEMPTS` | `2` | SQL 修复上限 |
| `MAX_RESULT_ROWS` | `200` | 结果返回上限 |
| `ATLAS_SESSION_DB_PATH` | `data/sessions.db` | 短期会话存储路径 |

## 工程文档

- [架构设计](docs/ARCHITECTURE.md)：组件边界、查询时序、失败模型与扩展点。
- [运行手册](docs/OPERATIONS.md)：启动、健康检查、模型接入、故障定位与部署检查。
- [评测说明](docs/EVALUATION.md)：数据、指标、复现方式与 CI 策略。
- [安全策略](SECURITY.md)：SQL 门禁、密钥管理与报告流程。
- [贡献指南](CONTRIBUTING.md)：开发流程、测试要求与提交检查。

## License

MIT。见 [LICENSE](LICENSE)。
