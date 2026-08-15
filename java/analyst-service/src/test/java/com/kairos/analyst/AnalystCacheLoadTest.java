package com.kairos.analyst;

import com.kairos.analyst.cache.CacheClient;
import com.kairos.analyst.constant.CacheConstants;
import com.kairos.analyst.model.AnalystRating;
import com.kairos.analyst.service.AnalystService;
import com.kairos.analyst.store.AnalystStore;
import org.junit.jupiter.api.Test;
import org.redisson.api.RBloomFilter;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.Arrays;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 分层缓存压测 —— 和 CityAIHub {@code ShopCacheLoadTest} 同款方法论:
 *
 * <ul>
 *   <li><b>in-process(不走 HTTP)</b>:JUnit + 原生线程池直接压缓存层,避开 Tomcat/网络噪声,测纯缓存性能。</li>
 *   <li><b>数据量超过 L1 容量</b>:灌 8000 只股 &gt; Caffeine maxSize(4096)→ 冷门溢出到 L2 → 才测得出"二级"分层。</li>
 *   <li><b>Zipfian 长尾</b>:少数热点股占大部分访问(真实),热点稳定在 L1、长尾打 L2。</li>
 *   <li><b>三阶段</b>:冷启动 → 预热(不计)→ 4 万实测。</li>
 * </ul>
 *
 * 产出:分层命中率 L1/L2/DB(证明多级架构有效)+ QPS + P95。
 * 需真 Redis(6379/redis)+ 真 Postgres(5432)。手动跑:{@code mvn -o test -Dtest=AnalystCacheLoadTest}。
 */
@SpringBootTest
class AnalystCacheLoadTest {

    private static final int UNIVERSE = 8000;     // > L1 maxSize(4096),逼分层
    private static final double ZIPF_S = 0.8;     // 偏度(越大越集中)
    private static final int WARMUP = 20_000;
    private static final int MEASURE = 40_000;
    private static final int THREADS = 16;

    @Autowired private AnalystService analystService;
    @Autowired private CacheClient cacheClient;
    @Autowired private RBloomFilter<String> stockBloomFilter;
    @Autowired private JdbcTemplate jdbc;
    @Autowired private AnalystStore store;   // 直查 DB 的口子 —— 无缓存基线用

    @Test
    void layeredCacheLoadTest() throws Exception {
        String[] symbols = seedUniverse();          // 灌 8000 只股入 PG + 布隆
        double[] cdf = zipfCdf(UNIVERSE, ZIPF_S);    // Zipfian 累计分布

        // ① 基线:无缓存直连库 —— 每次 store.load(sym) 直查 PG,绕过 L1/L2
        Stats base = measure(MEASURE, symbols, cdf, store::load);

        // ② 预热两级缓存(注满,不计数)
        runRequests(WARMUP, symbols, cdf, 1);

        // ③ 两级缓存【不含布隆】—— 直接 getWithMutex(L1→L2→回源),隔离布隆开销
        Stats noBloom = measure(MEASURE, symbols, cdf, sym -> cacheClient.getWithMutex(
                CacheConstants.CACHE_ANALYST_KEY + sym, CacheConstants.LOCK_ANALYST_KEY + sym,
                AnalystRating.class, store::load,
                CacheConstants.CACHE_ANALYST_TTL, CacheConstants.CACHE_ANALYST_TTL_UNIT));

        // ④ 两级缓存【含布隆】—— 走完整 getAnalyst(布隆 → L1 → L2 → 回源)
        cacheClient.resetHitCounters();
        Stats cached = measure(MEASURE, symbols, cdf, analystService::getAnalyst);
        long l1 = cacheClient.getL1HitCount(), l2 = cacheClient.getL2HitCount(), db = cacheClient.getDbHitCount();
        long tot = l1 + l2 + db;

        // ⑤ 三方对比:DB 基线 vs 纯缓存(无布隆) vs 完整缓存(含布隆)
        System.out.println("\n========== 缓存压测(隔离布隆开销)==========");
        System.out.printf("全集=%d 只股(> L1 maxSize 4096)| Zipf s=%.1f | 线程=%d | 各测=%d 请求%n",
                UNIVERSE, ZIPF_S, THREADS, MEASURE);
        System.out.printf("① 无缓存直连库      : QPS %.0f | avg %.2fms | P95 %.2fms | P99 %.2fms%n",
                base.qps, base.avg, base.p95, base.p99);
        System.out.printf("② 两级缓存(无布隆)  : QPS %.0f | avg %.2fms | P95 %.2fms | P99 %.2fms%n",
                noBloom.qps, noBloom.avg, noBloom.p95, noBloom.p99);
        System.out.printf("③ 两级缓存(含布隆)  : QPS %.0f | avg %.2fms | P95 %.2fms | P99 %.2fms%n",
                cached.qps, cached.avg, cached.p95, cached.p99);
        System.out.printf("②vs① 纯缓存提升     : QPS %+.0f%% | P95 %+.0f%%%n",
                (noBloom.qps / base.qps - 1) * 100, (noBloom.p95 / base.p95 - 1) * 100);
        System.out.printf("③vs② 布隆的代价     : QPS %+.0f%% | avg %+.2fms%n",
                (cached.qps / noBloom.qps - 1) * 100, cached.avg - noBloom.avg);
        System.out.printf("中位数 P50(L1命中主导): DB %.1fµs | 无布隆 %.1fµs | 含布隆 %.1fµs%n",
                base.p50us, noBloom.p50us, cached.p50us);
        System.out.printf("分层命中率(含布隆)  : L1 %.1f%% + L2 %.1f%% + DB %.1f%%  (总 %d)%n",
                pct(l1, tot), pct(l2, tot), pct(db, tot), tot);
        System.out.println("============================================\n");

        cleanup();
    }

