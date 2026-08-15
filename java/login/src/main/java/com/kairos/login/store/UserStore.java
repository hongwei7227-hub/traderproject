package com.kairos.login.store;

import com.kairos.login.entity.User;
import org.springframework.stereotype.Component;

import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 内存用户存储 —— 模拟"用户 DB 表"。
 *
 * 用内存 Map 代替 MySQL，是因为本服务重点演示 token + 布隆，不想拉起真库。
 * 布隆过滤器挡在它前面：登录时"肯定不存在"的账号根本不会走到 findByUsername（= 防穿透）。
 */
@Component
public class UserStore {
    private final ConcurrentHashMap<String, User> byUsername = new ConcurrentHashMap<>();
    private final AtomicLong idGen = new AtomicLong(1000);

    public User findByUsername(String username) {
        return byUsername.get(username);
    }

    public User save(String username, String bcryptPassword) {
        User user = new User(idGen.incrementAndGet(), username, bcryptPassword);
        byUsername.put(username, user);
        return user;
    }

    public boolean exists(String username) {
        return byUsername.containsKey(username);
    }
}
