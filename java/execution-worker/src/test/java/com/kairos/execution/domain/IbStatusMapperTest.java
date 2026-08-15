package com.kairos.execution.domain;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;

class IbStatusMapperTest {

    @Test
    void cancelledVariants() {
        assertEquals(OrderStatus.CANCELED, IbStatusMapper.map("Cancelled", "").status());
        assertEquals(OrderStatus.CANCELED, IbStatusMapper.map("ApiCancelled", "").status());
    }

    @Test
    void straightMappings() {
        assertEquals(OrderStatus.PENDING_CANCEL, IbStatusMapper.map("PendingCancel", "").status());
        assertEquals(OrderStatus.REJECTED, IbStatusMapper.map("Rejected", "").status());
        assertEquals(OrderStatus.FILLED, IbStatusMapper.map("Filled", "").status());
    }

    @Test
    void submittedVariantsAreAcceptedButIgnored() {
        for (String s : new String[]{"PendingSubmit", "PreSubmitted", "Submitted"}) {
            IbStatusMapper.MappingResult r = IbStatusMapper.map(s, "");
            assertEquals(OrderStatus.ACCEPTED, r.status(), s);
            assertTrue(r.ignoreEvent(), s + " 应 ignoreEvent");
        }
    }

    @Test
    void inactiveWithLocateKeepsActive_notRejected() {
        // 关键坑：卖空 locate 中不能当拒单
        IbStatusMapper.MappingResult r = IbStatusMapper.map("Inactive", "locate");
        assertTrue(r.handled());
        assertNull(r.status());              // 不改状态
        assertFalse(r.ignoreEvent());
        assertTrue(r.note().contains("locate"));
    }

    @Test
    void inactiveWithoutLocateIsRejected() {
        IbStatusMapper.MappingResult r = IbStatusMapper.map("Inactive", "");
        assertEquals(OrderStatus.REJECTED, r.status());
        assertEquals("Order inactive (IB)", r.note());
    }

    @Test
    void unknownStatusNotHandled() {
        assertFalse(IbStatusMapper.map("SomethingWeird", "").handled());
        assertFalse(IbStatusMapper.map(null, "").handled());
    }

    @Test
    void mappedStatusFeedsStateMachine() {
        // 端到端小验证：IB 回报 → 映射 → 喂状态机
        OrderStateMachine sm = new OrderStateMachine();
        sm.apply(IbStatusMapper.map("Submitted", "").status());   // ACCEPTED
        sm.apply(IbStatusMapper.map("Filled", "").status());      // FILLED
        assertEquals(OrderStatus.FILLED, sm.current());
        assertTrue(sm.isTerminal());
    }
}
