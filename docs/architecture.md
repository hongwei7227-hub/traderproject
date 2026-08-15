# 架构设计

Kairos Trader 是一个**多租户（multi-tenant）的 LLM agent 平台**，跑在金融数据之上。

一套部署服务多个账户。每个账户带自己的模型偏好、自己的凭据、自己的额度，彼此看不见也吃不掉对方的资源。Agent 的推理过程是**边产生边推送**的——一次耗时一分钟的对话，这一分钟里持续在出内容，而不是安静一分钟然后一次性到达。

本文写的是**当前代码的架构**。文末第 14 节列出尚未建成的部分。

---

## 1. 系统边界

```
┌─────────────┐   HTTP + SSE   ┌──────────────────┐
│  web/       │ ─────────────▶ │  server/kairos/  │
│  React 前端 │ ◀───────────── │  Python 平台     │
└──────┬──────┘                └────────┬─────────┘
       │                                │
       │ 登录直连                        ├──▶ LLM 供应商（4 种线协议）
       │                                ├──▶ PostgreSQL
       ▼                                │
┌─────────────┐                         ├──▶ analyst-service  (HTTP)
│ login 8081  │◀── 读同一个 Redis 键 ────┤
└─────────────┘                         ├──▶ recharge         (HTTP)
                                        │
                                        └──▶ order_outbox 表
                                                 │ relay drain
                                                 ▼
                                        execution-worker (MQ)
                                                 │ 写 execution.orders
                                                 └──▶ 平台只读
```

**三个可部署单元都在本仓库内**：Python 平台（`server/`）、React 前端（`web/`）、四个 Java 服务（`java/`）。

Java 那四个各自拥有一项平台不自己实现的能力：

| 服务 | 端口 | 拥有什么 | 平台怎么接 |
|---|---|---|---|
| `login` | 8081 | 身份：验密码、发 token、存会话 | 读同一个 Redis 键 |
| `recharge` | 8082 | 会员、充值、支付、幂等 | HTTP |
| `execution-worker` | 8090 | 券商适配、订单状态机、成交累加 | 消息队列投递 + 读它写的订单表 |
| `analyst-service` | 8091 | 分析师评级、两级缓存、布隆过滤 | HTTP |

**它们不被链接进 Python 进程**，而是通过 HTTP 或消息队列被调用。一个重启、扩容或替换，平台不需要知道；一个挂掉，降级的是一个页面区块，不是整个平台。契约见 [java/README.md](../java/README.md)。

### 两种部署形态

| 形态 | `deployment` | 鉴权 | 用途 |
|---|---|---|---|
| 单机（solo） | `SOLO` | 免鉴权，固定租户 `solo` / 用户 `operator` | 本地自托管、开发 |
| 平台（hosted） | `HOSTED` | 强制 JWT，多租户 | 线上多账户 |

两种形态**走同一条代码路径**，差别只在 `IdentityResolver` 用什么方式产出 `TenantScope`。solo 模式不是"关掉了租户层"，而是"租户恒为一个"——这样单机模式跑过的路径，平台模式也跑过。

`SOLO` 额外开放 `/docs` 和 `/openapi.json`；`HOSTED` 关掉，交互式文档等于把整个攻击面画成地图。

---

## 2. 分层：六边形架构（Hexagonal / Ports & Adapters）

按**领域主题**分包，不按技术类型分包。

```
server/kairos/
├── core/            领域核心 —— 不 import 任何框架、数据库、网络库
│   ├── tenancy/       请求属于谁
│   ├── identity/      已验证的调用方长什么样（port）
│   ├── catalog/       能触达哪些模型、怎么选
│   ├── quota/         token 计量
│   ├── resilience/    故障记忆
│   ├── reasoning/     一轮对话的编排
│   ├── streaming/     事件协议与其不变量
│   ├── tools/         工具注册表与执行沙箱
│   └── trading/       提案、订单、风控信封
├── adapters/        出站适配器 —— 实现 core 定义的 port
│   ├── persistence/   ORM 实体与带租户过滤的仓储
│   ├── llm/           线协议编码、凭据、调用
│   ├── identity/      直接读登录服务的 session
│   ├── trading/       订单 outbox，以及 worker 订单表的只读访问
│   └── services/      analyst / billing 两个 HTTP 客户端
├── api/             入站适配器
│   ├── http/          REST 路由、身份、租户中间件
│   └── stream/        SSE 编码与流桥接
├── runtime/         组装 —— 配置、依赖注入、生命周期
└── migrations/      schema 历史，随包发布
```

