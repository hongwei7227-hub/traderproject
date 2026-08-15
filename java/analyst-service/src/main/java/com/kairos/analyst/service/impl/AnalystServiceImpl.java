package com.kairos.analyst.service.impl;

import com.kairos.analyst.cache.CacheClient;
import com.kairos.analyst.constant.CacheConstants;
import com.kairos.analyst.model.AnalystRating;
import com.kairos.analyst.service.AnalystService;
import com.kairos.analyst.store.AnalystStore;
import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RBloomFilter;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

/**
 * 分析师观点服务 —— 对标 CityAIHub {@code ShopServiceImpl.queryById}:
 * <pre>
 *   布隆 contains? 否 → 直接返回(不落缓存/回源)   ← 防穿透硬门
 *   是 → CacheClient.getWithMutex(L1→L2→锁回源+空值缓存)
 *   更新 → 写真数据源 + Cache-Aside 删两层(+广播) + 布隆补录
 * </pre>
 */
@Slf4j
@Service
public class AnalystServiceImpl implements AnalystService {

    private final CacheClient cacheClient;
    private final AnalystStore store;
    private final RBloomFilter<String> stockBloomFilter;

    public AnalystServiceImpl(CacheClient cacheClient, AnalystStore store,
                              RBloomFilter<String> stockBloomFilter) {
        this.cacheClient = cacheClient;
        this.store = store;
        this.stockBloomFilter = stockBloomFilter;
    }

    @Override
    public AnalystRating getAnalyst(String symbol) {
        String sym = normalize(symbol);
        if (!StringUtils.hasText(sym)) {
            return null;
        }
        // 布隆防穿透:作为 gate 传入,放在 L1 之后 —— L1 命中(热点)直接返回、不查布隆,
        // 避免每次都付一次 Redis 布隆往返(毁掉进程内 L1 的意义);仅 L1 miss 才过布隆拦无效标的。
        // 布隆无假阴性 → 绝不误杀真实标的;1% 假阳性放过去由回源 + 空值缓存兜住。
        return cacheClient.getWithMutex(
                CacheConstants.CACHE_ANALYST_KEY + sym,
                CacheConstants.LOCK_ANALYST_KEY + sym,
                AnalystRating.class,
                stockBloomFilter::contains,
                store::load,
                CacheConstants.CACHE_ANALYST_TTL,
                CacheConstants.CACHE_ANALYST_TTL_UNIT
        );
    }

    @Override
    public void updateAnalyst(AnalystRating rating) {
        String sym = normalize(rating.getSymbol());
        rating.setSymbol(sym);
        rating.setUpdatedAt(System.currentTimeMillis());
        // 1) 先更新真数据源
        store.save(rating);
        // 2) 布隆补录(新标的自愈 = 新增 shop 时 add(id))
        stockBloomFilter.add(sym);
        // 3) Cache-Aside:删两层 + 广播其它节点清 L1(写后删,不写后覆盖)
        cacheClient.delete(CacheConstants.CACHE_ANALYST_KEY + sym);
        log.info("analyst updated + cache invalidated | symbol={}", sym);
    }

    private String normalize(String symbol) {
        return symbol == null ? "" : symbol.trim().toUpperCase();
    }
}
