package com.kairos.login.controller;

import com.kairos.login.common.Result;
import com.kairos.login.dto.UserDTO;
import com.kairos.login.interceptor.UserHolder;
import com.kairos.login.service.AuthService;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 鉴权接口。
 *  POST /register  注册（布隆判重 + 写入布隆）
 *  POST /login     登录（布隆防穿透 → 校验 → 发有状态 token）
 *  GET  /me        当前用户（TokenInterceptor 已从 Redis 加载 + 刷新 TTL）
 */
@RestController
public class AuthController {

    private final AuthService authService;

    public AuthController(AuthService authService) {
        this.authService = authService;
    }

    @PostMapping("/register")
    public Result register(@RequestBody Map<String, String> body) {
        return authService.register(body.get("username"), body.get("password"));
    }

    @PostMapping("/login")
    public Result login(@RequestBody Map<String, String> body) {
        return authService.login(body.get("username"), body.get("password"));
    }

    @GetMapping("/me")
    public Result me() {
        UserDTO user = UserHolder.getUser();
        if (user == null) {
            return Result.fail("未登录或 token 已过期");
        }
        return Result.ok(user);
    }
}
