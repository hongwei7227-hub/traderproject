
## 0. 术语与分层

| 术语 | 含义 |
|---|---|
| **清单（manifest）** | 随代码发布的两个静态 JSON：`models.json`（模型目录）+ `providers.json`（供应商目录 + 定价目录）。进程启动后只读一次，全局单例缓存。 |
| **model key** | `models.json` 的顶层字典键。系统内部**唯一的模型标识符**，用户偏好、YAML、API 请求参数、角色配置都存这个值。 |
| **model_id** | 真正发给上游 API 的模型名。与 model key **多对一**（79 个 key 中约 50 个 key ≠ model_id）。 |
| **provider slug** | 展平后的供应商标识（如 `openai`、`claude-oauth`、`dashscope-coding`）。 |
| **变体（variant）** | 同一品牌下不同接入路径（区域 / 协议 / 计划）的 provider slug。 |
| **ModelSource** | 模型来源：`system`（清单内）/ `custom`（用户自定义）/ `unknown`。 |
| **CredentialSource** | 实际付款凭据：`oauth` / `byok` / `platform` / `none`。与 ModelSource **正交**。 |

层次关系（自下而上）：

```
清单层 (models.json + providers.json)              静态、进程级单例
   ↓
工厂层 (create_llm / create_llm_from_custom)       无状态，每次调用构造一个 client
   ↓
解析层 (resolve_llm_config)                        每请求执行，产出配置副本
   ↓
韧性层 (ModelResilience)                           每次模型调用执行，重试 + 降级
```

## 1. 模型清单的数据模型

### 1.1 models.json

顶层结构：`{ "<model key>": <ModelEntry> }`，扁平字典，无嵌套分组。79 条（含 3 条 embedding 与 chat 模型混在同一命名空间）。

#### ModelEntry 字段表

| 字段 | 类型 | 必填 | 运行时消费 vs 纯展示 | 语义 |
|---|---|---|---|---|
| `model_id` | string | **必填** | **运行时** | 发给上游 API 的模型名 |
| `provider` | string | **必填** | **运行时** | provider slug，必须能在展平后的 `provider_config` 查到。决定 SDK / base_url / env_key / headers |
| `visible` | bool | 默认 `false` | **运行时（目录过滤）** | 是否出现在用户可选列表。`false` 仍可被显式按名构造。当前 13 条不可见 |
| `parameters` | object | 默认 `{}` | **运行时（核心）** | 直接展开进 client 构造参数。深拷贝后使用。见 §1.1.1 |
| `extra_body` | object | 默认 `{}` | **运行时（核心）** | 供应商私有请求体字段。按 SDK 以 `extra_body=` 或 `model_kwargs["extra_body"]` 注入。深拷贝后使用 |
| `input_modalities` | string[] | 事实必填 | **运行时** | 取值集合 `text`/`image`/`pdf`/`video`/`audio`。缺失默认 `["text"]`。**不变量：必须包含 `text`** |
| `context` | int | 可选 | **运行时 + 展示** | 上下文窗口 token 数。运行时：①Anthropic PDF 页数上限判定（≥1_000_000 → 600 页，否则 100）；②GLM 路径覆盖 vendored profile 的 `max_input_tokens` |
| `speed` | int 1–5 | 可选 | **纯展示** | 编辑性速度评分 |
| `intelligence` | int 1–5 | 可选 | **纯展示** | 编辑性能力评分 |
| `system_provider` | string | 可选 | **运行时** | 平台代理路由。**没有** BYOK key 时把 `provider` 改写为该值；有 key 时保持原值。当前清单未使用，但实现必须支持 |
| `tier` | int | 可选 | **运行时（平台计划门控）** | 平台管理的档位。缺失 = 非平台托管。当前未使用 |
| `oauth_plans` | string[] | 可选 | **运行时（门控）** | OAuth 订阅计划白名单。`plan_type`（小写比较）必须在列表内否则 400。**`plan_type` 未知时放行（fail-open）** |
| `additional_betas` | string[] | 可选 | **运行时** | 追加到 `anthropic-beta` 请求头（与 provider 级 header 逗号拼接，空段过滤） |
| `display_name` | string | 可选 | **死字段** | 无任何代码读取，UI 用 model key 做展示名。见 §7 A2 |
| `context_window` | int | 可选 | **死字段** | 与同条目 `context` 重复，无消费者。见 §7 A3 |

> **必填最小集**：`model_id` + `provider`
> **实践必填集（chat）**：`model_id` + `provider` + `input_modalities` + `visible` + `context`

#### 1.1.1 parameters 的语义

`parameters` 既是**推理模式的探测依据**，也是 **client 构造参数**。观察到的键：

| 键 | 次数 | 归属 SDK | 作用 |
|---|---|---|---|
| `max_tokens` | 28 | anthropic / glm | 最大输出 token；同时映射为 GLM profile 的 `max_output_tokens` |
| `thinking` | 28 | anthropic：`{type: "adaptive"\|"enabled", display, budget_tokens}` | 思考模式 |
| `output_config` | 11 | anthropic adaptive：`{effort: low\|medium\|high\|xhigh}` | adaptive 强度控制 |
| `reasoning` | 9 | openai / codex：`{effort, summary}` | Responses API 推理配置 |
| `use_previous_response_id` | 8 | openai | 模型级覆盖（provider 级同名键才是真正生效路径，见 §7 B3） |
| `reasoning_effort` | 6 | vLLM / Groq / Cerebras / OpenRouter | 扁平推理强度 |
| `include` | 5 | codex：`["reasoning.encrypted_content"]` | Responses API 返回字段 |
| `store` | 5 | codex：`false` | 无状态调用 |
| `extra_body` | 4 | openrouter / deepinfra | **嵌套在 parameters 里**的 extra_body（与顶层是两条不同路径，见 §7 A9） |
| `prompt_cache_options` | 3 | openai：`{mode: "implicit"}` | 非官方 OpenAI 端点会被剥离 |
| `include_thoughts` / `thinking_level` | 3 / 3 | gemini | Gemini 3.x 思考控制 |
| `dimensions` | 3 | embedding | 向量维度 |
| `enable_caching` | 历史 | anthropic | **不是** client 参数，构造时必须过滤；仅供缓存逻辑读取 |
| `default_headers` | 历史 | 全部 | 若出现，与 provider 级 headers **合并**（模型级优先），不得整体覆盖 |

顶层 `extra_body` 观察到的键：`enable_thinking`（14，dashscope/qwen）、`thinking`（12，volcengine/z-ai）、`reasoning_effort`（2，GLM 5.2+）。

#### 1.1.2 model key ↔ model_id 多对一

同一 `model_id` 被多个 key 引用以区分**接入路径**。命名约定（重新实现应保持）：

- `-oauth` → provider 指向 `claude-oauth` / `codex-oauth`
- `-cn` → 国内区域变体
- `-intl` → 国际区域变体
- `-coding` → coding-plan 变体
- `-anthropic` → 同一模型的 Anthropic 协议入口
- `-groq` / `-deepinfra` → 同一开源模型的不同托管商

### 1.2 providers.json

顶层四区块：

```
embedding_models       { provider: [PricedModel] }   仅定价/展示
models                 { provider: [PricedModel] }   仅定价/展示
provider_config        { group: ProviderGroup }      运行时核心
infrastructure_pricing {}                            当前空
credit_conversion      { usd_to_credits_rate: 1000 }
```

