package com.kairos.analyst.component;

import com.kairos.analyst.model.AnalystRating;
import com.kairos.analyst.store.AnalystStore;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * demo 种子数据:表为空时灌几只常见股,让服务开箱即跑。
 *
 * <p>@Order(1):在 {@link BloomFilterInitializer}(Order 2)之前跑,保证布隆预热能读到数据。
 * ApplicationRunner 在 context 刷新后执行,此时 Flyway 建表已完成。生产环境表非空 → 直接跳过。
 */
@Slf4j
@Component
@Order(1)
public class AnalystSeeder implements ApplicationRunner {

    private final AnalystStore store;

    public AnalystSeeder(AnalystStore store) {
        this.store = store;
    }

    @Override
    public void run(ApplicationArguments args) {
        if (store.count() > 0) {
            return; // 已有数据(生产/重启),不覆盖
        }
        store.save(new AnalystRating("AAPL", "Apple Inc.", "Buy", 245.0, 300.0, 200.0, 41,
                List.of(new AnalystRating.Grade("Morgan Stanley", "Equal-Weight", "Overweight", "upgrade", "2026-07-15")), 0L));
        store.save(new AnalystRating("NVDA", "NVIDIA Corp.", "Strong Buy", 210.0, 260.0, 170.0, 55,
                List.of(new AnalystRating.Grade("Goldman Sachs", "Buy", "Buy", "maintain", "2026-07-20")), 0L));
        store.save(new AnalystRating("TSLA", "Tesla Inc.", "Hold", 300.0, 400.0, 180.0, 38,
                List.of(new AnalystRating.Grade("UBS", "Buy", "Neutral", "downgrade", "2026-07-10")), 0L));
        store.save(new AnalystRating("SOFI", "SoFi Technologies", "Hold", 18.0, 25.0, 12.0, 14,
                List.of(new AnalystRating.Grade("Mizuho", "Neutral", "Buy", "upgrade", "2026-07-08")), 0L));
        log.info("analyst seed inserted | count={}", store.count());
    }
}