### 依赖方向恒向内

```
api ──▶ core ◀── adapters
         ▲
      runtime（只有它知道全部）
```

`core/` 定义接口（Protocol），`adapters/` 实现，`runtime/` 负责装配。

**这条线不靠约定维持,靠两道断言：**

1. `pyproject.toml` 里的 import-linter 契约：`core` 不得出现 `fastapi` / `sqlalchemy` / `redis` / `httpx` / `alembic`；层序 `runtime > api > adapters > core`。
2. [`tests/test_architecture.py`](../server/tests/test_architecture.py) —— **读源码的 import 语句**来判定，而不是真去 import。区别很实在：真 import 的话，一个未安装的包会报 `ImportError`，看起来像环境问题；读 AST 的话，越界就报越界。

---

## 3. 租户隔离

这是整个平台安全性的地基。设计目标是：**漏写一次检查不应该导致越权。**

### 3.1 身份是环境量，不是参数

```python
# core/tenancy/context.py
@dataclass(frozen=True, slots=True)
class TenantScope:
    tenant_id: TenantId
    user_id: UserId
    roles: frozenset[Role]

def current_scope() -> TenantScope:
    """未建立即抛错——不返回 None。"""
    if (s := _ACTIVE_SCOPE.get()) is None:
        raise ScopeNotEstablished
    return s
```

底层是 `ContextVar`，由中间件在**鉴权之后、路由之前**建立一次，全链路自动可见。

**为什么抛错而不返回 `None`**：返回 `None` 会诱导出 `if scope:` 这种写法，然后走进一个不带租户过滤的查询。作用域缺失是路由 bug，就该以 bug 的形式暴露。

### 3.2 隔离写在仓储基类里，不写在端点里

```python
# adapters/persistence/repository.py
class ScopedRepository(Generic[EntityT]):
    def _select(self) -> Select[tuple[EntityT]]:
        """所有查询的唯一起点，租户条件在此注入。"""
        return select(self.entity).where(
            self.entity.tenant_id == current_scope().tenant_id
        )

    def _unscoped_escape_hatch(self, *, justification: str) -> Select[...]:
        if not justification.strip():
            raise ValueError("an unscoped query requires a written justification")
        return select(self.entity)
```

子类只能基于 `_select()` 构造查询。想绕过，必须显式调用一个名字叫「**逃生舱**」的方法，并写下书面理由——**把安全的路径做成最省事的路径**。

写入同理：`add()` 从作用域盖章，不接受 tenant 参数。一个能指定自己往哪个租户写的调用方，就能指错。

`OwnedRepository` 在租户之上再加一层用户归属（`list_own` / `get_own`），用于 workspace、thread 这类归个人所有的实体。

### 3.3 两个 `Role` 不是一回事

| 位置 | 含义 |
|---|---|
| `core/tenancy/context.py` 的 `Role` | 租户内的**权限角色**（谁能干什么） |
| `core/catalog/resolution.py` 的 `Role` | 一轮对话内的**模型用途**（主推理/压缩/抽取…） |

同名不同域，跨模块引用时用完整路径。

---

## 4. 模型目录与选型

### 4.1 目录是类型化的，构造期校验

```python
# core/catalog/descriptors.py
class Wire(StrEnum):          # 按协议命名，不按厂商命名
    OPENAI_CHAT = "openai-chat"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    GEMINI_GENERATE = "gemini-generate"

class Capability(Flag):
    TEXT = auto(); VISION = auto(); DOCUMENT = auto()
    TOOL_CALLING = auto(); STREAMING = auto(); REASONING = auto()

    @classmethod
    def baseline(cls) -> Capability:
        """能驱动一轮 agent 对话的最低要求。"""
        return cls.TEXT | cls.TOOL_CALLING | cls.STREAMING
```

**按协议命名而不是按厂商命名**，因为一个厂商可以提供多种协议，而决定"请求怎么拼"的是协议。

`Catalog` 在构造时校验：模型引用的 provider 必须存在、provider 的 family 指针不能悬空、可选模型必须满足 `Capability.baseline()`。**配置错误在进程启动时炸，不在用户请求时炸。**

`ProviderDescriptor` 的两个字段值得说明：

