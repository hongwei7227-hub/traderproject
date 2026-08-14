
## 0. 全局约定

### 0.1 鉴权依赖

| 依赖 | 行为 |
|---|---|
| `CurrentUserId` | ①配了服务令牌且请求带 `X-Service-Token`：常时比对，不匹配 → **401**；匹配则取 `X-User-Id` 头，缺失 → **401**（**服务间可冒充任意用户**）。②`HOST_MODE == "oss"` → 直接返回本地用户，**无鉴权**。③否则要求 `Authorization: Bearer <JWT>`，缺失 → 401，走 JWKS 验签 |
| `AuthInfo` | 同上但**不支持服务令牌**；额外返回 `app_metadata.provider` |
| `authenticate_websocket` | 必须在 `accept()` **之前**调用。token 来源：`Authorization` 头 → `?token=`。失败 → `close(1008)` |
| `ChatRateLimited` | 内含 `CurrentUserId` + burst 限流，返回 `{user_id, is_byok, has_oauth, access_tier, burst_slot_id}` |
| `StampThreadAuth` | 服务令牌 + 无 `X-User-Id` 时返回 `None`（特权调用跳过归属校验） |

### 0.2 授权原语

- `require_thread_owner(thread_id, user_id)` → 无行 **404** / 非本人 **403**
- `require_workspace_owner(workspace, user_id)` → 空 **404** / 非本人 **403**
- 大量端点把归属下沉到 SQL（`WHERE ... AND user_id = ?`），越权表现为 **404 而非 403**——这是**有意的**，防资源枚举

### 0.3 统一异常装饰器

`handle_api_exceptions(action, logger, conflict_on_value_error=False)`：

- `HTTPException` 原样抛
- `ValueError` → 409（仅当 `conflict_on_value_error=True`）
- 其他 → 记 `logger.exception` 后 **500 `{"detail": "Failed to {action}"}`**（**不泄漏内部异常文本**）

### 0.4 错误响应形状

```
{"detail": <str 或 dict>}
```

`detail` 为 dict 时的常见结构：`{message, type, retry_after?, link?: {url, label}}`。
前端按 `type` 分支渲染。**422** 来自 Pydantic，形状是 `{"detail": [{loc, msg, type, input, url}]}`。

## 1. 端点清单

### 1.1 Threads（34 个）

| # | 方法 路径 | 鉴权 | 授权 | 关键行为 |
|---|---|---|---|---|
| 1 | `POST /threads` | `CurrentUserId` | `require_workspace_owner` | 201。预建线程行 + 异步生成标题。**限流是函数内联**不是 Depends |
| 2 | `GET /threads` | ✓ | 仅传 workspace_id 时校验 | 分页；`archived=true` 必须带 workspace_id 否则 400 |
| 3 | `GET /threads/{id}` | ✓ | `require_thread_owner` | 单线程元数据 |
| 4 | `POST /threads/{id}/seen` | ✓ | ✓ | 因果式已读游标；run 非终态/不属本线程 → **409** `seen_not_applicable` |
| 5 | `GET /threads/{id}/market-watch` | ✓ | ✓ | feature 关闭时**返回空列表**而非 403 |
| 6 | `DELETE /threads/{id}` | ✓ | ✓ | 独占围栏；冲突 **409**、不可用 **503** |
| 7 | `PATCH /threads/{id}` | ✓ | ✓ | 只应用 `model_fields_set` 中显式出现的字段 |
| 8 | `PUT /threads/{id}/external-id` | `StampThreadAuth` | **条件式** | 特权服务调用跳过归属；冲突 **409** |
| 9 | `POST /threads/messages` | `ChatRateLimited` | workspace 维度 | 新建线程门，SSE |
| 10 | `POST /threads/{id}/messages` | `ChatRateLimited` | owner 比对 | **主链路**，SSE |
| 11 | `GET /threads/{id}/messages/stream` | ✓ | ✓ | 重连；**分类必须在构造响应之前做**（生成器内抛错会在 200 之后到达） |
| 12 | `GET /threads/{id}/watch` | ✓ | ✓ | 45s keepalive，**30 分钟自动关闭** |
| 13 | `GET /threads/{id}/messages/replay` | ✓ | 手工复刻 | `source=auto\|checkpoint\|sse`；`X-Replay-Source` 响应头 |
| 14 | `GET /threads/{id}/status` | ✓ | 手工复刻 | `fields=report_back` 走廉价路径 |
| 15 | `GET /threads/dispatches/liveness` | ✓ | **SQL 内联过滤** | 批量；上限 100，**超出静默丢弃** |
| 16 | `POST /threads/{id}/cancel` | ✓ | ✓ | `run_id` 可选，防误杀新一轮 |
| 17 | `POST /threads/{id}/summarize` | ✓ | ✓ | 手动压缩；超时 **504** |
| 18 | `POST /threads/{id}/offload` | ✓ | ✓ | 仅 Tier 1 参数卸载 |
| 19 | `GET /threads/{id}/turns` | ✓ | ✓ | turn 边界 checkpoint |
| 20 | `POST /threads/{id}/retry` | `ChatRateLimited` | ✓ | **五段 409 语义，顺序敏感**（见 §3.3） |
| 21 | `GET /threads/{id}/stream` | ✓ | ✓ | 多路复用；`contract` 必须为 `v2` 否则 400 |
| 22-26 | `.../tasks/{task_id}[/status\|/history\|/cancel\|/messages]` | ✓ | ✓ | `task_id` 必须匹配 `^[A-Za-z0-9_-]{1,12}$` |
| 27-28 | `POST\|GET /threads/{id}/share` | ✓ | ✓ | 关闭分享时 token 保留复用 |
| 29-31 | `POST\|GET\|DELETE /threads/{id}/feedback` | ✓ | ✓ | rating 限 `thumbs_up\|thumbs_down` |
| 32-34 | `GET /threads/{id}/provenance[/bodies\|/{rid}/body]` | ✓ | ✓ | 只给 snippet，body 另取 |