> **关键认知**：`models` / `embedding_models` 区块**不参与模型解析**，只是定价目录。运行时唯一消费点是「按模型算价 / 算 1–5 档价格档位」。`models.json` 才是运行时目录。两者**手工同步**，天然漂移（见 §7 A1）。

#### pricing 的三种形态

1. **扁平**：`{input, cached_input?, cache_5m?, cache_1h?, output, cache_storage?, output_image?, unit}`
2. **分层（按输入长度）**：`{input_tiers: [{max_tokens|null, rate, ...}], output_tiers: [...], output_pricing_mode: "input_dependent", unit}`
   - `max_tokens: null` = 无上限的最后一档
   - `output_pricing_mode: "input_dependent"` = **输出单价由输入长度决定**
3. **二维矩阵**：`{pricing_mode: "2d_matrix", discount, matrix: [{input_max|null, output_max|null, input, output, cached_input}], unit}`

`unit` 恒为 `per_1m_tokens`。

#### 1.2.1 provider_config 结构

```
ProviderGroup = { ...ProviderFields, "variants"?: { slug: ProviderFields } }
```

| 字段 | 类型 | 语义 | 运行时消费 |
|---|---|---|---|
| `sdk` | `openai\|anthropic\|gemini\|deepseek\|glm\|qwq\|codex` | 客户端分派键。展平后必填（`platform: true` 豁免） | 是 |
| `base_url` | string | 端点。支持 `{HOST_IP}` 占位符（仅部分 SDK 路径替换，见 §7 B1） | 是 |
| `env_key` | string \| null | 平台密钥环境变量名。`null` = 无平台密钥 | 是 |
| `access_type` | `api_key\|oauth\|coding_plan\|local` | `oauth` 触发 OAuth 客户端；`local` 允许无 key 降级为 `"EMPTY"`；`coding_plan` 与 `api_key` 在构造层等价 | 是 |
| `byok_eligible` | bool | 是否允许存用户 key。**读取默认 `true`**（缺失即可用） | 是 |
| `platform` | bool | 平台专用代理。被排除出：BYOK 列表、子变体遍历、供应商目录、SDK 校验 | 是 |
| `parent_provider` | string | 显式父级。变体默认由展平设为组名；显式声明可指向非组名的兄弟 | 是 |
| `use_response_api` | bool | OpenAI Responses API | 是（sdk ∈ {openai, codex}） |
| `use_previous_response_id` | bool | Responses API 有状态链式 | 是（仅 sdk == openai） |
| `prompt_cache_key` | bool | 会话 cache_key 作为 `prompt_cache_key` 发送 | 是（仅 openai；codex 恒开） |
| `default_headers` | object | provider 级默认请求头 | 是 |
| `region` | `cn\|intl` | 区域标记 | 仅 UI 分组 |
| `display_name` | string | 展示名 | 是 |
| `dynamic_models` | bool | 模型列表由端点动态发现 | UI |
| `reasoning_compat_group` | string | 推理签名兼容组，同组间 reasoning block 可互认 | 是 |

### 1.3 变体继承机制（展平算法）

清单以「分组」书写，运行时必须**展平成扁平字典** `flat[slug] -> ProviderFields`。

对每个 `(group_key, config)`：

1. `variants = config["variants"]`（**不得原地删除**，原始清单要保持不变以供 UI 分组）；`shared = config 去掉 variants`
2. 无 variants → `flat[group_key] = shared`，结束
3. 有 variants：
   - `has_self_variant = (group_key ∈ variants)`
   - 对每个 `(vkey, overrides)`：`merged = {**shared, **overrides}`（**浅合并，一层覆盖**；`default_headers` 等嵌套对象整体替换，非深合并）
     - `vkey != group_key` → `merged.setdefault("parent_provider", group_key)`（**setdefault**，变体显式声明优先）
     - `flat[vkey] = merged`
   - `not has_self_variant` → 额外写 `flat[group_key] = shared`
4. **展平后校验**：跳过 `platform: true`；其余每条必须含 `sdk`，否则抛错

**两种书写范式**：

- **Pattern A — 组名本身是完整供应商**，变体只覆盖差异。
  例：`openai`（组级带 sdk + base_url + env_key）+ 变体 `codex-oauth`。
  结果：`flat["openai"] = shared`（无 parent_provider），`flat["codex-oauth"] = shared ∪ overrides ∪ {parent_provider: "openai"}`
- **Pattern B — 组名只是品牌容器**，默认变体与组名同名。
  例：`dashscope` 组级只有 env_key/access_type/region/display_name（**没有 sdk**），变体含 `dashscope`(self) / `dashscope-intl` / `dashscope-coding`。
  结果：
  - `flat["dashscope"]` = 组级 ∪ self → `sdk: openai`，兼容模式 base_url，**无 parent_provider**
  - `flat["dashscope-intl"]` = `sdk: openai`，新加坡 base_url，`env_key: DASHSCOPE_API_KEY_INT`，`parent_provider: dashscope`
  - `flat["dashscope-coding"]` = **`sdk: anthropic`**（协议都变了），coding base_url，`env_key: DASHSCOPE_API_KEY_CODING`，`access_type: coding_plan`，`parent_provider: dashscope`

**嵌套变体**：`z-ai-cn-coding` 显式声明 `parent_provider: "z-ai-cn"`，形成两级树。展平仍是一趟。

**派生索引（展平后一次性预计算，属聊天热路径）**：
`child_variants: dict[parent -> [slug]]`——遍历 `flat` 取 `parent_provider`；跳过缺失、跳过自指、**跳过 `platform: true`**。

**派生查询语义**：

- `get_provider_info(slug)` → `flat.get(slug, {})`（未知返回空 dict，不报错）
- `get_parent_provider(slug)` → `parent_provider` 或自身
- `get_child_variants(slug)` → O(1) 查索引
- `get_display_name(slug)` → 自身 → 父级 → `parent.title()` → `slug.title()`
- `get_byok_eligible_providers()` → `byok_eligible` 默认 **true** 且非 platform

### 1.4 派生的模型元数据接口

`get_model_metadata()` → **只含 `visible: true`**。每条：

| 键 | 来源 | 条件 |
|---|---|---|
| `sdk` | provider_info.sdk（缺失 → `"unknown"`） | 恒有 |
| `provider` | model.provider（缺失 → `"unknown"`） | 恒有 |
| `access_type` | provider_info（缺失 → `"api_key"`） | 恒有 |
| `tier` / `oauth_plans` | 同名字段 | 仅显式存在时 |
| `speed`/`intelligence`/`context`/`input_modalities` | 同名字段 | 仅存在时 |
| `price` | **实时**从 providers.json 定价推导的 1–5 档位（含父级回退） | 仅能算出时 |
| `requires_own_key` | 字符串 `"true"`（**不是布尔**，见 §7 B10） | provider 有 parent 且 env_key 与父级不同 |

`get_configured_llm_models()` → `{parent_provider: [model_key]}`，只含 visible，异常时返回 `{}` 不抛。

## 2. SDK 分派规则

`provider.sdk` 有 **7 种**取值：

