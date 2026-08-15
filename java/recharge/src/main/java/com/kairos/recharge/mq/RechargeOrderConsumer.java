package com.kairos.recharge.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.kairos.recharge.config.RocketMQConstants;
import com.kairos.recharge.entity.RechargeOrder;
import com.kairos.recharge.service.IRechargeOrderService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.spring.annotation.ConsumeMode;
import org.apache.rocketmq.spring.annotation.MessageModel;
import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
import org.apache.rocketmq.spring.core.RocketMQListener;
import org.springframework.stereotype.Component;

@Component
@RequiredArgsConstructor
@Slf4j
@RocketMQMessageListener(
        consumerGroup = RocketMQConstants.ORDER_SUBMIT_CONSUMER_GROUP,
        topic = RocketMQConstants.ORDER_SUBMIT_TOPIC,
        selectorExpression = RocketMQConstants.ORDER_SUBMIT_TAG,
        // ORDERLY：配合生产端按 userId 分片（syncSendOrderly hashKey=userId），
        // 同用户消息进同一 queue 且单线程按序消费 → "同用户有序"整条才成立。
        consumeMode = ConsumeMode.ORDERLY,
        messageModel = MessageModel.CLUSTERING
)
public class RechargeOrderConsumer implements RocketMQListener<String> {

    private final IRechargeOrderService rechargeOrderService;
    private final ObjectMapper objectMapper;

    @Override
    public void onMessage(String message) {
        try {
            log.info("收到充值下单消息：{}", message);
            RechargeOrder order = objectMapper.readValue(message, RechargeOrder.class);
            rechargeOrderService.createRechargeOrder(order);
        } catch (Exception e) {
            log.error("消费充值下单消息失败，message={}", message, e);
            throw new IllegalStateException("消费充值下单消息失败", e);
        }
    }
}