package com.kairos.analyst.component;

import com.kairos.analyst.store.AnalystStore;
import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RBloomFilter;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

/**
 * 布隆预热 —— 对标 CityAIHub {@code BloomFilterInitializer}:启动把真数据源里所有 symbol 灌进布隆
 * (= shopMapper.selectAllIds 分页灌所有 shopId)。新增 symbol 时在 updateAnalyst 里 add 自愈。
 *
 * <p>幂等:RBloomFilter.tryInit 已在 BloomFilterConfig 完成;这里只 add,重复启动不清空已有元素。
 */
@Slf4j
@Component
@Order(2)  // 在 AnalystSeeder(Order 1)之后:预热时表里已有数据
public class BloomFilterInitializer implements ApplicationRunner {

    private final RBloomFilter<String> stockBloomFilter;
    private final AnalystStore store;

    public BloomFilterInitializer(RBloomFilter<String> stockBloomFilter, AnalystStore store) {
        this.stockBloomFilter = stockBloomFilter;
        this.store = store;
    }

    @Override
    public void run(ApplicationArguments args) {
        long start = System.currentTimeMillis();
        int n = 0;
        for (String symbol : store.allSymbols()) {
            stockBloomFilter.add(symbol);
            n++;
        }
        log.info("stock bloom preheated | symbols={} cost={}ms", n, System.currentTimeMillis() - start);
    }
}