    /** 跑 n 个请求(Zipf 采样 + 16 线程),逐请求记耗时,返回 QPS/P95/P99/avg。 */
    private Stats measure(int n, String[] symbols, double[] cdf,
                          java.util.function.Consumer<String> op) throws Exception {
        long[] latNs = new long[n];
        AtomicInteger idx = new AtomicInteger();
        ExecutorService pool = Executors.newFixedThreadPool(THREADS);
        long wall0 = System.nanoTime();
        CountDownLatch done = new CountDownLatch(n);
        for (int i = 0; i < n; i++) {
            pool.submit(() -> {
                String sym = symbols[sampleZipf(cdf)];
                long t0 = System.nanoTime();
                op.accept(sym);
                latNs[idx.getAndIncrement()] = System.nanoTime() - t0;
                done.countDown();
            });
        }
        done.await(3, TimeUnit.MINUTES);
        long wallNs = System.nanoTime() - wall0;
        pool.shutdownNow();
        Arrays.sort(latNs);
        Stats s = new Stats();
        s.qps = n / (wallNs / 1e9);
        s.p50us = latNs[(int) (n * 0.50)] / 1e3;   // 中位数(µs)—— 82.8% 是 L1 命中,P50 即 L1 命中延迟
        s.p95 = latNs[(int) (n * 0.95)] / 1e6;
        s.p99 = latNs[(int) (n * 0.99)] / 1e6;
        s.avg = Arrays.stream(latNs).average().orElse(0) / 1e6;
        return s;
    }

    private static final class Stats { double qps, p95, p99, avg, p50us; }

    // ---------------------------------------------------------------- 辅助

    /** 灌 UNIVERSE 只合成股入 Postgres + 布隆(批量)。 */
    private String[] seedUniverse() {
        String[] syms = new String[UNIVERSE];
        java.util.List<Object[]> batch = new java.util.ArrayList<>();
        for (int i = 0; i < UNIVERSE; i++) {
            String s = String.format("SYM%05d", i);
            syms[i] = s;
            stockBloomFilter.add(s);
            batch.add(new Object[]{s,
                    "{\"symbol\":\"" + s + "\",\"consensusRating\":\"Buy\",\"numAnalysts\":10}"});
        }
        jdbc.batchUpdate(
                "INSERT INTO analyst.analyst_ratings (symbol, payload, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) " +
                "ON CONFLICT (symbol) DO UPDATE SET payload = EXCLUDED.payload",
                batch);
        return syms;
    }

    private void cleanup() {
        jdbc.update("DELETE FROM analyst.analyst_ratings WHERE symbol LIKE 'SYM%'");
    }

    private void runRequests(int n, String[] symbols, double[] cdf, int threads) throws Exception {
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        CountDownLatch done = new CountDownLatch(n);
        for (int i = 0; i < n; i++) {
            pool.submit(() -> {
                analystService.getAnalyst(symbols[sampleZipf(cdf)]);
                done.countDown();
            });
        }
        done.await(2, TimeUnit.MINUTES);
        pool.shutdownNow();
    }

    /** Zipf 累计分布:p(rank i) ∝ 1/i^s。 */
    private double[] zipfCdf(int n, double s) {
        double[] cdf = new double[n];
        double sum = 0;
        for (int i = 1; i <= n; i++) {
            sum += 1.0 / Math.pow(i, s);
            cdf[i - 1] = sum;
        }
        for (int i = 0; i < n; i++) cdf[i] /= sum; // 归一化
        return cdf;
    }

    /** 按 Zipf 采样一个下标(二分)。 */
    private int sampleZipf(double[] cdf) {
        double r = ThreadLocalRandom.current().nextDouble();
        int lo = 0, hi = cdf.length - 1;
        while (lo < hi) {
            int mid = (lo + hi) >>> 1;
            if (cdf[mid] < r) lo = mid + 1;
            else hi = mid;
        }
        return lo;
    }

    private double pct(long x, long tot) {
        return tot == 0 ? 0 : 100.0 * x / tot;
    }
}
