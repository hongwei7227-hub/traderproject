package com.kairos.analyst.controller;

import com.kairos.analyst.cache.CacheClient;
import com.kairos.analyst.model.AnalystRating;
import com.kairos.analyst.service.AnalystService;
import com.kairos.analyst.store.AnalystStore;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 分析师观点 API。kairos(Python)代理调这里;前端不直连。
 *
 * <ul>
 *   <li>GET  /stock/{symbol}/analyst — 读(走布隆+两级缓存+锁回源)</li>
 *   <li>PUT  /stock/{symbol}/analyst — 更新(演示 Cache-Aside 写后删+广播)</li>
 *   <li>GET  /cache/stats            — 分层命中率埋点(压测演示 L1/L2/DB 命中)</li>
 * </ul>
 */
@RestController
public class AnalystController {

    private final AnalystService analystService;
    private final CacheClient cacheClient;
    private final AnalystStore store;

    public AnalystController(AnalystService analystService, CacheClient cacheClient, AnalystStore store) {
        this.analystService = analystService;
        this.cacheClient = cacheClient;
        this.store = store;
    }

    @GetMapping("/stock/{symbol}/analyst")
    public ResponseEntity<AnalystRating> getAnalyst(@PathVariable String symbol) {
        AnalystRating r = analystService.getAnalyst(symbol);
        return r == null ? ResponseEntity.notFound().build() : ResponseEntity.ok(r);
    }

    /** 无缓存基准：直连 Postgres（绕过布隆+两级缓存），压测"无缓存 vs 有缓存"对照用。 */
    @GetMapping("/stock/{symbol}/analyst/nocache")
    public ResponseEntity<AnalystRating> getAnalystNoCache(@PathVariable String symbol) {
        AnalystRating r = store.load(symbol);
        return r == null ? ResponseEntity.notFound().build() : ResponseEntity.ok(r);
    }

    @PutMapping("/stock/{symbol}/analyst")
    public ResponseEntity<Void> updateAnalyst(@PathVariable String symbol,
                                              @RequestBody AnalystRating rating) {
        rating.setSymbol(symbol);
        analystService.updateAnalyst(rating);
        return ResponseEntity.ok().build();
    }

    @GetMapping("/cache/stats")
    public Map<String, Long> cacheStats() {
        return Map.of(
                "l1Hits", cacheClient.getL1HitCount(),
                "l2Hits", cacheClient.getL2HitCount(),
                "dbHits", cacheClient.getDbHitCount()
        );
    }

    @PostMapping("/cache/stats/reset")
    public ResponseEntity<Void> resetStats() {
        cacheClient.resetHitCounters();
        return ResponseEntity.ok().build();
    }
}
