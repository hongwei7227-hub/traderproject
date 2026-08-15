package com.kairos.execution.broker;

import com.kairos.execution.model.TradeProposal;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/**
 * IBKR Web API（Client Portal / OAuth）执行实现 —— <b>Phase 3 占位骨架，尚未接线</b>。
 *
 * <p>与 {@link IbkrBrokerAdapter}（TWS socket）实现<b>同一个</b> {@link BrokerAdapter} 接口，
 * 所以切换 = 翻配置 {@code broker.mode=webapi}，<b>业务代码（幂等/预算/状态机 CAS/MQ）一行不动</b>。
 *
 * <p><b>为什么将来换它</b>：Web API 是 REST + 全局订单 id、无 socket 会话 —— socket 那套
 * 会话内 orderId（sessionId 前缀 hack）、连接/clientId 管理、跨实例会话归属（leader）全部消失。
 *
 * <p><b>连接前提</b>：本适配器只对着一个<b>已认证的 CP Web API 网关</b>（IBeam 跑的 Client Portal
 * Gateway，本机/容器内，默认 https 端口）发 REST/WS；认证与保活由 IBeam 负责，本类不碰账密/2FA。
 * 零售当前只能走网关；OAuth 直连（无网关）仅机构 —— 拿到机构权限后把 baseUrl/认证换成 OAuth 端点即可，
 * 方法体不变。
 *
 * <p><b>接线清单（拿到权限后逐个填）</b>：
 * <ul>
 *   <li>{@link #placeOrder}：POST {@code /iserver/account/{accountId}/orders} → 返回全局 order id；
 *       CP Web API 常返回需二次确认的 reply → POST {@code /iserver/reply/{replyId}}{"confirmed":true}。</li>
 *   <li>{@link #cancelOrder}：DELETE {@code /iserver/account/{accountId}/order/{orderId}}。</li>
 *   <li>{@link #onOrderStatus}：订 WS 主题 {@code sor}（order updates）→ 收到推送转成
 *       {@link OrderStatusCallback#accept} 喂给 domain；WS 断线用 REST
 *       {@code GET /iserver/account/orders} 兜底轮询。</li>
 *   <li>多租户路由：{@code tenant_id → accountId}（同一网关多子账户）或
 *       {@code tenant_id → 网关 baseUrl}（一账户一 IBeam 容器），由外层路由表决定，本类只认 accountId。</li>
 *   <li>（接口待补）{@code getOpenOrders()}：重连后 {@code GET /iserver/account/orders} 拉回挂单对账。</li>
 * </ul>
 */
@Component
@ConditionalOnProperty(name = "broker.mode", havingValue = "webapi")
public class IbkrWebApiAdapter implements BrokerAdapter {

    private static final Logger log = LoggerFactory.getLogger(IbkrWebApiAdapter.class);

    /** 已认证的 CP Web API 网关基址（IBeam 暴露）。OAuth 直连时换成 OAuth 端点。 */
    @Value("${ibkr.webapi.base-url:https://localhost:5000/v1/api}")
    private String baseUrl;

    /** 目标账户（单账户占位；多租户由外层按 tenant_id → accountId 路由后传入）。 */
    @Value("${ibkr.webapi.account-id:}")
    private String accountId;

    /** 订单回报回调 —— 由 OrderConsumer 在启动时注册，与 socket 实现同构。 */
    private volatile OrderStatusCallback statusCallback;

    // TODO(Phase 3): 注入一个 HTTP 客户端（java.net.http.HttpClient）+ WS 客户端；
    //                启动时对 baseUrl 做一次 /tickle 保活探测，起 WS 订阅 sor 主题的后台循环。

    @PostConstruct
    void init() {
        log.warn("IbkrWebApiAdapter 已装配（broker.mode=webapi），但为 Phase 3 占位骨架，尚未接线：baseUrl={}", baseUrl);
        // TODO(Phase 3): 建 HttpClient；校验网关已认证（GET /iserver/auth/status）；
        //                起 WS 订阅订单更新 → 转 statusCallback。
    }

    @PreDestroy
    void shutdown() {
        // TODO(Phase 3): 关 WS、释放 HttpClient。
    }

    @Override
    public String placeOrder(TradeProposal proposal) {
        // TODO(Phase 3): 组 CP Web API 下单 body（secType=STK/action/qty/limit or MKT/tif/outsideRTH），
        //   POST /iserver/account/{accountId}/orders → 若返回 reply 则 POST /iserver/reply/{id}{"confirmed":true}
        //   → 取回全局 order id 作为 brokerOrderId（无需 socket 那样加 sessionId 前缀，本身已全局唯一）。
        throw new UnsupportedOperationException("IbkrWebApiAdapter.placeOrder: Phase 3 未实现");
    }

    @Override
    public boolean cancelOrder(String brokerOrderId) {
        // TODO(Phase 3): DELETE /iserver/account/{accountId}/order/{brokerOrderId}
        //   （brokerOrderId 即全局 id，直接用，不用像 socket 那样剥前缀还原 int）。
        throw new UnsupportedOperationException("IbkrWebApiAdapter.cancelOrder: Phase 3 未实现");
    }

    @Override
    public void onOrderStatus(OrderStatusCallback callback) {
        // 与 socket 实现同构：存回调，WS/轮询收到订单更新时 callback.accept(orderId, status, filledQty, avgPrice)。
        this.statusCallback = callback;
        log.info("IbkrWebApiAdapter 已注册订单回报回调（等待 Phase 3 WS 接线后生效）");
    }
}