| sdk | 客户端 | base_url 参数名 | HOST_IP 替换 | 固定参数 | 特殊行为 |
|---|---|---|---|---|---|
| `openai` | ChatOpenAI | `base_url` | ✅ | `stream_usage=True`, `max_retries=5`, `timeout=600` | `use_response_api` → `output_version="responses/v1"`；headers 合并；**非官方端点强制剥离 `prompt_cache_options`**；`extra_body=` 直传 |
| `codex` | ChatCodexOpenAI（自研） | `base_url` | ✅ | `streaming=True`, `stream_usage=True`, `max_retries=5`, `timeout=600` | 强制注入 `originator` + `User-Agent`（否则新模型 404）；**恒开** prompt_cache_key；由 cache_key 派生会话亲和头（大小写不敏感去重，已钉住的优先）；**强制 pop `prompt_cache_options`**（会 400） |
| `anthropic` | ChatAnthropic，`access_type == "oauth"` 时用 ChatAnthropicOAuth | `base_url` | ❌ | `streaming=True`, `max_tokens=32000`（可被覆盖）, `max_retries=5`, `timeout=600` | 构造前**过滤 `parameters["enable_caching"]`**；`extra_body` 走 `model_kwargs`；OAuth 变体把 api_key 重定向为 `Authorization: Bearer`（`sk-ant-oat*` 不能走 `X-Api-Key`） |
| `gemini` | ChatGoogleGenerativeAI | `base_url` | ❌ | `timeout=600`（无 stream_usage/max_retries） | `extra_body` 走 `model_kwargs` |
| `deepseek` | ChatDeepSeek | **`api_base`** | ✅ | 同 openai | `extra_body=` 直传 |
| `glm` | vendored ChatZai | `base_url` | ✅ | 同上 | 构造后**用清单值覆盖包内 profile**：`context → max_input_tokens`、`parameters.max_tokens → max_output_tokens`、`input_modalities → 各模态布尔位` |
| `qwq` | ChatQwen | **`api_base`** | ✅ | 同上 | 当前清单无 provider 使用，但实现必须保留 |

未知 sdk → 抛 `ValueError`。

### 2.1 base_url 参与方式

- 取值链：`provider_info["base_url"]` → 调用方传 `base_url_override` 则整体替换（用**哨兵对象**区分「未传」与「传 None」）。传 `None` = 清空，用 SDK 默认端点
- `{HOST_IP}` 在 openai/codex/deepseek/glm/qwq 路径替换（本地推理：`:1234/v1` / `:8000/v1` / `:11434/v1`）
- 为空则该参数**完全不传**

### 2.2 env_key 参与方式（api_key 解析）

1. **BYOK/OAuth 覆盖**：`api_key_override` 非 None 且非空字符串 → 直接返回（空字符串视为「未提供」，为本地供应商留口）
2. **环境变量**：`env_key` 非空 → `os.getenv(env_key)`，有值返回
3. **无值**：`access_type != "local"` → 抛 `ValueError`；否则继续
4. **兜底**：返回 `"EMPTY"`

### 2.3 客户端元数据标记

每个 client 必须在 `metadata` 上**合并**（不覆盖既有键）：

| 键 | 值 | 用途 |
|---|---|---|
| `billing_type` | `oauth`（override 且 access_type==oauth）/ `byok`（有 override）/ `platform`（无） | 逐调用计费归属 |
| `provider_route` | `base_url` 与清单不同 → `f"{provider}@{base_url}"`；否则 `provider` | 推理签名血缘。`@` 是保留分隔符。off-manifest 端点必须自成一系 |
| `manifest_model` | **model key**（不是 model_id） | 中间件读模态能力时必须从"手上这个 client"取（韧性中间件会替换 client） |

> `provider` 打标时读的是 **system_provider 改写之后**的值。不能依赖 LangChain 自带的 `model_provider`（ChatAnthropic 对所有兼容 shim 都报 `"anthropic"`）。

### 2.4 两条构造入口

| 入口 | 输入 | 差异 |
|---|---|---|
| `create_llm(model_key, ...)` | 清单模型 | 查 models.json，找不到抛 ValueError；支持 `reasoning_effort`；`default_headers` 在构造前浅合并 |
| `create_llm_from_custom(config, ...)` | 内联 dict | **跳过 models.json**；provider 未知时 sdk 兜底 `"openai"`；`_use_response_api` 显式覆盖；**不支持 `reasoning_effort`**（见 §7 B4） |

两者最终都返回**已构造的 client**。

### 2.5 推理强度覆盖

输入 `level ∈ {low, medium, high, xhigh}`（其他值 no-op），就地修改 `parameters` 与 `extra_body`。**探测式**——按模型现有的键判断推理范式。

`parameters` 分支（**互斥 if/elif 链**，第一个命中）：

1. `reasoning` ∈ params → `reasoning.effort = clamp(level)`
2. `output_config` ∈ params **或** `thinking.type == "adaptive"` → `output_config.effort = level`（**唯一原生支持 xhigh，不 clamp**）
3. `thinking` ∈ params → `budget_tokens`：low=5000 / medium=10000 / high=32000（xhigh 先 clamp）
4. `thinking_level` ∈ params → `clamp(level)`（Gemini 3.x）
5. `thinking_budget` ∈ params → low=1024 / medium=8192 / high=32768（Gemini 2.x）
6. `reasoning_effort` ∈ params → `clamp(level)`

`extra_body` 分支（**独立三个 if，可同时命中**，与 parameters 分支并行）：

- `thinking` ∈ eb → `.type = "disabled" if low else "enabled"`
- `enable_thinking` ∈ eb → `(level != "low")`
- `reasoning_effort` ∈ eb → GLM：low→`none`, medium→`medium`, high→`high`, xhigh→`max`

`clamp(level)` = `"high" if level == "xhigh" else level`

### 2.6 其他清单派生行为

- `should_enable_caching(key)` → `parameters.enable_caching`，默认 false，异常返回 false
- `get_input_modalities(key, custom?)` → 传了 custom 直接返回；未知模型返回 `["text"]`（**失败关闭**：宁可丢图也不发出会被拒的请求）
- `get_max_pdf_pages(key)` → 每请求 PDF 页数上限：
  - anthropic 系（`anthropic`/`claude-oauth`/`doubao-anthropic`）→ `context >= 1_000_000 ? 600 : 100`
  - `gemini` → 1000
  - openai 系（`openai`/`codex-oauth`）→ `None`（无页数限制）
  - **其他/未知 → 100**（取最紧的已知上限，猜宽会变成调用方无法恢复的 400）
- `narrow_prompt_cache_key(client, suffix)` → `model_copy` 且 `prompt_cache_key = f"{parent}:{suffix}"`。把并行子任务分散到不同缓存分片（OpenAI 按 `prefix + prompt_cache_key` 桶限流 ~15 RPM）。suffix 为空 / 非 BaseChatModel / 原本没有该键 → **no-op 返回原对象**。**Codex 的会话亲和头故意不 narrow**
- `ensure_model_in_manifest(key)` → 不在清单抛用户友好文案（指向 Settings）

## 3. 模型解析的完整优先级链

### 3.1 四层数据源