- `family` —— 同一厂商的多个端点（国内区/国际区、按量/订阅）归组，凭据查找沿 family 走，**自身优先于兄弟**。同厂商不同端点的协议和 host 可以不同，继承来的 key 打过去只会得到读起来像鉴权失败的 401。
- `byok_allowed` —— 租户能否自带 key，**默认关**。OAuth 和本地端点无论标什么都排除：那里没有 key 可粘贴。

### 4.2 选型走责任链（Chain of Responsibility）

```
显式请求指定 → 租户偏好 → 工作区默认 → 系统兜底
```

一级一个类，返回 `None` 表示"不归我管，往下传"：

```python
# core/catalog/resolution.py
DEFAULT_CHAIN: tuple[Resolver, ...] = (
    ExplicitRequestResolver(),    # 本次请求点名
    TenantPreferenceResolver(),   # 租户偏好（每请求实时读库）
    WorkspaceDefaultResolver(),   # 工作区默认
    SystemBaselineResolver(),     # 平台兜底
)
```

三个性质：

- **加优先级 = 加一个类**，不动既有代码；重排 = 重排一个 tuple；单测某一级不需要把另外四级立起来。
- **租户偏好每请求读库**，这是"切模型立刻生效"而不是"下次发版生效"的原因。
- 结果是 `ModelChoice(model_id, decided_by)`，**带上是谁决定的**。这不是装饰——租户问"我明明配了 A，为什么请求跑去了 B"，答案得有个来源，不能靠人肉读责任链。

`ExplicitRequestResolver` 只对 `primary` 角色生效。允许调用方改写压缩模型，等于让他把成本转嫁到租户没选过的角色上。

### 4.3 角色化选型：一轮对话用多个模型

| 角色 | 用途 | 能力要求 |
|---|---|---|
| `primary` | 主推理、决定调什么工具 | baseline + **vision** |
| `swift` | 短、对延迟敏感的回复 | baseline |
| `condense` | 上下文超窗后压缩历史 | baseline |
| `extract` | 抽取网页/文档正文 | baseline |
| `delegate:*` | 具名子 agent | baseline |

拆开的意义是**成本**。压缩历史和扒网页正文是高频、低判断力的活，给它们付旗舰价是这类平台最容易白烧钱的地方。

角色声明自己需要什么能力（`Role.requires()`），**配的时候就校验**：设置界面里选一个没有 vision 的模型当 primary，当场 422 拒绝并说明缺什么。否则这个错误要等到几分钟后一轮对话跑到一半才炸。

### 4.4 上下文预算参与运行时决策

```python
class TokenBudget(BaseModel):
    context: int
    max_output: int

    def compaction_threshold(self, headroom: float = 0.6) -> int:
        return int((self.context - self.max_output) * headroom)
```

压缩阈值**由模型描述符按比例推导**，不是写死的绝对数字。从百万上下文的模型降级到二十万上下文的模型时，阈值跟着一起下来。

---

## 5. 配额：两阶段协议

agent 一轮对话的成本能差几个数量级，所以**按请求数限流管不住花销**。计量单位是 token，这就逼出两阶段：

```
Estimate（悲观估）→ Reservation（预留）→ 干活 → Settlement（按实际结算）
```

```python
# core/quota/reservation.py
def reserve(allowance, reservation) -> Allowance     # 先扣
def settle(allowance, settlement) -> Allowance       # 后调平
def abandon(allowance, reservation) -> Allowance     # 出错则退还
```

**在途预留计入余额**，这是并发请求不能集体超支的关键：十个请求同时进来，第一个预留完，第二个看到的余额已经少了。

额度耗尽时的行为可配（`on_exhaustion`）：

| 值 | 行为 |
|---|---|
| `reject` | 直接拒（默认） |
| `degrade` | 降级到更便宜的模型继续 |
| `allow` | 放行，只记账 |

`QuotaPolicy` 还会在余额低于 `warn_at_fraction`（默认 0.9）时产出警告，通过流里的 `notice` 事件递给前端。

---

## 6. 韧性：有状态熔断

供应商挂了要被**记住**，不能每个新请求都重新耗尽重试预算去重新发现一遍。

```python
# core/resilience/breaker.py
class BreakerKey(NamedTuple):
    tenant: TenantId
    provider: ProviderId
```

**维度的选择是这里的核心决策**：

