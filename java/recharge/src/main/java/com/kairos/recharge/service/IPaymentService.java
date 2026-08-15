package com.kairos.recharge.service;

import com.kairos.recharge.common.Result;
import com.kairos.recharge.entity.RechargeOrder;

/**
 * 支付服务接口
 */
public interface IPaymentService {

    /**
     * 创建支付订单
     * @param orderId 订单ID
     * @param payType 支付方式：1-余额支付，2-支付宝，3-微信
     * @return 支付结果
     */
    Result createPayment(Long orderId, Integer payType);

    /**
     * 模拟支付成功回调（实际项目中应由支付平台回调）
     * @param orderId 订单ID
     * @return 支付结果
     */
    Result simulatePaymentCallback(Long orderId);

    /**
     * 微信支付回调
     * @param orderId 订单ID
     * @return 处理结果
     */
    Result wechatPayCallback(Long orderId);

    /**
     * 支付宝支付回调
     * @param orderId 订单ID
     * @return 处理结果
     */
    Result alipayCallback(Long orderId);

    /**
     * 检查订单支付状态
     * @param orderId 订单ID
     * @return 订单状态
     */
    Result checkPaymentStatus(Long orderId);
}
