package com.kairos.analyst.config;

import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.concurrent.TimeUnit;

/**
 * Caffeine L1 本地缓存。
 *
 * <p>expireAfterWrite(10min)—— 这是**正确性兜底**:当广播失效关闭 / broker 抖 / 广播消息漏投时,
 * 各节点本地 L1 最多陈旧 10 分钟就自然收敛。分析师数据天级才变,10 分钟陈旧完全可接受,
 * 且比 2min 命中率更高、更省回源。广播删是即时主力,短 TTL 只是删失败时的自愈上限。
 */
@Configuration
public class CacheConfig {

    @Bean
    public Cache<String, Object> caffeineCache() {
        return Caffeine.newBuilder()
                .initialCapacity(128)
                .maximumSize(4096)
                .expireAfterWrite(10, TimeUnit.MINUTES)
                .recordStats()
                .build();
    }
}
