package com.kairos.recharge;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * 会员充值/订阅支付 demo 入口。
 *
 * <p>承载 RocketMQ 支付栈（顺序消息下单 + 延迟消息超时关单 + SETNX 幂等 + 状态机 CAS +
 * Redisson 锁"支付回调 vs 超时关单"两条链路）。与 execution-worker / analyst-service / 登录
 * 并列，共用同一套 RocketMQ / Redis 基础设施，独享 MySQL schema。
 */
@SpringBootApplication
@MapperScan("com.kairos.recharge.mapper")
public class RechargeApplication {

    public static void main(String[] args) {
        SpringApplication.run(RechargeApplication.class, args);
    }
}
