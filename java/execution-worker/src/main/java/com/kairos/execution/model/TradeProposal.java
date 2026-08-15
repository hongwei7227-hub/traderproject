package com.kairos.execution.model;

import lombok.Data;

import java.math.BigDecimal;

/**
 * 下单消息载荷 —— Python 决策层审批通过后发进 RocketMQ 的内容。
 *
 * <p>字段对齐 ibkr下单 doc §3 时序步骤 2 的 TradeProposal 与 07-plan-phase2 任务 1。
 * Python 侧构造，Java worker 消费。跨语言靠 JSON（RocketMQ 只认字节，语言解耦）。
 *
 * <p>⚠️ {@code proposalId} + {@code tenantId} 组成幂等键（消息可能多次投递）。
 * {@code accountId} 作为 RocketMQ 顺序键（同账户按序消费）。
 */
@Data
public class TradeProposal {

    /** 幂等键之一：一次提议唯一 id（Python 侧生成）。 */
    private String proposalId;

    /** 幂等键之二 + 租户隔离。 */
    private String tenantId;

    /** RocketMQ 顺序键：同一账户的消息进同一 queue，保证按序。 */
    private String accountId;

    private String symbol;      // e.g. "NVDA"
    private String action;      // "BUY" / "SELL"
    private long quantity;      // 股数
    private BigDecimal limitPrice;

    // bracket 三联单（可空；非空则下父单 + 止盈 + 止损）
    private BigDecimal takeProfit;
    private BigDecimal stopLoss;

    /** 挂单超时毫秒（>0 则发延迟消息，到点未成交自动撤单）。 */
    private long timeoutMillis;

    /**
     * 下单理由快照（agent 的投资论点，Python 决策时生成、随消息带来）。
     *
     * <p>只当不透明文本存进订单，供事后跨工作区复盘自包含（原对话可能已被上下文压缩摘要）。
     * 要看完整分析用 {@code proposalId} 回 Python 平台解析——本字段不指向 Python 内部结构。
     */
    private String rationale;
}
