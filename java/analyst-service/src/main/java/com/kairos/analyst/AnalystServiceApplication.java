package com.kairos.analyst;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Kairos 分析师观点缓存服务。
 *
 * <p>读多写少的共享参考数据(每股一条分析师评级/目标价),对标 CityAIHub 的 shop 缓存三兄弟:
 * 布隆防穿透 → Caffeine L1 → Redis L2 → Redisson 同步互斥锁回源 + 空值缓存,写后 Cache-Aside
 * 删两层;并用(可关的)RocketMQ 广播补上 CityAIHub 缺失的跨节点 L1 失效。
 *
 * <p>集成:kairos(Python)HTTP 代理调本服务;与主平台共用同一 Redis / RocketMQ broker。
 */
@SpringBootApplication
public class AnalystServiceApplication {
    public static void main(String[] args) {
        SpringApplication.run(AnalystServiceApplication.class, args);
    }
}
