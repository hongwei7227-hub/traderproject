package com.kairos.login.service;

import com.kairos.login.common.Result;

public interface AuthService {
    /** 注册：布隆判重 + 回退精确查；建用户后写入布隆。 */
    Result register(String username, String password);

    /** 登录：布隆防穿透 → 校验密码 → 发有状态 token。 */
    Result login(String username, String password);
}
