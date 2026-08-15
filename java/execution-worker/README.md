# execution-worker —— Java 下单执行 Worker（Phase 2 骨架）

> 对应计划：`docs/superpowers/specs/后端/07-plan-phase2-RocketMQ与Java-worker骨架.md`
> 下单细节：`docs/phase2/待实现功能/ibkr下单/README.md`
> 状态（2026-07-04）：🟢 **端到端 + 超时撤单都实跑通过**。
> ① 下单链路：发消息→消费→真下 IB paper 单→状态机 SUBMITTED→ACCEPTED。
> ② 挂单超时撤单：下单发 RocketMQ 5.x 延迟消息(精准 15s)→`OrderTimeoutListener`(Redisson RLock)→检查未成交→撤单→ACCEPTED→CANCELED→IB 回报 Cancelled。
> 真 MQ+真 Redis 幂等/锁+真 IB Gateway+真状态机全通。
> ⚠️ **踩坑**：RocketMQ topic 名**不许有点**（只允许 `^[%|a-zA-Z0-9_-]+$`）→ topic 用连字符 `kairos-order-submit`。
> 环境（全实测起好）：IB Gateway `4002` / Redis `6379`(密码 redis) / RocketMQ `9876`+`10911`；TWS API jar = `com.ib:tws-api:10.45.01`（已装本地仓，备份 `lib/TwsApi-10.45.01.jar`）。

---

## 这是什么

一个**独立的 Spring Boot 微服务**：从 RocketMQ 竞争消费 Python 决策层批准的下单消息 → 幂等去重 → 订单状态机 → 调 IBKR（paper）→ 状态回流 Redis Stream。

**不并入 CityAIHub**（那是电商域）——只从 CityAIHub 搬脚手架 + 配置 + 可靠性代码模式。

```
Python 主平台 ──审批──▶ [RocketMQ] ──ORDERLY+CLUSTERING──▶ execution-worker(本服务) ──▶ IBKR(paper)
   (发下单消息)                                              幂等→状态机→成交累加→回流
```

## 从 CityAIHub 搬了什么（compose over invent）

| 搬的东西 | 来自 CityAIHub | 用在这 |
|---|---|---|
| pom 依赖 + 版本 | Spring Boot 3.2.4 / Redisson 3.27.2 / rocketmq-spring | `pom.xml` |
| RocketMQ 常量模式 | `config/RocketMQConstants.java` | `config/RocketMQConstants.java`（改成 order 域 topic） |
| listener 写法 | `mq/SeckillVoucherListener.java` | `mq/OrderConsumer.java` |
| 延迟消息撤单 | `mq/OrderTimeoutListener.java` | 待加（挂单超时撤单） |
| CAS 乐观锁 | `VoucherOrderServiceImpl` | 订单状态机流转 CAS |
| **Redisson**（非 SimpleRedisLock） | `config/RedissonConfig.java` | 幂等 + 分布式锁（对齐 spec 00 §4.4） |

> ❌ 不搬：VoucherOrder/Seckill/Product/Cart（电商域无关）、`SimpleRedisLock`（无看门狗，反例）。

## 当前骨架文件

```
execution-worker/
├── pom.xml                                  ✅ 依赖搬自 CityAIHub
├── src/main/resources/application.yml       ✅ rocketmq/redis/ibkr 配置
└── src/main/java/com/kairos/execution/
    ├── ExecutionWorkerApplication.java      ✅ 入口
    ├── config/RocketMQConstants.java        ✅ kairos.order.submit/cancel/timeout
    ├── model/TradeProposal.java             ✅ 下单消息载荷（幂等键+顺序键）
    ├── mq/OrderConsumer.java                ✅ ORDERLY+CLUSTERING 消费骨架
    ├── idempotency/IdempotencyGuard.java    ✅ Redisson SETNX（逻辑同 Python 原型）
    ├── broker/BrokerAdapter.java            ✅ 券商接口（抽象自 LEAN IBrokerage）
    └── domain/                              ⬜ 待插入（见下）
```

## 待插入：你已写好的 Java 块（不要重写！）

这两块**已实现且测过**，只需搬进 `domain/` 包 + 补 `package` 声明：

| 已有块 | 测试 | 搬到 |
|---|---|---|
| `../java_order_state_machine/`（OrderStateMachine/IbStatusMapper/OrderStatus） | 16✅ | `domain/`，加 `package com.kairos.execution.domain;` |
| `../java_fill_math/`（Fill/FillAccumulator） | 7✅ | `domain/`，同上 |

> ⚠️ 现有块在**默认包**（无 package 声明），搬进来要加包名 + 把测试一并迁到 `src/test/java/...`。

## 怎么 build / 跑（前提）

