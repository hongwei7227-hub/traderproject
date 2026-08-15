package com.kairos.analyst.mq;

import com.kairos.analyst.cache.CacheClient;
import com.kairos.analyst.constant.MqConstants;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.spring.annotation.ConsumeMode;
import org.apache.rocketmq.spring.annotation.MessageModel;
import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
import org.apache.rocketmq.spring.core.RocketMQListener;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/**
 * 跨节点缓存失效广播<b>接收端</b>。
 *
 * <p><b>BROADCASTING</b>:每个实例都收到消息(不是竞争消费),各自清本地 Caffeine L1 —— 这正是
 * CityAIHub 缺失的"集群广播失效"。<b>CONCURRENTLY</b>:失效无顺序要求。消费组独立于下单。
 *
 * <p><b>非致命开关(review B1)</b>:整个 Bean 由 {@code cache.invalidate.mq.enabled=true} 才创建。
 * 默认关 → 不声明 RocketMQ 消费者 → 启动<b>不依赖 broker</b>,读服务照常。开启前请先起 broker。
 *
 * <p>广播模式 offset 存本地(review W3):实例宕机期间的失效消息不会补投,该实例 L1 靠 2min TTL
 * 自然收敛 —— 可接受,短 TTL 是兜底。
 */
@Slf4j
@Component
@ConditionalOnProperty(name = "cache.invalidate.mq.enabled", havingValue = "true")
@RocketMQMessageListener(
        topic = MqConstants.CACHE_INVALIDATE_TOPIC,
        consumerGroup = MqConstants.CACHE_INVALIDATE_GROUP,
        messageModel = MessageModel.BROADCASTING,
        consumeMode = ConsumeMode.CONCURRENTLY
)
public class CacheInvalidateConsumer implements RocketMQListener<String> {

    private final CacheClient cacheClient;

    public CacheInvalidateConsumer(CacheClient cacheClient) {
        this.cacheClient = cacheClient;
    }

    @Override
    public void onMessage(String cacheKey) {
        // 只清本地 L1(Redis 是共享的,发起方已删)。收到自己发的广播也无害(已删,再删 no-op)。
        cacheClient.invalidateL1Only(cacheKey);
        log.debug("cache.invalidate received | key={}", cacheKey);
    }
}
