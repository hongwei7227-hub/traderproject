package com.kairos.analyst.config;

import com.kairos.analyst.constant.CacheConstants;
import org.redisson.api.RBloomFilter;
import org.redisson.api.RedissonClient;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * 股票布隆过滤器 Bean(和 CityAIHub shopBloomFilter 同款:Redisson RBloomFilter,Redis bitmap 支撑,
 * 跨重启/跨节点共享)。预热见 {@link com.kairos.analyst.component.BloomFilterInitializer}。
 *
 * <p>RedissonClient 由 redisson-spring-boot-starter 依 spring.data.redis.* 自动装配。
 */
@Configuration
public class BloomFilterConfig {

    @Bean
    public RBloomFilter<String> stockBloomFilter(RedissonClient redissonClient) {
        RBloomFilter<String> bloom = redissonClient.getBloomFilter(CacheConstants.STOCK_BLOOM_FILTER);
        // tryInit 幂等:已初始化则跳过。预留 10 万、误判率 1%(和 CityAIHub tryInit(100000,0.01) 同款)。
        bloom.tryInit(100_000L, 0.01);
        return bloom;
    }
}
