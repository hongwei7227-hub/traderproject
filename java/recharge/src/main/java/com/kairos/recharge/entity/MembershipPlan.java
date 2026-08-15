package com.kairos.recharge.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 会员套餐（时长制）—— 移植 CityAIHub {@code Voucher} 简化版：代金券 → 会员套餐。
 */
@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)
@TableName("membership_plan")
public class MembershipPlan implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.INPUT)
    private Long id;

    /** 月卡/季卡/年卡 */
    private String name;

    private BigDecimal price;

    /** 会员时长（天） */
    private Integer durationDays;

    /** 1 上架 / 0 下架 */
    private Integer status;

    private LocalDateTime createTime;
}
