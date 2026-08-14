# 数据持久层行为规格（spec-03-data-layer）

> 目标读者：不看原实现、用 **SQLAlchemy 2.0 async ORM** 重写本层的工程师。
> 原实现：裸 `psycopg3` + `psycopg_pool.AsyncConnectionPool`，手写 SQL 字符串，连接 `autocommit=True`。
> 数据库：PostgreSQL，需 `pgcrypto` 扩展（`gen_random_uuid()`）。
> 迁移基线：`migrations/versions/001` ~ `025`（alembic 线性链）。
>
> **本文描述"必须具备的行为"，不是"现有代码的形状"。** 标注 `【缺陷】` 的地方新实现必须修。

## 目录

1. [完整数据库 Schema](#1-完整数据库-schema)
2. [实体关系图](#2-实体关系图)
3. [数据访问行为契约](#3-数据访问行为契约)
4. [Redis 使用全貌](#4-redis-使用全貌)
5. [LangGraph Checkpointer 接入与连接池分工](#5-langgraph-checkpointer-接入与连接池分工)
6. [已知缺陷清单（新实现必修）](#6-已知缺陷清单新实现必修)

---

## 0. 全局约定

| 约定 | 内容 |
|---|---|
| 主键风格 | 业务表统一 `UUID PRIMARY KEY DEFAULT gen_random_uuid()`；`users` 例外，PK 是 `VARCHAR(255)` 的外部认证 id（如 `local-dev-user`、OIDC sub） |
| `user_id` 类型 | **全库统一 `VARCHAR(255)`**。migration 007 专门把 `market_insights.user_id` 从 UUID 改回 VARCHAR 就是为了这个统一（UUID 会破坏自托管/本地开发时认证层返回纯字符串的场景） |
| 时间列 | 一律 `TIMESTAMPTZ`；`created_at`/`updated_at` 默认 `NOW()` |
| `updated_at` 维护 | PL/pgSQL 触发器函数 `update_updated_at_column()`（001 定义），逐表挂 `BEFORE UPDATE FOR EACH ROW`。**`conversation_threads` 的该触发器在 022 被显式 DROP**（见 §6 D3），该表所有写路径必须手写 `updated_at = NOW()` |
| JSONB 默认 | `'{}'::jsonb` / `'[]'::jsonb`，多数列带 `NOT NULL DEFAULT` |
| 加密列 | `BYTEA`，应用层对称加密（密钥来自 `BYOK_ENCRYPTION_KEY`），DB 侧不解密、不索引明文 |
| NUL 字节 | 绑定到 TEXT/VARCHAR 的用户输入必须先剥离 `\x00`；绑定 JSONB 前必须剥离 `\u0000` 转义并把 NaN/Inf 置 NULL。否则 Postgres 抛 `cannot contain NUL` / `UntranslatableCharacter`，**整行写入丢失** |
| UUID 归一化 | 外部传入的 id 绑定到 `uuid` 列前必须 `str(uuid.UUID(x))` 归一化；解析失败直接当"未找到"返回 `None`，**不要**让 `22P02` 冒泡成 500。批量查询要在绑定前丢弃非法 id，一个坏 id 不能毒死整批 |
| 事务默认 | 连接层 `autocommit=True`，**任何多语句原子性必须显式开事务**。ORM 版建议反过来：默认事务，单语句读用 `AUTOCOMMIT` 隔离或短事务 |

---

## 1. 完整数据库 Schema

### 1.1 `users`

| 列 | 类型 | 约束 / 默认 | 说明 |
|---|---|---|---|
| `user_id` | VARCHAR(255) | **PK** | 外部认证主体 id |
| `email` | VARCHAR(255) | | 索引 `idx_users_email` |
| `name` | VARCHAR(255) | | |
| `avatar_url` | TEXT | | |
| `timezone` | VARCHAR(100) | | |
| `locale` | VARCHAR(20) | | |
| `onboarding_completed` | BOOLEAN | NOT NULL DEFAULT FALSE | 投资偏好引导 |
| `personalization_completed` | BOOLEAN | DEFAULT FALSE | BYOK 向导（006，注意**没有** NOT NULL） |
| `membership_id` | INT | NOT NULL DEFAULT 1 | 会员等级 |
| `byok_enabled` | BOOLEAN | NOT NULL DEFAULT FALSE | |
| `auth_provider` | VARCHAR(50) | | |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | 索引 `(created_at DESC)` |
| `updated_at` | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | 触发器维护 |
| `last_login_at` | TIMESTAMPTZ | | |

### 1.2 `workspaces`

| 列 | 类型 | 约束 / 默认 | 说明 |
|---|---|---|---|
| `workspace_id` | UUID | **PK** DEFAULT gen_random_uuid() | |
| `user_id` | VARCHAR(255) | NOT NULL, **FK → users ON DELETE CASCADE** | 租户键 |
| `name` | VARCHAR(255) | NOT NULL | |
| `description` | TEXT | | |
| `sandbox_id` | VARCHAR(255) | | 外部沙箱 id |
| `status` | VARCHAR(50) | NOT NULL DEFAULT 'creating'，CHECK ∈ {creating, running, **starting**, stopping, stopped, error, deleted, flash} | `starting` 由 009 加（懒重启中间态） |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | 触发器 |
| `last_activity_at` | TIMESTAMPTZ | | 空闲回收依据 |
| `stopped_at` | TIMESTAMPTZ | | |
| `config` | JSONB | DEFAULT '{}' | **用户可覆盖**，不得存安全关键状态 |
| `artifacts` | JSONB | NOT NULL DEFAULT '{}' | 004，按 port 索引的预览服务器命令 |
| `is_pinned` | BOOLEAN | NOT NULL DEFAULT FALSE | |
| `sort_order` | INTEGER | NOT NULL DEFAULT 0 | |
| `mcp_config_version` | INTEGER | NOT NULL DEFAULT 0 | 012，**任何 workspace MCP 行变更必须同事务 +1** |
| `resource_tier` | VARCHAR(32) | NOT NULL DEFAULT 'standard' | 016，standard/performance/max |
| `is_always_on` | BOOLEAN | NOT NULL DEFAULT FALSE | 016，禁用自动停止 |
| `platform_secret_version` | INTEGER | NOT NULL DEFAULT 0 | 021，服务端拥有，0 = 从未认证 |

**索引**：`(user_id)`、`(user_id, status)`、`(updated_at DESC)`、`(user_id, is_pinned DESC, sort_order ASC)`、部分索引 `(user_id) WHERE is_always_on`（配额计数）。

### 1.3 `workspace_files`

| 列 | 类型 | 约束 |
|---|---|---|
| `workspace_file_id` | UUID | PK |
| `workspace_id` | UUID | NOT NULL FK → workspaces CASCADE |
| `file_path` | VARCHAR(1024) | NOT NULL |
| `file_name` | VARCHAR(255) | NOT NULL |
| `file_size` | BIGINT | NOT NULL DEFAULT 0 |
| `content_hash` | VARCHAR(64) | 同步差异判定 |
| `content_text` | TEXT | 文本内容 |
| `content_binary` | BYTEA | 二进制内容 |
| `mime_type` | VARCHAR(255) | |
| `is_binary` | BOOLEAN | NOT NULL DEFAULT FALSE（决定读哪一列） |
| `permissions` | VARCHAR(10) | |
| `sandbox_modified_at` | TIMESTAMPTZ | |
| `created_at` / `updated_at` | TIMESTAMPTZ | 触发器 |

**联合唯一**：`unique_file_per_workspace UNIQUE (workspace_id, file_path)`
→ 工作区内路径唯一；是全量文件同步 upsert 的冲突目标。**索引**：`(workspace_id)`。

### 1.4 `user_preferences`

| 列 | 类型 | 约束 |
|---|---|---|
| `user_preference_id` | UUID | PK |
| `user_id` | VARCHAR(255) | **UNIQUE** NOT NULL FK → users CASCADE |
| `risk_preference` / `investment_preference` / `agent_preference` / `other_preference` | JSONB | DEFAULT '{}' |
| `created_at` / `updated_at` | TIMESTAMPTZ | 触发器 |

`UNIQUE(user_id)` → 一人一行，是 upsert 冲突目标。

### 1.5 `watchlists` / `watchlist_items`

**watchlists**

| 列 | 类型 | 约束 |
|---|---|---|
| `watchlist_id` | UUID | PK |
| `user_id` | VARCHAR(255) | NOT NULL FK → users CASCADE |
| `name` | VARCHAR(100) | NOT NULL |
| `description` | TEXT | |
| `is_default` | BOOLEAN | NOT NULL DEFAULT FALSE |
| `display_order` | INTEGER | DEFAULT 0 |
| `created_at` / `updated_at` | TIMESTAMPTZ | 触发器 |

**联合唯一**：`unique_user_watchlist_name UNIQUE (user_id, name)` → 同一用户不能重名清单，跨用户可重名。索引 `(user_id)`。

**watchlist_items**

| 列 | 类型 | 约束 |
|---|---|---|
| `watchlist_item_id` | UUID | PK |
| `watchlist_id` | UUID | NOT NULL FK → watchlists CASCADE |
| `user_id` | VARCHAR(255) | NOT NULL FK → users CASCADE（**冗余租户键**，为免 JOIN 直接按用户查） |
| `symbol` | VARCHAR(50) | NOT NULL |
| `instrument_type` | VARCHAR(30) | NOT NULL |
| `exchange` / `name` | VARCHAR | |
| `notes` | TEXT | |
| `alert_settings` / `metadata` | JSONB | DEFAULT '{}' |
| `created_at` / `updated_at` | TIMESTAMPTZ | 触发器 |

**联合唯一**：`unique_watchlist_item UNIQUE (watchlist_id, symbol, instrument_type)`
→ 同一清单里同一标的（**按品种区分**：股票/期权/加密）只能出现一次；同名不同 `instrument_type` 是两条合法记录。

**索引**：`(watchlist_id)`、`(user_id)`、`(symbol)`、`(user_id, symbol, instrument_type)`、`(created_at DESC)`。

### 1.6 `user_portfolios`

| 列 | 类型 | 约束 |
|---|---|---|
| `user_portfolio_id` | UUID | PK |
| `user_id` | VARCHAR(255) | NOT NULL FK → users CASCADE |
| `symbol` | VARCHAR(50) | NOT NULL |
| `instrument_type` | VARCHAR(30) | NOT NULL |
| `exchange` / `name` | VARCHAR | |
| `quantity` | DECIMAL(18,8) | NOT NULL |
| `average_cost` | DECIMAL(18,4) | |
| `currency` | VARCHAR(10) | DEFAULT 'USD' |
| `account_name` | VARCHAR(100) | **可空** |
| `notes` | TEXT | |
| `metadata` | JSONB | DEFAULT '{}' |
| `first_purchased_at` | TIMESTAMPTZ | |
| `created_at` / `updated_at` | TIMESTAMPTZ | 触发器 |

**联合唯一**：`unique_user_holding UNIQUE (user_id, symbol, instrument_type, account_name)`
→ 同一用户、同一标的、同一券商账户下只有一条持仓；加仓走 upsert 合并数量与均价。
→ **【缺陷 D6】** `account_name` 可空，Postgres 中 NULL 互不相等，该唯一约束对 `account_name IS NULL` 的行**完全失效**，会产生重复持仓。新实现应改为 `NOT NULL DEFAULT ''`，或在 `COALESCE(account_name,'')` 上建唯一索引。

**索引**：`(user_id)`、`(symbol)`、`(instrument_type)`、`(user_id, symbol, instrument_type)`、`(account_name)`。

### 1.7 `user_api_keys`（BYOK）

| 列 | 类型 | 约束 |
|---|---|---|
| `user_id` | VARCHAR(255) | **复合 PK #1**，FK → users **ON DELETE CASCADE ON UPDATE CASCADE** |
| `provider` | VARCHAR(50) | **复合 PK #2** |
| `api_key` | BYTEA | NOT NULL，应用层加密 |
| `base_url` | TEXT | 自定义网关 |
| `created_at` / `updated_at` | TIMESTAMPTZ | DEFAULT NOW()，**无触发器**，写路径需手动更新 |

`PRIMARY KEY (user_id, provider)` → 每用户每 provider 一把 key。`ON UPDATE CASCADE` 用于支持 `migrate_user_id`（改主键值时级联跟随）。

### 1.8 `user_oauth_tokens`

| 列 | 类型 | 约束 |
|---|---|---|
| `user_id` | TEXT | **复合 PK #1**（类型是 TEXT 而非 VARCHAR(255)，且 **无 FK**） |
| `provider` | TEXT | **复合 PK #2**（`claude` / `codex` …） |
| `access_token` | BYTEA | NOT NULL，加密 |
| `refresh_token` | BYTEA | NOT NULL，加密 |
| `account_id` | TEXT | NOT NULL |
| `email` / `plan_type` | TEXT | |
| `expires_at` | TIMESTAMPTZ | NOT NULL |
| `created_at` / `updated_at` | TIMESTAMPTZ | DEFAULT NOW()，无触发器 |

**【缺陷 D5】** 无 `FK → users`：删用户不会级联清 token，加密凭据成孤儿长期残留。新实现补 FK CASCADE，并把类型统一成 `VARCHAR(255)`。

### 1.9 `conversation_threads`（主链第 3 层）

| 列 | 类型 | 约束 / 默认 | 说明 |
|---|---|---|---|
| `conversation_thread_id` | UUID | PK | 与 LangGraph 的 `thread_id` 同值 |
| `workspace_id` | UUID | NOT NULL FK → workspaces CASCADE | **唯一的租户归属路径**（thread 上没有 user_id 列） |
| `msg_type` | VARCHAR(50) | CHECK ∈ {flash, ptc, interrupted, task} | |
| `current_status` | VARCHAR(50) | NOT NULL, CHECK ∈ {in_progress, interrupted, completed, error, cancelled} | **投影列**，真值在最新 run 行上 |
| `thread_index` | INTEGER | NOT NULL | 工作区内序号，0-based |
| `title` | VARCHAR(255) | | 创建时取首条 query 截断 255 |
| `external_id` | VARCHAR(255) | | 渠道外部 id，形如 `chat_id:topic_id` |
| `platform` | VARCHAR(50) | | `web` / `market_view:<SYMBOL>` / `telegram` / `slack` / `discord` / `feishu` / NULL(系统发起) |
| `share_token` | VARCHAR(32) | UNIQUE | 公开分享令牌 |
| `is_shared` | BOOLEAN | NOT NULL DEFAULT FALSE | |
| `share_permissions` | JSONB | NOT NULL DEFAULT '{}' | 如 `{"allow_files": false, "allow_download": false}` |
| `shared_at` | TIMESTAMPTZ | | |
| `latest_checkpoint_id` | TEXT | | LangGraph checkpoint 尖端指针（分支跟踪） |
| `metadata` | JSONB | NOT NULL DEFAULT '{}' | 022；已知键 `origin = {"type":"agent"\|"automation"\|"system","id":"<发起者 id>"}`；**缺 origin 键 = 用户发起**（常见情形不写） |
| `last_seen_run_seq` | BIGINT | NOT NULL DEFAULT 0 | 023；持久化"已读游标" |
| `is_pinned` | BOOLEAN | NOT NULL DEFAULT FALSE | 024 |
| `archived_at` | TIMESTAMPTZ | | 024；**时间戳既是标记也是归档时间**，NULL = 活跃 |
| `created_at` / `updated_at` | TIMESTAMPTZ | DEFAULT NOW() | **通用 updated_at 触发器已在 022 移除** |

**联合唯一 / 部分唯一（关键）**

| 约束 / 索引 | 定义 | 业务含义与冲突处理 |
|---|---|---|
| `unique_thread_index_per_workspace` | UNIQUE `(workspace_id, thread_index)` | 工作区内线程序号唯一。并发创建会撞它，属于**可重试冲突**：重算 `COALESCE(MAX(thread_index),-1)+1` 后重插，最多 3 次 |
| `idx_conversation_threads_external` | UNIQUE `(platform, external_id) WHERE external_id IS NOT NULL` | **全局**渠道去重：一个 Telegram 话题只映射一个 thread。撞它**不可重试**，必须抛领域异常 → HTTP 409 且 body 带 `error_type="external_id_conflict"`；流式场景发同名 SSE error 帧 |
| `idx_threads_share_token` | UNIQUE `(share_token) WHERE share_token IS NOT NULL` | 分享令牌全局唯一 |

**其它索引**：`(created_at DESC)`、`(current_status)`、`idx_threads_ws_pin_updated (workspace_id, is_pinned DESC, updated_at DESC) WHERE archived_at IS NULL`（侧边栏热路径；001 只有 `(workspace_id, thread_index)` 唯一约束，任何列表排序都用不上它）。

### 1.10 `conversation_queries`（一轮一条用户输入）

| 列 | 类型 | 约束 |
|---|---|---|
| `conversation_query_id` | UUID | PK |
| `conversation_thread_id` | UUID | NOT NULL FK → conversation_threads CASCADE |
| `turn_index` | INTEGER | NOT NULL |
| `content` | TEXT | 用户原文（**必须剥 NUL**） |
| `type` | VARCHAR(50) | NOT NULL, CHECK ∈ {initial, follow_up, resume_feedback, regenerate, **steering**, **system**}（008 扩充） |
| `feedback_action` | TEXT | HITL 反馈动作 |
| `metadata` | JSONB | DEFAULT '{}' |
| `created_at` | TIMESTAMPTZ | **NOT NULL 且无默认**，必须显式写 |

**联合唯一**：`unique_turn_index_per_thread_query UNIQUE (conversation_thread_id, turn_index)`
→ 一轮只有一条用户消息（与 responses 的"一轮可多次尝试"形成对照）。它同时是：
1. 幂等写入的冲突目标（§3.3）；
2. **精确轮次计数的唯一依据** —— 列表页的 `turn_count` 必须数它，数 responses 会被重试行虚增。

**索引**：`(conversation_thread_id)`、`(created_at DESC)`、`(type)`。

### 1.11 `conversation_responses` — **运行台账（run ledger）**，全库最复杂的表

一行 = 一次运行（run）。行在 START 事务中以 `in_progress` 出生，由**唯一一次守卫 CAS** 转成终态。

| 列 | 类型 | 约束 / 默认 | 说明 |
|---|---|---|---|
| `conversation_response_id` | UUID | PK | 即 `run_id` |
| `conversation_thread_id` | UUID | NOT NULL FK → conversation_threads CASCADE | |
| `turn_index` | INTEGER | NOT NULL | **非单调**：重试复用同值；编辑/重生成删高轮次再跑低轮次 |
| `status` | VARCHAR(50) | NOT NULL CHECK ∈ {in_progress, interrupted, completed, error, cancelled} | |
| `interrupt_reason` | VARCHAR(100) | | 仅 interrupted 有值 |
| `metadata` | JSONB | DEFAULT '{}' | START 时写入派生终态副作用所需的全部上下文（见 §3.4） |
| `warnings` / `errors` | TEXT[] | | |
| `execution_time` | FLOAT | | 秒 |
| `created_at` | TIMESTAMPTZ | NOT NULL 无默认 | |
| `sse_events` | JSONB | | 事件归档数组，**只追加不替换** |
| `attempt_no` | INTEGER | NOT NULL DEFAULT 1 | 017 |
| `retry_of_run_id` | UUID | FK → self（`fk_responses_retry_of`） | 017，尝试链前驱 |
| `request_key` | UUID | **NOT NULL** DEFAULT gen_random_uuid() | 017，调用方提供的幂等键 |
| `cancel_requested_at` | TIMESTAMPTZ | | 017，**持久化取消意图** |
| `run_seq` | BIGINT | NOT NULL DEFAULT nextval(序列) | 023，**唯一的单调运行序**；序列 `OWNED BY` 该列 |

**CHECK `chk_responses_attempt_chain`**：`attempt_no >= 1 AND ((attempt_no = 1) = (retry_of_run_id IS NULL))`
→ 首次尝试必须无前驱，非首次必须有前驱。

**联合唯一 / 部分唯一 —— 每一条都是准入语义，撞了不是"错误"而是"被拒"**

| 索引 | 定义 | 业务含义 → 领域异常 |
|---|---|---|
| `uq_responses_in_progress_slot` | UNIQUE `(conversation_thread_id) WHERE status='in_progress'` | **单活跃运行槽**：一个 thread 同时最多一个 run。撞 → `RunSlotBusyError`（409）。同时兼作恢复扫描器的发现索引 |
| `uq_responses_thread_turn_attempt` | UNIQUE `(conversation_thread_id, turn_index, attempt_no)` | 尝试链身份。撞 → `AttemptConflictError`（并发重试） |
| `uq_responses_request_key` | UNIQUE `(request_key)` —— **全局**，不是 thread 内 | **HTTP 重传去重**：首条消息的重传必须在创建出第二个服务端生成的 thread 之前被拦下，所以必须全局。撞 → `DuplicateRequestError`（返回既有 run，不新建） |
| `uq_responses_retry_of` | UNIQUE `(retry_of_run_id) WHERE NOT NULL` | 一次尝试最多一个直接后继；并发 `/retry` 只有一方赢 |
| `uq_responses_run_seq` | UNIQUE `(run_seq)` | run_seq 不可变的约定式保证 |

**其它索引**：`(status)`、`(created_at DESC)`、
`ix_responses_thread_run_seq (conversation_thread_id, run_seq DESC) INCLUDE (conversation_response_id, status, cancel_requested_at, interrupt_reason, created_at)`
→ 覆盖索引，让"每 thread 最新尝试"的 LATERAL 走 index-only scan。

**已被删除、新实现不要重建**：`unique_turn_index_per_thread_response`（018 删；留着就无法有尝试链）、`idx_responses_thread_id`（023 删；被复合索引完全覆盖）。

**触发器 `trg_responses_lifecycle_guard`（BEFORE INSERT OR UPDATE，018）—— 数据库级生命周期护栏**

```
INSERT:  NEW.status 必须 = 'in_progress'，否则 RAISE EXCEPTION
UPDATE 且 OLD.status <> 'in_progress'（已终态）:
    NEW.status 与 OLD.status 不同           -> RAISE  终态状态不可变
    NEW.cancel_requested_at 与 OLD 不同     -> RAISE  终态后取消意图被冻结
    其余列（sse_events / metadata）允许补丁  -> 供后台 subagent 归档回填
```

> **新实现必须保留这个触发器。** ORM 乐观锁替代不了它：finalize、取消、恢复扫描器分属不同进程，只有数据库能仲裁。
