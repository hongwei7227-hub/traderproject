package com.kairos.analyst.service;

import com.kairos.analyst.model.AnalystRating;

public interface AnalystService {

    /** 读一只股的分析师观点(布隆防穿透 → 两级缓存 → 锁回源)。不存在返 null。 */
    AnalystRating getAnalyst(String symbol);

    /** 更新一只股的分析师观点(写真数据源 → Cache-Aside 删两层 + 广播 → 布隆补录)。 */
    void updateAnalyst(AnalystRating rating);
}
