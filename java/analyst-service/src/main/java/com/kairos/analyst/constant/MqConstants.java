package com.kairos.analyst.constant;

/**
 * RocketMQ 常量 —— 跨节点缓存失效广播。
 *
 * <p>⚠️ topic 名只允许 {@code ^[%|a-zA-Z0-9_-]+$},不许有点(照 execution-worker
 * RocketMQConstants 的血泪注释)。
 */
public final class MqConstants {

    private MqConstants() {}

    /** 缓存失效广播 topic。 */
    public static final String CACHE_INVALIDATE_TOPIC = "kairos-cache-invalidate";
    /** 广播消费组(独立于下单的消费组)。 */
    public static final String CACHE_INVALIDATE_GROUP = "kairos-cache-invalidate-consumer";
}
