package com.kairos.login.interceptor;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.util.StrUtil;
import com.kairos.login.constant.RedisConstants;
import com.kairos.login.dto.UserDTO;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.web.servlet.HandlerInterceptor;

import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * Token 拦截器（对齐 CityAIHub RefreshTokenInterceptor）：
 * 每个请求拿 authorization 头的 token → 从 Redis Hash 取用户 → 存 ThreadLocal → 刷新 TTL（滑动过期）。
 *
 * 这就是"有状态 token"的核心：token 本身只是随机串，真用户数据在 Redis；
 * 每次访问续 TTL，活跃用户不掉线，30 分钟不动自动登出。
 */
public class TokenInterceptor implements HandlerInterceptor {

    private final StringRedisTemplate stringRedisTemplate;

    public TokenInterceptor(StringRedisTemplate stringRedisTemplate) {
        this.stringRedisTemplate = stringRedisTemplate;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        String token = request.getHeader("authorization");
        if (StrUtil.isBlank(token)) {
            return true;   // 没带 token 也放行；是否要求登录由具体接口判 UserHolder
        }
        String key = RedisConstants.LOGIN_USER_KEY + token;
        Map<Object, Object> userMap = stringRedisTemplate.opsForHash().entries(key);
        if (userMap.isEmpty()) {
            return true;   // token 无效/已过期 → 当未登录
        }
        UserDTO userDTO = BeanUtil.fillBeanWithMap(userMap, new UserDTO(), false);
        UserHolder.saveUser(userDTO);
        // 滑动刷新有效期
        stringRedisTemplate.expire(key, RedisConstants.LOGIN_USER_TTL, TimeUnit.MINUTES);
        return true;
    }

    @Override
    public void afterCompletion(HttpServletRequest request, HttpServletResponse response, Object handler, Exception ex) {
        UserHolder.removeUser();   // 请求结束清 ThreadLocal
    }
}