### 1.2 用户与配置

| 路径 | 要点 |
|---|---|
| `POST /auth/sync` | 登录后同步。**【缺陷】legacy 邮箱迁移分支用客户端可控的 `body.email`，未与 JWT 声明比对即执行 `migrate_user_id`——账号接管面** |
| `GET\|PUT /users/me` | tier 与计划名共用 Redis 缓存 5 分钟；`refresh_tier=true` 强制穿透 |
| `GET\|PUT\|DELETE /users/me/preferences` | 用 `exclude_unset`（**不是** `exclude_none`），显式传 `null` = 删除该 key。自定义模型/供应商校验极密集（见 §4） |
| `POST /users/me/avatar` | **【缺陷】`await file.read()` 无大小上限**，与 memo 的分块防护形成对比 |
| `GET\|PUT /features[/{key}]` | 未知 key 404；gate 不允许覆盖 403。**整包读改写，并发 PUT 不同 key 互相覆盖（有意的 last-writer-wins）** |
| `/users/me/watchlists/*` | 10 个端点。字面量 `"default"` 解析为默认列表，**不存在则自动创建** |
| `/users/me/portfolio/*` | 5 个。POST **状态码动态**：命中已有持仓则合并并改写为 200 |
| `/brokers/ibkr/*` | 凭据从不落库。**【缺陷】`_REF_CODE_CACHE` 键只含 query_id 不含 user_id，跨用户串味** |

### 1.3 数据与代理

| 路径 | 鉴权 | 要点 |
|---|---|---|
| `GET /news[/{id}]` | ✓ | **两层防击穿**：进程内 single-flight + Redis 分布式锁（TTL 30s，等待方 50ms 轮询最长 3s）。全局 300s / 带 ticker 180s。有游标或过滤参数时**完全旁路缓存** |
| `/calendar/*` | ✓ | 4 个。Query 用 `alias="from"/"to"`。**【缺陷】异常时把 `str(e)` 放进 500 detail** |
| `/sec-proxy/document` | **无！** | **【缺陷】完全开放的对外 HTTP 代理**。白名单只对首跳生效（`follow_redirects=True`），无体积上限、无限流 |
| `/market-data/*` | ✓ | 14 个。符号边界规范化把所有拼法折叠成单一 key。**【缺陷】`analyst-data` 缓存键不含 `grade_limit`，先发 200 会污染后续 1** |
| `/market-data/bars/{instrument}` | ✓ | 协议原生序列。三种模式：live / `after`（**含端点**，因为游标那根 bar 可能还在形成）/ `before`（周期对齐翻页） |
| `WS /ws/v1/market-data/aggregates/{market}` | ws_auth | 每连接 200 符号上限，超额**静默丢弃**。WS 写缓存与 REST 读同一个键 |
| `/skills` | **无！** | **【缺陷】未认证可枚举全部技能与工具清单** |
| `/models` | **无！** | 有意公开（配置信息） |

### 1.4 存储类

