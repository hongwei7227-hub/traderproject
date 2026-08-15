package com.kairos.login.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.bean.copier.CopyOptions;
import cn.hutool.core.util.IdUtil;
import cn.hutool.core.util.StrUtil;
import cn.hutool.crypto.digest.BCrypt;
import com.kairos.login.common.Result;
import com.kairos.login.constant.RedisConstants;
import com.kairos.login.dto.UserDTO;
import com.kairos.login.entity.User;
import com.kairos.login.service.AuthService;
import com.kairos.login.store.UserStore;
import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RBloomFilter;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;

@Slf4j
@Service
public class AuthServiceImpl implements AuthService {

    private final StringRedisTemplate stringRedisTemplate;
    private final RBloomFilter<String> userBloomFilter;
    private final UserStore userStore;

    public AuthServiceImpl(StringRedisTemplate stringRedisTemplate,
                           RBloomFilter<String> userBloomFilter,
                           UserStore userStore) {
        this.stringRedisTemplate = stringRedisTemplate;
        this.userBloomFilter = userBloomFilter;
        this.userStore = userStore;
    }

    @Override
    public Result register(String username, String password) {
        if (StrUtil.isBlank(username) || StrUtil.isBlank(password)) {
            return Result.fail("用户名/密码不能为空");
        }
        // 布隆"可能存在"→ 回退精确查确认;布隆假阳性(实际不存在)则继续注册。
        // 布隆"肯定不存在"→ 无需查 store,直接建(利用布隆无假阴性)。
        if (userBloomFilter.contains(username) && userStore.exists(username)) {
            return Result.fail("用户名已存在");
        }
        String hash = BCrypt.hashpw(password, BCrypt.gensalt());   // 不存明文
        User user = userStore.save(username, hash);
        userBloomFilter.add(username);                             // 新用户加入布隆
        log.info("register ok: {} (id={})", username, user.getId());
        return Result.ok();
    }

    @Override
    public Result login(String username, String password) {
        // 1) 布隆防穿透:肯定不存在的账号直接拒,不查 store(挡撞库)。
        //    返回与"密码错"同样的笼统提示 → 不泄露"用户是否存在"(防用户枚举)。
        if (!userBloomFilter.contains(username)) {
            log.info("bloom blocked non-existent user: {}", username);
            return Result.fail("用户名或密码错误");
        }
        // 2) 精确查 + 校验密码(布隆假阳性会走到这,BCrypt 校验兜住)
        User user = userStore.findByUsername(username);
        if (user == null || !BCrypt.checkpw(password, user.getPassword())) {
            return Result.fail("用户名或密码错误");
        }
        // 3) 有状态 token:随机 UUID → Redis Hash 存 UserDTO + TTL
        String token = IdUtil.simpleUUID();
        UserDTO userDTO = new UserDTO(user.getId(), user.getUsername());
        Map<String, Object> userMap = BeanUtil.beanToMap(userDTO, new HashMap<>(),
                CopyOptions.create()
                        .setIgnoreNullValue(true)
                        .setFieldValueEditor((k, v) -> v == null ? null : v.toString()));
        String key = RedisConstants.LOGIN_USER_KEY + token;
        stringRedisTemplate.opsForHash().putAll(key, userMap);
        stringRedisTemplate.expire(key, RedisConstants.LOGIN_USER_TTL, TimeUnit.MINUTES);
        return Result.ok(token);
    }
}
