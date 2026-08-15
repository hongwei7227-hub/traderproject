package com.kairos.execution.broker;

import com.ib.client.Contract;
import com.ib.client.Decimal;
import com.ib.client.Order;
import com.ib.client.OrderCancel;
import com.ib.client.OrderState;
import com.ib.client.OrderType;
import com.ib.contracts.StkContract;
import com.ib.controller.ApiConnection.ILogger;
import com.ib.controller.ApiController;
import com.kairos.execution.model.TradeProposal;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * 真实 IBKR 执行实现 —— 用官方 TWS API 的 {@link ApiController} 连 IB Gateway 下单。
 *
 * <p>Phase 2：连本机 paper Gateway（socket，默认 127.0.0.1:4002）。Phase 3 换 OAuth/Web API
 * （见 {@link IbkrWebApiAdapter}）。回报经 {@code IbStatusMapper} → {@code OrderStateMachine} +
 * {@code FillAccumulator}（domain），由 {@code OrderConsumer} 注册的 {@link OrderStatusCallback} 消费。
 *
 * <p><b>选型开关</b>：{@code broker.mode=socket}（缺省即此）装配本类；{@code broker.mode=webapi}
 * 切到 {@link IbkrWebApiAdapter}。业务代码只依赖 {@link BrokerAdapter} 接口，切换不动业务。
 *
 * <p>⚠️ 真跑会向 paper 账户发真单。
 */
@Component
@ConditionalOnProperty(name = "broker.mode", havingValue = "socket", matchIfMissing = true)
public class IbkrBrokerAdapter implements BrokerAdapter, ApiController.IConnectionHandler {

    private static final Logger log = LoggerFactory.getLogger(IbkrBrokerAdapter.class);

    @Value("${ibkr.host:127.0.0.1}")
    private String host;
    @Value("${ibkr.port:4002}")
    private int port;
    @Value("${ibkr.client-id:101}")
    private int clientId;

    private ApiController controller;
    private volatile boolean connected = false;
    private volatile OrderStatusCallback statusCallback;

    /**
     * 进程级会话前缀 —— 给 IB orderId 加命名空间,使 brokerOrderId 跨会话/重连全局唯一。
     *
     * <p>根因:IB orderId 是<b>单个 API 连接会话内</b>从 nextValidId 递增的计数,<b>非全局唯一</b>;
     * paper Gateway 重连或换 clientId 后会从头复用(见"后端修复记录:broker_order_id 撞库")。
     * 直接拿它当主键/回报反查键,会撞上库里历史遗留的同号行:INSERT 冲突落库失败、
     * CAS 却把<b>旧单</b>误改状态。加会话前缀 → 每次进程启动一个唯一命名空间,历史行永不被匹配。
     */
    private final String sessionId = java.util.UUID.randomUUID().toString().substring(0, 8);

    @PostConstruct
    public void init() {
        ILogger in = msg -> log.trace("[IB<-] {}", msg);
        ILogger out = msg -> log.trace("[IB->] {}", msg);
        controller = new ApiController(this, in, out);
        log.info("连接 IB Gateway {}:{} (clientId={}) ...", host, port, clientId);
        controller.connect(host, port, clientId, "");
    }

    @PreDestroy
    public void shutdown() {
        if (controller != null) {
            controller.disconnect();
        }
    }

    @Override
    public void onOrderStatus(OrderStatusCallback callback) {
        this.statusCallback = callback;
    }

