package com.kairos.recharge.config;

/**
 * RocketMQ topic / tag / 消费组常量。
 *
 * <p>两条链路（对齐 spec 13 §4）：
 * <ul>
 *   <li>下单：{@code recharge-submit}（顺序消息，顺序键=user_id → 同用户有序、不同用户并行）</li>
 *   <li>超时关单：{@code recharge-timeout}（延迟消息，delay=支付超时时长）</li>
 * </ul>
 *
 * <p>⚠️ RocketMQ topic 名只允许 {@code ^[%|a-zA-Z0-9_-]+$}，不许有点。
 * broker 复用 execution-worker 在用的同一个 {@code rmqnamesrv:9876}，本应用只是新增这两个 topic。
 */
public final class RocketMQConstants {

    private RocketMQConstants() {
    }

    // 下单（顺序消息）
    public static final String ORDER_SUBMIT_TOPIC = "recharge-submit";
    public static final String ORDER_SUBMIT_TAG = "create";
    public static final String ORDER_SUBMIT_DESTINATION = ORDER_SUBMIT_TOPIC + ":" + ORDER_SUBMIT_TAG;
    public static final String ORDER_SUBMIT_CONSUMER_GROUP = "recharge-submit-consumer";

    // 超时关单（延迟消息）
    public static final String ORDER_TIMEOUT_TOPIC = "recharge-timeout";
    public static final String ORDER_TIMEOUT_TAG = "expire";
    public static final String ORDER_TIMEOUT_DESTINATION = ORDER_TIMEOUT_TOPIC + ":" + ORDER_TIMEOUT_TAG;
    public static final String ORDER_TIMEOUT_CONSUMER_GROUP = "recharge-timeout-consumer";

    // 退款（对账：单关了但付款成功 → 退钱；异步 + 失败重试）
    public static final String REFUND_TOPIC = "recharge-refund";
    public static final String REFUND_TAG = "refund";
    public static final String REFUND_DESTINATION = REFUND_TOPIC + ":" + REFUND_TAG;
    public static final String REFUND_CONSUMER_GROUP = "recharge-refund-consumer";
}
