-- §8 订单状态落库：把订单从 worker 进程内存搬进 Postgres，为乐观锁 CAS 打底。
-- 建在独立 execution schema（由 Flyway default-schema=execution 定位），不碰主平台 public 的表。
-- 兼容 Postgres 18（生产）与 H2 PostgreSQL 模式（CAS 并发单测）：只用标准 SQL 类型。

CREATE TABLE orders (
    broker_order_id VARCHAR(64)  NOT NULL,           -- IB 分配的 orderId（字符串），主键
    tenant_id       VARCHAR(64)  NOT NULL,           -- 租户隔离
    proposal_id     VARCHAR(64)  NOT NULL,           -- 决策层提案 id（幂等键的一半）
    symbol          VARCHAR(32)  NOT NULL,
    action          VARCHAR(8)   NOT NULL,           -- BUY / SELL
    quantity        BIGINT       NOT NULL,
    limit_price     NUMERIC(18,4),                   -- 限价（市价单为 NULL）
    status          VARCHAR(24)  NOT NULL,           -- OrderStatus 枚举名（INITIALIZED..DENIED）
    filled_qty      BIGINT       NOT NULL DEFAULT 0, -- 累计成交量（§7 execDetails 落库用）
    avg_px          NUMERIC(18,4) NOT NULL DEFAULT 0,-- 量加权均价（§7）
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_orders PRIMARY KEY (broker_order_id)
);

-- 幂等兜底：同租户同提案只允许一张单（DB 层再加一道，与 Redis 幂等互为保险）
CREATE UNIQUE INDEX ux_orders_tenant_proposal ON orders (tenant_id, proposal_id);

-- 超时撤单/对账按状态筛活跃单
CREATE INDEX ix_orders_status ON orders (status);
