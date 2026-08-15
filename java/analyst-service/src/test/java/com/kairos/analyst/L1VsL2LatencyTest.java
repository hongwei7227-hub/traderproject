package com.kairos.analyst;

import com.github.benmanes.caffeine.cache.Cache;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.util.Arrays;

/**
 * L1(Caffeine 本地内存)vs L2(Redis GET)单跳延迟隔离实测。
 * 同一个 key、单线程、各测 N 次,剥掉布隆/HTTP/多线程噪声,纯量两级缓存的取一次有多快。
 * 手动跑:mvn -o test -Dtest=L1VsL2LatencyTest（需 Redis 6379/redis + Postgres 起）。
 */
@SpringBootTest
class L1VsL2LatencyTest {

    private static final int N = 100_000;
    private static final int WARMUP = 20_000;

    @Autowired private Cache<String, Object> caffeine;
    @Autowired private StringRedisTemplate redis;

    @Test
    void l1VsL2() {
        String key = "bench:l1l2:AAPL";
        String val = "{\"symbol\":\"AAPL\",\"consensusRating\":\"Strong Buy\",\"targetPriceConsensus\":260.0,\"numAnalysts\":50}";
        caffeine.put(key, val);
        redis.opsForValue().set(key, val);

        for (int i = 0; i < WARMUP; i++) { caffeine.getIfPresent(key); redis.opsForValue().get(key); }

        long[] l1 = new long[N];
        for (int i = 0; i < N; i++) { long t0 = System.nanoTime(); caffeine.getIfPresent(key); l1[i] = System.nanoTime() - t0; }

        long[] l2 = new long[N];
        for (int i = 0; i < N; i++) { long t0 = System.nanoTime(); redis.opsForValue().get(key); l2[i] = System.nanoTime() - t0; }

        Arrays.sort(l1);
        Arrays.sort(l2);
        System.out.println("\n========== L1(本地内存)vs L2(Redis)单跳实测 ==========");
        System.out.printf("各 %d 次,单线程,同一 key%n", N);
        report("L1 Caffeine 本地内存", l1);
        report("L2 Redis GET       ", l2);
        double l1p50 = l1[N / 2] / 1e3, l2p50 = l2[N / 2] / 1e3;
        double l1avg = avgUs(l1), l2avg = avgUs(l2);
        System.out.printf("倍数(L2/L1): P50 %.0fx | avg %.0fx%n", l2p50 / l1p50, l2avg / l1avg);
        System.out.println("======================================================\n");

        redis.delete(key);
    }

    private void report(String label, long[] sorted) {
        int n = sorted.length;
        System.out.printf("%s: P50 %.2fµs | P95 %.2fµs | P99 %.2fµs | avg %.2fµs%n",
                label, sorted[(int) (n * 0.50)] / 1e3, sorted[(int) (n * 0.95)] / 1e3,
                sorted[(int) (n * 0.99)] / 1e3, avgUs(sorted));
    }

    private double avgUs(long[] a) {
        long s = 0;
        for (long x : a) s += x;
        return (double) s / a.length / 1e3;
    }
}
