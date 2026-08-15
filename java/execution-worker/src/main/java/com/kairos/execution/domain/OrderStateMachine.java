package com.kairos.execution.domain;

import java.util.EnumMap;
import java.util.EnumSet;
import java.util.Map;

/**
 * 订单有限状态机 —— 移植自 NautilusTrader base.pyx 的 _ORDER_STATE_TABLE。
 *
 * 关键：保留了 Nautilus 标注 "Real world possibility" / "Allow multiple requests" /
 * "Allows failed cancel requests" 的那些边界转换 —— 朴素实现必错的并发 race。
 * 非法转换抛 InvalidStateTransition（对齐 Nautilus 的 InvalidStateTrigger）。
 */
public final class OrderStateMachine {

    /** 非法状态转换异常。 */
    public static final class InvalidStateTransition extends RuntimeException {
        public InvalidStateTransition(OrderStatus from, OrderStatus to) {
            super("Invalid order state transition: " + from + " -> " + to);
        }
    }

    private static final Map<OrderStatus, EnumSet<OrderStatus>> TABLE =
            new EnumMap<>(OrderStatus.class);

    private static void allow(OrderStatus from, OrderStatus... tos) {
        TABLE.computeIfAbsent(from, k -> EnumSet.noneOf(OrderStatus.class))
             .addAll(EnumSet.of(tos[0], tos));
    }

    static {
        allow(OrderStatus.INITIALIZED,
                OrderStatus.DENIED, OrderStatus.SUBMITTED, OrderStatus.REJECTED,
                OrderStatus.ACCEPTED, OrderStatus.CANCELED, OrderStatus.EXPIRED,
                OrderStatus.TRIGGERED);
        allow(OrderStatus.SUBMITTED,
                OrderStatus.PENDING_UPDATE, OrderStatus.PENDING_CANCEL, OrderStatus.REJECTED,
                OrderStatus.CANCELED, OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED);
        allow(OrderStatus.ACCEPTED,
                OrderStatus.REJECTED, OrderStatus.PENDING_UPDATE, OrderStatus.PENDING_CANCEL,
                OrderStatus.CANCELED, OrderStatus.TRIGGERED, OrderStatus.EXPIRED,
                OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED);
        // Real world possibility：撤单与成交撞车
        allow(OrderStatus.CANCELED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED);
        allow(OrderStatus.PENDING_UPDATE,
                OrderStatus.REJECTED, OrderStatus.ACCEPTED, OrderStatus.CANCELED,
                OrderStatus.EXPIRED, OrderStatus.TRIGGERED,
                OrderStatus.PENDING_UPDATE,   // Allow multiple requests
                OrderStatus.PENDING_CANCEL, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED);
        allow(OrderStatus.PENDING_CANCEL,
                OrderStatus.REJECTED,
                OrderStatus.PENDING_CANCEL,   // Allow multiple requests
                OrderStatus.CANCELED, OrderStatus.EXPIRED,
                OrderStatus.ACCEPTED,         // Allows failed cancel requests
                OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED);
        allow(OrderStatus.TRIGGERED,
                OrderStatus.REJECTED, OrderStatus.PENDING_UPDATE, OrderStatus.PENDING_CANCEL,
                OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED);
        allow(OrderStatus.PARTIALLY_FILLED,
                OrderStatus.PENDING_UPDATE, OrderStatus.PENDING_CANCEL, OrderStatus.CANCELED,
                OrderStatus.EXPIRED,
                OrderStatus.PARTIALLY_FILLED,  // 多笔部分成交累加
                OrderStatus.FILLED);
    }

    private OrderStatus current = OrderStatus.INITIALIZED;

    public OrderStatus current() {
        return current;
    }

    public static boolean canTransition(OrderStatus from, OrderStatus to) {
        EnumSet<OrderStatus> allowed = TABLE.get(from);
        return allowed != null && allowed.contains(to);
    }

    /** 应用一个目标状态；合法则流转并返回新状态，非法抛 InvalidStateTransition。 */
    public OrderStatus apply(OrderStatus to) {
        if (!canTransition(current, to)) {
            throw new InvalidStateTransition(current, to);
        }
        current = to;
        return current;
    }

    public boolean isTerminal() {
        return current.isTerminal();
    }
}
