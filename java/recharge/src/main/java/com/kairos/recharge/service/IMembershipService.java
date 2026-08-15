package com.kairos.recharge.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.kairos.recharge.entity.UserMembership;

public interface IMembershipService extends IService<UserMembership> {

    /**
     * 授予/延长会员：未过期则在原到期时间上叠加，已过期（或无记录）则从现在起算。
     * 由支付成功回调调用，须与订单 CAS 在同一事务。
     */
    void grant(Long userId, int durationDays);
}
