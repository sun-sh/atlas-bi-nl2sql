# HTTP API

Atlas BI 的交互式 OpenAPI 文档位于运行实例的 `/docs`，默认地址为 <http://127.0.0.1:8000/docs>。

## HTTP 接口

| 接口 | 说明 |
| --- | --- |
| `GET /api/health` | 当前分析库、检索后端、模型状态与修复配置 |
| `GET /api/metrics` | 进程内请求数、错误率和 P50/P95/Max 延迟；查询路由默认不消费检索补充 |
| `GET /api/overview` | 经营总览指标 |
| `GET /api/examples` | 获取工作台示例问题 |
| `POST /api/query` | 同步执行完整 NL2SQL 查询 |
| `POST /api/query/stream` | SSE 流式查询与结果解读 |
| `POST /api/query/cancel/{trace_id}` | 请求停止运行中的查询 |
| `GET/POST /api/sessions` | 列出或创建会话 |
| `GET/DELETE /api/sessions/{id}` | 恢复或删除会话 |
| `GET /api/data-assets` | 固定业务域的数据资产巡检 |
| `GET /api/data-platform` | 数据中台概览、质量、血缘和审计 |
| `GET/POST /api/data-platform/workspaces` | 工作区列表与创建 |
| `GET/POST /api/data-platform/sources` | 数据源列表与注册 |
| `POST /api/data-platform/sources/{id}/sync` | 执行快照或入库同步 |
| `POST /api/data-platform/sources/{id}/activate` | 将兼容 SQLite/PostgreSQL/MySQL 设为当前 NL2SQL 分析库 |
| `GET/POST /api/data-platform/metrics` | 指标目录 |
| `GET/POST /api/data-platform/quality-rules` | 质量规则 |
| `POST /api/data-platform/sources/{id}/quality` | 执行质量巡检 |
| `GET /api/data-platform/data-service` | 受控只读数据服务（当前仅支持 SQLite 快照） |
| `POST /api/data-platform/scheduler/run` | 手动执行到期同步 |
| `GET/POST /api/evaluation/datasets` | 评测数据集版本 |
| `GET/POST /api/evaluation/experiments` | 评测实验 |
| `GET/POST /api/evaluation/feedback` | 失败用例反馈 |
| `POST /api/evaluation/tasks/{id}/regression-candidates` | 从失败报告生成待审核回归候选 |
| `GET /api/evaluation/regression-candidates` | 查看待审或已批准的回归候选 |
| `POST /api/evaluation/regression-candidates/approve` | 审核候选并生成数据集版本 |
| `GET/POST /api/evaluation/tasks` | 查看或提交评测任务 |
| `GET /api/evaluation/tasks/{id}` | 任务进度与报告 |
| `POST /api/evaluation/tasks/{id}/cancel` | 取消任务 |
| `POST /api/evaluation/tasks/{id}/retry` | 重试已经结束的任务 |
| `POST /api/evaluation/queue/claim` | 手动领取一个排队任务 |
| `GET /api/evaluation/compare` | 对比两次运行；返回可比性标记 |
| `GET /api/evaluation` | 旧同步评测入口，默认返回 410；仅显式设置 `ATLAS_ENABLE_SYNC_EVALUATION=1` 时启用 |
| `GET /docs` | OpenAPI 交互文档 |

同步查询返回计划、召回结果、SQL、数据、尝试记录、告警、`trace_id` 和解读。`narrative.mode` 标明解读来自模型还是确定性摘要。带会话的查询还会返回 `session_id` 和更新后的快照，`plan.inherited` 列出继承了哪些条件。评测任务走 `POST /api/evaluation/tasks`；`GET /api/evaluation` 默认关闭，避免公网请求同步跑完整评测并消耗模型额度。

## SSE 事件

`POST /api/query/stream` 使用 Server-Sent Events：

```text
start → status* → result → [thinking] → (delta* | reset?) → narrative → done
```

- `status`：召回、规划、生成、执行、修复和整理阶段。
- `result`：SQL 已执行完成，先返回结果、SQL、检索依据和首屏摘要。
- `thinking` / `delta`：模型开始写结论及逐段文本。
- `reset`：流中途失败时，用确定性摘要替换不完整草稿。
- `narrative`：最终文本和 `model` / `digest` 来源。
- `done`：本轮完成；有会话时返回持久化快照。
- `cancelled`：用户停止分析或请求中断；浏览器保留已收到的可信结果。

```bash
curl -N -X POST http://127.0.0.1:8000/api/query/stream \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"question":"上个月各渠道ROI","session_id":null}'
```
