package com.kairos.recharge.controller;

import com.kairos.recharge.common.Result;
import com.kairos.recharge.entity.UserMembership;
import com.kairos.recharge.service.IMembershipService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 会员状态控制器 —— demo：用户身份走 {@code X-User-Id} 请求头。
 */
@RestController
@RequestMapping("/membership")
@RequiredArgsConstructor
public class MembershipController {

    private final IMembershipService membershipService;

    /** 查我的会员状态（level + expireTime）；无记录返回 null 表示非会员。 */
    @GetMapping("/me")
    public Result me(@RequestHeader(value = "X-User-Id", defaultValue = "1") Long userId) {
        UserMembership m = membershipService.getById(userId);
        return Result.ok(m);
    }
}
