package com.kairos.execution.mq;

import com.kairos.execution.broker.BrokerAdapter;
import com.kairos.execution.config.RocketMQConstants;
import com.kairos.execution.domain.OrderStatus;
import com.kairos.execution.service.OrderTracker;
import org.apache.rocketmq.spring.annotation.ConsumeMode;
import org.apache.rocketmq.spring.annotation.MessageModel;
import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
import org.apache.rocketmq.spring.core.RocketMQListener;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

/**
 * 挂单超时自动撤单 —— RocketMQ 延迟消息到点触发（对应 CityAIHub 的"支付超时关单"）。
 *
 * <p>下单时发一条延迟消息（延迟=timeoutMillis）；到点本 listener 消费：
 * 若订单<b>仍未到终态</b>（未成交/未撤/未拒）→ 调 IBKR 撤单；已终态则幂等跳过
 * （处理"到点了订单已成交"的竞态 —— 与 CityAIHub 的 CAS"仅未支付才关"同理）。
 *
 * <p>⚠️ 锁用 <b>Redisson RLock（看门狗自动续期）</b>，不是 CityAIHub 的 SimpleRedisLock
 * （无看门狗，是反例，见 spec 00 §4.4）。
 *
 * <p>✅ §8 已把订单状态外置到 Postgres（{@link OrderTracker} 走 {@code execution.orders} 表），
 * 多 worker 共享同一账本 —— 超时消息即使被非下单实例消费也能查到状态，单机限制已解除。
 * 仍待 Phase 3/4：IB cancel 的 clientId 约束（跨实例撤单需对齐下单时的 clientId）。
 */
@Component
@RocketMQMessageListener(
        topic = RocketMQConstants.ORDER_TIMEOUT_TOPIC,
        consumerGroup = RocketMQConstants.ORDER_TIMEOUT_CONSUMER_GROUP,
        selectorExpression = RocketMQConstants.ORDER_TIMEOUT_TAG,
        consumeMode = ConsumeMode.CONCURRENTLY,
        messageModel = MessageModel.CLUSTERING
)
public class OrderTimeoutListener implements RocketMQListener<String> {

    private static final Logger log = LoggerFactory.getLogger(OrderTimeoutListener.class);

    private final RedissonClient redisson;
    private final OrderTracker tracker;
    private final BrokerAdapter broker;

    public OrderTimeoutListener(RedissonClient redisson, OrderTracker tracker, BrokerAdapter broker) {
        this.redisson = redisson;
        this.tracker = tracker;
        this.broker = broker;
    }

    @Override
    public void onMessage(String brokerOrderId) {
        log.info("收到挂单超时消息: orderId={}", brokerOrderId);

        RLock lock = redisson.getLock("lock:order-timeout:" + brokerOrderId);
        boolean locked = false;
        try {
            // waitTime=0 不等待；leaseTime 交给看门狗自动续期（Redisson 2 参 tryLock）
            locked = lock.tryLock(0, TimeUnit.SECONDS);
            if (!locked) {
                log.warn("订单 {} 超时处理锁未获取到，跳过（另一消费者在处理）", brokerOrderId);
                return;
            }

            OrderStatus st = tracker.currentStatus(brokerOrderId);
            if (st == null) {
                // 库里查无此单（理论不该发生：下单落库先于发延迟消息）；仍尝试撤单兜底（IB 为真相源，已成交会 no-op）
                log.warn("订单 {} 库中无记录，尝试撤单兜底", brokerOrderId);
                broker.cancelOrder(brokerOrderId);
                return;
            }
            if (st.isTerminal()) {
                log.info("订单 {} 已到终态 {}，无需超时撤单（幂等跳过）", brokerOrderId, st);
                return;
            }

            // 仍活跃 → 超时撤单
            log.info("订单 {} 超时未成交（当前 {}），发起撤单", brokerOrderId, st);
            boolean sent = broker.cancelOrder(brokerOrderId);
            log.info("订单 {} 超时撤单请求已{}", brokerOrderId, sent ? "发送" : "失败");

        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            log.error("订单 {} 超时处理被中断", brokerOrderId, e);
        } finally {
            if (locked && lock.isHeldByCurrentThread()) {
                lock.unlock();
            }
        }
    }
}
