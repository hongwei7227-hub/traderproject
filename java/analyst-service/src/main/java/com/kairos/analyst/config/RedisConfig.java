package com.kairos.analyst.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.data.redis.connection.RedisPassword;
import org.springframework.data.redis.connection.RedisStandaloneConfiguration;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.serializer.StringRedisSerializer;
import org.springframework.util.StringUtils;

/**
 * 独立的 Lettuce 连接工厂 + @Primary StringRedisTemplate。
 *
 * <p>照搬 CityAIHub 的 RedisConfig(必须):redisson-spring-boot-starter 会自动装配一个
 * RedissonConnectionFactory,若不隔离,StringRedisTemplate 可能绑到 Redisson 的编解码器上,
 * 序列化行为和预期的 StringRedisSerializer 不一致。这里给 StringRedisTemplate 独立的 Lettuce
 * 工厂 + @Primary,和 Redisson(锁/布隆)各走各的连接,互不串味。
 */
@Configuration
public class RedisConfig {

    @Value("${spring.data.redis.host:localhost}")
    private String host;

    @Value("${spring.data.redis.port:6379}")
    private int port;

    @Value("${spring.data.redis.password:}")
    private String password;

    @Value("${spring.data.redis.database:0}")
    private int database;

    @Bean
    public LettuceConnectionFactory analystRedisConnectionFactory() {
        RedisStandaloneConfiguration config = new RedisStandaloneConfiguration(host, port);
        config.setDatabase(database);
        if (StringUtils.hasText(password)) {
            config.setPassword(RedisPassword.of(password));
        }
        return new LettuceConnectionFactory(config);
    }

    @Bean
    @Primary
    public StringRedisTemplate stringRedisTemplate() {
        StringRedisTemplate template = new StringRedisTemplate();
        template.setConnectionFactory(analystRedisConnectionFactory());
        StringRedisSerializer str = new StringRedisSerializer();
        template.setKeySerializer(str);
        template.setValueSerializer(str);
        template.setHashKeySerializer(str);
        template.setHashValueSerializer(str);
        template.setEnableTransactionSupport(false);
        template.setExposeConnection(false);
        return template;
    }
}
