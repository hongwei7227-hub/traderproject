package com.kairos.recharge.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 用户会员状态 —— 支付成功后延长 {@code expireTime}。
 */
@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)
@TableName("user_membership")
public class UserMembership implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户 id 即主键（外部传入，不自增） */
    @TableId(value = "user_id", type = IdType.INPUT)
    private Long userId;

    /** 会员等级（demo 固定 1=VIP） */
    private Integer level;

    /** 会员到期时间 */
    private LocalDateTime expireTime;

    private LocalDateTime updateTime;
}
