package com.kairos.recharge.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 充值订单 —— 移植 CityAIHub {@code VoucherOrder} 精简版：只保留支付 demo 需要的 3 态，
 * 砍掉核销/退款；代金券字段换成会员套餐（planId + amount 快照）。
 */
@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)
@TableName("recharge_order")
public class RechargeOrder implements Serializable {

    private static final long serialVersionUID = 1L;

    public static final Integer PAY_TYPE_BALANCE = 1;
    public static final Integer PAY_TYPE_ALIPAY = 2;
    public static final Integer PAY_TYPE_WECHAT = 3;

    /** 订单状态：1 待支付；2 已支付；3 已取消；4 退款中；5 已退款。status 即乐观锁守卫（CAS WHERE status=?）。 */
    public static final Integer STATUS_UNPAID = 1;
    public static final Integer STATUS_PAID = 2;
    public static final Integer STATUS_CANCELLED = 3;
    public static final Integer STATUS_REFUNDING = 4;
    public static final Integer STATUS_REFUNDED = 5;

    @TableId(value = "id", type = IdType.INPUT)
    private Long id;

    private Long userId;

    /** 购买的会员套餐 id */
    private Long planId;

    /** 下单时冻结的 plan.price 快照 */
    private BigDecimal amount;

    /** 支付方式 1余额/2支付宝/3微信（demo 仅模拟） */
    private Integer payType;

    private Integer status;

    private LocalDateTime createTime;
    private LocalDateTime payTime;
    private LocalDateTime closeTime;
    private LocalDateTime refundTime;

    /** 幂等 requestId —— 不落库，仅随下单顺序消息传递，供消费端 SETNX 去重。 */
    @TableField(exist = false)
    private String requestId;
}