✅ **已用 IntelliJ 自带 Maven（`E:\learning software\IntelliJ IDEA 2024.3.2.2\...\maven3`）+ JDK 24 实测 `mvn compile` 通过。**

⚠️ **JDK 24 的 Lombok 坑（已在 pom 修好）**：JDK 23+ 默认不再运行"只挂在 classpath 上"的注解处理器，导致 `@Data`/`@Slf4j` 静默不生成代码。修法（`pom.xml` 已含）：
1. Lombok 升到 **1.18.38**（JDK 24 支持；1.18.36 只到 JDK 23）。
2. compiler 插件**显式声明** `annotationProcessorPaths` 里的 lombok。
> CityAIHub 没这问题是因为在 IntelliJ 用 JDK 17 SDK；命令行 JDK 24 才触发。IntelliJ 里若 SDK 设 24 也需此 fix。

命令行 build（复现用）：
```bash
export JAVA_HOME='E:\计算机相关\java\jdk'
MVN="E:/learning software/IntelliJ IDEA 2024.3.2.2/plugins/maven/lib/maven3/bin/mvn.cmd"
MSYS_NO_PATHCONV=1 "$MVN" -B -f "<此目录>/pom.xml" compile
```
或直接 **IntelliJ 打开**（自带 Maven，最省事）。

**跑起来需要的服务（2026-07-04 全部实测起好）：**
- RocketMQ `rmqnamesrv`(9876) + `rmqbroker`(10911，已重建，conf 在 `C:\rmq-conf\broker.conf`)
- IB paper Gateway `kairos-ibkr-paper`（host `127.0.0.1:4002`→容器 4004）
- Redis `kairos-trader-redis-1`（`localhost:6379`，**密码 `redis`**）
- TWS API jar `com.ib:tws-api:10.45.01`（已装本地仓 + `lib/` 备份）

## 对应 07 计划的进度

| 07 任务 | 状态 |
|---|---|
| 任务 2：Java worker 骨架 + MQ 消费 | 🟡 骨架已搭（`OrderConsumer`，ORDERLY+CLUSTERING） |
| 任务 4：幂等（Redisson） | 🟡 骨架 `IdempotencyGuard`（逻辑已在 Python 原型验证） |
| 任务 3：订单状态机 | ✅ **已搬入 `domain/`**，16 测在 worker 内跑绿 |
| 任务 3：成交数学 | ✅ **已搬入 `domain/`**，7 测在 worker 内跑绿 |
| **TWS API jar** | ✅ **已下载+装本地仓**（com.ib:tws-api:10.45.01），编译通过 |
| 任务 5：BrokerAdapter | ✅ **`IbkrBrokerAdapter` 已写**（TWS API ApiController 连 4002，placeOrder/cancelOrder + orderStatus 回调），编译过 |
| 任务 3：回报接 domain | ✅ **`OrderTracker` 已写并接线**：IB orderStatus → IbStatusMapper → OrderStateMachine 流转（幂等跳重复状态、非法转换告警） |
| 任务 1：Python 事务消息 producer | ⬜ 未做（`order_dispatch.py`；⚠️ Python 事务消息客户端可行性待 spike） |
| 任务 6：状态回流 Redis Stream | ⬜ 未做（`publish/EventPublisher`） |
| 任务 3b：成交明细累加 FillAccumulator | ⬜ 未接（走 IB execDetails 个别成交，domain 已备好测过；orderStatus 只给累计） |
| **任务 7：端到端 paper 跑通** | ✅ **已通**（2026-07-04）：发消息→消费→真下 IB 单→状态机 SUBMITTED→ACCEPTED。冒烟发单器 `test/TestOrderSender`（`--kairos.test.send-order=true`）。 |
| **Phase4：挂单超时撤单（延迟消息）** | ✅ **已通**（2026-07-04）：`OrderConsumer` 下单后发 RocketMQ 5.x 延迟消息（`syncSendDelayTimeMills`，任意时长）→ `OrderTimeoutListener`（**Redisson RLock**，非 SimpleRedisLock）到点检查未成交→撤单→CANCELED→IB 确认。对应 CityAIHub"支付超时关单"。 |

## 下一步

1. **写 `IbkrBrokerAdapter`（真连 4002 下 paper 单）** ← 当前这步。用 TWS API `ApiController` 高层封装；placeOrder/cancelOrder + 回报经 `IbStatusMapper`→`OrderStateMachine`+`FillAccumulator`。⚠️ 真跑会发真 paper 单（需用户点头）。
2. 接线 `OrderConsumer` → `IbkrBrokerAdapter` + domain 跟踪。
3. 发一条 `kairos.order.submit` 消息，验证 worker 消费→下单到 paper→状态机跟踪端到端。
4. Python 侧 `order_dispatch.py`（⚠️ 先 spike 事务消息客户端可行性）。
