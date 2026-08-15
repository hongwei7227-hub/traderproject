package com.kairos.analyst.cache;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.github.benmanes.caffeine.cache.Cache;
import com.kairos.analyst.constant.CacheConstants;
import com.kairos.analyst.constant.MqConstants;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.spring.core.RocketMQTemplate;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Function;

/**
 * 通用两级缓存工具 —— 对标 CityAIHub CacheClient,三处改进/收敛:
 *
 * <ol>
 *   <li><b>回源锁</b>:CityAIHub 用自研 SimpleRedisLock;这里用 Redisson RLock,且
 *       {@code tryLock(waitTime, leaseTime, unit)} 显式给 leaseTime → <b>禁用看门狗自动续租</b>,
 *       避免持锁线程崩溃时锁被无限续租、永久死锁(review B2)。拿不到锁<b>不递归</b>(避免爆栈),
 *       退化为再探一次 L2 后返回(review W4)。</li>
 *   <li><b>Cache-Aside 删</b>:{@link #delete} 删本地 L1 + Redis L2,并(可关地)发 RocketMQ 广播,
 *       让其它节点清各自的 L1 —— 补上 CityAIHub 缺失的跨节点失效。</li>
 *   <li><b>广播非致命</b>:broker 未起 / 开关关闭时,发布走 no-op,只影响"跨节点更快收敛",
 *       不影响本地删除与读服务(review B1)。</li>
 * </ol>
 *
 * 空值缓存(Redis 存空串)兜布隆 1% 假阳性;L1/L2/DB 命中埋点用于压测分层命中率。
 */
@Slf4j
@Component
public class CacheClient {

    private final StringRedisTemplate redis;
    private final Cache<String, Object> caffeine;
    private final RedissonClient redisson;
    private final ObjectMapper objectMapper;
    /** RocketMQTemplate 仅在配置了 rocketmq.name-server 时存在 → 用 Provider 惰性取,缺省安全。 */
    private final ObjectProvider<RocketMQTemplate> rocketMQTemplateProvider;

    @Value("${cache.caffeine.enabled:true}")
    private boolean l1Enabled;

    @Value("${cache.invalidate.mq.enabled:false}")
    private boolean mqInvalidateEnabled;

    /** 空值标记:Redis/Caffeine 里存空串代表"确认不存在"。 */
    private static final String NULL_MARKER = "";

    private final AtomicLong l1Hits = new AtomicLong();
    private final AtomicLong l2Hits = new AtomicLong();
    private final AtomicLong dbHits = new AtomicLong();

    public CacheClient(StringRedisTemplate redis,
                       Cache<String, Object> caffeine,
                       RedissonClient redisson,
                       ObjectMapper objectMapper,
                       ObjectProvider<RocketMQTemplate> rocketMQTemplateProvider) {
        this.redis = redis;
        this.caffeine = caffeine;
        this.redisson = redisson;
        this.objectMapper = objectMapper;
        this.rocketMQTemplateProvider = rocketMQTemplateProvider;
    }

    // ---------------------------------------------------------------- 写

    /** 写两层(L1 + L2)。 */
    public void set(String key, Object value, long ttl, TimeUnit unit) {
        try {
            String json = objectMapper.writeValueAsString(value);
            if (l1Enabled) {
                caffeine.put(key, value);
            }
            redis.opsForValue().set(key, json, ttl, unit);
        } catch (Exception e) {
            log.warn("cache.set failed | key={}", key, e);
        }
    }

    /**
     * Cache-Aside 删除:删本地 L1 + Redis L2,再(可关地)广播让其它节点清 L1。
     * 写路径(数据更新后)调它。
     */
    public void delete(String key) {
        invalidateLocalAndRedis(key);
        broadcastInvalidate(key);
    }

    /** 删本地 L1 + Redis L2(不广播)。发起删除的本节点用。 */
    public void invalidateLocalAndRedis(String key) {
        if (l1Enabled) {
            caffeine.invalidate(key);
        }
        redis.delete(key);
    }

    /** 只清本地 L1(不删 Redis、不再广播)—— 广播<b>接收端</b>调它(Redis 是共享的,发起方已删)。 */
    public void invalidateL1Only(String key) {
        if (l1Enabled) {
            caffeine.invalidate(key);
        }
    }

    private void broadcastInvalidate(String key) {
        if (!mqInvalidateEnabled) {
            return; // 开关关:退化为各节点靠 Caffeine 短 TTL 自然收敛
        }
        RocketMQTemplate template = rocketMQTemplateProvider.getIfAvailable();
        if (template == null) {
            return; // 没配 broker:非致命,不影响本地删除
        }
        try {
            template.convertAndSend(MqConstants.CACHE_INVALIDATE_TOPIC, key);
        } catch (Exception e) {
            log.warn("cache.invalidate broadcast failed(非致命)| key={}", key, e);
        }
    }

    // ---------------------------------------------------------------- 读(互斥锁防击穿)

