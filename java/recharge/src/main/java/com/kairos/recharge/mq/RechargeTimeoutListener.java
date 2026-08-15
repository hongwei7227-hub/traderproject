package com.kairos.recharge.mq;

import com.kairos.recharge.config.RocketMQConstants;
import com.kairos.recharge.service.IRechargeOrderService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.spring.annotation.ConsumeMode;
import org.apache.rocketmq.spring.annotation.MessageModel;
import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
import org.apache.rocketmq.spring.core.RocketMQListener;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

import static com.kairos.recharge.constant.RedisConstants.ORDER_STATE_LOCK_PREFIX;

@Slf4j
@Component
@RequiredArgsConstructor
@RocketMQMessageListener(
        consumerGroup = RocketMQConstants.ORDER_TIMEOUT_CONSUMER_GROUP,
        topic = RocketMQConstants.ORDER_TIMEOUT_TOPIC,
        selectorExpression = RocketMQConstants.ORDER_TIMEOUT_TAG,
        consumeMode = ConsumeMode.CONCURRENTLY,
        messageModel = MessageModel.CLUSTERING
)
public class RechargeTimeoutListener implements RocketMQListener<String> {

    private final IRechargeOrderService rechargeOrderService;
    private final RedissonClient redissonClient;

    @Override
    public void onMessage(String orderIdStr) {
        log.info("收到订单超时消息：订单ID={}", orderIdStr);

        try {
            Long orderId = Long.parseLong(orderIdStr);

            // 与"支付回调"链路共用同一把锁 ORDER_STATE_LOCK_PREFIX + orderId，两条链路对同一订单互斥串行。
            RLock lock = redissonClient.getLock(ORDER_STATE_LOCK_PREFIX + orderId);

            boolean isLock = lock.tryLock(0, 5, TimeUnit.SECONDS);
            if (!isLock) {
                log.warn("获取订单状态锁失败，订单ID={}", orderId);
                return;
            }

            try {
                rechargeOrderService.closeTimeoutOrder(orderId);
            } finally {
                if (lock.isHeldByCurrentThread()) {
                    lock.unlock();
                }
            }

        } catch (Exception e) {
            log.error("订单超时处理异常，订单ID={}", orderIdStr, e);
            throw new IllegalStateException("订单超时处理失败，订单ID=" + orderIdStr, e);
        }
    }
}