    @Override
    public String placeOrder(TradeProposal p) {
        Contract contract = new StkContract(p.getSymbol());   // secType=STK, exchange=SMART, currency=USD

        Order order = new Order();
        order.action(p.getAction());                          // "BUY" / "SELL"
        if (p.getLimitPrice() != null) {
            order.orderType(OrderType.LMT);
            order.lmtPrice(p.getLimitPrice().doubleValue());
        } else {
            order.orderType(OrderType.MKT);                   // limitPrice 为空 → 市价单(即时成交，剥离撮合等待)
        }
        order.totalQuantity(Decimal.get(p.getQuantity()));
        order.outsideRth(true);                               // 允许盘前/盘后执行(RTH 外的常规延长时段)；夜盘(overnight)需 TIF=OVT+API升级，本版本不支持

        // placeOrModifyOrder 会在 orderId==0 时自动分配 m_orderId 并自增；调用后即可读回
        controller.placeOrModifyOrder(contract, order, new ApiController.IOrderHandler() {
            @Override
            public void orderState(OrderState orderState, Order o) {
                // 提交确认（含保证金/佣金预览），此处不处理
            }

            @Override
            public void orderStatus(com.ib.client.OrderStatus status, Decimal filled, Decimal remaining,
                                    double avgFillPrice, long permId, int parentId, double lastFillPrice,
                                    int clientId, String whyHeld, double mktCapPrice) {
                OrderStatusCallback cb = statusCallback;
                if (cb != null) {
                    // IB 状态枚举 → 字符串，交给 domain 的 IbStatusMapper 处理。
                    // 反查键必须与 placeOrder 返回的 brokerOrderId 同构：sessionId-ibOrderId（否则回报落不到本单）
                    cb.accept(sessionId + "-" + order.orderId(), status.toString(),
                            filled.longValue(), avgFillPrice);
                }
            }

            @Override
            public void handle(int errorCode, String errorMsg) {
                log.warn("下单回报错误 orderId={} code={} msg={}", order.orderId(), errorCode, errorMsg);
            }
        });

        String brokerOrderId = sessionId + "-" + order.orderId();   // 会话前缀 → 全局唯一，防 IB orderId 跨会话复用撞库
        log.info("已提交 IB 订单 brokerOrderId={} {} {} x{} @{}",
                brokerOrderId, p.getAction(), p.getSymbol(), p.getQuantity(), p.getLimitPrice());
        return brokerOrderId;
    }

    @Override
    public boolean cancelOrder(String brokerOrderId) {
        try {
            // brokerOrderId = sessionId-ibOrderId；撤单要还原成 IB 的原始 int orderId（取末段）
            int id = Integer.parseInt(brokerOrderId.substring(brokerOrderId.lastIndexOf('-') + 1));
            controller.cancelOrder(id, new OrderCancel(), new ApiController.IOrderCancelHandler() {
                @Override
                public void orderStatus(String orderStatus) {
                    log.info("撤单回报 orderId={} status={}", id, orderStatus);
                }

                @Override
                public void handle(int errorCode, String errorMsg) {
                    log.warn("撤单错误 orderId={} code={} msg={}", id, errorCode, errorMsg);
                }
            });
            return true;
        } catch (NumberFormatException e) {
            log.error("非法 orderId: {}", brokerOrderId);
            return false;
        }
    }

    public boolean isConnected() {
        return connected;
    }

    // ---------- ApiController.IConnectionHandler ----------

    @Override
    public void connected() {
        connected = true;
        log.info("✅ 已连上 IB Gateway {}:{}", host, port);
    }

    @Override
    public void disconnected() {
        connected = false;
        log.warn("与 IB Gateway 断开");
    }

    @Override
    public void accountList(List<String> list) {
        log.info("IB 账户: {}", list);
    }

    @Override
    public void error(Exception e) {
        log.error("IB 连接异常", e);
    }

    @Override
    public void message(int id, long errorTime, int errorCode, String errorMsg, String advancedOrderRejectJson) {
        // IB 的 code 2104/2106/2158 是行情农场"已连接"信息，非错误
        if (errorCode >= 2100 && errorCode < 2200) {
            log.debug("IB info [{}] {}", errorCode, errorMsg);
        } else {
            log.warn("IB message id={} code={} msg={}", id, errorCode, errorMsg);
        }
    }

    @Override
    public void show(String msg) {
        log.info("IB show: {}", msg);
    }
}