| 路径 | 要点 |
|---|---|
| `/memory/*` | 4 个只读。namespace 带 user_id 双保险。store 未接线 **503**，操作超时 2s → **504** |
| `/memo/*` | 7 个。namespace 用**复数** `memos`（前缀匹配会撞 `memory`）。上传 **202**，元数据后台生成。**两阶段 + 补偿**：慢的 S3 PUT 在锁外，失败删孤儿 blob |
| `/workflows/*` | 4 个。**条件注册**——功能关闭时整组 404。列表编译预算 32，超预算的行 `valid=None`（三态） |
| `/public/shared/{token}/*` | **全部无鉴权**，凭不透明令牌。**脱敏是安全关键**：replay 必须剔除 `workspace_id`（它是文件服务的 bearer 凭据） |

### 1.5 SSE 响应头

```
Cache-Control: no-cache
X-Accel-Buffering: no
Connection: keep-alive
Content-Type: text/event-stream
```

`POST /threads[/{id}]/messages` 与 `/retry` 额外带：

```
Content-Location: /api/v1/threads/{thread_id}/messages/stream?run_id={run_id}
```

`replay` 额外带 `X-Replay-Source: checkpoint|sse`（**注意它用的是自定义头组，没有 `X-Accel-Buffering`**）。

## 2. SSE 事件协议

### 2.1 顺序契约

1. **首个事件必须是 `metadata`**，携带 `run_id`——重连的客户端靠它续上
2. 每帧带 `id: {seq}` 递增，**必须连续无洞**（重连按 `Last-Event-ID` 续传）
3. 终态事件恰好一次

### 2.2 事件类型

| 事件 | 用途 |
|---|---|
| `metadata` | 开场，带 run_id |
| `message_chunk` | 文本增量 |
| `tool_calls` / `tool_call_result` | 工具调用与结果 |
| `artifact` | 产物（`file_operation` / `todo_update` / 图表等，用 `artifact_type` 判别） |
| `provenance` | 数据来源记录（**不进 LLM context**） |
| `context_window` | 压缩生命周期，`action` 判别 `token_usage\|summarize\|offload` |
| `model_retry` | 重试（实时通道，不回放） |
| `model_fallback` | 降级（**走可回放通道**，双写 checkpoint） |
| `steering_delivered` | steering 已投递 |
| `workflow_started` | 新 workflow 启动（`/watch` 专用） |
| `user_message` / `snapshot` / `replay_done` | 回放专用 |
| `error` | 错误终态 |

### 2.3 重连与回放

- **重连**：`GET /{id}/messages/stream?run_id=&last_event_id=`。分类在**构造响应之前**做，可抛 404（run 不属本线程）/ **410** `stream_expired`（终态且流已消失）/ **503** `transport_unavailable`
- **回放**：`GET /{id}/messages/replay?source=auto|checkpoint|sse`。`auto` 在 checkpoint 不可用时静默降级到 sse；显式 `checkpoint` 不可用则 **409**
- **多路复用**：`GET /{id}/stream?contract=v2&cursors=run:{rid}#{eid},...`

## 3. 关键路径的错误码语义

### 3.1 发消息（`_handle_send_message`）

| 场景 | 码 |
|---|---|
| burst 并发超限 | **429** `{type: "burst_limit", retry_after: 5}` + `Retry-After: 5` |
| `request_key` 重传 | **409** `duplicate_request`；**只对该 run 的 owner** 附加 thread_id/run_id（否则裸 409，fail closed） |
| 平台模式且无任何 provider | **403** `{type: "no_provider", link: {...}}` |
| 非 internal 却发 `X-Dispatch: background` | **403** |
| 线程 owner 不符 | **403** |
| agent config 未初始化 | **503** |
| ptc 模式无 workspace_id | **400** |
| 额度超限 | **429** `credit_limit` / `negative_balance` |
| 未配 Redis 事件存储 | **503** `transport_unavailable`（无 Retry-After） |
| Redis PING 失败 | **503** + **`Retry-After: 3`** |

**关键不变式**：`run_id` 在此唯一生成，贯穿流键、checkpoint config、SSE 首个 metadata 事件，与响应记录 1:1。所有前置失败路径必须 `release_slot()` 归还 burst 名额（幂等）。

### 3.2 重试端点的 409 顺序（**顺序敏感**）

```
1. transport 检查        → 503
2. 重传探测              → 409 duplicate_request   ← 必须最先，否则重复 /retry 会被误判成 stale_retry
3. 线程无 run            → 404
4. run_id 与最新不符     → 409 stale_retry
5. 最新是 in_progress    → 409 running
6. 最新状态非 error      → 409 not_retryable
7. 解析不到 workspace    → 404
```

任何早退都必须归还 burst 名额，否则反复 stale retry 会耗尽用户并发额度直到 TTL。

`retry_of_run_id` 是**路由内部参数，永不经过 body**——防止伪造把新 attempt 挂到任意失败 run 上。

