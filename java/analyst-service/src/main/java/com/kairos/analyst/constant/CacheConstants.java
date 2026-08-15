package com.kairos.analyst.constant;

import java.util.concurrent.TimeUnit;

/** 缓存键 / TTL / 锁 / 布隆常量。对齐 CityAIHub RedisConstants 的命名习惯。 */
public final class CacheConstants {

    private CacheConstants() {}

    /** 分析师观点缓存 key 前缀:cache:analyst:{SYMBOL} */
    public static final String CACHE_ANALYST_KEY = "cache:analyst:";
    /** L2 正常值 TTL(分钟)。数据天级才变,30min 足够;写路径还会 Cache-Aside 主动删。 */
    public static final long CACHE_ANALYST_TTL = 30L;
    public static final TimeUnit CACHE_ANALYST_TTL_UNIT = TimeUnit.MINUTES;

    /** 空值缓存 TTL(分钟):兜布隆 1% 假阳性 + provider 解析不出的垃圾标的。 */
    public static final long CACHE_NULL_TTL = 2L;

    /** Redisson 回源锁 key 前缀:lock:analyst:{SYMBOL} */
    public static final String LOCK_ANALYST_KEY = "lock:analyst:";
    /** 锁等待时间(秒):拿不到就退化(不递归)。 */
    public static final long LOCK_WAIT_SEC = 2L;
    /** 锁租约(秒):显式给,禁用 Redisson 看门狗自动续租 —— 避免异步/崩溃场景永久死锁。 */
    public static final long LOCK_LEASE_SEC = 10L;

    /** 股票布隆过滤器名(Redis bitmap key)。 */
    public static final String STOCK_BLOOM_FILTER = "bloom:stocks";
}
