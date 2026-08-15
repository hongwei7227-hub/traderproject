package com.kairos.recharge.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.kairos.recharge.entity.UserMembership;
import com.kairos.recharge.mapper.UserMembershipMapper;
import com.kairos.recharge.service.IMembershipService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;

@Slf4j
@Service
public class MembershipServiceImpl extends ServiceImpl<UserMembershipMapper, UserMembership>
        implements IMembershipService {

    private static final Integer LEVEL_VIP = 1;

    @Override
    public void grant(Long userId, int durationDays) {
        LocalDateTime now = LocalDateTime.now();
        UserMembership m = getById(userId);

        // 未过期 → 在原到期时间上叠加续期；已过期或无记录 → 从现在起算。
        LocalDateTime base = (m != null && m.getExpireTime() != null && m.getExpireTime().isAfter(now))
                ? m.getExpireTime() : now;
        LocalDateTime newExpire = base.plusDays(durationDays);

        if (m == null) {
            m = new UserMembership().setUserId(userId).setLevel(LEVEL_VIP);
        }
        m.setLevel(LEVEL_VIP).setExpireTime(newExpire).setUpdateTime(now);
        saveOrUpdate(m);
        log.info("会员已授予/续期 userId={} 到期={}", userId, newExpire);
    }
}