    /**
     * 读:L1 →(gate 拦截)→ L2 → Redisson 互斥锁回源 + 空值缓存。6 参重载:无 gate。
     *
     * @param key      完整缓存 key(如 cache:analyst:AAPL)
     * @param lockName 回源锁 key(如 lock:analyst:AAPL)
     * @param type     反序列化目标类型
     * @param loader   回源函数(真数据源)
     */
    public <R> R getWithMutex(String key, String lockName, Class<R> type,
                              Function<String, R> loader, long ttl, TimeUnit unit) {
        return getWithMutex(key, lockName, type, null, loader, ttl, unit);
    }

    /**
     * 读:L1 →(gate 拦截)→ L2 → Redisson 互斥锁回源 + 空值缓存。
     *
     * <p><b>gate 放在 L1 之后</b>:L1 命中(热点)直接返回、不过 gate,避免每次都付 gate 的网络往返
     * (如 Redisson 布隆 = Redis 往返);仅 L1 miss 才用 gate 拦无效标的。防穿透不受影响 ——
     * 无效标的从不在 L1,必然 miss 后落到 gate 被拦。
     *
     * @param gate 可空。{@code test} 返回 false → 判无效标的,直接返回 null(不落 L2/回源)。
     */
    public <R> R getWithMutex(String key, String lockName, Class<R> type,
                              java.util.function.Predicate<String> gate,
                              Function<String, R> loader, long ttl, TimeUnit unit) {
        // 0) L1(命中含空值直接返回,不过 gate)
        if (l1Enabled) {
            Object cached = caffeine.getIfPresent(key);
            if (cached != null) {
                l1Hits.incrementAndGet();
                return NULL_MARKER.equals(cached) ? null : cast(cached, type);
            }
        }
        // 0.5) gate(如布隆):L1 miss 后、查 L2/回源前拦无效标的(防穿透);L1 命中不走此路
        if (gate != null && !gate.test(stripPrefix(key))) {
            return null;
        }
        // 1) L2
        R hit = readRedis(key, type);
        if (hit != null) {
            l2Hits.incrementAndGet();
            if (l1Enabled) caffeine.put(key, hit);
            return hit;
        }
        if (isNullCached(key)) {          // L2 命中空值标记
            if (l1Enabled) caffeine.put(key, NULL_MARKER);
            return null;
        }
        // 2) 回源:Redisson 互斥锁(显式 leaseTime 禁看门狗;拿不到锁不递归)
        RLock lock = redisson.getLock(lockName);
        boolean locked = false;
        try {
            locked = lock.tryLock(CacheConstants.LOCK_WAIT_SEC, CacheConstants.LOCK_LEASE_SEC, TimeUnit.SECONDS);
            if (!locked) {
                // 等不到锁:持锁者可能已回填,再探一次 L2;仍没有则本轮返 null(不递归、不阻塞)
                R again = readRedis(key, type);
                return again;
            }
            // double-check:拿到锁后再查一次(别人可能刚回填)
            R recheck = readRedis(key, type);
            if (recheck != null) {
                if (l1Enabled) caffeine.put(key, recheck);
                return recheck;
            }
            if (isNullCached(key)) {
                if (l1Enabled) caffeine.put(key, NULL_MARKER);
                return null;
            }
            // 真回源
            R r = loader.apply(stripPrefix(key));
            if (r == null) {
                if (l1Enabled) caffeine.put(key, NULL_MARKER);
                redis.opsForValue().set(key, NULL_MARKER, CacheConstants.CACHE_NULL_TTL, TimeUnit.MINUTES);
                return null;
            }
            dbHits.incrementAndGet();
            set(key, r, ttl, unit);
            return r;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("cache mutex interrupted: " + key, e);
        } finally {
            if (locked && lock.isHeldByCurrentThread()) {
                lock.unlock();
            }
        }
    }

    // ---------------------------------------------------------------- 内部工具

    private <R> R readRedis(String key, Class<R> type) {
        String json = redis.opsForValue().get(key);
        if (!StringUtils.hasText(json)) {
            return null; // blank/null:未命中或命中空值(空值由 isNullCached 区分)
        }
        try {
            return objectMapper.readValue(json, type);
        } catch (Exception e) {
            log.warn("cache.read deserialize failed | key={} json={}", key, json, e);
            return null;
        }
    }

    /** Redis 里 key 存在且为空串 → 命中空值缓存。 */
    private boolean isNullCached(String key) {
        String json = redis.opsForValue().get(key);
        return json != null && json.isEmpty();
    }

    @SuppressWarnings("unchecked")
    private <R> R cast(Object cached, Class<R> type) {
        if (type.isInstance(cached)) {
            return (R) cached;
        }
        // L1 里存的可能是反序列化对象;类型不符时兜底转一次
        return objectMapper.convertValue(cached, type);
    }

    /** cache:analyst:AAPL → AAPL(给 loader 传裸标识)。 */
    private String stripPrefix(String key) {
        int idx = key.lastIndexOf(':');
        return idx >= 0 ? key.substring(idx + 1) : key;
    }

    // ---------------------------------------------------------------- 埋点(压测分层命中率)

    public long getL1HitCount() { return l1Hits.get(); }
    public long getL2HitCount() { return l2Hits.get(); }
    public long getDbHitCount() { return dbHits.get(); }

    public void resetHitCounters() {
        l1Hits.set(0);
        l2Hits.set(0);
        dbHits.set(0);
    }
}
