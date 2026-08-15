package com.kairos.recharge.constant;

/**
 * Redis key 前缀常量。
 */
public final class RedisConstants {

    private RedisConstants() {
    }

    /**
     * 订单状态锁前缀 —— <b>支付回调链路与超时关单链路共用同一把</b>（{@code lock:recharge:order:{orderId}}），
     * 两条链路对同一订单互斥串行。移植 CityAIHub {@code ORDER_STATE_LOCK_PREFIX}。
     */
    public static final String ORDER_STATE_LOCK_PREFIX = "lock:recharge:order:";

    /**
     * 下单幂等前缀 —— {@code idem:recharge:submit:{requestId}}，SETNX 去重，
     * 防重复点击 / RocketMQ 至少一次投递下的重复消费。
     */
    public static final String SUBMIT_IDEM_PREFIX = "idem:recharge:submit:";
}