- 纯全局维度 → 一个租户配额耗尽触发的失败，会把所有租户从这家供应商切断（吵闹邻居 / noisy neighbour）。
- 纯供应商维度 → 反映不了各租户不同的配额状态和凭据状态。
- `(租户, 供应商)` → 两者都成立。

状态机是标准三态：`CLOSED`（放行）→ 连续失败达 `failure_threshold` → `OPEN`（拒绝）→ 过 `recovery_after_seconds` → `HALF_OPEN`（试探）→ 连续成功达 `success_threshold` → `CLOSED`。

`FallbackPlan` 承载降级序列，`RetryPolicy` 负责指数退避（带抖动，避免同时重试造成的雪崩同步）。

---

## 7. 推理管线：声明式装配

一轮对话由若干**阶段（Stage）**组成，位置是契约——某些阶段必须在重试外面，某些必须在缓存断点里面，错了行为就错。

**做法是让阶段声明约束，由装配器排序，而不是让人维护一个顺序敏感的列表。**

```python
# core/reasoning/pipeline.py
@dataclass(frozen=True, slots=True)
class Stage:
    name: StageName
    phase: Phase                        # 粗粒度分带
    provides: frozenset[Provides]       # 我提供什么能力
    requires: frozenset[Provides]       # 我需要什么能力
    inside_of: frozenset[StageName]     # 我必须在谁里面
    outside_of: frozenset[StageName]    # 我必须在谁外面
    why: str                            # 为什么——和约束放在一起
```

`Pipeline.assemble()` 做拓扑排序，三类错误**在装配期抛**，不在运行期：

| 异常 | 触发条件 |
|---|---|
| `CyclicOrdering` | 约束成环 |
| `MissingStage` | 引用了不存在的阶段 |
| `UnmetRequirement` | 需要的能力没人提供 |

粗粒度分带（`Phase`）给了默认次序，阶段只需要声明**真正重要的**那几条约束：

```
INTAKE → CONTEXT → CACHE_BOUNDARY → DISPATCH → ADAPT
```

### 标准管线的两条边界

**重试边界（`retry`）**——外面的每轮跑一次，里面的每次尝试都跑一次，且能看到降级后真正选中的模型。

| 阶段 | 位置 | 为什么 |
|---|---|---|
| `oversized-results` | 最外 | 大工具结果换成引用，后面所有阶段都不必搬运全文 |
| `media-capture` | retry 外 | 它上传答案里的图片。放里面每次重试都重传一遍 |
| `compaction` | retry 外 | 摘要贵，且对每次尝试都有效。放里面，供应商抖动就得反复付钱 |
| `modality-fit` | retry **内** | 剥掉选中模型吃不下的内容。放外面，从 vision 模型降级到纯文本模型后会把导致失败的图片原样重放，再失败一次 |
| `provider-markers` | retry 内 | 缓存标记是厂商私有的，降级后不能把 A 家的标记发给 B 家 |
| `reasoning-compat` | 最内 | 推理块不透明且厂商私有，跨厂商重放会被直接拒。只有到这里才真正知道目标模型 |

**缓存断点（`cache-breakpoint`）**——标记可复用前缀的终点。断点之前必须逐字节稳定，之后随便变。

| 阶段 | 位置 | 为什么 |
|---|---|---|
| `live-context` | 断点**内** | 当前时间这类每轮都变的事实，放外面会让缓存前缀每轮全失效 |

`STANDARD_STAGES` 这个 tuple **是无序声明的**——它是集合不是序列，顺序来自约束。重排这个字面量不改变任何行为，这正是它要有的性质。

### 一轮对话的状态机

```
PENDING ──▶ THINKING ⇄ ACTING
   │            │
   └──────┬─────┴────────▶ CANCELLING ──▶ CANCELLED
          ▼                     │
      COMPLETED / FAILED ◀──────┘
```

合法迁移**编码在字典里**（`_TRANSITIONS`），非法迁移抛 `InvalidTransition`。

`CANCELLING` 单独存在，因为取消不是瞬时的：已经发出的工具调用得跑完或被放弃，而这一轮无论如何欠一次结算。把它并进 `CANCELLED` 会丢掉这个必须结算的窗口。

停止原因（`StopReason`）区分 `answered` / `iteration_budget` / `token_budget` / `time_budget` / `cancelled` / `error`——**预算打断的答案和正常写完的答案长得一模一样**，说清楚是"限制"和"缺陷"的区别。

---

## 7.5 交易：提案出站、订单读回

