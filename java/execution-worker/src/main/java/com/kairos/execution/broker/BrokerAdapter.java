package com.kairos.execution.broker;

import com.kairos.execution.model.TradeProposal;

/**
 * 券商执行接口 —— 抽象自 LEAN {@code IBrokerage}（命令 + 事件回调），见 07-plan-phase2 任务 5、
 * 参考源码 {@code kairos/下端后端/其他项目重要代码/LEAN/IBrokerage.cs}。
 *
 * <p>把"怎么连券商"与"下单业务逻辑"解耦：
 * <ul>
 *   <li><b>Phase 2</b>：实现类连本机 paper Gateway（TWS socket / 复用现有 ib_async 等价），跑通端到端。</li>
 *   <li><b>Phase 3</b>：换 OAuth REST 实现类（多租户），业务代码不动。</li>
 * </ul>
 *
 * <p>回报处理抄 {@code nautilus_trader/ib_execution.py}（IB 状态映射 + filledQty 缓存 + id 双查，见 03 §1）。
 */
public interface BrokerAdapter {

    /** 下单（限价 / bracket）。返回券商侧订单 id。 */
    String placeOrder(TradeProposal proposal);

    /** 撤单。 */
    boolean cancelOrder(String brokerOrderId);

    /** 订阅订单回报（状态变化 / 成交），driver 内部转成状态机事件。 */
    void onOrderStatus(OrderStatusCallback callback);

    /** 券商回报回调：交给 domain/OrderStateMachine 流转 + java_fill_math 累加成交。 */
    interface OrderStatusCallback {
        void accept(String brokerOrderId, String ibStatus, long filledQty, double avgFillPrice);
    }
}
