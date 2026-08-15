package com.kairos.recharge.idempotency;

import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.time.Duration;

/**
 * 幂等守卫 —— Redis SET NX。RocketMQ 至少投递一次，消费端必须去重（spec 13 §4）。
 * 首次返回 true（放行），重复返回 false（跳过）。
 */
@Component
@RequiredArgsConstructor
public class IdempotencyGuard {

    private static final Duration TTL = Duration.ofHours(24);

    private final StringRedisTemplate redis;

    /** @param key 调用方拼好的完整 key（如 idem:recharge:submit:{requestId}）。 */
    public boolean firstSeen(String key) {
        Boolean ok = redis.opsForValue().setIfAbsent(key, "1", TTL);
        return Boolean.TRUE.equals(ok);
    }
}
