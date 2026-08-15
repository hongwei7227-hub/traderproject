package com.kairos.execution;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Kairos 下单执行 Worker 入口。
 *
 * <p>职责（见 docs/superpowers/specs/后端/07-plan-phase2）：
 * 从 RocketMQ 竞争消费 Python 决策层批准的下单消息 → 幂等去重 → 订单状态机 →
 * 调 IBKR（Phase 2 先 paper socket，Phase 3 换 OAuth REST）→ 状态回流 Redis Stream。
 *
 * <p>这是一个独立微服务，<b>不并入 CityAIHub</b>（那是电商域）；只从 CityAIHub 搬
 * 脚手架依赖 + Redisson/RocketMQ 配置 + 可靠性代码模式。
 */
@SpringBootApplication
public class ExecutionWorkerApplication {

    public static void main(String[] args) {
        SpringApplication.run(ExecutionWorkerApplication.class, args);
    }
}
