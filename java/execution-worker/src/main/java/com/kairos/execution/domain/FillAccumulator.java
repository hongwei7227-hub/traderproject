package com.kairos.execution.domain;

import java.util.HashSet;
import java.util.Set;

/**
 * 成交累加器 —— 移植自 NautilusTrader base.pyx `_filled` / `calculate_overfill_c` /
 * `is_duplicate_fill_c`。
 *
 * 维护三个量：filledQty(已成) / leavesQty(剩余,钳0) / overfillQty(溢出)，
 * 以及量加权均价 avgPx；重复成交（四元组相同）被忽略不重复计入。
 */
public final class FillAccumulator {

    private final long totalQuantity;
    private long filledQty = 0;
    private long leavesQty;
    private long overfillQty = 0;
    private double avgPx = 0.0;
    private double weightedPxSum = 0.0;   // Σ(|qty| * px)，用于量加权均价
    private final Set<String> seen = new HashSet<>();

    public FillAccumulator(long totalQuantity) {
        if (totalQuantity <= 0) {
            throw new IllegalArgumentException("totalQuantity must be > 0");
        }
        this.totalQuantity = totalQuantity;
        this.leavesQty = totalQuantity;
    }

    /**
     * 应用一笔成交。返回 true=已计入，false=重复成交被忽略。
     */
    public boolean apply(Fill fill) {
        if (!seen.add(fill.dedupKey())) {
            return false;   // 重复成交（回报可能多次推送）→ 忽略
        }
        long newFilled = filledQty + fill.qty();
        long rawLeaves = totalQuantity - newFilled;
        if (rawLeaves < 0) {
            overfillQty += -rawLeaves;   // 券商多成交
            rawLeaves = 0;               // 剩余钳到 0
        }
        filledQty = newFilled;
        leavesQty = rawLeaves;
        weightedPxSum += Math.abs(fill.qty()) * fill.px();
        avgPx = weightedPxSum / Math.abs(filledQty);
        return true;
    }

    public long filledQty()   { return filledQty; }
    public long leavesQty()   { return leavesQty; }
    public long overfillQty() { return overfillQty; }
    public double avgPx()     { return avgPx; }

    /** 全部成交（含溢出）→ 订单可置 FILLED；否则 PARTIALLY_FILLED。 */
    public boolean isComplete() {
        return filledQty >= totalQuantity;
    }
}
