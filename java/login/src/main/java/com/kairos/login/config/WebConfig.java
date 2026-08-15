package com.kairos.login.config;

import com.kairos.login.interceptor.TokenInterceptor;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/** 注册 TokenInterceptor 到所有请求。 */
@Configuration
public class WebConfig implements WebMvcConfigurer {

    private final StringRedisTemplate stringRedisTemplate;

    public WebConfig(StringRedisTemplate stringRedisTemplate) {
        this.stringRedisTemplate = stringRedisTemplate;
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(new TokenInterceptor(stringRedisTemplate))
                .addPathPatterns("/**");
    }
}