平台**只负责提案**。券商对话、订单状态机、成交累加全在 execution-worker 里，在这边复刻任何一样都会制造出关于同一张订单的第二种意见。

### 出站走事务性 outbox（Transactional Outbox）

worker 消费消息队列，本进程不说那个协议。在请求路径里直接发消息有两个问题：

1. broker 恰好不可达时下单失败——用户看到的失败和他的订单毫无关系。
2. 更糟：可能发出一条消息、外层事务随后回滚，worker 手里握着一条平台**没有记录**的指令。

所以提案与产生它的请求**同一个事务**落进 `order_outbox` 表，relay 事后 drain 到队列：

```
请求事务 ─┬─ 业务写入
          └─ order_outbox 插入        ← 要么都提交，要么都不
                    │
                    ▼  relay 扫描（跨租户，不带租户谓词）
              RocketMQ kairos-order-submit
```

| 设计点 | 为什么 |
|---|---|
| `proposal_id` 在**提交前**生成 | 它是 worker 幂等键的一半。重试带同一个 id 才能塌缩成同一张单；在 worker 侧生成的话，重试就是第二笔订单 |
| `(tenant_id, proposal_id)` 唯一约束 | 数据库层再兜一道，与 Redis 幂等互为保险 |
| 重复插入用 **SAVEPOINT** | 约束冲突会污染它所在的事务。直接 `rollback()` 会把这个请求做过的**其他所有事**一起撤销 |
| 顺序键 = `account_id` | 同账户的下单/撤单必须按序，否则撤单会跑到它要撤的买入前面 |
| 标记 sent 在 publish **返回之后** | 中途崩溃要重投而不是丢单。重投安全的前提正是幂等键先生成 |
| 失败 5 次转 `FAILED` | 永不 drain 的队列会把它后面每一张单都挡住 |

### 回程：只读

`execution.orders` 是 worker 的表，用 Core `Table` 描述而**不映射成实体**——实体意味着所有权（关系、级联、写入），这些都不是我们的。唯一写者不是本进程：两个写者就是成交量被一个读出来就已经过期的状态覆盖的成因。

**持仓由成交推导**，不单独存表。订单就是记录，另存一份就会和它对不上，而对账是没人想干的活。卖出实现盈亏、**不改剩余部分的成本**——卖出时重算均价是持仓成本漂移的常见来源。

### 风控在提交时判

worker 也判，而且它的拒绝才算数（它离券商最近，且别处来的提案也归它管）。这层存在的理由不同：**这里的拒绝发生在人还看着表单的时候**，能说清撞的是哪条限额、超了多少。worker 的拒绝几秒后才到，形式是一张没人想要的订单挂着 `DENIED` 状态。

| 限额 | 默认 | 判定点 |
|---|---|---|
| 单笔 | 净值 2% | 下单金额 |
| 持仓 | 净值 30% | **成交后**的持仓，不是成交前——按前者判会让一串单笔合规的订单累积越线 |
| 单日笔数 | 3 | 当日已下单数 |
| universe | 空=不限制 | 空表示未配置而非「什么都不能交易」 |
| 卖空 | 不建模 | 卖出超过持有量直接拒 |

**多项违规一次全报。**只报第一条会把改单变成猜谜：每修一次冒出下一个反对意见。

`bypass` 需要**书面理由**并记录在决策上——用它会留痕，而不是看起来像一张顺利通过的单。

## 7.6 身份：登录服务是权威

登录服务验密码、发 token、存会话。token 是**不透明的**：一个随机 UUID，真正的用户数据在 Redis 的 `login:token:<token>` 哈希里，30 分钟 TTL，每次使用滑动续期。

平台**读同一个键**，不是每个请求回调它一次：

- 回调等于为一次本可以直接做的 Redis 查询加一跳网络，流式请求的每一帧都要付。
- 键格式做成**配置项**而非常量。它变的时候是一处设置移动，失败形式是干净的「未登录」，而不是难以察觉的不匹配。

平台**同样续期**。只在用户访问登录服务时才滑动，会让人在平台里对话到一半掉线。

`TokenVerifier` 因此是**异步**的。只让这一个实现异步会造成中间件里两条代码路径，而测试跑的那条不是线上跑的那条。

裸 token 和 `Bearer <token>` 都接受：浏览器发前者，登录服务自己的拦截器裸读 `authorization` 头。只认一种会让同一个 token 在系统的一半能用、另一半不能。`Basic <base64>` 明确拒绝——把编码后的口令当成不透明 token 去查会把凭据写进缓存键。

