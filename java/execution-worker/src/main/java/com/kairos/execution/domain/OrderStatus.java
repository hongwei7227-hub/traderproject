package com.kairos.execution.domain;

/**
 * 订单状态枚举 —— 移植自 NautilusTrader model/orders/base.pyx 的 OrderStatus。
 * 去掉 kairos 用不上的 EMULATED/RELEASED（Nautilus 模拟单专用）。
 */
public enum OrderStatus {
    INITIALIZED,
    SUBMITTED,
    ACCEPTED,
    PENDING_UPDATE,
    PENDING_CANCEL,
    TRIGGERED,
    PARTIALLY_FILLED,
    FILLED,      // 终态
    CANCELED,    // 终态
    EXPIRED,     // 终态
    REJECTED,    // 终态
    DENIED;      // 终态（风控拒绝）

    public boolean isTerminal() {
        switch (this) {
            case FILLED:
            case CANCELED:
            case EXPIRED:
            case REJECTED:
            case DENIED:
                return true;
            default:
                return false;
        }
    }
}
