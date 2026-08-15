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

/**
 * 退款消费者 —— 对账退款（单关了但付款成功）异步执行 + 失败重试。
 *
 * <p><b>锁在这里终于有真活</b>：临界区是"查状态 → 调外部支付网关退款"，网关调用是 CAS 盖不住的
 * 外部副作用，用 Redisson 锁串行化、防并发重入重复退。<b>崩溃重试的重复由网关幂等键兜</b>
 * （锁扛不住进程崩溃），见 {@code PaymentGatewayClient}。三层各守一种失败：
 * CAS 管首次并发、锁管并发重入、网关幂等键管崩溃重试。
 */
@Slf4j
@Component
@RequiredArgsConstructor
@RocketMQMessageListener(
        consumerGroup = RocketMQConstants.REFUND_CONSUMER_GROUP,
        topic = RocketMQConstants.REFUND_TOPIC,
        selectorExpression = RocketMQConstants.REFUND_TAG,
        consumeMode = ConsumeMode.CONCURRENTLY,
        messageModel = MessageModel.CLUSTERING
)
public class RefundConsumer implements RocketMQListener<String> {

    private final IRechargeOrderService rechargeOrderService;
    private final RedissonClient redissonClient;

    @Override
    public void onMessage(String orderIdStr) {
        log.info("收到退款消息：订单ID={}", orderIdStr);
        Long orderId = Long.parseLong(orderIdStr);

        RLock lock = redissonClient.getLock(ORDER_STATE_LOCK_PREFIX + orderId);
        boolean locked = false;
        try {
            locked = lock.tryLock(0, 10, TimeUnit.SECONDS);
            if (!locked) {
                log.warn("退款锁未获取，跳过（另一消费者在处理）orderId={}", orderId);
                return;
            }
            rechargeOrderService.doRefund(orderId);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            log.error("退款处理被中断 orderId={}", orderId, e);
        } finally {
            if (locked && lock.isHeldByCurrentThread()) {
                lock.unlock();
            }
        }
    }
}