## 8. 工具层

### 8.1 两种暴露方式

```python
class Exposure(StrEnum):
    DIRECT = "direct"              # 作为 LLM 工具暴露
    PROGRAMMATIC = "programmatic"  # 渲染成可 import 的 Python 模块
    BOTH = "both"
```

`PROGRAMMATIC` 即 **PTC（Programmatic Tool Calling，程序化工具调用）**：工具不是丢给模型一堆 JSON schema 让它一个个调，而是渲染成真正的 Python 模块，让模型写代码去 import 和组合。

`render_module()` 生成模块源码，`render_manifest()` 生成清单。这条路径上**所有标识符都过 `safe_identifier()`**——不合法就抛 `UnsafeDefinition`，不做"清洗后凑合用"。凑合出来的名字会在生成的代码里静默变成别的东西。

### 8.2 信任级别

```python
class Trust(StrEnum):
    BUILTIN = "builtin"   # 平台自带
    TENANT = "tenant"     # 租户接入的
```

租户提供的工具定义走额外校验（`_validate_tenant_definition`）。

### 8.3 沙箱路径守卫

```python
class Workspace:
    def resolve(self, path: str) -> str:
        """先规范化，再判定。"""
```

**规范化必须在判定之前**。反过来的话，`a/../../etc/passwd` 这种路径在归一之前看着像在工作区里。

拒绝分四类，各自带可执行的建议：

| `Refusal` | 含义 |
|---|---|
| `OUTSIDE_WORKSPACE` | 逃出工作区 |
| `RESERVED_PATH` | 命中保留路径 |
| `NOT_ON_DISK` | 不在磁盘上 |
| `SECRET_MATERIAL` | 命中密钥物料 |

---

## 9. 流式协议

### 9.1 事件类型

十种，刻意保持小：

`metadata` · `text` · `reasoning` · `tool_call` · `tool_result` · `artifact` · `usage` · `notice` · `error` · `done`

判据是"客户端会不会渲染得不一样"。客户端一视同仁的东西应该是已有类型上的一个字段，不是一个新类型。

### 9.2 三条不变量

`EventStream` 在产生端强制，`Transcript.validate()` 在存储前复核：

1. **`metadata` 先于任何内容**
2. **恰好一个终止事件**（`done` 或 `error`），无论成功、失败还是取消
3. **序号连续**——客户端能区分"断了"和"在等"

### 9.3 真流式，不是攒完再放

`api/stream/bridge.py` 里，一轮对话作为独立 task 跑，帧产生即发出。

**背压策略：丢内容事件，绝不丢结构事件。**读得慢的客户端丢的是 token，不是那条告诉它"这轮结束了"的帧。

**放弃迭代器会取消背后的对话**，而不是留着它为没人跑完。

### 9.4 断线续传

`ResumePoint.from_request()` **先读 query 参数，再读 `Last-Event-ID` 头**。

原因很具体：带鉴权的流必须用 `fetch` 读（`EventSource` 发不了 `Authorization` 头），而 `fetch` 不会自动发送标准规定的那个头。只认头的实现，在真实的鉴权场景下续传永远失效。

重放（`replay`）是**先整体渲染再发送**的——一份违反顺序契约的记录应该在这里以 500 报出来，而不是变成一段畸形的流，让客户端渲染成一段坏掉的对话。

---

## 10. 数据模型

| 实体 | 作用 | 作用域 |
|---|---|---|
| `Tenant` | 账户 | — |
| `Member` | 账户内的人 | — |
| `Workspace` | 工作区 | 租户 + 归属人 |
| `Thread` | 会话 | 租户 + 归属人 |
| `Turn` | 一轮对话及其 token 记账 | 租户 |
| `ModelPreference` | 角色 → 模型 | 租户（`(tenant, role)` 唯一） |
| `ProviderCredential` | 租户自带的 key（加密存储，不索引） | 租户（`(tenant, provider)` 唯一） |
| `UsageQuota` | 周期额度 | 租户（`(tenant, period)` 唯一） |

两个 mixin：`ScopedEntity` 挂 `tenant_id`，`TimestampMixin` 挂创建/更新时间。

**列类型选可移植的**，因为测试套件跑在内存 SQLite 上——验证租户隔离不应该需要先把一个数据库立起来。