| 层 | 来源 | 读取时机 | 覆盖语义 |
|---|---|---|---|
| **L4 清单** | models.json / providers.json | **进程首次使用时**加载一次，类级单例，永不失效 | 只提供「模型是否存在 + 怎么构造」 |
| **L3 YAML** | `agent_config.yaml` 的 `llm:` 段 | **进程启动时**加载进基线配置（跨请求共享） | 系统默认。可为 `null` / 字符串（等价 `{name: str}`）/ dict（`name` 必填）。dict 下 `compaction`/`fetch` 为空则**回退到 `flash`** |
| **L2 用户偏好** | DB `user_preferences.other_preference` | **每请求**一次读取（带缓存），本次解析内复用 | 覆盖 YAML |
| **L1 请求级** | `request_model` / `reasoning_effort` / `fast_mode` / `enabled_subagents` | 每请求 | 最高 |

### 3.2 用户偏好键

| 键 | 映射到 | 备注 |
|---|---|---|
| `preferred_model` | `llm.name`（mode=ptc） | |
| `preferred_flash_model` | `llm.flash`（mode=flash） | |
| `compaction_model` | `llm.compaction` | 新键 |
| `summarization_model` | `llm.compaction` | 旧键，**先应用**，故新键在两者同存时获胜 |
| `fetch_model` | `llm.fetch` | |
| `fallback_models` | `llm.fallback` | `is not None` 才覆盖（空列表也会覆盖） |
| `custom_models` | 自定义模型列表 | 每项 `{name, model_id, provider, parameters?, extra_body?, input_modalities?}` |
| `custom_providers` | 自定义子供应商 | 每项 `{name, parent_provider, use_response_api?}` |
| `reasoning_effort` / `fast_mode` | 推理强度 / 优先档位 | 请求级优先 |
| `compaction_profile` | 预设批量覆盖 | |
| `search_provider` / `search_depth` | 搜索能力 | 受平台档位门控 |
| `feature_overrides` | 特性开关覆盖 | |

压缩预设：

| 名称 | token_threshold | truncate_args_trigger | keep_messages |
|---|---|---|---|
| aggressive | 100000 | 30 | 5 |
| moderate | 130000 | 40 | 8 |
| extended | 200000 | 60 | 10 |
| relaxed | 300000 | 70 | 15 |

### 3.3 解析时序

输入 `(base_config, user_id, request_model, is_byok?, mode, reasoning_effort?, fast_mode?, thread_id?, enabled_subagents?)`，输出配置**副本**（永不修改基线）。

模式映射：`{"ptc": ("name", "preferred_model"), "flash": ("flash", "preferred_flash_model")}`

1. **BYOK 自解析**：`is_byok is None` → 查 DB（`byok_enabled=TRUE` 且至少一把 key；Redis 缓存，写 key 时失效）
2. **读偏好**：一次读，贯穿全流程
3. **subagent 列表**：请求级 → 配置默认
4. **特性开关**：**无条件 COW 后写入**（所以副本总会发生，见 §7 C1）
5. **模型名解析三分支**：
   - 配置为 null → `resolved = request_model or 偏好`；都空则抛 `ValueError`。新建 LLMConfig：`name = resolved`（ptc）或**字面量 `"placeholder"`**（flash，见 §7 C3）；`compaction = compaction_model or summarization_model or preferred_flash_model`
   - `request_model` 存在 → 直接设，置 `llm_client = None`
   - 否则偏好存在 → 设，置 `llm_client = None`；都无则保持 YAML 默认
6. **其他角色覆盖**（顺序敏感）：`summarization_model → compaction`，然后 `compaction_model → compaction`，然后 `fetch_model → fetch`
7. **fallback 覆盖**：`is not None` 时
8. **平台档位懒解析**：仅 `HOST_MODE == "platform"` 门控；极性统一为 `not is_platform or tier >= min_tier`（OSS/BYOK 永不门控）
9. **搜索偏好**：未知值告警忽略；档位不足忽略；`search_depth` 必须针对**最终生效的 provider** 校验
10. **crawl 能力**：特性开启但档位不足 → 就地关闭
11. **compaction profile**：命中预设逐字段覆盖
12. **算 effective_model**
13. **分类**：**自定义优先**。先查 `custom_models` → `(CUSTOM, cm)`；否则查清单 → `(SYSTEM, entry)`；否则 `(UNKNOWN, {})`。共用**扁平命名空间**，同名时自定义**遮蔽**内置（为了让用户把内置名路由到自己的变体）
14. `UNKNOWN` 时再查是否是 **custom provider slug**（用户把 provider 名当模型名填了）
15. **自定义必须有 BYOK**：否则抛 400 `byok_key_required`
16. **陈旧模型回收**：`UNKNOWN` 且非 custom provider：
    - 来自五个标量偏好键之一 → 执行偏好清洗，抛 400 `model_removed`（列出一并清掉的名字）
    - 来自 `request_model` → 直接抛 400（不清洗）
    - 来自 YAML 默认 → **放行**，让下游错误暴露配置 bug
17. **自定义模态** → 配置
18. **推理强度**：请求级 → 偏好 → `None`
19. **fast mode**：请求级 → 偏好 → `None`；`service_tier = "priority" if fast else None`
20. **构建角色表**（一次，供预取与解析共用）
21. **STEP 0 — BYOK 批量预取**（仅 is_byok 时）
22. **主模型解析** → 写 `credential_source`（**即使 NONE 也写**，它是下游信用门/物化门/计费的唯一真相源）
23. **cache_key**：`thread_id` 存在且与现值不同 → 设置
24. **并发收尾**：`gather(角色 client 解析, fallback client 解析)`——写入不相交字段

### 3.4 陈旧偏好清洗

1. 先失效缓存并**重读**（避免覆盖并发的 Settings 保存）
2. 「可解析」= 名字为空（未设置）或 ∈ `custom_models` ∪ `custom_providers` ∪ `models.json` 键
3. 五个标量键中不可解析的 → 置 `None`（merge-upsert 语义 = 删除）
4. `fallback_models` 逐项过滤；过滤后为空则整体置 `None`（不留 `[]`）
5. 有变更才写 DB，写后再次失效缓存
6. 返回 `[(key, name)]` 供错误文案

> 已知竞态：重读与 upsert 之间仍有窗口。设计上接受（窗口极窄且自愈）。见 §7 C5

### 3.5 Copy-on-write

**怎么做**：基线配置是**进程级共享**对象。闭包 `_cow()`：

```
if config is base_config:          # 身份比较，不是相等比较
    config = config.model_copy(deep=True)
```

每个**将要写入 config 的分支**在写前调用，幂等。

**为什么必需**：

1. 基线被所有并发请求共享，任何写入都会把 A 用户的偏好泄漏给 B
2. 配置上挂着可变运行时字段（`llm_client`、各 client 容器、`features` dict），必须每请求独占
3. **深**拷贝必需：`llm` / `compaction` 是嵌套模型，浅拷贝会直接改到共享对象上

**同类纪律（清单侧）**：构造时对 `parameters` 和 `extra_body` 做**深拷贝**。原因相同——推理强度覆盖会改嵌套 dict，浅拷贝会污染进程级单例清单。

**角色 client 复制纪律**：`client_for_role` 返回 `.model_copy()`（浅即可），因为角色本地会就地改属性（compaction 设 `streaming=False`），不复制会破坏主 client 的 SSE 流。

## 4. 角色体系

### 4.1 角色槽位

