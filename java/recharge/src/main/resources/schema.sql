-- 会员充值/订阅支付 demo —— 建库建表 + 套餐种子
-- 用法：MySQL 容器起好后执行，或让应用配 spring.sql.init 自动跑。

CREATE DATABASE IF NOT EXISTS kairos_recharge
    DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE kairos_recharge;

-- 会员套餐（时长制）
CREATE TABLE IF NOT EXISTS membership_plan (
    id            BIGINT       NOT NULL PRIMARY KEY,
    name          VARCHAR(50)  NOT NULL COMMENT '月卡/季卡/年卡',
    price         DECIMAL(10,2) NOT NULL,
    duration_days INT          NOT NULL COMMENT '会员时长（天）',
    status        TINYINT      NOT NULL DEFAULT 1 COMMENT '1上架/0下架',
    create_time   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB COMMENT='会员套餐';

-- 充值订单（移植 tb_voucher_order 精简版：3 态）
CREATE TABLE IF NOT EXISTS recharge_order (
    id          BIGINT        NOT NULL PRIMARY KEY,
    user_id     BIGINT        NOT NULL,
    plan_id     BIGINT        NOT NULL,
    amount      DECIMAL(10,2) NOT NULL COMMENT '下单时冻结的 plan.price 快照',
    pay_type    TINYINT       NULL COMMENT '1余额/2支付宝/3微信（demo 仅模拟）',
    status      TINYINT       NOT NULL DEFAULT 1 COMMENT '1待支付/2已支付/3已取消/4退款中/5已退款（status 即乐观锁守卫）',
    create_time DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    pay_time    DATETIME      NULL,
    close_time  DATETIME      NULL,
    refund_time DATETIME      NULL,
    KEY idx_user (user_id)
) ENGINE=InnoDB COMMENT='充值订单';

-- 用户会员状态（支付成功 → 延长 expire_time）
CREATE TABLE IF NOT EXISTS user_membership (
    user_id     BIGINT   NOT NULL PRIMARY KEY,
    level       TINYINT  NOT NULL DEFAULT 1 COMMENT '会员等级（demo 固定 1=VIP）',
    expire_time DATETIME NULL COMMENT '会员到期时间',
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB COMMENT='用户会员状态';

-- 套餐种子
INSERT INTO membership_plan (id, name, price, duration_days, status) VALUES
    (1, '月度会员', 30.00,  30,  1),
    (2, '季度会员', 80.00,  90,  1),
    (3, '年度会员', 288.00, 365, 1)
ON DUPLICATE KEY UPDATE name=VALUES(name), price=VALUES(price), duration_days=VALUES(duration_days);
