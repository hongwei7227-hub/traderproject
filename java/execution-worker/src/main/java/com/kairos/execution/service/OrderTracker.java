package com.kairos.execution.service;

import com.kairos.execution.domain.IbStatusMapper;
import com.kairos.execution.domain.OrderStateMachine;
import com.kairos.execution.domain.OrderStatus;
import com.kairos.execution.model.TradeProposal;
import com.kairos.execution.repository.OrderRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * 订单跟踪器（§8 已落库版）—— 把 IB 回报驱动进 Postgres 里每一单的状态，用乐观锁 CAS 防并发改乱。
 *
 * <p><b>相比旧版（内存 Map）的变化：</b>状态从进程内存搬进 {@code execution.orders} 表 →
 * worker 重启不丢、多 worker 共享同一账本、能加锁。{@link OrderStateMachine} 不再持有实例状态，
 * 改用<b>静态</b> {@link OrderStateMachine#canTransition} 做“这个转换合不合法”的纯校验；
 * “写时没人动过它”交给 {@link OrderRepository#casUpdateStatus} 的 DB CAS。
 *
 * <p><b>分工：</b>内存状态表答“转换合法性”，DB CAS 答“并发安全”。两者叠加 = 既不乱转、也不脏写。
 */
@Component
public class OrderTracker {

    private static final Logger log = LoggerFactory.getLogger(OrderTracker.class);

    /** CAS 撞车时的重读重判次数（乐观锁标准做法：失败→重读最新态→再判再试）。 */
    private static final int MAX_CAS_RETRY = 3;

    private final OrderRepository repo;

    public OrderTracker(OrderRepository repo) {
        this.repo = repo;
    }

    /** 下单成功后登记：把订单以 SUBMITTED 落库（INITIALIZED→SUBMITTED 恒合法）。 */
    public void register(TradeProposal proposal, String brokerOrderId) {
        repo.insert(proposal, brokerOrderId, OrderStatus.SUBMITTED);
        log.info("开始跟踪订单 {}（{}）已落库", brokerOrderId, OrderStatus.SUBMITTED);
    }

    /**
     * IB orderStatus 回报入口（由 BrokerAdapter 注册的回调调用）。
     * 读库当前态 → 映射目标态 → 合法性校验 → CAS 落库；CAS 撞车则重读重判。
     */
    public void onStatus(String brokerOrderId, String ibStatus, long filledQty, double avgFillPrice) {
        IbStatusMapper.MappingResult r = IbStatusMapper.map(ibStatus, "");
        if (!r.handled()) {
            log.warn("订单 {} 未知 IB 状态: {}", brokerOrderId, ibStatus);
            return;
        }
        if (r.status() == null) {
            // locate 挂起等：保持 active，不改状态
            log.info("订单 {} 保持 active: {}", brokerOrderId, r.note());
            return;
        }
        OrderStatus target = r.status();

        for (int attempt = 1; attempt <= MAX_CAS_RETRY; attempt++) {
            OrderStatus current = repo.findStatus(brokerOrderId);
            if (current == null) {
                log.warn("收到未跟踪订单的状态: {} ({})", brokerOrderId, ibStatus);
                return;
            }
            if (target == current) {
                return;   // IB 会重复推同一状态，幂等跳过
            }
            if (!OrderStateMachine.canTransition(current, target)) {
                log.warn("订单 {} 非法状态转换（忽略）: {} -> {}", brokerOrderId, current, target);
                return;
            }

            // 乐观锁 CAS：仅当库里仍是 current 才改成 target
            if (repo.casUpdateStatus(brokerOrderId, current, target)) {
                log.info("订单 {} 状态: {} → {}（累计成交 {} @ {}）",
                        brokerOrderId, current, target, filledQty, avgFillPrice);
                if (target.isTerminal()) {
                    log.info("订单 {} 已到终态 {}", brokerOrderId, target);
                }
                return;
            }

            // 影响行数 0：撞车（成交与撤单并发等），别人已改状态 → 重读重判
            log.info("订单 {} CAS 撞车（第 {} 次），状态已被并发改动，重读重判", brokerOrderId, attempt);
        }
        log.warn("订单 {} CAS 重试 {} 次仍失败，放弃本次流转（目标 {}）", brokerOrderId, MAX_CAS_RETRY, target);
    }

    /** 读当前状态；不存在返回 null。 */
    public OrderStatus currentStatus(String brokerOrderId) {
        return repo.findStatus(brokerOrderId);
    }
}