| 槽位 | 模型来源 | 消费方 | 默认回退 |
|---|---|---|---|
| **主模型** | `llm.name`（ptc）/ `llm.flash`（flash） | 主 agent | 无（必须成功） |
| `flash` | `llm.flash` | Flash agent（独立 agent，非 subsidiary 槽位） | 空 → `llm.name` |
| `compaction` | `llm.compaction` | 压缩中间件 | YAML 层：空 → flash。运行时：**有专用模型** → 角色 client 或 `None`（平台用户走廉价按名路径）；**无专用模型** → 主 client 副本 |
| `fetch` | `llm.fetch` | Web fetch 工具（contextvar 注入） | YAML 层：空 → flash。运行时：无角色 client 则工具自行按名构造 |
| `subagent:<name>` | subagent 定义的 `model` | Subagent 编译器 | `fallback_to_main=False`——无角色 client 则退回**字符串模型名**交给框架解析；再无则继承父 agent |
| `fallback[]` | `llm.fallback`（有序） | 韧性中间件 | 见 §6 |

### 4.2 角色注册表

```
LLMRole = { key, model, fallback_to_main=True, service_tier=None }
```

构建顺序：`compaction` → `fetch` → 每个启用 subagent（若定义有 model）→ **过滤掉 model 为假值的项**。

subagent 注册表**构建失败必须捕获并降级为「无 subagent 角色」**，不能炸掉整个请求。

> 优先服务档位（`service_tier="priority"`）是**主模型专属**，角色与 fallback 一律 `None`。

### 4.3 并发模型

```
STEP 0: BYOK 批量预取（1 次 DB 查询，串行前置）
   ↓
主模型解析（串行，后续要靠 credential_source 决定物化策略）
   ↓
gather(
   角色解析 ── 内部 gather(每角色一协程)
   fallback 解析 ── 内部 gather(每 fallback 一协程)
)
```

**并发不变量**：

- 角色与 fallback 写入**不相交**字段，可并发
- 角色内部：I/O 并发，**所有写入在 gather 之后串行**——SSE 热路径只等一个 round-trip 而非 N 个
- 单角色失败必须捕获、记 error、返回 `None` 跳过，**不得中断其他角色**
- fallback 并发解析但**按声明顺序追加**，保持降级优先级

**角色 client 写入规则**：

1. 有 client → 写入容器
2. 无 client 且 `model_source != SYSTEM` → 告警「自定义模型无可用 key，回退默认」
3. 无 client 且 SYSTEM → 静默（走按名路径）

**BYOK-pure 写时物化**（写入之后）：

条件：`credential_source ∈ {OAUTH, BYOK}` **且** 主 client 非 None。
动作：对每个 `fallback_to_main=True` 且尚无角色 client 的角色，写入主 client 的副本，记 info 日志「成本转移到主模型费率」。

理由：BYOK/OAuth 用户的所有调用必须走**用户自己的凭据**，绝不能让某个角色偷偷落到平台 key。而 PLATFORM/NONE 用户**什么都不存**，让廉价的按名懒构造路径保持生效。显式 `is not None` 检查把不变量变成守卫而非假设。

**读取**（`client_for_role(role, fallback_to_main=False)`）：

1. 容器命中 → `.model_copy()`
2. `fallback_to_main=False` → `None`
3. 主 client 存在 → 主 client 副本；否则 `None`

`resolve_compaction_client` = `client_for_role("compaction", fallback_to_main = not bool(llm.compaction))`——**配了专用 compaction 模型就不回退主 client**；没配才回退。

## 5. BYOK 凭据解析

### 5.1 存储契约

`user_api_keys` 按 **provider slug** 存 `{provider, api_key(加密), base_url(可空)}`。所有读取必须 JOIN `users.byok_enabled = TRUE`。

两个原语：`get_byok_config_for_provider(user, provider)` / `get_byok_configs_for_providers(user, [providers])`（缺失的不在结果里）。

### 5.2 候选 slug 顺序

给定 provider slug，按**固定优先级**产出（去重保序）：

1. `provider` 自己
2. `provider` 的所有**子变体**（嵌套变体先于父级家族）
3. `parent`（若 != provider）
4. `root` 的所有子变体（即**兄弟变体**）

平台变体永远被排除（BYOK key 绝不存在那儿）。

此函数必须被 **key 查找**与 **STEP-0 预取**共用，否则两者会漂移。

### 5.3 遍历规则

返回 `(byok_config, holding_slug)`——第一个有 key 的候选。

**三态缓存契约**（关键，容易实现错）：

| 缓存状态 | 含义 | 行为 |
|---|---|---|
| slug → dict | 确认有 key | 直接用 |
| slug → `None` | **确认无 key** | 视为无，不再查 |
| slug **不在缓存里** | 从未预取 | **必须直接查库**，不得当成「无 key」 |

未命中的 slug 直查后**回写缓存**。完全没传缓存时走兼容路径：一次性批量查全部候选。

### 5.4 系统模型的 BYOK 解析

1. 分类 → SYSTEM，取 `provider`
2. 遍历候选 → `(byok_config, holding)`
3. 无 key → `None`
4. **base_url 优先级**：`byok_config.base_url` → **模型自己 provider 的 base_url**
   - ⚠️ **不是** holding 的 base_url，**不是**父级的
   - 这是「coding 变体修复」：一个 `dashscope-coding` 模型（anthropic SDK）即使 key 存在父级 `dashscope`（openai SDK）下，也**必须**打到 anthropic coding 端点
5. 构造 client

### 5.5 自定义模型的 BYOK 解析

按序三条链：

1. **模型名本身是 custom provider slug** → `parent = cp.parent_provider`；`base_url = byok.base_url or manifest[parent].base_url`；应用 SDK 继承
2. **自定义模型的 `provider` 字段是 custom provider slug** → 同上
3. **系统 provider 扇出** → `base_url = byok.base_url or manifest[holding].base_url`（**注意用 holding，与系统模型路径相反**，见 §7 C7）
   - 若 `holding != provider`：**把 custom_config 的 `provider` 改写为 holding**。因为构造时从 provider 字段读 SDK / headers / use_response_api——一个标了 `dashscope` 的自定义模型若通过 `dashscope-coding` 解析出 key，SDK 必须跟着换成 anthropic，否则会构造出指向 Anthropic 形状 URL 的 Qwen client，每次必挂

### 5.6 自定义 provider 的 SDK 继承

用户自定义 slug 不在清单 → provider_info 为空 → sdk 兜底 `"openai"` → 打 Anthropic 形状端点会 404。

规则：

- `manifest[parent].sdk` **不在** `{None, "openai"}` → 把 `config["provider"]` 改写为 parent（继承正确 SDK + headers）
- 父级是 openai（或未知）→ **不改写**。因为默认已是 openai，而继承清单里的 openai 条目会把 `use_response_api` / `prompt_cache_key` 强加给只会说 `/chat/completions` 的兼容网关（vLLM / LiteLLM / OpenRouter）
- 无论哪支，自定义 provider 自己声明的 `use_response_api` 都显式透传

### 5.7 OAuth 解析

**独立于 BYOK 开关，且优先于 BYOK 尝试**。

1. 模型必须在清单，否则 `None`
2. `access_type != "oauth"` → `None`
3. 按 provider 分派 token 服务
4. 无 token → 抛 400 `oauth_required`
5. **计划门控**：有 `oauth_plans` 且 `plan_type` 非空且不在白名单（小写比较）→ 抛 400 `oauth_plan_unsupported`。`plan_type` 为空/未知 → **放行**
6. token 非字符串或为空 → 记 error 返回 `None`
7. Codex 路径若有 account_id 则加对应头
8. `service_tier` 只在**非 claude-oauth** 时透传

