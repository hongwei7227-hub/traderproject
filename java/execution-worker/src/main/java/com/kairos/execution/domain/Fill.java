package com.kairos.execution.domain;

/**
 * 单笔成交回报。移植自 Nautilus OrderFilled 的关键字段。
 * 去重四元组 = tradeId + side + px + qty（base.pyx is_duplicate_fill_c）。
 */
public record Fill(String tradeId, String side, double px, long qty) {
    public String dedupKey() {
        return tradeId + "|" + side + "|" + px + "|" + qty;
    }
}
