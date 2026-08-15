package com.kairos.recharge.controller;

import com.kairos.recharge.common.Result;
import com.kairos.recharge.entity.MembershipPlan;
import com.kairos.recharge.entity.RechargeOrder;
import com.kairos.recharge.mapper.MembershipPlanMapper;
import com.kairos.recharge.service.IPaymentService;
import com.kairos.recharge.service.IRechargeOrderService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 会员充值控制器 —— demo：用户身份走 {@code X-User-Id} 请求头（未接真实登录鉴权）。
 */
@Slf4j
@RestController
@RequestMapping("/recharge")
@RequiredArgsConstructor
public class RechargeController {

    private final IRechargeOrderService rechargeOrderService;
    private final IPaymentService paymentService;
    private final MembershipPlanMapper planMapper;

    /** 列出会员套餐 */
    @GetMapping("/plans")
    public Result plans() {
        List<MembershipPlan> plans = planMapper.selectList(null);
        return Result.ok(plans);
    }

    /** 下单：发 recharge-submit 顺序消息（顺序键=userId），返回 orderId 供查询。 */
    @PostMapping("/order")
    public Result order(@RequestHeader(value = "X-User-Id", defaultValue = "1") Long userId,
                        @RequestParam("planId") Long planId,
                        @RequestParam("requestId") String requestId) {
        return rechargeOrderService.submitOrder(userId, planId, requestId);
    }

    /** 模拟支付回调（链路①）—— 实际项目由支付网关异步回调。 */
    @PostMapping("/pay/mock-callback")
    public Result mockCallback(@RequestParam("orderId") Long orderId) {
        return paymentService.simulatePaymentCallback(orderId);
    }

    /** 查订单状态（1待支付/2已支付/3已取消） */
    @GetMapping("/order/{id}")
    public Result orderStatus(@PathVariable("id") Long orderId) {
        RechargeOrder order = rechargeOrderService.getById(orderId);
        if (order == null) {
            return Result.fail("订单不存在");
        }
        return Result.ok(order);
    }
}
