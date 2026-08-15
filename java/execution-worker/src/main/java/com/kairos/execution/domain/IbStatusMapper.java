package com.kairos.execution.domain;

/**
 * IB 订单状态回报映射 —— 移植自 NautilusTrader ib_execution.py `_on_order_status`（:1949）。
 *
 * IB 的 orderStatus 字符串 → 内部 OrderStatus，含几个必须小心的坑：
 * - Inactive + whyHeld 含 "locate"：卖空 locate 中，**订单仍 active，不能当拒单**（keepActive）。
 * - PendingSubmit / PreSubmitted / Submitted：映射 ACCEPTED 但 ignoreEvent=true
 *   （只缓存成交进度，不推状态事件）。
 * - 未知状态：handled=false，调用方告警并忽略，不乱改状态。
 */
public final class IbStatusMapper {

    /** 映射结果。status 为 null 且 handled=true 表示"保持 active、不改状态"（locate 挂起）。 */
    public record MappingResult(
            OrderStatus status,     // 目标状态；null=不改状态
            boolean handled,        // 是否识别；false=未知状态
            boolean ignoreEvent,    // true=只缓存成交进度、不推事件（PendingSubmit 系列）
            String note) {
        static MappingResult of(OrderStatus s) { return new MappingResult(s, true, false, null); }
        static MappingResult accepted() { return new MappingResult(OrderStatus.ACCEPTED, true, true, null); }
        static MappingResult keepActive(String note) { return new MappingResult(null, true, false, note); }
        static MappingResult unknown() { return new MappingResult(null, false, false, null); }
    }

    private IbStatusMapper() {}

    public static MappingResult map(String ibStatus, String whyHeld) {
        if (ibStatus == null) {
            return MappingResult.unknown();
        }
        switch (ibStatus) {
            case "ApiCancelled":
            case "Cancelled":
                return MappingResult.of(OrderStatus.CANCELED);
            case "PendingCancel":
                return MappingResult.of(OrderStatus.PENDING_CANCEL);
            case "Rejected":
                return MappingResult.of(OrderStatus.REJECTED);
            case "Filled":
                return MappingResult.of(OrderStatus.FILLED);
            case "Inactive":
                if (whyHeld != null && whyHeld.contains("locate")) {
                    // 卖空 locate 中，订单仍 active，不能当拒单
                    return MappingResult.keepActive("held for short-sell locate");
                }
                return new MappingResult(OrderStatus.REJECTED, true, false, "Order inactive (IB)");
            case "PendingSubmit":
            case "PreSubmitted":
            case "Submitted":
                // 映射 ACCEPTED 但只缓存进度、不推事件
                return MappingResult.accepted();
            default:
                return MappingResult.unknown();
        }
    }
}
