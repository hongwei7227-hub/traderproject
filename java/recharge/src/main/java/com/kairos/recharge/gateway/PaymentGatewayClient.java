package com.kairos.recharge.gateway;

import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 模拟外部支付网关（demo，非真实网关）。
 *
 * <p><b>幂等键（退款请求号）是崩溃/重试安全的关键</b>：调网关退款调一半进程崩了 → 状态卡"退款中"，
 * 重试再来会再调一次网关。用 {@code refundNo} 做幂等：网关认得"这个退款号退过没"，重复调不重复退钱。
 * 这是 CAS 和分布式锁都做不到的（锁扛不住崩溃，CAS 挡不住"退款中重驱"）。真实网关自己维护这张幂等表；
 * 这里用内存 Set 模拟。
 */
@Slf4j
@Component
public class PaymentGatewayClient {

    private final Set<String> refunded = ConcurrentHashMap.newKeySet();

    /** 退款到用户账户。以 refundNo 幂等：同号只真退一次。 */
    public void refund(String refundNo, BigDecimal amount) {
        if (!refunded.add(refundNo)) {
            log.info("网关幂等：退款号 {} 已退过，跳过（不重复退钱）", refundNo);
            return;
        }
        // 代表真正的外部网络调用（把钱退回用户支付账户）
        log.info("调用支付网关退款成功 refundNo={} amount={}", refundNo, amount);
    }
}
