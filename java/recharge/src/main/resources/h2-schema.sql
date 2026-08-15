-- H2 内存库版 schema（本地测试用，MySQL 语法精简：去 ENGINE/CREATE DATABASE/表级 COMMENT）
CREATE TABLE IF NOT EXISTS membership_plan (
    id            BIGINT        NOT NULL PRIMARY KEY,
    name          VARCHAR(50)   NOT NULL,
    price         DECIMAL(10,2) NOT NULL,
    duration_days INT           NOT NULL,
    status        TINYINT       NOT NULL DEFAULT 1,
    create_time   DATETIME      DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS recharge_order (
    id          BIGINT        NOT NULL PRIMARY KEY,
    user_id     BIGINT        NOT NULL,
    plan_id     BIGINT        NOT NULL,
    amount      DECIMAL(10,2) NOT NULL,
    pay_type    TINYINT,
    status      TINYINT       NOT NULL DEFAULT 1,
    create_time DATETIME      DEFAULT CURRENT_TIMESTAMP,
    pay_time    DATETIME,
    close_time  DATETIME,
    refund_time DATETIME
);

CREATE TABLE IF NOT EXISTS user_membership (
    user_id     BIGINT   NOT NULL PRIMARY KEY,
    level       TINYINT  NOT NULL DEFAULT 1,
    expire_time DATETIME,
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO membership_plan (id, name, price, duration_days, status) VALUES (1, '月度会员', 30.00, 30, 1);
INSERT INTO membership_plan (id, name, price, duration_days, status) VALUES (2, '季度会员', 80.00, 90, 1);
INSERT INTO membership_plan (id, name, price, duration_days, status) VALUES (3, '年度会员', 288.00, 365, 1);
