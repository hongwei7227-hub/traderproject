# Java 服务

四个 Spring Boot 服务,各自拥有一项平台不自己实现的能力。它们通过 HTTP 或消息队列被调用,不被链接进 Python 进程——一个重启、扩容或替换,平台不需要知道。

| 服务 | 端口 | 拥有什么 | 平台怎么用 |
|---|---|---|---|
| [login](login/) | 8081 | 身份:验密码、发 token、存会话 | 读同一个 Redis 键认 token |
| [recharge](recharge/) | 8082 | 会员与充值:订单、支付网关、幂等、超时关单 | HTTP 调用 |
| [execution-worker](execution-worker/) | 8090 | 下单执行:券商适配、订单状态机、成交累加 | 消息队列投递 + 读它写的订单表 |
| [analyst-service](analyst-service/) | 8091 | 分析师评级:两级缓存 + 布隆过滤器 | HTTP 调用 |

## 各服务的对外契约

### login — 身份

```
POST /register   {username, password}
POST /login      {username, password}  → Result.ok(token)
GET  /me                               → 当前用户
```

**token 是不透明的**:一个随机 UUID,真正的用户数据在 Redis 的 `login:token:<token>` 哈希里,TTL 30 分钟,每次使用滑动续期。

平台**读同一个键**而不是每个请求回调这个服务。回调等于为一次本可以直接做的 Redis 查询加一跳网络,而流式请求的每一帧都要付这个代价。键格式在平台侧是配置项(`KAIROS_AUTH_SESSION_KEY_PREFIX`),它变的时候是一处设置移动,不是所有人被登出。

平台也**同样续期**。只在用户访问登录服务时才滑动,会让人在平台里对话到一半掉线。

### recharge — 会员与充值

```
GET  /recharge/plans
POST /recharge/order          {planId, requestId}
GET  /recharge/order/{id}
POST /recharge/pay/mock-callback
GET  /membership/me
```

`requestId` 是幂等键,**由调用方生成**。双击购买必须只产生一张订单,而这只有在两次请求带同一个键时才可能——键在这个调用内部生成就做不到。

平台传 `X-User-Id` 头指明为谁调用。这只安全在一个前提上:该头永远来自已验证的作用域,绝不来自客户端发来的任何东西。

### execution-worker — 下单执行

**没有 REST 接口。**它消费消息队列:

```
kairos-order-submit:create    下单
kairos-order-cancel:cancel    撤单
kairos-order-timeout:expire   挂单超时(延迟消息)
```

顺序键 = `accountId`。同账户的下单和撤单必须按序到达,否则撤单会跑到它要撤的那笔买入前面。

平台**不说这个协议**。提案写进 Postgres 的 `order_outbox` 表,与产生它的请求同一个事务,由 relay 事后 drain 到队列。在请求路径里直接发消息会让下单在 broker 恰好不可达时失败(用户看到的失败和他的订单无关),更糟的是可能发出一条消息、外层事务随后回滚,worker 拿着一条平台没有记录的指令。

回程是 worker 写的 `execution.orders` 表,平台**只读**。唯一写者不是平台进程——两个写者就是成交量被一个读出来就已经过期的状态覆盖的成因。

### analyst-service — 分析师评级

```
GET /stock/{symbol}/analyst
GET /stock/{symbol}/analyst/nocache
PUT /stock/{symbol}/analyst
GET /cache/stats
```

它自己拥有缓存:本地 Caffeine 前置 Redis 前置 Postgres,加布隆过滤器挡住不存在的股票代码,让它们根本到不了数据库。

**平台不再做一层缓存。**再加一层就是第二个要失效的东西,而这个服务的失效广播只发给它自己的实例,不发给我们。

## 编译与运行

命令行没有 Maven。用 IntelliJ 自带的 Maven 3.9.9 + JBR 21。

> JDK 24 与 Lombok 1.18.36 不兼容会崩,用 JDK 21。

依赖:

| 服务 | Redis | Postgres | RocketMQ | 券商 |
|---|---|---|---|---|
| login | ✅ | — | — | — |
| recharge | ✅ | ✅ | ✅ | 支付网关(有 mock 回调) |
| execution-worker | ✅ | ✅ | ✅ | IB Gateway |
| analyst-service | ✅ | ✅ | 可关 | — |

Redis 容器密码是 `redis`,不填会 NOAUTH。

四个服务各自有 Flyway 迁移,建在**独立 schema**(`execution`、`analyst`)里,不碰平台的 `public`。

## 不用起 Java 也能看

平台的 demo 用内存替身覆盖了这四条路径,包括订单从接受到部分成交到全部成交的推进:

```bash
cd ../server
python scripts/demo.py
```

替身不是为了省事——它让「无覆盖的股票」「被风控拒绝的订单」「等待支付的充值」这些**最容易写错的分支**在没有任何外部依赖时也能被看到和测到。
