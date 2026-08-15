package com.kairos.recharge.service.impl;

import com.kairos.recharge.common.Result;
import com.kairos.recharge.entity.RechargeOrder;
import com.kairos.recharge.service.IPaymentService;
import com.kairos.recharge.service.IRechargeOrderService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Service;

import java.util.concurrent.TimeUnit;

import static com.kairos.recharge.constant.RedisConstants.ORDER_STATE_LOCK_PREFIX;

@Slf4j
@Service
@RequiredArgsConstructor
public class PaymentServiceImpl implements IPaymentService {

    private final IRechargeOrderService rechargeOrderService;
    private final RedissonClient redissonClient;

    @Override
    public Result createPayment(Long orderId, Integer payType) {
        RechargeOrder order = rechargeOrderService.getById(orderId);
        if (order == null) {
            return Result.fail("订单不存在");
        }

        if (!RechargeOrder.STATUS_UNPAID.equals(order.getStatus())) {
            return Result.fail("订单状态不正确");
        }

        order.setPayType(payType);
        rechargeOrderService.updateById(order);

        log.info("创建支付订单：订单ID={}, 支付方式={}", orderId, payType);
        return Result.ok("支付订单创建成功");
    }

    @Override
    public Result simulatePaymentCallback(Long orderId) {
        return handlePaymentCallback(orderId);
    }

    @Override
    public Result wechatPayCallback(Long orderId) {
        log.info("收到微信支付回调：订单ID={}", orderId);
        return handlePaymentCallback(orderId);
    }

    @Override
    public Result alipayCallback(Long orderId) {
        log.info("收到支付宝支付回调：订单ID={}", orderId);
        return handlePaymentCallback(orderId);
    }

    private Result handlePaymentCallback(Long orderId) {
        // 与"超时关单"链路共用同一把锁 ORDER_STATE_LOCK_PREFIX + orderId，两条链路对同一订单互斥串行。
        RLock lock = redissonClient.getLock(ORDER_STATE_LOCK_PREFIX + orderId);

        boolean isLock;
        try {
            isLock = lock.tryLock(0, 5, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            log.warn("获取订单状态锁被中断，订单ID={}", orderId);
            return Result.fail("订单处理中，请稍后重试");
        }
        if (!isLock) {
            log.warn("获取订单状态锁失败，订单ID={}", orderId);
            return Result.fail("订单处理中，请稍后重试");
        }

        try {
            RechargeOrder order = rechargeOrderService.getById(orderId);
            if (order == null) {
                return Result.fail("订单不存在");
            }
            // 边界：付款成功却发现单已被超时关单 → 转对账退款（把这笔本不该收的钱退回去）。
            if (RechargeOrder.STATUS_CANCELLED.equals(order.getStatus())) {
                return rechargeOrderService.reconcileRefund(orderId);
            }
            // 正常：锁内 @Transactional 的 CAS(待支付→已支付) + 授予会员，事务提交后才释放锁。
            // false = 对方先改（重复回调）→ DB 层兜底幂等。
            return rechargeOrderService.payAndGrant(orderId);
        } catch (Exception e) {
            log.error("支付回调处理异常，订单ID={}", orderId, e);
            return Result.fail("支付回调处理失败");
        } finally {
            if (lock.isHeldByCurrentThread()) {
                lock.unlock();
            }
        }
    }

    @Override
    public Result checkPaymentStatus(Long orderId) {
        RechargeOrder order = rechargeOrderService.getById(orderId);
        if (order == null) {
            return Result.fail("订单不存在");
        }
        return Result.ok(order.getStatus());
    }
}