迁移用 Alembic，放在 `kairos/migrations/`（**包内**，不是包旁边），这样打出来的 wheel 自带与之匹配的 schema 历史。迁移写声明式，不写 `op.execute()` 裸 SQL。

---

## 11. API 层

### 11.1 中间件顺序

注册顺序是**反的执行顺序**，所以租户中间件最后注册、最先执行——它下游的一切（包括所有路由）都可以依赖作用域已经建立。

```python
app.add_middleware(CORSMiddleware, ...)          # 后执行
app.add_middleware(TenantScopeMiddleware, ...)   # 先执行
```

CORS 的 `allow_methods` 写显式列表而不是通配符：通配符会把框架以后新增的方法也一并放行，绕过审查。

### 11.2 身份校验

```python
class ClaimsMapper:   # JWT claims → TenantScope
class IdentityResolver:
    #  SOLO   → 固定 scope
    #  HOSTED → Bearer token / service token
```

JWT **签发方（issuer）、受众（audience）、算法（algorithms）全部显式校验**。只验签名和受众的话，同一个身份提供商签出的、受众恰好匹配的任意 token 都会被接受。

公开路径是**白名单**（`DEFAULT_PUBLIC_PATHS = /health, /docs, /openapi.json`），其余一律需要鉴权。默认关闭、显式开放。

### 11.3 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/threads` | 列会话（仓储自动按归属过滤） |
| `GET` | `/api/v1/threads/{id}` | 单个会话 |
| `POST` | `/api/v1/threads/{id}/messages` | 提问，**SSE 流式返回** |
| `GET` | `/api/v1/threads/{id}/messages/replay` | 重放已结束的一轮 |
| `DELETE` | `/api/v1/threads/{id}` | 删会话 |
| `GET` | `/api/v1/models` | 本租户可选模型 |
| `GET` | `/api/v1/preferences` | 各角色当前模型 + **是谁决定的** |
| `PUT` `DELETE` | `/api/v1/preferences/{role}` | 设置 / 清除覆盖 |
| `GET` | `/api/v1/usage` | 已消耗 token |
| `GET` | `/health` | 存活探针 |

两条贯穿全部路由的规则：

- **能拒绝请求的检查，全部发生在响应体开始之前。**一旦开始流式输出，状态行已经发出去了，之后才抛的拒绝只能变成 200 里的一个 error 事件——客户端分不清它和"这轮真的失败了"。
- **不存在和无权访问返回同样的 404。**区分开等于告诉探测者哪些 id 是真的。

### 11.4 依赖注入

路由声明自己需要什么（`get_engine` / `get_repositories` / `get_selection`），由 `runtime/app.py` 决定用什么满足。测试塞假的，部署塞真的，路由两边都不知道。

`Container` 的数据库引擎**懒建**：构造一个应用对象不该要求装好数据库驱动、连得上数据库。进程必须能起到"报告自己连不上数据库"的程度。

---

## 12. 前端

React 19 · Vite 7 · TanStack Query 5 · Tailwind 3 · TypeScript strict。

```
web/src/
├── api/          axios 实例：重试、token 刷新、错误整形
├── components/
│   ├── ui/         基础件（cva 管变体）
│   └── chat/       对话业务件
├── contexts/     主题
├── hooks/        数据钩子（会话、流、模型设置）
├── lib/
│   ├── stream/     SSE 读取与重连
│   ├── turn/       一轮对话的客户端状态
│   └── queryKeys.ts  全部缓存键的唯一来源
├── pages/        Chat / Threads / Settings
├── types/        服务端返回的形状 + 逐字段收窄
└── styles/       设计令牌（CSS 变量）
```

### 12.1 流用 `fetch` 读，不用 `EventSource`

`EventSource` 发不了 `Authorization` 头。所以流走 `fetch` + `ReadableStream` 手动读帧，普通请求走 axios——两条路各管各的：axios 管重试和 token 刷新，流读取器管分帧和重连。

### 12.2 网络边界逐字段收窄

`types/api.ts` 里 `asTurnEvent()` **一个字段一个字段地建**，跨网络边界没有 `as` 断言：

```typescript
case 'tool_call':
  return {
    kind: 'tool_call', seq,
    payload: {
      call_id: str(data, 'call_id'),
      name: str(data, 'name'),
      arguments: record(data, 'arguments'),
    },
  }
```

