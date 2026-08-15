package com.kairos.recharge.service.impl;

import com.baomidou.mybatisplus.core.toolkit.IdWorker;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kairos.recharge.common.Result;
import com.kairos.recharge.config.RocketMQConstants;
import com.kairos.recharge.constant.RedisConstants;
import com.kairos.recharge.entity.MembershipPlan;
import com.kairos.recharge.entity.RechargeOrder;
import com.kairos.recharge.gateway.PaymentGatewayClient;
import com.kairos.recharge.idempotency.IdempotencyGuard;
import com.kairos.recharge.mapper.MembershipPlanMapper;
import com.kairos.recharge.mapper.RechargeOrderMapper;
import com.kairos.recharge.service.IMembershipService;
import com.kairos.recharge.service.IRechargeOrderService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.spring.core.RocketMQTemplate;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;

/**
 * 充值订单服务 —— 移植 CityAIHub {@code VoucherOrderServiceImpl}：
 * 保留"下单发 MQ + 消费端建单 + 超时 CAS 关单"骨架，剥掉秒杀 Lua/库存/pending，
 * 换会员语义（支付成功授予会员）。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class RechargeOrderServiceImpl extends ServiceImpl<RechargeOrderMapper, RechargeOrder>
        implements IRechargeOrderService {

    private final RocketMQTemplate rocketMQTemplate;
    private final ObjectMapper objectMapper;
    private final MembershipPlanMapper planMapper;
    private final IdempotencyGuard idempotency;
    private final IMembershipService membershipService;
    private final PaymentGatewayClient gateway;

    @Value("${recharge.pay-timeout-millis:900000}")
    private long payTimeoutMillis;

    @Override
    public Result submitOrder(Long userId, Long planId, String requestId) {
        MembershipPlan plan = planMapper.selectById(planId);
        if (plan == null || !Integer.valueOf(1).equals(plan.getStatus())) {
            return Result.fail("套餐不存在或已下架");
        }

        long orderId = IdWorker.getId();
        RechargeOrder order = new RechargeOrder()
                .setId(orderId)
                .setUserId(userId)
                .setPlanId(planId)
                .setAmount(plan.getPrice())
                .setStatus(RechargeOrder.STATUS_UNPAID)
                .setCreateTime(LocalDateTime.now())
                .setRequestId(requestId);

        // 顺序消息，hashKey=userId → 同用户下单/撤单严格有序、不同用户并行消费可水平扩展。
        try {
            rocketMQTemplate.syncSendOrderly(
                    RocketMQConstants.ORDER_SUBMIT_DESTINATION,
                    objectMapper.writeValueAsString(order),
                    String.valueOf(userId));
        } catch (Exception e) {
            log.error("下单消息发送失败 orderId={}", orderId, e);
            return Result.fail("系统繁忙，请稍后重试");
        }
        return Result.ok(orderId);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void createRechargeOrder(RechargeOrder order) {
        // SETNX 幂等：防重复点击 / RocketMQ 至少一次投递下的重复消费。
        if (order.getRequestId() != null
                && !idempotency.firstSeen(RedisConstants.SUBMIT_IDEM_PREFIX + order.getRequestId())) {
            log.info("重复下单消息，跳过 requestId={}", order.getRequestId());
            return;
        }
        // 二次防护：订单已存在
        if (getById(order.getId()) != null) {
            log.warn("订单已存在，忽略重复消息 orderId={}", order.getId());
            return;
        }

        if (order.getCreateTime() == null) {
            order.setCreateTime(LocalDateTime.now());
        }
        boolean saved = save(order);
        if (!saved) {
            throw new IllegalStateException("保存充值订单失败 orderId=" + order.getId());
        }

        // 超时关单：发延迟消息（RocketMQ 5.x 任意时长），到点 RechargeTimeoutListener 检查未支付则关。
        try {
            rocketMQTemplate.syncSendDelayTimeMills(
                    RocketMQConstants.ORDER_TIMEOUT_DESTINATION,
                    String.valueOf(order.getId()),
                    payTimeoutMillis);
            log.info("充值订单落库成功 orderId={}，已发超时延迟消息（{}ms）", order.getId(), payTimeoutMillis);
        } catch (Exception e) {
            log.error("超时延迟消息发送失败 orderId={}", order.getId(), e);
        }
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public Result payAndGrant(Long orderId) {
        RechargeOrder order = getById(orderId);
        if (order == null) {
            return Result.fail("订单不存在");
        }
        if (RechargeOrder.STATUS_PAID.equals(order.getStatus())) {
            return Result.ok("订单已支付");
        }

        // CAS：仅当仍是待支付才改成已支付。false = 对方先改了（重复回调 / 已被超时关单）→ 幂等跳过。
        LocalDateTime now = LocalDateTime.now();
        boolean paid = lambdaUpdate()
                .set(RechargeOrder::getStatus, RechargeOrder.STATUS_PAID)
                .set(RechargeOrder::getPayTime, now)
                .eq(RechargeOrder::getId, orderId)
                .eq(RechargeOrder::getStatus, RechargeOrder.STATUS_UNPAID)
                .update();
        if (!paid) {
            return Result.fail("订单状态不正确");
        }

        // 授予会员（同事务：改成已支付与发会员要么都成、要么都不成）
        MembershipPlan plan = planMapper.selectById(order.getPlanId());
        membershipService.grant(order.getUserId(), plan.getDurationDays());
        log.info("订单支付成功并授予会员 orderId={}", orderId);
        return Result.ok("支付成功");
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void closeTimeoutOrder(Long orderId) {
        RechargeOrder order = getById(orderId);
        if (order == null) {
            log.warn("订单不存在 orderId={}", orderId);
            return;
        }
        // CAS：仅当仍是待支付才改成已取消。false = 已支付回调先成功 → 跳过，不误关。
        boolean cancelled = lambdaUpdate()
                .set(RechargeOrder::getStatus, RechargeOrder.STATUS_CANCELLED)
                .set(RechargeOrder::getCloseTime, LocalDateTime.now())
                .eq(RechargeOrder::getId, orderId)
                .eq(RechargeOrder::getStatus, RechargeOrder.STATUS_UNPAID)
                .update();
        if (!cancelled) {
            log.info("订单非待支付，无需关闭 orderId={} status={}", orderId, order.getStatus());
            return;
        }
        log.info("订单超时已关闭 orderId={}", orderId);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public Result reconcileRefund(Long orderId) {
        // 付了但单已被超时关单 → CAS(已取消→退款中) + 发退款 MQ（异步 + 失败重试）。
        boolean toRefunding = lambdaUpdate()
                .set(RechargeOrder::getStatus, RechargeOrder.STATUS_REFUNDING)
                .eq(RechargeOrder::getId, orderId)
                .eq(RechargeOrder::getStatus, RechargeOrder.STATUS_CANCELLED)
                .update();
        if (!toRefunding) {
            return Result.ok("退款处理中");   // 已被并发转入退款中/已退款 → 幂等跳过
        }
        try {
            rocketMQTemplate.syncSend(RocketMQConstants.REFUND_DESTINATION, String.valueOf(orderId));
        } catch (Exception e) {
            log.error("退款消息发送失败 orderId={}", orderId, e);
        }
        log.info("订单已关闭但付款成功，转入退款 orderId={}", orderId);
        return Result.ok("订单已关闭，将自动退款");
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void doRefund(Long orderId) {
        RechargeOrder order = getById(orderId);
        if (order == null) {
            log.warn("退款：订单不存在 orderId={}", orderId);
            return;
        }
        if (RechargeOrder.STATUS_REFUNDED.equals(order.getStatus())) {
            log.info("退款：订单已退款，幂等跳过 orderId={}", orderId);
            return;
        }
        // 以"退款中"为条件重驱（不从已取消 CAS）→ 崩溃后重试能重新进来，补上 CAS 挡不住的那段。
        if (!RechargeOrder.STATUS_REFUNDING.equals(order.getStatus())) {
            log.warn("退款：订单不在退款中，跳过 orderId={} status={}", orderId, order.getStatus());
            return;
        }
        // 调外部网关退款（幂等键=orderId）——崩溃重试重驱到这里，网关认得退过没，绝不重复退钱。
        gateway.refund(String.valueOf(orderId), order.getAmount());
        // 网关成功 → CAS(退款中→已退款)
        lambdaUpdate()
                .set(RechargeOrder::getStatus, RechargeOrder.STATUS_REFUNDED)
                .set(RechargeOrder::getRefundTime, LocalDateTime.now())
                .eq(RechargeOrder::getId, orderId)
                .eq(RechargeOrder::getStatus, RechargeOrder.STATUS_REFUNDING)
                .update();
        log.info("退款完成 orderId={}", orderId);
    }
}
