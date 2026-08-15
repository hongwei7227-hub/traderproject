package com.kairos.execution.mq;

import com.kairos.execution.broker.BrokerAdapter;
import com.kairos.execution.config.RocketMQConstants;
import com.kairos.execution.idempotency.IdempotencyGuard;
import com.kairos.execution.model.TradeProposal;
import com.kairos.execution.service.OrderTracker;
import jakarta.annotation.PostConstruct;
import org.apache.rocketmq.spring.annotation.ConsumeMode;
import org.apache.rocketmq.spring.annotation.MessageModel;
import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
import org.apache.rocketmq.spring.core.RocketMQListener;
import org.apache.rocketmq.spring.core.RocketMQTemplate;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * 下单消息消费者（07-plan-phase2 任务 2）。
 *
 * <p><b>ORDERLY</b>：同账户按序消费（顺序键 = accountId，防撤单跑到买入前）。
 * <b>CLUSTERING</b>：竞争消费，多 worker 实例分摊下单量。
 * listener 写法参考 CityAIHub {@code SeckillVoucherListener}。
 */
@Component
@RocketMQMessageListener(
        topic = RocketMQConstants.ORDER_SUBMIT_TOPIC,
        consumerGroup = RocketMQConstants.ORDER_SUBMIT_CONSUMER_GROUP,
        consumeMode = ConsumeMode.ORDERLY,
        messageModel = MessageModel.CLUSTERING
)
public class OrderConsumer implements RocketMQListener<TradeProposal> {

    private static final Logger log = LoggerFactory.getLogger(OrderConsumer.class);

    private final IdempotencyGuard idempotency;
    private final BrokerAdapter broker;
    private final OrderTracker tracker;
    private final RocketMQTemplate rocketMQTemplate;

    public OrderConsumer(IdempotencyGuard idempotency, BrokerAdapter broker,
                         OrderTracker tracker, RocketMQTemplate rocketMQTemplate) {
        this.idempotency = idempotency;
        this.broker = broker;
        this.tracker = tracker;
        this.rocketMQTemplate = rocketMQTemplate;
    }

    /** 启动时把券商回报接到订单跟踪器（IB orderStatus → 状态机流转）。 */
    @PostConstruct
    void wireBrokerCallback() {
        broker.onOrderStatus(tracker::onStatus);
    }

    @Override
    public void onMessage(TradeProposal proposal) {
        // 1) 幂等：重复消息直接跳过（任务 4）
        if (!idempotency.firstSeen(proposal.getTenantId(), proposal.getProposalId())) {
            log.info("重复下单消息，跳过 proposalId={}", proposal.getProposalId());
            return;
        }

        // 2) 下单 → 券商（Phase 2 paper）
        String brokerOrderId = broker.placeOrder(proposal);

        // 3) 登记跟踪：把订单以 SUBMITTED 落库（§8）；后续 IB 回报经 wireBrokerCallback 的回调
        //    → tracker → 状态机校验 + CAS 更新库
        tracker.register(proposal, brokerOrderId);

        log.info("已下单并开始跟踪 proposalId={} brokerOrderId={}", proposal.getProposalId(), brokerOrderId);

        // 4) 挂单超时撤单：发延迟消息（RocketMQ 5.x 任意时长），到点 OrderTimeoutListener 检查未成交则撤单
        long timeout = proposal.getTimeoutMillis();
        if (timeout > 0) {
            rocketMQTemplate.syncSendDelayTimeMills(
                    RocketMQConstants.ORDER_TIMEOUT_DESTINATION,
                    brokerOrderId,
                    timeout);
            log.info("已发挂单超时延迟消息 orderId={} 延迟={}ms", brokerOrderId, timeout);
        }

        // TODO(任务 6)：状态事件 publish 回 Redis Stream → Python SSE 推用户。
    }
}
