package com.kairos.recharge.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.kairos.recharge.common.Result;
import com.kairos.recharge.entity.RechargeOrder;

public interface IRechargeOrderService extends IService<RechargeOrder> {

    /** 下单：校验套餐 → 发 recharge-submit 顺序消息（顺序键=userId）→ 返回 orderId。 */
    Result submitOrder(Long userId, Long planId, String requestId);

    /** MQ 消费端建单：SETNX 幂等 → 落库（待支付）→ 发超时延迟消息。 */
    void createRechargeOrder(RechargeOrder order);

    /** 支付成功：CAS(待支付→已支付) + 授予会员，同一事务。由支付回调在锁内调用。 */
    Result payAndGrant(Long orderId);

    /** 超时关单：CAS(待支付→已取消)。由超时延迟消息在锁内调用。 */
    void closeTimeoutOrder(Long orderId);

    /**
     * 对账退款触发：支付回调发现订单已被超时关单（已取消），但钱付成功了 →
     * CAS(已取消→退款中) + 发退款 MQ。由支付回调在锁内调用。
     */
    Result reconcileRefund(Long orderId);

    /**
     * 执行退款：以"退款中"为条件重驱（不从已取消 CAS，故崩溃后重试能重新进来），
     * 调外部网关退款（幂等键=orderId，网关认得退过没）→ CAS(退款中→已退款)。
     * 由退款消费者在锁内调用。锁防并发重入，网关幂等键防崩溃重试重复退。
     */
    void doRefund(Long orderId);
}
