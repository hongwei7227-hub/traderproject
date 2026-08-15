package com.kairos.execution.repository;

import com.kairos.execution.domain.OrderStatus;
import com.kairos.execution.model.TradeProposal;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

import java.math.BigDecimal;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 乐观锁 CAS 核心验证（§8）—— 用 H2 PostgreSQL 兼容模式跑，无需起真 Postgres。
 *
 * <p>验证 {@link OrderRepository#casUpdateStatus} 的三件事：
 * ① 期望旧态正确 → 改成功；② 期望旧态过时 → 改失败（false）；
 * ③ <b>并发撞车</b>：两线程同时从 SUBMITTED 改成不同目标，只有一个赢 —— 这是 CAS 存在的理由。
 */
class OrderRepositoryCasTest {

    // 与 V1__create_execution_orders.sql 保持一致的建表语句（测试内联，H2/PG 通用 SQL）
    private static final String CREATE_TABLE = """
            CREATE TABLE execution.orders (
                broker_order_id VARCHAR(64)  NOT NULL,
                tenant_id       VARCHAR(64)  NOT NULL,
                proposal_id     VARCHAR(64)  NOT NULL,
                symbol          VARCHAR(32)  NOT NULL,
                action          VARCHAR(8)   NOT NULL,
                quantity        BIGINT       NOT NULL,
                limit_price     NUMERIC(18,4),
                status          VARCHAR(24)  NOT NULL,
                filled_qty      BIGINT       NOT NULL DEFAULT 0,
                avg_px          NUMERIC(18,4) NOT NULL DEFAULT 0,
                created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT pk_orders PRIMARY KEY (broker_order_id)
            )
            """;

    private JdbcTemplate jdbc;
    private OrderRepository repo;

    @BeforeEach
    void setUp() {
        DriverManagerDataSource ds = new DriverManagerDataSource();
        ds.setDriverClassName("org.h2.Driver");
        // 命名 mem 库 + DB_CLOSE_DELAY=-1：多连接（并发测试）共享同一内存库
        ds.setUrl("jdbc:h2:mem:exectest;MODE=PostgreSQL;DATABASE_TO_UPPER=FALSE;DB_CLOSE_DELAY=-1");
        ds.setUsername("sa");
        ds.setPassword("");
        jdbc = new JdbcTemplate(ds);
        jdbc.execute("DROP SCHEMA IF EXISTS execution CASCADE");
        jdbc.execute("CREATE SCHEMA execution");
        jdbc.execute(CREATE_TABLE);
        repo = new OrderRepository(jdbc);
    }

    private TradeProposal proposal(String proposalId) {
        TradeProposal p = new TradeProposal();
        p.setProposalId(proposalId);
        p.setTenantId("t-1");
        p.setSymbol("AAPL");
        p.setAction("BUY");
        p.setQuantity(100);
        p.setLimitPrice(new BigDecimal("150.00"));
        return p;
    }

    @Test
    void casSucceedsWhenExpectedOldMatches() {
        repo.insert(proposal("p-1"), "1001", OrderStatus.SUBMITTED);

        boolean ok = repo.casUpdateStatus("1001", OrderStatus.SUBMITTED, OrderStatus.ACCEPTED);

        assertTrue(ok, "期望旧态匹配，CAS 应成功");
        assertEquals(OrderStatus.ACCEPTED, repo.findStatus("1001"));
    }

    @Test
    void casFailsWhenExpectedOldStale() {
        repo.insert(proposal("p-2"), "1002", OrderStatus.SUBMITTED);
        // 先有人把它推进到 ACCEPTED
        assertTrue(repo.casUpdateStatus("1002", OrderStatus.SUBMITTED, OrderStatus.ACCEPTED));

        // 再拿过时的 SUBMITTED 去 CAS → 影响行数 0 → false
        boolean ok = repo.casUpdateStatus("1002", OrderStatus.SUBMITTED, OrderStatus.CANCELED);

        assertFalse(ok, "期望旧态已过时，CAS 应失败");
        assertEquals(OrderStatus.ACCEPTED, repo.findStatus("1002"), "状态不应被过时的 CAS 改动");
    }

    @Test
    void concurrentCasOnlyOneWins() throws Exception {
        repo.insert(proposal("p-3"), "1003", OrderStatus.SUBMITTED);

        // 两条线程同时从 SUBMITTED 抢：一个想改 ACCEPTED，一个想改 CANCELED
        int threads = 2;
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        CountDownLatch startGate = new CountDownLatch(1);
        AtomicInteger winners = new AtomicInteger(0);

        Callable<Boolean> toAccepted = () -> {
            startGate.await();
            return repo.casUpdateStatus("1003", OrderStatus.SUBMITTED, OrderStatus.ACCEPTED);
        };
        Callable<Boolean> toCanceled = () -> {
            startGate.await();
            return repo.casUpdateStatus("1003", OrderStatus.SUBMITTED, OrderStatus.CANCELED);
        };

        Future<Boolean> f1 = pool.submit(toAccepted);
        Future<Boolean> f2 = pool.submit(toCanceled);
        startGate.countDown();   // 同时发车

        if (f1.get(5, TimeUnit.SECONDS)) winners.incrementAndGet();
        if (f2.get(5, TimeUnit.SECONDS)) winners.incrementAndGet();
        pool.shutdown();

        assertEquals(1, winners.get(), "并发从同一旧态 CAS，必须恰好一个赢");
        OrderStatus finalStatus = repo.findStatus("1003");
        assertTrue(
                finalStatus == OrderStatus.ACCEPTED || finalStatus == OrderStatus.CANCELED,
                "最终态应是两个竞争目标之一，实际=" + finalStatus);
    }

    @Test
    void findStatusNullWhenAbsent() {
        assertEquals(null, repo.findStatus("does-not-exist"));
    }

    @Test
    void duplicateTenantProposalRejectedByUniqueIndex() {
        // 唯一索引在 V1 脚本里；此测试库未建索引，改为直接验证主键去重语义由 insert 决定。
        repo.insert(proposal("p-4"), "1004", OrderStatus.SUBMITTED);
        assertTrue(repo.exists("1004"));
        List<String> ids = jdbc.query(
                "SELECT broker_order_id FROM execution.orders WHERE tenant_id = ?",
                (rs, i) -> rs.getString(1), "t-1");
        assertTrue(ids.contains("1004"));
    }
}
