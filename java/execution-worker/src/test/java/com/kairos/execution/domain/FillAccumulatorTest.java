package com.kairos.execution.domain;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;

class FillAccumulatorTest {

    @Test
    void singlePartialFill() {
        FillAccumulator acc = new FillAccumulator(100);
        assertTrue(acc.apply(new Fill("t1", "BUY", 100.0, 40)));
        assertEquals(40, acc.filledQty());
        assertEquals(60, acc.leavesQty());
        assertEquals(0, acc.overfillQty());
        assertFalse(acc.isComplete());
        assertEquals(100.0, acc.avgPx(), 1e-9);
    }

    @Test
    void weightedAveragePriceAcrossFills() {
        FillAccumulator acc = new FillAccumulator(30);
        acc.apply(new Fill("t1", "BUY", 100.0, 10));
        acc.apply(new Fill("t2", "BUY", 130.0, 20));
        // 量加权：(10*100 + 20*130) / 30 = 3600/30 = 120
        assertEquals(30, acc.filledQty());
        assertEquals(0, acc.leavesQty());
        assertTrue(acc.isComplete());
        assertEquals(120.0, acc.avgPx(), 1e-9);
    }

    @Test
    void fillCompletingOrder() {
        FillAccumulator acc = new FillAccumulator(50);
        acc.apply(new Fill("t1", "BUY", 10.0, 30));
        assertFalse(acc.isComplete());
        acc.apply(new Fill("t2", "BUY", 10.0, 20));
        assertTrue(acc.isComplete());
        assertEquals(0, acc.leavesQty());
    }

    @Test
    void overfillClampsLeavesAndTracksExcess() {
        FillAccumulator acc = new FillAccumulator(100);
        acc.apply(new Fill("t1", "BUY", 10.0, 80));
        acc.apply(new Fill("t2", "BUY", 10.0, 40));   // 80+40=120 > 100
        assertEquals(120, acc.filledQty());
        assertEquals(0, acc.leavesQty());             // 钳到 0
        assertEquals(20, acc.overfillQty());          // 溢出 20
        assertTrue(acc.isComplete());
    }

    @Test
    void duplicateFillIgnored() {
        FillAccumulator acc = new FillAccumulator(100);
        assertTrue(acc.apply(new Fill("t1", "BUY", 100.0, 40)));
        assertFalse(acc.apply(new Fill("t1", "BUY", 100.0, 40)));   // 四元组全同 → 重复
        assertEquals(40, acc.filledQty());                          // 未重复计入
        assertEquals(60, acc.leavesQty());
    }

    @Test
    void sameTradeIdDifferentQtyIsNotDuplicate() {
        FillAccumulator acc = new FillAccumulator(100);
        acc.apply(new Fill("t1", "BUY", 100.0, 40));
        assertTrue(acc.apply(new Fill("t1", "BUY", 100.0, 30)));    // qty 不同 → 非重复
        assertEquals(70, acc.filledQty());
    }

    @Test
    void rejectsNonPositiveTotal() {
        assertThrows(IllegalArgumentException.class, () -> new FillAccumulator(0));
    }
}