## 4. 配额与限流

### 4.1 门控层级

```
HOST_MODE ("oss" | "platform")    主开关，oss 跳过所有门
AUTH_SERVICE_URL                  平台配额服务，可能缺失
→ _platform_gating_active() = HOST_MODE != "oss" and bool(AUTH_SERVICE_URL)
```

**全局策略：fail-open**——平台服务不可达时一律放行。

### 4.2 Burst guard（本地 Redis ZSET）

key `usage:burst:slots:{user_id}`，一条 pipeline 原子完成：

```
ZREMRANGEBYSCORE key -inf (now - horizon)   # 按分数收割泄漏的陈旧成员
ZADD key {slot_id: now}
ZCARD key
EXPIRE key horizon
```

超限时**只回滚自己的成员**（`ZREM key slot_id`）。

**为什么用 ZSET 而非计数器**：①释放是 `ZREM`，**幂等**，所以可以搭 outbox 重试顺风车；旧的 DECR 一旦重试就会释放从未持有的槽位。②崩溃遗留的陈旧成员每次检查按分数收割，泄漏能自愈。

**收割视界必须大于最长合法运行时长**（`workflow_timeout + 300`）——视界过短会收割仍在运行的 turn 的槽位，长任务悄悄不再计入上限。

### 4.3 三层限流

| 层 | 作用 |
|---|---|
| 全局 | 保平台 |
| 租户 | 保公平 |
| 用户并发 | 防单点滥用 |

### 4.4 缓存矩阵

| 缓存 | 存储 | TTL | 负缓存 |
|---|---|---|---|
| BYOK 余额 | Redis | 60s | — |
| 平台会员（tier + 计划名） | Redis | 300s | 15s（防对已宕服务的惊群） |
| 用户 scopes | **进程内 dict** | 300s | 15s |
| burst 槽位 | Redis ZSET | = 收割视界 | — |

**【缺陷】scopes 缓存是进程内且无失效接口**——多 worker 下权益变更各 worker 不同步，最长滞后 5 分钟。

**关键语义**：空列表 `[]` 是平台的确定性答案**并会被执行**（不授予任何 scope 的用户不能伪装成"服务下线"溜过去）；只有 `None` 才是 fail-open 信号。

## 5. 状态词表

公共词表是一个**划分（partition）**而非列表：

```
LIVE     = (queued, running, stopping, recovering)
TERMINAL = (completed, failed, cancelled)
PUBLIC   = {idle, interrupted} ∪ LIVE ∪ TERMINAL      # 共 9 个
```

从两个族**派生**，所以新增状态被强制选择归属族。前端手工镜像这两个集合。

**内部词表与公共词表不同**：

```
内部 run 行:  in_progress / completed / interrupted / error / cancelled
公共:        运行中→running/stopping/recovering, error→failed, unknown→idle
```

⚠️ run 行持久化的是 `error`，`failed` 只存在于映射下游。**拿公共词表做 NOT-IN 过滤会把每个出错的 run 永远归类为 live**。

`interrupted` 算 **live-LIKE**（它在等用户，归 feed 的 live 分支）。

映射函数 `to_public(raw, *, cancel_requested_at, has_executor)`：

- 值在内部 live 集中时按持久状态细化：有取消时间戳 → `stopping`；`has_executor is False`（**三态，None = 未知**）→ `recovering`；否则 `running`
- 否则走 legacy 映射，结果不在公共集中则塌缩为 `idle`（**绝不泄露内部拼写**）

## 6. 应用装配

### 6.1 中间件（注册顺序的**反序**执行）

```
请求 → CORS → RequestID → MalformedIdDiag → GZip → 路由
```

- **CORS**：显式方法列表非 `*`，`allow_credentials=True`
- **RequestID**：纯 ASGI 类（**刻意不用 BaseHTTPMiddleware**）；`OPTIONS` 立即放行；响应头 `x-trace-id`。**【缺陷】`scope["state"] = {...}` 整体覆盖会丢弃上游写入**
- **MalformedIdDiag**：临时诊断中间件。日志注入防护——用户可控字段一律 `%r` 打印（转义 CR/LF）+ 长度上限
- **GZip**：`minimum_size=1000`。**【缺陷】会压缩 SSE 流**

### 6.2 全局异常处理器

**没有注册任何全局异常处理器。**错误响应完全由三层决定：框架默认 → `handle_api_exceptions` 装饰器 → 各路由自行 try/except。

未捕获异常返回**纯文本** `Internal Server Error`（非 JSON）。

### 6.3 启动顺序（关键因果）

