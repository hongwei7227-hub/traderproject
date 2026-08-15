package com.kairos.analyst.store;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.kairos.analyst.model.AnalystRating;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * 分析师观点「数据真身」—— Postgres（analyst schema，analyst_ratings 表）。
 *
 * <p>这就是 Cache-Aside 里"先更新数据库"的那个数据库层(缓存 Caffeine+Redis 是它前面的快副本)。
 * payload 存 {@link AnalystRating} 的 JSON 文本。生产替换点:{@code save} 由"每日同步 FMP"的任务调,
 * {@code load} 是 CacheClient 回源锁里的 loader —— 只有 L1/L2 都 miss 时才落到这里。
 */
@Slf4j
@Component
public class AnalystStore {

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;

    public AnalystStore(JdbcTemplate jdbc, ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
    }

    /** 回源:按 symbol 读真身;不存在返 null(→ CacheClient 写空值缓存)。 */
    public AnalystRating load(String symbol) {
        String sym = norm(symbol);
        try {
            String json = jdbc.queryForObject(
                    "SELECT payload FROM analyst.analyst_ratings WHERE symbol = ?", String.class, sym);
            return json == null ? null : objectMapper.readValue(json, AnalystRating.class);
        } catch (EmptyResultDataAccessException e) {
            return null;
        } catch (Exception e) {
            log.warn("analyst load failed | symbol={}", sym, e);
            return null;
        }
    }

    /** 写真身(UPSERT)。demo 的更新端点 / 生产的同步任务用。 */
    public void save(AnalystRating rating) {
        String sym = norm(rating.getSymbol());
        rating.setSymbol(sym);
        try {
            String json = objectMapper.writeValueAsString(rating);
            jdbc.update(
                    "INSERT INTO analyst.analyst_ratings (symbol, payload, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) " +
                    "ON CONFLICT (symbol) DO UPDATE SET payload = EXCLUDED.payload, updated_at = CURRENT_TIMESTAMP",
                    sym, json);
        } catch (Exception e) {
            throw new IllegalStateException("analyst save failed: " + sym, e);
        }
    }

    /** 全部 symbol —— 布隆预热用(= CityAIHub shopMapper.selectAllIds)。 */
    public List<String> allSymbols() {
        return jdbc.queryForList("SELECT symbol FROM analyst.analyst_ratings", String.class);
    }

    public long count() {
        Long n = jdbc.queryForObject("SELECT COUNT(*) FROM analyst.analyst_ratings", Long.class);
        return n == null ? 0 : n;
    }

    private String norm(String s) {
        return s == null ? "" : s.trim().toUpperCase();
    }
}