整体断言能编译通过，然后把一个缺字段的 payload 交给界面，三个组件之后才以 undefined 的形式炸出来。

未知事件类型返回 `null` 而不是报错：新版服务端可能发老客户端没见过的类型，忽略一个总好过整个崩掉。

### 12.3 缓存键只有一个来源

`lib/queryKeys.ts` 是全部键的工厂。失效是**按前缀**工作的，写在调用点的键**没有任何东西能成组失效它**——而且失败是静默的：mutation 成功了，缓存留着旧值，界面一直显示过期数据直到别的什么恰好触发了重取。

### 12.4 重连的三条游标规则

1. 子任务事件**不推进**共享游标
2. 重放**永不写入**游标
3. `Generation` 守卫丢弃被取代的那次尝试的帧

第三条管的是"重连过程中又断了"：没有它，两段历史会交错。

### 12.5 主题

`data-theme` 属性 + CSS 变量。Tailwind 配置里 `darkMode: ['selector', '[data-theme="dark"]']` —— **变体选择器必须和实际写入的属性对上**，否则每一个 `dark:` 工具类都编译成一个永不匹配的选择器。

三档（亮/暗/跟随系统）而不是二档开关：两态开关表达不了"跟随系统"，选一次就再也回不去，而跟随系统恰恰是多数人想要的状态。

---

## 13. 配置

单棵 Pydantic Settings 树，环境变量注入：

```python
Settings
├── deployment          SOLO | HOSTED
├── database            连接串、连接池
├── cache               Redis 地址、TTL
├── auth                JWKS、issuer、audience、算法、solo 身份、service token
├── quota               开关、周期额度、预留倍率、耗尽行为
├── resilience          熔断三参数、重试与退避
└── rate_limit          全局/租户/用户并发上限
```

一处定义，一处读取。配置分散在多个文件里，最后一定会出现两处说法不一致而没人知道哪个生效。

---

## 14. 质量门禁与当前进度

### 门禁

| 项 | 状态 |
|---|---|
| 后端测试 | **729 通过**，内存 SQLite，不需要任何外部服务 |
| 前端测试 | **140 通过** |
| 类型检查 | mypy `strict = true` / tsc `strict` 双向干净 |
| 架构边界 | import-linter 契约 + 读 AST 的架构测试 |
| 前端 lint | type-aware 规则（浮空 promise、unsafe 断言、hooks 依赖）；**不管格式** |

不管格式是有意的：两个工具在花括号该放哪儿上吵架，产生的噪音会训练出"闭眼跑 `--fix`"的习惯，而那正是让真问题溜过去的习惯。

### 已建成

**平台**：租户隔离 · 登录服务 session 认证 · 模型目录与选型链 · 配额两阶段 · 熔断与降级 · 声明式管线 · 流式协议与重放 · 持久化与迁移 · 四种线协议 · 工具注册表与沙箱 · 组装根

**交易**：风控信封 · 订单 outbox · worker 订单只读访问 · 持仓推导

**服务接入**：analyst / billing 两个 HTTP 客户端，按 `(租户, 服务)` 维度熔断

**前端**：登录 · 对话 · 会话列表 · 下单与持仓 · 分析师卡片 · 会员充值 · 设置

### 未建成

| 项 | 状态 |
|---|---|
| **outbox relay 进程** | `OutboxRelay` 已写、已用假 publisher 测过；缺的是真 publisher 和一个跑它的常驻进程。这是下单链路上唯一还断着的一环 |
| 供应商 HTTP 客户端 | 编码层已完成，`Transport` 实现未接 |
| Redis 缓存适配器 | 配置项已有，`adapters/cache/` 未建。**键构造器必须强制前置租户段**——HTTP 层漏判时，缓存层要能自然落空，构成纵深防御。注意：分析师数据**不在此列**，那一层缓存归 analyst-service 自己，平台再加一层就是第二个要失效的东西 |
| MCP 工具接入 | 注册表已支持，接入层未建 |
| WebSocket | 行情推送用，`api/ws/` 未建 |
| 长期记忆与用户资料库 | 未建 |
| 前端 Dashboard / MarketView | 未建 |
| **凭据解密** | **有意留空**——抛 `NotImplementedError` 而不是返回明文。接密钥管理应该是个显眼的缺口，不该是一个"看起来能用、直到有人去读存储"的东西 |

---

*本文档随实现推进修订。与代码不一致时以代码为准。*