### 5.8 统一原语

```
resolve_model_client(user, model, *, is_byok, cache_key, reasoning_effort,
                     service_tier, allow_platform_fallback, caches)
  -> { client, model_source, credential_source }
```

顺序：

1. 分类 → model_source
2. **OAuth 优先**（无条件尝试，与 is_byok 无关）→ `(client, source, OAUTH)`；`oauth_required` 允许冒泡
3. is_byok 时尝试 BYOK → `(client, source, BYOK)`
4. `allow_platform_fallback` **且** SYSTEM → 平台构造 → `(client, source, PLATFORM)`
   - `service_tier` 故意**不**传（priority 只给 OAuth 路径）
5. 否则 `(None, source, NONE)`

各调用点的 `allow_platform_fallback`：

| 调用点 | 值 | 理由 |
|---|---|---|
| 主模型 | `bool(effective_reasoning)` | 有推理强度覆盖就必须预构造（否则覆盖无处安放）；无则留 None 走懒路径 |
| 角色 | `False` | 角色永远不拿平台 client |
| fallback | `True` | SYSTEM fallback 名不能被静默丢弃 |

### 5.9 STEP-0 批量预取

**纯性能优化，绝不能成为正确性依赖。**

1. 仅 is_byok 时执行
2. 候选模型 = `[effective_model] + 各角色 model + fallback 列表`
3. 逐个分类（用偏好缓存，无额外 DB 读）
4. 每个 provider 展开候选 slug 汇入 set
5. 空集 → `{}`
6. **一次**批量查询
7. 返回时**显式为未命中的 slug 写 `None`**，这样三态缓存才能区分「确认无」与「未预取」
8. **任何异常** → warning，返回 `{}`（退化为直查，正确但未优化）

### 5.10 用户可见的失败形态

| 类型 | HTTP | detail.type | 触发条件 | CTA |
|---|---|---|---|---|
| BYOK 缺失 | 400 | `byok_key_required` | 选了自定义模型/provider 但 BYOK 关闭或找不到 key | `/settings?tab=model` |
| 模型已下架 | 400 | `model_removed` | 偏好或请求参数里的模型名已不存在 | 同上，文案列出一并清掉的名字 |
| 需要连接账号 | 400 | `oauth_required` | OAuth 模型但没连账号 | `/setup/method` |
| 计划不支持 | 400 | `oauth_plan_unsupported` | plan_type 不在白名单 | 无 |

统一形状：`{message, type, link?: {url, label}}`，前端渲染成单条带 CTA 的横幅。

> **分层日志纪律**：底层找不到 key 只记 **debug**，由上层决定抛 400 还是记 warning——用户只应看到一条错误，不是两条。

## 6. 重试与降级

### 6.1 两级重试（嵌套）

```
韧性中间件         每候选最多 max_retries+1 = 4 次
  └─ SDK 层        max_retries = 5  →  最多 6 次 HTTP
```

⚠️ **相乘**：单候选最坏 24 次上游请求；三候选最坏 72 次。见 §7 D1。

### 6.2 中间件参数

| 参数 | 取值 | 语义 |
|---|---|---|
| `max_retries` | 3 | 每候选**额外**重试次数（总调用 4） |
| `backoff_factor` | 2.0 | 指数底 |
| `initial_delay` | 1.0 s | |
| `max_delay` | 60.0 s | 上限 |
| `jitter` | True | ±25% 均匀抖动，钳到 ≥0 |

延迟：`delay = initial_delay * backoff_factor ** retry_number`（`backoff_factor == 0` 时取 initial），再 `min(max_delay)`，再抖动。`retry_number` 从 0 开始。

**一个实例被主栈和 subagent 栈共享**，所有循环状态必须是**调用本地**的（不得存实例属性）。

### 6.3 候选链构造

优先级：

1. 预解析的 fallback client 非空 → 若名字列表存在且**长度相等** 则 zip；否则用展示名兜底
2. 否则 `llm.fallback` 名列表 → 逐个按名构造（走平台 key）
3. 都无 → `[]`

展示名 = 第一个非空字符串属性，顺序 `model` → `model_name` → `model_id`，全空则类名。

完整候选链 = `[(主模型展示名, None), *fallbacks]`。首项 client 为 `None` 表示「用请求自带的 model，不替换」。

### 6.4 错误分类

状态码提取：沿异常链（`__cause__` 优先，其次 `__context__`，带 id 去环）两轮扫描：

1. 找 `status_code` 或 `response.status_code`（int）
2. 对每个异常的 `str()` 正则匹配 `\b([45]\d{2})\b`
3. 都无 → `None`

可重试判定：`status not in {400, 401, 403, 404, 405, 413, 422}`。
**`None` 视为可重试**（连接重置、超时）。**408 和 429 故意不在不可重试集里**（瞬时）。

### 6.5 单次失败决策

返回 `(status, Recovery | None)`：

1. 可重试且 `attempts <= max_retries` → `Recovery(delay=...)`
2. 未升级过且 `status == 400` → 尝试**推理载荷升级修复**：剥离所有 reasoning/thinking 块
   - 剥离后 messages **是同一对象**（无可剥离）→ `None`（不浪费一次调用重发同样请求）
   - 否则 → `Recovery(request=stripped)`，置 `escalated=True`（每候选只升级一次）
3. 否则 → `None`（放弃该候选）

**为什么结构化判定而非文案匹配**：供应商错误措辞会随 API 版本漂移，匹配文案会静默失效。代价是「无关的 400 恰好碰上历史里有 reasoning」时浪费一次调用——而这轮本来就已在失败。

**为什么原地重试安全**：请求校验类 400 在建流阶段抛出，此时还没有 chunk 发出、没有 token 计费。

**为什么不顺便关掉 thinking**：实测在 thinking 开启下重放一个被剥离了 thinking 块的助手轮次是被接受的。关掉什么也换不来，反而会掩盖真正的配置类 400（例如 `max_tokens < thinking.budget_tokens`，关掉后会"成功"，把应当暴露的错误变成一个悄悄没有思考的回答）。

### 6.6 主循环

```
candidates = [(主模型名, None), *fallbacks]
records = []
for index, (name, client) in enumerate(candidates):
    req = request if client is None else request.override(model=client)
    attempts = 0; escalated = False
    while True:
        attempts += 1
        try:  return await handler(req)
        except Exception as exc:
            status, recovery = decide(exc, attempts, req, escalated)
            if recovery is None:
                records.append(Record(name, exc, status, attempts)); break
            emit_retry(...)
            if recovery.request: req, escalated = recovery.request, True
            elif recovery.delay > 0: await sleep(recovery.delay)
    if index + 1 < len(candidates):
        emit_fallback(records[-1], candidates[index+1][0], from_is_primary=(index == 0))
raise_exhausted(records)
```

### 6.7 客户端可见事件

**`model_retry`**（实时 stream 事件）：
`{ type, model, attempt, max_retries, error(≤300字符), status_code, delay_seconds }`
`attempt` = 迄今**失败**的调用次数；即将发生的是第 `attempt+1` 次。

