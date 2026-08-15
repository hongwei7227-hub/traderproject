package com.kairos.execution.repository;

import com.kairos.execution.domain.OrderStatus;
import com.kairos.execution.model.TradeProposal;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.List;

/**
 * 订单落库仓储（§8）—— 把订单状态从进程内存搬进 Postgres 的 {@code execution.orders} 表。
 *
 * <p>核心是 {@link #casUpdateStatus}：乐观锁 CAS，照抄 CityAIHub {@code closeTimeoutOrder}
 * 的 {@code UPDATE ... WHERE status=:expectedOld}——用状态字段本身当条件，<b>不加 version 列</b>。
 * 影响行数=0 说明状态已被别人改过（成交/撤单撞车），调用方据此重读重判。
 *
 * <p>用 {@link JdbcTemplate} 直接写 SQL，不引 MyBatis/JPA（对齐主平台 raw SQL 无 ORM 哲学）。
 * SQL 显式带 {@code execution.} schema 前缀，避免依赖运行时 search_path。
 */
@Repository
public class OrderRepository {

    private final JdbcTemplate jdbc;

    public OrderRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** 下单成功后登记一张单（初始状态通常 SUBMITTED）。 */
    public void insert(TradeProposal p, String brokerOrderId, OrderStatus initialStatus) {
        jdbc.update(
                "INSERT INTO execution.orders " +
                        "(broker_order_id, tenant_id, proposal_id, symbol, action, quantity, limit_price, status, rationale) " +
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                brokerOrderId,
                p.getTenantId(),
                p.getProposalId(),
                p.getSymbol(),
                p.getAction(),
                p.getQuantity(),
                p.getLimitPrice(),
                initialStatus.name(),
                p.getRationale());
    }

    /**
     * 乐观锁 CAS 状态流转：仅当当前状态仍是 {@code expectedOld} 才改成 {@code newStatus}。
     *
     * @return true=改成功（这次流转赢了）；false=影响行数 0，状态已被别人改过，需重读重判
     */
    public boolean casUpdateStatus(String brokerOrderId, OrderStatus expectedOld, OrderStatus newStatus) {
        int rows = jdbc.update(
                "UPDATE execution.orders SET status = ?, updated_at = CURRENT_TIMESTAMP " +
                        "WHERE broker_order_id = ? AND status = ?",
                newStatus.name(), brokerOrderId, expectedOld.name());
        return rows > 0;
    }

    /** 读当前状态；不存在返回 null。 */
    public OrderStatus findStatus(String brokerOrderId) {
        List<String> rows = jdbc.query(
                "SELECT status FROM execution.orders WHERE broker_order_id = ?",
                (rs, i) -> rs.getString("status"),
                brokerOrderId);
        return rows.isEmpty() ? null : OrderStatus.valueOf(rows.get(0));
    }

    /** 订单是否已登记（幂等/兜底判断用）。 */
    public boolean exists(String brokerOrderId) {
        Integer n = jdbc.queryForObject(
                "SELECT COUNT(1) FROM execution.orders WHERE broker_order_id = ?",
                Integer.class, brokerOrderId);
        return n != null && n > 0;
    }
}
