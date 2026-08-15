package com.kairos.login;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Kairos 下端后端 · 登录鉴权服务。
 *
 * trader 生产鉴权走 Supabase(无状态 JWT)；本服务是自建鉴权链路，把 CityAIHub 的后端思想
 * 搬进 trader 生态(CityAIHub 从简历下线，思想留在 trader 后端)：
 *  1. 有状态 token 存储：随机 UUID → Redis Hash 存用户 + 滑动 TTL（服务端可即时踢下线）
 *  2. 布隆过滤器防穿透：登录先查布隆，挡掉大量"不存在账号"撞库
 *
 * 与 execution-worker(Java 下单执行) 并列，构成 trader 下端后端的 Java 微服务层。
 */
@SpringBootApplication
public class LoginApplication {
    public static void main(String[] args) {
        SpringApplication.run(LoginApplication.class, args);
    }
}
