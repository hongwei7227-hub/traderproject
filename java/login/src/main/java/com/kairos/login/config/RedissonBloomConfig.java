package com.kairos.login.config;

import com.kairos.login.constant.RedisConstants;
import org.redisson.api.RBloomFilter;
import org.redisson.api.RedissonClient;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * 布隆过滤器 Bean（和 CityAIHub 同款：Redisson RBloomFilter，Redis 支撑、跨重启/跨节点共享）。
 *
 * RedissonClient 由 redisson-spring-boot-starter 依据 spring.data.redis.* 自动装配。
 * 预计 10 万用户、1% 假阳性 —— 假阳性由"回退精确查 UserStore"兜底，绝不漏。
 */
@Configuration
public class RedissonBloomConfig {

    @Bean
    public RBloomFilter<String> userBloomFilter(RedissonClient redissonClient) {
        RBloomFilter<String> bloom = redissonClient.getBloomFilter(RedisConstants.USER_BLOOM_FILTER);
        // tryInit 幂等：已初始化则跳过，不会清空已加入的元素
        bloom.tryInit(100_000L, 0.01);
        return bloom;
    }
}
