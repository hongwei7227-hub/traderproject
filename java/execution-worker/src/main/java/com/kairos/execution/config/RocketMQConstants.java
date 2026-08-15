package com.kairos.execution.config;

/**
 * RocketMQ topic / tag / 消费组常量。
 *
 * <p>命名对齐 07-plan-phase2 任务 1：
 * <ul>
 *   <li>下单：{@code kairos.order.submit}</li>
 *   <li>撤单：{@code kairos.order.cancel}</li>
 * </ul>
 *
 * <p>⚠️ 顺序键 = {@code account_id}（同账户下单/撤单必须按序，防乱序把撤单跑到买入前）。
 * 消费用 ORDERLY + CLUSTERING（顺序 + 竞争消费）。
 *
 * <p>模式参考 CityAIHub {@code config/RocketMQConstants.java}（本地 F:\project\CityAIHub-master）。
 */
public final class RocketMQConstants {

    private RocketMQConstants() {
    }

    // 下单
    public static final String ORDER_SUBMIT_TOPIC = "kairos-order-submit";  // ⚠️ RocketMQ topic 名不许有点，只允许 ^[%|a-zA-Z0-9_-]+$
    public static final String ORDER_SUBMIT_TAG = "create";
    public static final String ORDER_SUBMIT_DESTINATION = ORDER_SUBMIT_TOPIC + ":" + ORDER_SUBMIT_TAG;
    public static final String ORDER_SUBMIT_CONSUMER_GROUP = "kairos-order-submit-consumer";

    // 撤单
    public static final String ORDER_CANCEL_TOPIC = "kairos-order-cancel";
    public static final String ORDER_CANCEL_TAG = "cancel";
    public static final String ORDER_CANCEL_DESTINATION = ORDER_CANCEL_TOPIC + ":" + ORDER_CANCEL_TAG;
    public static final String ORDER_CANCEL_CONSUMER_GROUP = "kairos-order-cancel-consumer";

    // 挂单超时撤单（延迟消息，模式抄 CityAIHub OrderTimeoutListener，见 07 任务 6 / ibkr下单 §6.1）
    public static final String ORDER_TIMEOUT_TOPIC = "kairos-order-timeout";
    public static final String ORDER_TIMEOUT_TAG = "expire";
    public static final String ORDER_TIMEOUT_DESTINATION = ORDER_TIMEOUT_TOPIC + ":" + ORDER_TIMEOUT_TAG;
    public static final String ORDER_TIMEOUT_CONSUMER_GROUP = "kairos-order-timeout-consumer";
}