除 DB 池、平台密钥错误、多 worker 门外，**所有启动步骤失败都只 warn 并继续**。

值得复刻的顺序约束：

1. **OTel 分两阶段**：类级 instrumentor 补丁必须在构造 app **之前**；provider 与守护线程必须每 worker 自己建（不能跨 fork 存活）
2. **MCP registry 失败时先强制拆子进程**再降级——普通 disconnect 在 frozen 后短路，会泄漏
3. **outbox drainer 在恢复扫描之前启动**，让扫描入队的作业立刻执行
4. **executor 注册放在 try 之外**——注册失败是代码 bug，必须大声崩溃；吞掉会留下一个永久关闭的 drainer 藏在一行 WARN 后面
5. **多 worker 硬门放在顶层**，不能被 catch-all 吞掉：无写者围栏时宁可拒绝启动

### 6.4 关闭顺序（严格因果）

```
恢复扫描器（最先，排空期不要新工作）
→ 各监听器（反序，保证没有东西还在往已消失的 listener 发布）
→ 价格监控 → 调度器（让执行能排空）
→ 后台任务注册表
→ workspace 管理器（先排空 warm 任务，让中途取消的任务 revert 状态而非卡死）
→ session 服务
→ outbox drainer（在后台任务之后，让最终 finalize 入队的作业还能执行）
→ 写者池、checkpoint 池（同理在后台任务之后：取消活跃 run 会在退出路上 flush checkpoint）
→ DB 池 → 三个 Redis 池（无条件全关）
→ HTTP 客户端 → 浏览器
→ OTel（最后，且跑在工作线程上——force_flush 是同步的、可能阻塞 30s）
```

## 7. 已知缺陷（新实现必须修复）

| # | 缺陷 | 修复方向 |
|---|---|---|
| D1 | **`/sessions` 完全无鉴权**，泄露全部活跃 workspace_id + sandbox_id | 全端点默认需鉴权，公开端点显式标注 |
| D2 | **`/sec-proxy/document` 无鉴权的对外 HTTP 代理**，白名单只对首跳生效 | 关闭重定向跟随或每跳校验；加鉴权、体积上限、限流 |
| D3 | **`/skills` 无鉴权**可枚举全部技能 | 同 D1 |
| D4 | **JWT 不校验 issuer** | 签发方、受众、算法全部显式校验 |
| D5 | **服务令牌可冒充任意用户**且文档未提 | 至少限定可代理的租户范围 |
| D6 | `DELETE api-keys/{provider}` 缺缓存失效（PUT 有） | 写路径统一失效 |
| D7 | OAuth 回调把上游异常文本原样回传 | 复用同仓库已有的脱敏函数 |
| D8 | `/automations/{id}/trigger` 的 not-found 返回 409 而非 404 | 语义对齐 |
| D9 | `/automations/{id}/executions` 对不存在的 automation 返回 200 空列表 | 返回 404 |
| D10 | insights 的额度检查未传 `byok=` | BYOK 用户走欠费快路径 |
| D11 | SSRF 检查在 DNS 解析失败时放行，且只在 platform 生效；未覆盖 rebinding/TOCTOU | 解析后逐 IP 校验 + 连接期再校验 |
| D12 | scopes 进程内缓存无失效接口 | 移到共享缓存或加失效通道 |
| D13 | `/docs` 与 `/openapi.json` 生产公开 | 按部署模式关闭 |
| D14 | GZip 压缩 SSE 流 | 按 content-type 排除 |
| D15 | RequestID 整体覆盖 `scope["state"]` | 改为更新 |
| D16 | 头像上传无大小上限 | 分块读 + 上限（memo 已有正确实现可参照） |
| D17 | 日历/行情异常把 `str(e)` 放进 500 detail | 统一走脱敏装饰器 |
| D18 | analyst-data 缓存键不含 `grade_limit` | 参数进键 |
| D19 | IBKR ref code 缓存键不含 user_id | 键加租户段 |
| D20 | `/auth/sync` 用客户端可控 email 做账号迁移 | 与令牌声明比对 |

## 8. 重建验收清单

1. 首个 SSE 事件是 metadata 且带 run_id；序号连续；终态恰好一次
2. 重连分类在构造响应之前完成（否则错误码到达时已经 200）
3. 所有前置失败路径归还并发名额
4. 重试端点的七段判断顺序不变
5. 公共状态词表与内部词表严格分离，映射函数是唯一出口
6. 服务令牌路径的租户必须显式指名
7. 分享回放剔除 `workspace_id` 与 `sandbox_state`
8. 关闭顺序：outbox 与 checkpoint 池在后台任务之后
