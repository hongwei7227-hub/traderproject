package com.kairos.execution.idempotency;

import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Component;

import java.time.Duration;

/**
 * 下单幂等守卫 —— RocketMQ 至少投递一次，消息可能重复，消费端必须去重（见 spec 00 §4.2、07 任务 4）。
 *
 * <p>逻辑对齐已验证的 Python 原型 {@code kairos/下端后端/实现/idempotency_guard/}（6 单测）：
 * key = {@code idem:{tenantId}:{proposalId}}，Redisson {@code setIfAbsent}（SETNX + TTL）。
 * 首次返回 true（放行）；重复返回 false（跳过，已处理过）。
 */
@Component
public class IdempotencyGuard {

    private static final Duration TTL = Duration.ofHours(24);

    private final RedissonClient redisson;

    public IdempotencyGuard(RedissonClient redisson) {
        this.redisson = redisson;
    }

    /**
     * @return true=首次，放行下单；false=重复消息，跳过。
     */
    public boolean firstSeen(String tenantId, String proposalId) {
        String key = "idem:" + tenantId + ":" + proposalId;
        return redisson.getBucket(key).setIfAbsent("1", TTL);
    }
}
