package com.kairos.execution.domain;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;

/**
 * 订单状态机 TDD 测试 —— 重点验证 Nautilus 那几条 real-world race 边界。
 */
class OrderStateMachineTest {

    @Test
    void startsInitialized() {
        assertEquals(OrderStatus.INITIALIZED, new OrderStateMachine().current());
    }

    @Test
    void happyPathToFilled() {
        OrderStateMachine sm = new OrderStateMachine();
        sm.apply(OrderStatus.SUBMITTED);
        sm.apply(OrderStatus.ACCEPTED);
        sm.apply(OrderStatus.PARTIALLY_FILLED);
        sm.apply(OrderStatus.FILLED);
        assertEquals(OrderStatus.FILLED, sm.current());
        assertTrue(sm.isTerminal());
    }

    @Test
    void illegalTransitionThrows() {
        OrderStateMachine sm = new OrderStateMachine();
        // INITIALIZED -> FILLED 不在表里
        assertThrows(OrderStateMachine.InvalidStateTransition.class,
                () -> sm.apply(OrderStatus.FILLED));
    }

    @Test
    void terminalCannotLeave() {
        OrderStateMachine sm = new OrderStateMachine();
        sm.apply(OrderStatus.REJECTED);
        assertTrue(sm.isTerminal());
        assertThrows(OrderStateMachine.InvalidStateTransition.class,
                () -> sm.apply(OrderStatus.SUBMITTED));
    }

    // ---------- Real world possibility：这些必须允许（朴素实现会错拒） ----------

    @Test
    void canceledThenFilled_realWorldRace() {
        // 已发撤单，但成交撞车 → 券商仍成交
        assertTrue(OrderStateMachine.canTransition(OrderStatus.CANCELED, OrderStatus.FILLED));
        assertTrue(OrderStateMachine.canTransition(OrderStatus.CANCELED, OrderStatus.PARTIALLY_FILLED));
        OrderStateMachine sm = new OrderStateMachine();
        sm.apply(OrderStatus.SUBMITTED);
        sm.apply(OrderStatus.CANCELED);
        assertDoesNotThrow(() -> sm.apply(OrderStatus.FILLED));
    }

    @Test
    void pendingCancelThenAccepted_failedCancel() {
        // 撤单失败，订单还活着
        assertTrue(OrderStateMachine.canTransition(OrderStatus.PENDING_CANCEL, OrderStatus.ACCEPTED));
    }

    @Test
    void multiplePendingUpdateAllowed() {
        assertTrue(OrderStateMachine.canTransition(OrderStatus.PENDING_UPDATE, OrderStatus.PENDING_UPDATE));
    }

    @Test
    void multiplePartialFillsAllowed() {
        OrderStateMachine sm = new OrderStateMachine();
        sm.apply(OrderStatus.ACCEPTED);
        sm.apply(OrderStatus.PARTIALLY_FILLED);
        assertDoesNotThrow(() -> sm.apply(OrderStatus.PARTIALLY_FILLED)); // 又来一笔部分成交
        assertEquals(OrderStatus.PARTIALLY_FILLED, sm.current());
    }

    @Test
    void invalidTransitionMessageMentionsBoth() {
        OrderStateMachine sm = new OrderStateMachine();
        OrderStateMachine.InvalidStateTransition ex = assertThrows(
                OrderStateMachine.InvalidStateTransition.class,
                () -> sm.apply(OrderStatus.PARTIALLY_FILLED)); // INITIALIZED -> PARTIALLY_FILLED 非法
        assertTrue(ex.getMessage().contains("INITIALIZED"));
        assertTrue(ex.getMessage().contains("PARTIALLY_FILLED"));
    }
}
