package com.kairos.execution.test;

import com.kairos.execution.config.RocketMQConstants;
import com.kairos.execution.model.TradeProposal;
import org.apache.rocketmq.spring.core.RocketMQTemplate;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.CommandLineRunner;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;

/**
 * 一次性冒烟测试发单器 —— 仅当启动带 {@code --kairos.test.send-order=true} 时激活。
 *
 * <p>启动后等 ~8s（让 consumer + IB 连接就绪），发一条 TradeProposal 到 RocketMQ，
 * 验证 收消息→幂等→下单→状态机跟踪 整条链路。
 *
 * <p>测试单 = 买 1 股 AAPL 限价 $1（远低于市价 → 会被接受但不会成交），安全不真建仓。
 */
@Component
@ConditionalOnProperty(name = "kairos.test.send-order", havingValue = "true")
public class TestOrderSender implements CommandLineRunner {

    private static final Logger log = LoggerFactory.getLogger(TestOrderSender.class);

    private final RocketMQTemplate rocketMQTemplate;

    public TestOrderSender(RocketMQTemplate rocketMQTemplate) {
        this.rocketMQTemplate = rocketMQTemplate;
    }

    @Override
    public void run(String... args) throws Exception {
        Thread.sleep(8000);   // 等 consumer 订阅 + IB Gateway 连接就绪

        TradeProposal p = new TradeProposal();
        p.setProposalId("mkt-" + System.currentTimeMillis());  // 每次唯一，永不撞 Redis 幂等键
        p.setTenantId("t-smoke");
        p.setAccountId("paper");
        p.setSymbol("NVDA");
        p.setAction("BUY");
        p.setQuantity(1);
        p.setLimitPrice(null);                        // 市价单(MKT)：即时成交，剥离撮合等待，测纯回报延迟
        p.setTimeoutMillis(60000);                    // 60s 内未成交才自动撤

        log.info(">>> [冒烟] 发送测试下单消息: {} {} x{} @{}",
                p.getAction(), p.getSymbol(), p.getQuantity(), p.getLimitPrice());
        rocketMQTemplate.syncSend(RocketMQConstants.ORDER_SUBMIT_DESTINATION, p);
        log.info(">>> [冒烟] 消息已发送，等 worker 消费下单...");
    }
}