**`model_fallback`**（走可回放的 UI 消息通道，**不是** stream writer）：
`{ from_model, to_model, from_is_primary, error(≤300字符), status_code, attempts_on_from }`

为什么用可回放通道：降级事件**必须能在回放中重现**。它双写到实时 SSE 和 checkpoint 的 ui 通道。重试事件不需要回放。

**两者都必须容错**：流写入器/运行时上下文在非流式调用中不存在，此时记 debug 跳过——**韧性逻辑本身不得依赖事件发射**。

### 6.8 全部耗尽

1. `primary = records[0]`
2. 构造 trace：`{model, attempted_models: [{model, error, status_code, attempts}]}`
3. 尝试把 trace 挂到**主模型的异常**上；带 `__slots__` 的异常会拒绝，静默忽略
4. 记 warning
5. **抛出主模型的异常**（不是最后一个 fallback 的）——用户看到的必须是他配置的那个模型的错误

### 6.9 中间件栈嵌套顺序

靠前 = 更外层：

```
LargeResultEviction
SubAgent
...
ImageCapture            ← 外于韧性：沙箱图片只在最终响应捕获一次
Compaction              ← 外于韧性：压缩基于原始请求，不随重试重复
ModelResilience         ◄── 重试/降级边界
Multimodal              ← 内：按【降级后】的模型剥离模态
AnthropicPromptCaching  ← 内：provider 专属断点绝不能泄漏到另一 provider
OpenAIPromptCaching     ← 内：同上
EmptyToolCallRetry
PatchToolCalls
WorkspaceContext / RuntimeContext  ← 最内：在缓存断点之后追加动态内容
ReasoningCompatibility  ← 最内
```

**关键理由**：

- `Multimodal` 必须在韧性**内**，否则支持视觉的主模型降级到纯文本候选时会重放 image/PDF 块，正好吃到降级本要避免的那个 400
- 提示缓存中间件必须在韧性**内**，因为断点是 provider 专属标记，绝不能带到另一个 provider
- `ImageCapture` / `Compaction` 必须在韧性**外**，避免每次重试重复执行

subagent 栈以 steering 中间件**打头**（follow-up 必须先于其他中间件可见），其余同构。

### 6.10 软依赖降级汇总

| 依赖 | 不可用时 |
|---|---|
| stream writer | debug，跳过 retry 事件；调用照常 |
| UI 消息通道 | debug，跳过 fallback 事件；调用照常 |
| 异常拒绝设属性 | 跳过 trace；异常照常抛出 |
| BYOK 预取失败 | warning，返回 `{}`，退化为直查 |
| subagent 注册表构建失败 | error，角色列表为空 |
| 单角色解析异常 | error，跳过该角色 |
| 单 fallback 解析异常 | error，names/clients 同步跳过保持索引对齐 |
| fallback 是 CUSTOM/UNKNOWN 且无 key | warning「加 key 以启用」，跳过 |
| Redis 不可用 | 静默穿透到 DB |

## 7. 已知缺陷（新实现必须修复，勿复刻）

### A. 清单数据模型

| # | 缺陷 | 影响 | 修复方向 |
|---|---|---|---|
| A1 | **两套模型目录**手工同步，已漂移——定价目录缺 `claude-oauth`/`codex-oauth`/`doubao-anthropic`/`deepinfra`/`z-ai-cn-coding`/`dashscope-coding` 等 | 变体定价只能靠父级回退；`deepinfra` 父级是 `openrouter` 而后者模型列表为空 → 价格档位恒 `None` | 合并为单一目录，或按 `(group, model_id)` 索引并 CI 校验双向覆盖 |
| A2 | 死字段 `display_name` | 误导维护者 | 删除，或在元数据里真正返回 |
| A3 | 死字段 `context_window`，与 `context` 重复 | 「哪个才是真的」的歧义 | 删除，只留 `context` |
| A4 | **embedding 与 chat 共用命名空间**，只靠 `visible: false` 隐藏。按 embedding 名构造会得到一个 **Chat** 客户端 | 类型混淆，随时可能误路由 | 拆独立目录或加 `kind` 判别字段并在工厂层强制 |
| A5 | **`byok_eligible` 默认 `true`** | 与那些显式声明的条目自相矛盾；OAuth 变体不该出现在 BYOK 列表 | 默认改 `false`，显式声明；OAuth/local 硬排除 |
| A6 | `lm-studio` 的 `env_key` 是字面量而非环境变量名，靠 `access_type == "local"` 兜底才没炸 | env_key 语义被滥用 | 本地供应商一律 `env_key: null` |
| A7 | 变体浅合并导致 `region` 语义脏（coding 变体继承了 `cn` 没覆盖） | 当前无运行时影响，但按 region 分组会分错 | 变体显式声明所有区分性字段 |
| A8 | `default_headers` 整体替换而非深合并 | 变体想加一个头就得重复整份 | 对已知 map 型字段深合并 |
| A9 | `parameters.extra_body` 与顶层 `extra_body` 是**两条不同路径** | 同一语义两种写法，后写覆盖前写且行为不一致 | 统一为顶层，构造时合并 |
| A10 | `sdk: "qwq"` 是死分支；`openrouter` 的 sdk 被设成 `"deepseek"` | 死分支 + 反直觉映射 | 清理；sdk 名改为描述**协议**而非供应商（`openai-chat` / `openai-responses` / `anthropic-messages`） |

### B. 工厂层

| # | 缺陷 | 影响 | 修复方向 |
|---|---|---|---|
| B1 | **`{HOST_IP}` 替换只在部分 SDK 路径生效**，anthropic / gemini 直接用原值 | anthropic-SDK 的本地端点写 `{HOST_IP}` 会原样发出 | 所有分支统一走同一个解析函数 |
| B2 | **PDF 页数上限硬编码 provider 集合**，遗漏了同为 anthropic SDK 的 moonshot / minimax / deepseek / 各 coding 变体 | 落到兜底 100 页（保守可用），但新增 anthropic 变体会静默错判 | 改为按 `sdk` 派发 |
| B3 | `use_previous_response_id` 只对 openai 生效不含 codex；三个同族开关三套 SDK 条件 | 难以推理 | 抽成「SDK → 支持的开关」能力表 |
| B4 | **自定义模型入口不接受 `reasoning_effort`** | 用户调了强度对自定义模型无效且无提示 | 统一签名 |
| B5 | 自定义入口返回已构造 client，而主入口返回工厂对象 | 同名工厂两种返回类型，极易误用 | 统一：构造器只建工厂，`get_llm()` 才产 client |
| B6 | 自定义入口用 `__new__` 逐属性拼装，绕过 `__init__` | 新增实例属性可能在这条路径缺失 | 共用私有初始化方法 |
| B7 | `parameters.update(override)` 是浅合并 | 只想改一个子键就得复制整份 | 对已知 map 型深合并，或明确文档化 |
| B8 | 清单是**类属性单例，无失效机制** | 改动必须重启；测试之间串味 | 提供 `reload()` 或依赖注入 |
| B9 | 失败时用 `print()` 而非 logger | 生产日志丢失 | 用 logger |
| B10 | `requires_own_key` 的值是**字符串 `"true"`** 而非布尔 | 前端要做字符串判真 | 用布尔 |
| B11 | 「偏离清单」判定在两者都是 `None` 时误判为「未偏离」 | 边界正确性存疑 | 用显式的「是否发生过 override」标志 |

