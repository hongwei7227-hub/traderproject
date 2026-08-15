package com.kairos.login.constant;

/** Redis key 前缀与 TTL（对齐 CityAIHub 约定）。 */
public class RedisConstants {
    /** 登录 token Hash 的 key 前缀：login:token:{token} → 用户字段 */
    public static final String LOGIN_USER_KEY = "login:token:";
    /** token 有效期（分钟）；每次请求滑动刷新 */
    public static final Long LOGIN_USER_TTL = 30L;

    /** 用户名布隆过滤器的 key */
    public static final String USER_BLOOM_FILTER = "bloom:users";
}