### C. 解析层

| # | 缺陷 | 影响 | 修复方向 |
|---|---|---|---|
| C1 | **COW 实际退化为「总是拷贝」**（特性开关写入前无条件调用） | 优化名存实亡 | 要么承认并简化为「入口即拷贝」，要么把特性开关挪到不需要 COW 的载体 |
| C2 | **深拷贝会连运行时字段一起拷**，包括持有 httpx 连接池的客户端。`Field(exclude=True)` 只影响序列化不影响拷贝 | 每请求深拷贝活跃 HTTP 客户端：昂贵，且可能破坏连接复用或产生状态分裂 | 先浅拷贝配置树，再显式重置运行时字段 |
| C3 | **flash bootstrap 把 `llm.name` 设成字面量 `"placeholder"`** | 所有读该字段的下游（模态解析、韧性中间件展示名、PDF 上限）拿到不存在的模型名 → 模态退化为 text、上限退化为 100、错误消息出现 "placeholder" | 用 `None` 或直接用 flash 模型名 |
| C4 | **角色与 fallback 都不传 `reasoning_effort`** | 用户设了强度只有主模型生效 | 显式决策：全传，或文档写明「仅作用于主模型」 |
| C5 | 陈旧偏好清洗存在**写-写竞态** | 罕见但会丢用户设置 | 条件更新（带版本号）或 CTE 单语句读改写 |
| C6 | UNKNOWN 归因检查排除了 fallback 列表，但清洗动作会连它一起过滤 | 只在 fallback 里的失效模型不会触发清洗 | 单独提供 fallback 校验入口 |
| C7 | **系统模型与自定义模型的 base_url 选取规则相反**（前者用模型自己的 provider，后者第 3 条链用 holding） | 两条路径心智模型不一致，容易改错 | 统一规则并在文档给出理由 |
| C8 | **BYOK 兄弟遍历可能拿错 key**：coding 变体在自己 slug 无 key 时退到父级，但两者 env_key 和域名都不同 | 用父级 key 打 coding 端点大概率 401，用户看到认证错误而非「请为 coding 计划单独配 key」 | env_key 不同时不做跨 slug 复用，或给定向错误提示 |
| C9 | fallback 平台回退会构造必然失败的 client（部署没有该环境变量时抛错被吞） | 每请求刷 error 日志 | 构造前先检查 env_key 是否可解析 |
| C10 | **角色物化会静默换模型**（成本按主模型费率计），只有 info 日志 | 账单意外 | 通过事件告知，或 Settings 预先说明 |
| C11 | 解析函数近 350 行，混杂模型解析/特性开关/搜索偏好/crawl 门控/压缩预设五类关注点 | 难测试难演进 | 拆成 resolver 管线，每个关注点一个纯函数 |
| C12 | 预取在 for 循环里逐个 await | 形式上串行 | 改同步函数或 gather |

### D. 韧性层

| # | 缺陷 | 影响 | 修复方向 |
|---|---|---|---|
| D1 | **两级重试相乘**：单候选最坏 24 次上游请求，3 候选 72 次。一个 5xx 抖动被放大成小型压测 | 成本、限流、延迟全面失控 | SDK 层设 0/1 只留中间件重试，或反之 |
| D2 | **429 与 5xx 用同一套退避，不读 `Retry-After`** | 被限流时退避可能远短于服务端要求，加剧限流 | 429 优先采用响应头 |
| D3 | **状态码提取会正则匹配任意 4xx/5xx 数字** | 错误消息里的模型名、token 数、价格可能被误判成状态码 | 只信结构化属性；正则兜底严格限定 `status code: NNN` 上下文 |
| D4 | 主模型展示名在 `primary_client is None` 时无条件返回主模型名，而该实例被 subagent 栈共享 | subagent 的事件会错误标成主模型名 | 始终以 `request.model` 推导 |
| D5 | **升级修复不重置重试计数** | 修复后的请求可用重试次数被前面的失败吃掉 | 升级后重置，或把升级建模成新候选 |
| D6 | 耗尽时 records 为空会 IndexError | 理论不可达，但是隐式不变量 | 加显式守卫 |
| D7 | 同步孪生是死代码，里面的 `sleep` 若被误调用会阻塞事件循环 | 双份逻辑 + 踩雷风险 | 删除或 `raise NotImplementedError` |
| D8 | 名称兜底拿到的是 model_id 而非 model key | 降级事件里的名字与 Settings 显示对不上 | 名称与 client 必须成对产出 |
| D9 | `records[-1]` 依赖循环内 break 的位置 | 脆弱耦合 | 用显式变量承载「上一候选的失败记录」 |
| D10 | **降级不做能力预检**，只靠内层中间件事后剥离 | 剥离有损（用户上传的图直接消失且无提示） | 构造降级链时按 `input_modalities` 过滤，或剥离时发用户可见事件 |
| D11 | 主模型异常被就地打属性 | 修改了别人拥有的对象；`__slots__` 异常静默丢 trace | 包一层自有异常类型承载 |
| D12 | 错误摘要硬截断 300 字符，不做结构化提取 | 上游 error code 可能被截掉 | 先提结构化字段，正文再截断 |
| D13 | **中间件相对顺序靠列表位置的注释维持**，无机制化约束 | 插错一行就引入「降级后重放图片」这类难查 bug | 声明 `must_be_inside/outside` 并在装配时校验 |

### E. 跨层

| # | 缺陷 | 修复方向 |
|---|---|---|
| E1 | 模块移动后旧路径的 `.pyc` 仍在工作区，测试目录仍是旧层级 | 清理缓存，同步测试目录，加入 ignore |
| E2 | 两个来源枚举分居不同层，类型标注被迫用 `Any` 避免循环导入 | 提到共享 domain 模块 |
| E3 | 日志器名硬编码，模块移动后名实不符 | 用 `__name__` 并调整日志路由 |
| E4 | 「模型不可用」判定散落四处，规则可能漂移 | 收敛到单一解析函数 |

## 8. 重新实现的验收清单

1. 清单展平：Pattern A / B 两种范式都正确产出 flat，platform 变体豁免 sdk 校验，非 platform 缺 sdk 必须抛错
2. 每个模型条目的 provider 都能解析出非空 provider_info、含 sdk、且含 base_url 或 env_key 之一
3. 每个 chat 模型的 `input_modalities` 非空且含 `"text"`
4. coding 变体的定价能通过父级回退解析；`get_parent_provider` 正确
5. `system_provider` 分叉：无 key → 改写；有 key → 保持
6. 元数据只含 visible；`tier` 仅显式存在时出现
7. platform 变体不出现在：BYOK 列表、子变体列表、供应商目录、允许的 provider 集合
8. 优先级链正确，且 `summarization_model` 先于 `compaction_model` 应用
9. **基线配置在任何路径下都不被修改**（用解析后比较原对象快照验证）
10. BYOK 三态缓存：未预取的 slug 必须触发直查
11. 系统模型的 BYOK：key 可来自兄弟变体，但 base_url 必须来自模型自己的 provider
12. 韧性：不可重试状态码直接跳下一候选；408/429/无状态码可重试；400 + 历史含 reasoning → 剥离后重试一次
13. 全部耗尽时抛出的是**主模型**的异常，且带 trace
14. 降级事件走可回放通道，重试事件走实时通道；两者都不可用时调用照常成功
