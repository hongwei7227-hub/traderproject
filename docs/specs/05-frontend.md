# 规格 05：前端行为规格（kairos-trader / web）
>
> **本文描述"必须具备的行为"，不是"现有代码的形状"。** 标 `【缺陷】` 的地方新实现必须修。

## 目录

0. [技术栈与工程约定](#0-技术栈与工程约定)
1. [路由表](#1-路由表)
2. [页面清单](#2-页面清单)
3. [状态管理](#3-状态管理)
4. [API 客户端层（含 SSE / WebSocket）](#4-api-客户端层含-sse--websocket)
5. [组件体系](#5-组件体系)
6. [Dashboard widget 系统](#6-dashboard-widget-系统)
7. [国际化、主题、样式方案](#7-国际化主题样式方案)
8. [已知问题清单](#8-已知问题清单新实现该修的)

---

## 0. 技术栈与工程约定

### 0.1 依赖清单（按职责分组）

| 职责 | 库 | 版本 | 说明 |
|---|---|---|---|
| 框架 | `react` / `react-dom` | ^19.2 | 使用 `createRoot`；无 RSC |
| 路由 | `react-router-dom` | ^6.21 | `BrowserRouter`，开启 `future: { v7_startTransition, v7_relativeSplatPath }` |
| 服务端状态 | `@tanstack/react-query` | ^5.90 | 唯一的服务端状态源，**禁止把服务端数据镜像进 local state** |
| HTTP | `axios` | ^1.7 | 只用于普通 REST；**SSE 不走 axios** |
| 认证 | `@supabase/supabase-js` + `@supabase/ssr` | ^2.110 / ^0.10 | 仅 platform 模式启用 |
| 样式 | `tailwindcss` 3 + `clsx` + `tailwind-merge` + `class-variance-authority` | — | `cn()` 合并类名 |
| 无障碍原语 | `@radix-ui/react-*`（context-menu / dialog / dropdown-menu / hover-card / popover / toast / tooltip）+ `react-aria-components` | — | shadcn 风格封装在 `components/ui/` |
| 动画 | `framer-motion` | ^12.34 | 全局 `<MotionConfig reducedMotion="user">` |
| 图表 | `lightweight-charts` ^4.1（K 线）、`recharts` ^3.7（统计图） | — | 两套并存，用途不同 |
| 网格布局 | `react-grid-layout` ^2.2 | — | Dashboard widget 网格 |
| 拖拽排序 | `@dnd-kit/core` + `sortable` + `utilities` | — | 工作区/自选列表排序 |
| Markdown | `react-markdown` ^9 + `remark-gfm` + `remark-math` + `remark-cjk-friendly` + `rehype-katex` + `rehype-raw` + `rehype-sanitize` + `katex` | — | 聊天正文渲染 |
| 代码高亮 | `react-syntax-highlighter` ^16 | — | 懒加载 |
| 代码编辑 | `@monaco-editor/react` ^4.7 + `monaco-editor` | — | 文件面板编辑器 |
| 文档查看 | `react-pdf` + `pdfjs-dist` 5.4、`exceljs` ^4.4（带 patch）、`pagedjs` ^0.4、`react-to-print`、`html2canvas` | — | PDF/Excel/打印导出 |
| 国际化 | `i18next` ^25 + `react-i18next` ^16 | — | en-US / zh-CN |
| 校验 | `zod` ^4.3 | — | **只校验持久化 / 用户输入，不校验 API 响应** |
| 图标 | `lucide-react` ^0.562 | — | |

开发依赖关键项：`vite` ^7.3、`typescript` ^5.9、`vitest` ^3.2 + `jsdom` + Testing Library、`@playwright/test` ^1.58、ESLint 9 flat config。包管理器 **pnpm 10.18**（`exceljs@4.4.0` 打了本地 patch；`minimatch`/`dompurify` 有 overrides）。

### 0.2 脚本与门禁

```
pnpm dev        # Vite dev server，127.0.0.1:5173
pnpm build      # tsc --noEmit && vite build   ← 类型检查是构建硬门禁
pnpm typecheck  # tsc --noEmit（CI 门禁）
pnpm lint       # ESLint 9（CI 不门禁，仅提示）
pnpm test       # vitest run
pnpm test:e2e   # playwright test
```

**约定：类型是硬门禁，lint 是软的。** 禁止 `any`，遇到不确定类型用 `unknown` 再收窄。

### 0.3 TypeScript 配置（必须一致）

```jsonc
{
  "target": "ES2022",
  "lib": ["ES2023", "DOM", "DOM.Iterable"],
  "module": "ESNext",
  "moduleResolution": "bundler",
  "jsx": "react-jsx",
  "strict": true,
  "noEmit": true,
  "isolatedModules": true,
  "esModuleInterop": true,
  "skipLibCheck": true,
  "forceConsistentCasingInFileNames": true,
  "resolveJsonModule": true,
  "paths": { "@/*": ["./src/*"] }   // 路径别名 @ → src/，vite.config 与 vitest.config 都要配
}
```

### 0.4 构建配置要点（Vite）

- `base` = `VITE_CDN_BASE || '/'`。
- 入口显式锁定 `index.html`（防止 dev-only 的 `intro-preview.html` 被打进产物）。
- `manualChunks` 手动分包：
  - `vendor-react`：react / react-dom / react-router-dom
  - `vendor-markdown`：react-markdown / remark-gfm / remark-math / remark-cjk-friendly / rehype-katex / rehype-raw / katex
  - `vendor-charts`：recharts / lightweight-charts
  - `vendor-motion`：framer-motion
  - `vendor-dnd`：@dnd-kit/*
- dev server：`host: 127.0.0.1`，proxy `/api/v1` → `VITE_PROXY_BACKEND`（默认 `http://localhost:8000`，`changeOrigin: true`），`/ws/v1` → 同 target 改 `ws://`（`ws: true`）。
- 容器内开发时 `CHOKIDAR_USEPOLLING=true` 才开轮询 watch（bind mount 不转发 fsevents）；原生 `pnpm dev` 保持事件驱动。
- 走 nginx 代理时用 `VITE_HMR_CLIENT_PORT` 指定 HMR 客户端端口，且 HMR socket 路径挪到 `/vite-hmr`（避免与代理根路径 302 冲突）。

### 0.5 环境变量（全部 `VITE_` 前缀，构建期注入）

| 变量 | 默认 | 用途 |
|---|---|---|
| `VITE_HOST_MODE` | `oss` | `platform` → Supabase 真认证；`oss` → 本地开发恒登录 |
| `VITE_API_BASE_URL` | 空（同源） | axios baseURL；SSE 的 `${baseURL}${path}` 也用它 |
| `VITE_PROXY_BACKEND` | `http://localhost:8000` | 仅 dev server 代理目标 |
| `VITE_SUPABASE_URL` | — | **只决定是否构造 Supabase client，不决定模式** |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | — | anon key |
| `VITE_AUTH_USER_ID` | `local-dev-user` | oss 模式的固定 user id |
| `VITE_CDN_BASE` | `/` | 静态资源基址 |
| `VITE_COOKIE_DOMAIN` | 未设（host-only） | 跨子域共享 auth/locale cookie 的父域 |
| `VITE_APP_ENTRY_PATH` | platform=`/app`，oss=`/` | SPA 入口挂载路径 |
| `VITE_PLATFORM_URL` | `/account` | 平台控制台（账户/套餐/集成）链接前缀，**结尾不带斜杠**（调用点直接拼接） |
| `VITE_HMR_CLIENT_PORT` | — | dev only |

> **判定模式一律用 `isPlatformMode`（读 `VITE_HOST_MODE`），绝不要用 `VITE_SUPABASE_URL` 是否存在来判断。**

### 0.6 目录结构约定

```
src/
  main.tsx              入口：QueryClientProvider → BrowserRouter → ThemeProvider → AuthProvider → App + Toaster
  App.tsx               顶层路由 + 已认证外壳（侧栏/主区/底部 tab）
  api/                  跨页共享的 API 客户端（client.ts / features.ts / model.ts）
  components/           通用组件（ui/ 基础原语、model/、Sidebar/、BottomTabBar/、Main/、PageLoading/、nav/）
  config/hostMode.ts    部署模式常量
  contexts/             AuthContext、ThemeContext
  features/             跨页业务片段（analyst-standalone/）
  hooks/                跨页共享的 React Query hook 与 UI hook
  lib/                  纯逻辑库：queryKeys、format、locale、bars/、quotes/、threadLifecycle/、utils…
  locales/              en-US.json / zh-CN.json（各约 2.6k 行）
  pages/<Page>/         页面组：组件 + hooks + utils/api.ts + 局部 store
  styles/               tokens.css（设计令牌）、animations.css
  types/                共享 TS 类型（api/chat/sse/market/automation/platform）
  test/                 vitest setup + 测试工具
```

**分层铁律（新实现必须遵守）**

1. **API 调用只允许出现在 api 层**：页面组的 `utils/api.ts`，或共享 `lib/*` 客户端模块（如 `lib/bars`、`lib/quotes`）。组件里不准直接 `fetch`/`axios`。
2. **服务端状态一律 React Query**，通过 key 前缀失效；不要把服务端数据复制进 `useState`。
3. **横切模块唯一真源**：query key → `lib/queryKeys.ts`（禁止内联 key 数组，会破坏前缀失效）；agent 路径 → `pages/ChatAgent/utils/agentPaths.ts`；locale/格式化 → `lib/locale.ts` + `lib/format.ts`（禁止散落 `Intl.*`）；类名合并 → `cn()`。
4. **Zod 只在"持久化 / 用户输入"边界用**，且只用 `safeParse` + 逐字段 `.catch()`，**永不 throw**；受信的 API 响应用普通 TS interface，不做运行时校验。
5. **活得比 React 长的模块级单例，必须在登出时重置**（见 §3.6）。
6. 测试与被测代码同目录 `__tests__/`。

---

## 1. 路由表

路由分两层：`App.tsx` 的**顶层路由**（决定是否需要登录）与 `components/Main/Main.tsx` 的**应用内路由**（已认证外壳内部）。

### 1.1 顶层路由（App.tsx）

渲染前置：`const { isLoggedIn, isInitialized } = useAuth()`；`isInitialized === false` 时整页渲染 `<PageLoading />`，不渲染任何路由。
整棵 `<Routes>` 包在 `<MotionConfig reducedMotion="user">` 里（全局遵守 `prefers-reduced-motion`，transform/layout 动画降级为瞬时，opacity 仍动画）。

| 路径 | 元素 | 加载策略 | 守卫/重定向 |
|---|---|---|---|
| `APP_ENTRY_PATH`（platform=`/app`，oss=`/`，可被 `VITE_APP_ENTRY_PATH` 覆盖） | 已登录 → `<RootRedirect />`；未登录 → `<LoginPage />` | LoginPage **懒加载**（它带约 2k 行行情画布子系统，登出用户才渲染） | — |
| `/app`（仅当 `isPlatformMode && APP_ENTRY_PATH === '/'`） | `<LegacyAppPathRedirect />` | 静态 | `<Navigate to={{ pathname: '/', search }} replace />`，保留 query |
| `/callback` | `<AuthCallback />` | 静态 | OAuth 回跳处理，见下 |
| `/auth/confirm` | `<AuthConfirm />` | **静态导入**（避免验证时闪一次 chunk 加载） | Supabase 邮件链接落地页（注册确认 / magic link / 恢复） |
| `/reset-password` | `<ResetPassword />` | 懒加载 + `<Suspense fallback={<PageLoading/>}>` | 密码重置表单 |
| `/s/:shareToken` | `<SharedChatView />` | **静态导入** | 公开分享只读会话视图，**无需登录** |
| `/privacy` | `<PrivacyPolicy />` | 懒加载 | 公开 |
| `/legal` | `<Legal />` | 懒加载 | 公开 |
| `/setup/*` | 已登录 → `<SetupWizard />`（懒加载）；未登录 → `<Navigate to={APP_ENTRY_PATH} replace />` | 懒加载 | 需登录 |
| `/*` | 已登录 → `<AuthenticatedShell />`；未登录 → `<Navigate to={APP_ENTRY_PATH} replace />` | — | 需登录 |

**`RootRedirect`**：读 `?redirect=`，若通过 `isSafeRedirect()` 则 `window.location.href = redirectTo`，否则 `<Navigate to="/dashboard" replace />`。

**`isSafeRedirect(target)`**（安全必备，重写必须原样保留）：
```ts
try { return new URL(target, window.location.origin).origin === window.location.origin; }
catch { return false; }
```
用「相对当前 origin 解析后比对 origin」而非前缀匹配 —— 这样才能同时挡住协议相对 URL（`//evil.com/x`）、跨域绝对 URL、以及反斜杠绕过（`/\evil.com` → `//evil.com`）。

**`AuthCallback`（`/callback`）** 两种模式，`useEffect` 在 `isLoggedIn` 为真后执行：
- **弹窗模式**（`window.opener && window.opener !== window`）：向 `BroadcastChannel(AUTH_BROADCAST_CHANNEL)` post `{ type: 'oauth-complete' }`，关闭 channel，`window.close()`。BroadcastChannel 不可用时静默降级（opener 下次 session check 自会拿到 cookie）。
- **顶层模式**（弹窗被拦截时的兜底）：读 `?redirect=` 并做 `isSafeRedirect` 校验后跳转；否则 `navigate('/dashboard', { replace: true })`。
- 渲染期间显示居中文案 `t('auth.signingIn')`。

### 1.2 应用内路由（Main.tsx，位于 `AuthenticatedShell` 内）

全部 **`React.lazy` 懒加载**，外包一层 `<Suspense fallback={<PageLoading variant="pane" />}>`。

| 路径 | 组件 | chunk 名 |
|---|---|---|
| `/dashboard` | `DashboardRouter`（再分流 Classic/Custom） | `dashboard` |
| `/chat` | `ChatAgent`（工作区画廊视图） | `chat` |
| `/chat/:workspaceId` | `ChatAgent`（该工作区的线程画廊） | `chat` |
| `/chat/t/:threadId` | `ChatAgent`（会话视图） | `chat` |
| `/chat/t/:threadId/:taskId` | `ChatAgent`（会话视图 + 直接打开某 subagent 任务详情） | `chat` |
| `/market` | `MarketView` | `market` |
| `/automations` | `Automations` | `automations` |
| `/settings` | `Settings` | `settings` |
| `/news/:id` | `NewsDetailPage` | `news` |
| `*` | `<Navigate to="/dashboard" replace />` | — |

**chunk 预热（`preloadRouteChunk`）**：路由 chunk 的 `import()` thunk 存在一张表里，`React.lazy` 和预热共用同一个 thunk（`import()` 天然去重，两者共享一次网络请求）。`AuthenticatedShell` 挂载时（仅一次）调用 `preloadRouteChunk(window.location.pathname)`：取 `pathname.split('/')[1] || 'dashboard'` 作为 key，未知段落回退预热 dashboard chunk。目的是让「`/users/me` 门禁请求」与「目标路由 chunk 下载」并行，而不是串行。

**页面切换动画**：桌面端用 `<AnimatePresence mode="wait">` 包一层 `motion.div`，`key = pathname.split('/')[1] || 'dashboard'`（所以 `/chat` 子路由共用一个 key，切线程不重放动画），`opacity 0→1`，`duration: 0.15, ease: 'easeInOut'`。**移动端完全跳过 AnimatePresence**（原生手势更跟手）。两种分支都在末尾渲染 `<ContextOverflowPill />`。

`Main` 内还调用 `useSyncUserLocale()`（把服务端偏好里的 locale 同步进 i18n）。

### 1.3 `AuthenticatedShell` 行为

```
useSetupGate() → { isLoading, needsSetup }
  isLoading  → <PageLoading />（避免受保护内容闪现）
  needsSetup → <Navigate to="/setup/method" replace />
否则渲染：
  <OnboardingProvider>
    <ThreadLifecycleFeed />          ← 每个 tab 一条用户级 SSE 连接（§4.5）
    <div class="app-layout">
      {!isMobile && <AppSidebar collapsed width onToggleCollapse onWidthChange />}
      {isMobile && !hideTabBar && <BottomTabBar />}
      <main ref={mainRef} class={`app-main${hideTabBar ? ' app-main--no-tab' : ''}`}><Main /></main>
    </div>
    <OnboardingHostGate />
  </OnboardingProvider>
```

- `hideTabBar = isMobile && pathname.startsWith('/chat/t/')`（移动端进入具体会话时隐藏底部 tab）。
- **每路由滚动记忆**：`useScrollMemory(mainRef, \`route:${location.pathname}\`)`，离开再回来恢复位置；首次访问为 0（位置不会在共用同一滚动容器的路由间串味）。
- **侧栏折叠**：`localStorage['app-sidebar-collapsed']`（`'true'`/其他）。
- **侧栏宽度**：`localStorage['app-sidebar-width']`（数字，读时用 `clampSidebarWidth()` 夹取，非法值回落 `SIDEBAR_DEFAULT_WIDTH`）。
- **拖拽调宽性能约定**：每次 `pointermove` 只写 DOM 自定义属性 `document.documentElement.style.setProperty('--sidebar-width', \`${w}px\`)`，**不 setState**（整棵路由树在这个外壳下）；只有 commit（松手 / 双击复位）才 `setState` + 写 localStorage。注意：即使非 commit 也要写 DOM——否则当 commit 宽度恰好等于当前 state 时 React 会 bail out，effect 不重跑，DOM 会停在最后一次 pointermove 的值。
- `--sidebar-width` **发布在 `document.documentElement` 上**（不是 `.app-layout`）：自定义属性只向下继承，而多个需要避开侧栏的 fixed 浮层（getting-started 卡、dashboard 悬浮聊天框与编辑工具条）是视口锚定、甚至被 portal 移出布局子树的。移动端该值为 `0px`，折叠态 `80px`，展开态实际宽度。`useLayoutEffect` 写入、卸载时 `removeProperty`。

### 1.4 导航项定义（侧栏 + 移动底部 tab 共用唯一真源）

```ts
interface NavItem { key: string; icon: LucideIcon; labelKey: string; match: 'prefix' | 'exact-or-sub' }

NAV_ITEMS = [
  { key: '/dashboard',   icon: LayoutDashboard,   labelKey: 'sidebar.dashboard',   match: 'exact-or-sub' },
  { key: '/chat',        icon: MessagesSquare,    labelKey: 'sidebar.chatAgent',   match: 'prefix' },
  { key: '/market',      icon: ChartCandlestick,  labelKey: 'sidebar.marketView',  match: 'exact-or-sub' },
  { key: '/automations', icon: Timer,             labelKey: 'sidebar.automations', match: 'exact-or-sub' },
]
SETTINGS_ITEM = { key: '/settings', icon: Settings, labelKey: 'sidebar.settings', match: 'exact-or-sub' }
// Settings 只出现在移动端底部 tab；桌面端从 AccountMenu 进入。
```

`useNavActive()` 返回匹配器：`match === 'prefix'` → `pathname.startsWith(key)`；否则 `pathname === key || pathname.startsWith(key + '/')`。

### 1.5 路由守卫汇总

| 守卫 | 位置 | 条件 → 行为 |
|---|---|---|
| 认证门禁 | `App.tsx` `/*` 与 `/setup/*` | `!isLoggedIn` → `Navigate(APP_ENTRY_PATH, replace)` |
| 初始化门禁 | `App.tsx` 顶部 | `!isInitialized` → 整页 `<PageLoading />` |
| Setup 门禁 | `AuthenticatedShell` | `needsSetup` → `Navigate('/setup/method', replace)` |
| Setup 跳过 | `useSetupGate` | `sessionStorage['setup_skipped']` 存在 → 本浏览器会话内不再拦截 |
| 线程访问守卫 | `ChatAgent` | `getThread` 返回 403 → 渲染"无权访问"覆盖层；`shouldLeaveThreadRoute()` 为真 → `navigate('/chat', { replace: true })` |
| 非法 UUID | `ChatAgent` | URL/state 里的 workspaceId 非合法 UUID 一律当作不存在（`isValidUuid`），绝不进 API 调用 |
| `__default__` 兜底 | `ChatAgent` | `threadId === '__default__'` 且解析不出 workspaceId → `navigate('/chat', { replace: true })` |

`useSetupGate()` 判定逻辑：
```ts
if (isLoading || !user) return { isLoading: true, needsSetup: false };
if (sessionStorage.getItem('setup_skipped')) return { isLoading: false, needsSetup: false };
const needsSetup = !user.has_api_key && !user.has_oauth_token && !((user.access_tier ?? -1) >= 0);
```
即：既没配 BYOK key、也没 OAuth token、也没有平台准入等级时才需要走 setup。oss 自托管模式同样会触发，但 SetupWizard 一定提供"退出 setup"按钮（点了写 `sessionStorage['setup_skipped'] = '1'`）。

---

## 2. 页面清单

按 `src/pages/<Page>/` 组织。每个页面组的内部约定统一为：

```
pages/<Page>/
  <Page>.tsx        页面入口（default export）
  components/       页面私有组件
  hooks/            页面私有 hook（含 React Query 封装）
  utils/api.ts      页面私有 API 客户端（【唯一】允许发请求的地方）
  utils/*.ts        纯逻辑
  stores/           页面私有的模块级 store（可选）
  __tests__/        与被测代码同级
```

### 2.0 页面总览

| 页面 | 路由 | 一句话 | 规模 |
|---|---|---|---|
| **ChatAgent** | `/chat`、`/chat/:workspaceId`、`/chat/t/:threadId[/:taskId]` | 多智能体会话主界面：工作区画廊 / 线程画廊 / 会话视图三态 | 约 90 组件，最大单文件 2648 行 |
| **Dashboard** | `/dashboard` | 行情首页，Classic（固定卡片）/ Custom（widget 网格）双形态 | 约 2.6 万行，34 个 widget |
| MarketView | `/market` | K 线工作台 + 图表注解 + 侧边聊天 | `MarketChart.tsx` 2406 行 |
| Automations | `/automations` | 定时/触发式自动化任务 CRUD + 执行历史 | |
| Settings | `/settings` | 四个 tab：账号 / 偏好 / 模型 / 实验 | |
| Setup | `/setup/*` | 六步接入向导 | |
| Login | `APP_ENTRY_PATH` | 登录/注册/魔法链接/找回密码 + 装饰画布 | |
| SharedChat | `/s/:shareToken` | **免登录**只读分享会话 | |
| Detail | `/news/:id` | 单条新闻详情页 | 214 行 |
| Legal | `/legal`、`/privacy` | 纯静态法务页 | 无状态无 API |
| Onboarding | 非路由 | 引导编排（页面 intro / What's New / 新手清单） | |
| OAuth | 非路由 | `CodexCallback.tsx` 14 行**死代码**，仅兼容旧链接 → 无条件跳 `/dashboard`。**新实现直接删除** | |

---

### 2.1 ChatAgent（重点）

#### 2.1.1 入口与视图分流（`ChatAgent.tsx`）

读 `useParams<{ workspaceId, threadId, taskId }>()`，四条路由共用一个组件：

| 条件 | 渲染 |
|---|---|
| 无 `threadId` 且无 `workspaceId` | `WorkspaceGallery`（懒加载） |
| 无 `threadId`，有 `workspaceId` | `ThreadGallery`（懒加载） |
| 有 `threadId` | 从 LRU 缓存取 `ChatView` 实例 |
| 有 `threadId` 且 `getThread` 返回 403 | 访问被拒覆盖层（锁图标 + "去我的会话"） |

**视图深度动画**：`getViewDepth(threadId, workspaceId) → 2 / 1 / 0`，驱动带方向的 `AnimatePresence`。**popstate 导航在移动端把方向置 0**，避免和 iOS 自己的返回手势动画叠加成双重动画。

**工作区解析顺序**（缺一不可）：URL 参数 → `location.state.workspaceId` → `getThread(threadId)` 查询结果。非法 UUID（`isValidUuid`）一律当作不存在，**绝不进 API 调用**。

**会话恢复**：`sessionStorage['chat_session_restore']`，**TTL 5 分钟**。落到 `/chat` 时若有存档会话就立刻往深处导航，让浏览器返回栈保持自然。

**【核心】ChatView LRU 缓存**（`hooks/useChatViewCache.ts`）：最多 **5 个** `ChatView` 实例常驻挂载，只有当前那个是 `display:flex`，其余 `display:none`。**切换线程时绝不卸载** —— 这是滚动位置、输入框草稿、进行中的 SSE 流能存活的唯一原因。

**`__default__` 线程桥接**：新建会话在服务端分配真 id 之前，路由里是 `'__default__'`。`onThreadResolved(oldTid, newTid)` 在 URL 追上之前先把缓存 key 改名；`resolvingRef`（按 workspaceId 建 Map）在两次渲染的桥接窗口里抑制重复条目。

#### 2.1.2 会话视图组件树（`components/ChatView.tsx`，1938 行）

```
<WorkspaceProvider>                         ← contexts/WorkspaceContext
 <motion.div ref=containerRef>
  <div aria-live sr-only>                   ← useToolCallAnnouncer 的无障碍播报
  <ShareReportLinkModal>
  ├─ 左列
  │   ├─ 顶栏
  │   │    返回按钮（目标优先级：ownerTaskId → 'main' → fromThreadId → onBack）
  │   │    移动端 <Menu> 打开导航抽屉
  │   │    <h1>{workspaceName}</h1> + "加载历史中"
  │   │    <ShareButton threadId>
  │   │    FolderOpen 图标 → handleToggleFilePanel
  │   ├─ 内容区（ref=contentAreaRef，container-type: inline-size）
  │   │    移动端遮罩 + 拖拽关闭的 <NavigationPanel headerActions={<NavDisplayOptions/>}>
  │   │    └─ 聊天窗口
  │   │       <SubagentTelemetryContext.Provider>
  │   │        <WorkflowRunContext.Provider>
  │   │         <div ref=msgAreaRef onMouseUp={选中文本 → "加入上下文"气泡}>
  │   │           ┌ activeAgentId === 'main'
  │   │           │   <ScrollArea ref=scrollAreaRef>
  │   │           │     <MessageActionsProvider actions={messageActions}>
  │   │           │       <MessageList messages isLoading isLoadingHistory
  │   │           │                    hideAvatar feedbackByTurn flashContext/>
  │   │           ├ activeAgent.type === WORKFLOW_TASK_TYPE
  │   │           │   <ScrollArea ref=subagentScrollAreaRef><WorkflowRunDetail/>
  │   │           ├ activeAgent（子代理 tab）
  │   │           │   描述头 + 提示词气泡(<Markdown variant="chat">)
  │   │           │   <SubagentStatusIndicator/>
  │   │           │   <MessageActionsProvider actions={subagentMessageActions}>
  │   │           │     <MessageList isSubagentView hideAvatar/>
  │   │           └ else "找不到该 agent"
  │   │           <ChatMinimap/>       ← 仅桌面 + main tab + 右面板关闭时
  │   │           <JumpToLatestPill visible hasNew newCount/>
  │   └─ 输入区（max-w-3xl）—— 见 2.1.5
  ├─ 移动端 <MobileBottomSheet> → <DetailPanel/>     (rightPanelType==='detail')
  ├─ 移动端 <MobileBottomSheet> → <PreviewViewer/>   (rightPanelType==='preview')
  └─ 右侧
       移动端：motion.div 覆盖层 + 拖拽关闭 → <RightPanel/>
       桌面：`chat-split-divider`（handleDividerMouseDown）
             + AnimatePresence motion.div width={rightPanelWidth}
               → <RightPanel/> | <DetailPanel/> | <PreviewViewer/>
```

懒加载：`RightPanel`、`DetailPanel`、`viewers/PreviewViewer`。

ChatView 组合的控制器（都在 `components/chatView/`）：`useNavPanel`、`useChatScroll`、`useSubagentTabs`、`useRightPanel`、`useToolCallAnnouncer`；页面级 hook：`useChatMessages`、`useCardState`、`useWorkspaceFiles`、`useNavTreeProps`、`useMarketWatch`、`useChatFeedback`。

#### 2.1.3 消息渲染分支

**第一步：轮次投影**（`messageList/turnProjection.ts`）

```
user 消息            → turnIndex = c
非 steering 的 assistant → turnIndex = c，然后 c++
steering 续写         → turnIndex = c - 1     ← 归属【上一轮】
```

判定式（`messageList/messagePredicates.ts`）：

```ts
isSteeringUserMessage(m)    // role==='user' && (steeringDelivered || steering)
isSteeringContinuation(m)   // role==='assistant' && isSteering
isOrphanAssistantMessage(m) // assistant、非流式、无 segments/content/provenance/error/stopped
                            // → 保留在 state 里但【不渲染】
```

`MessageList.tsx` 一次投影 → 可见性过滤 → 计算 `turnTails`（每轮最后一条，决定"重新生成"按钮出现在哪）。整体包在 `<DispatchStatusProvider>` 里，让所有 `PTCAgentCard` 共享一次 liveness 轮询。`role === 'notification'` → `<NotificationDivider>`，其余 → `<MessageBubble>`。空态：`isLoadingHistory` 时骨架屏脉冲，否则"开始一段对话…"；子代理视图返回 `null`。

**第二步：气泡**（`messageList/MessageBubble.tsx`，memo 化）

| 角色 | 样式 | 特殊 |
|---|---|---|
| `user` | 右对齐、`rounded-lg rounded-tr-none`、`--color-bg-elevated`、包一层 `<OverflowCollapse maxHeight={240}>` | pending/steering/queued 状态用 `<TextShimmer>`；头像取 `useUser().avatar_url`，无则 `<User/>` 图标 |
| `assistant` | 左侧头像（`logo.svg` / `logo-dark.svg`）、透明背景、通栏、`rounded-tl-none` | 流式中且无 `pendingToolCallChunks` 时显示 `<LissajousLoading>` |

正文：`contentSegments.length > 0` → `<CitationMetadataProvider><MessageContentSegments textOnly/></...>`；否则回落到 `<TextMessageContent>`（向后兼容旧数据）。

气泡附加元素：
- **来源 pill**：assistant 且 `countDedupedSources(provenanceRecords) > 0` → `FileSearch` 图标 pill → `onOpenSources(message.id)`
- **停止 chip**：`message.stopped` → `StopCircle` + `chat.stoppedChip`
- **用户附加物**（气泡下方）：`<AttachmentCard>` 列表、`<InlineWidgetDeck snapshots>`、`<InlineSelectionCards selections>`（均在 `messageList/attachments.tsx`）
- **动作行**：**永远挂载**（保持布局稳定），`opacity-0 group-hover:opacity-100`，隐藏时加 `inert`。按钮：编辑（user 且非 steering）· 复制 · 赞 · 踩（→ `ThumbDownModal`）· 重新生成（assistant + `isTurnTail` + 无错误）· 重试（assistant + 有错误）
- **编辑态**：内联自增高 `<textarea>`，Enter=保存 / Shift+Enter=换行 / Escape=取消，并提示"将从当前会话分叉"

**第三步：段落 → 渲染块**（`MessageContentSegments.tsx` + `buildRenderBlocks.ts`）

段落按 `order` 排序，相邻 `text` 合并（`groupSegments`），再归约成渲染块：

```ts
export type RenderBlock =
  | ActivityRenderBlock          // { type:'activity'; key; items[] }
  | TextRenderBlock
  | CompactArtifactRenderBlock   // { type:'compact_artifact'; toolCallId; proc }
  | SubagentTaskRenderBlock
  | PlanApprovalRenderBlock
  | UserQuestionRenderBlock
  | CreateWorkspaceRenderBlock
  | StartQuestionRenderBlock
  | PTCAgentRenderBlock
  | SecretaryActionRenderBlock   // delete_workspace | stop_workspace | delete_thread
  | NotificationRenderBlock
  | HtmlWidgetRenderBlock;
```

段落 `type` 全集（`messageList/types.ts` 的 `ContentSegmentRecord`）：
`text`、`reasoning`、`tool_call`、`subagent_task`、`plan_approval`、`user_question`、`create_workspace`、`start_question`、`ptc_agent`、`delete_workspace`、`stop_workspace`、`delete_thread`、`html_widget`、`notification`。

块 → 组件映射（`textOnly` 模式，即主聊天）：

| 块 | 组件 |
|---|---|
| `activity` | `<ActivityBlock>`；`compactToolCalls` 时降级成 `ToolCallMessageContent` / `ReasoningMessageContent` 平铺 |
| `compact_artifact` | `INLINE_ARTIFACT_MAP[artifact.type]`（见 2.1.6） |
| `text` | `TextBlock` → `<TextMessageContent>` 或 `<StructuredResultBlock>`（子代理的 JSON 结论，`utils/structuredResult.ts`） |
| `notification` | `<NotificationDivider content detail detailKind>` |
| `html_widget` | `viewers/InlineWidget` |
| `subagent_task` | `messageList/TaskSegmentCard.tsx` |
| `plan_approval` | `PlanApprovalCard.tsx` |
| `user_question` | `UserQuestionCard.tsx` |
| `create_workspace` | `CreateWorkspaceCard.tsx` |
| `start_question` | `StartQuestionCard.tsx` |
| `ptc_agent` | `PTCAgentCard.tsx` |
| `delete_workspace` / `stop_workspace` / `delete_thread` | `SecretaryConfirmCard.tsx` |

尾部追加：还没有 activity 块时渲染独立的 `<ActivityBlock preparingToolCall>`；以及 `<FileMentionCards>`（自动识别 assistant 文本里的文件路径，`FileCard.tsx:extractFilePaths` + `utils/normalizeFileRefs.ts`）。

非 `textOnly` 模式（agent 面板）逐段渲染，**不做实时/折叠分组**。

**错误渲染**：`message.error: boolean` + `message.structuredError: StructuredError`（`@/utils/rateLimitError`）传给 `TextMessageContent` 渲染内联错误卡；同时气泡把"重新生成"换成"重试"，并隐藏赞/踩。

**只读宿主契约**：`READ_ONLY_MESSAGE_ACTIONS`（`MessageActionsContext.tsx`）—— SharedChat 用它；`readOnly` 同时把 `MessageContentSegments` 里所有 HITL 卡片的回调置空。

#### 2.1.4 工具调用卡片

**注册表**（`components/toolDisplayConfig.ts`，602 行）

```ts
interface ToolDisplayEntry { displayName: string; i18nKey: string; icon: LucideIcon }
export const TOOL_DISPLAY_CONFIG: Record<string, ToolDisplayEntry>
```

约 40 条，覆盖行情（`get_daily_prices` / `get_quote` / `get_market_overview` / `screen_stocks` / `watch_market`）、SEC（`get_sec_filing`）、新闻检索（`get_entity_news` / `search_tickers`）、MCP 基本面、用户数据、核心工具（`Bash` / `Glob` / `Grep` / `WebSearch` / `WebFetch` / `Write` / `Read` / `Edit` / `ExecuteCode` / `think_tool`）、`TaskOutput`、自动化、图表注解。**旧工具名必须保留** —— SSE 历史重放里还会出现。

导出的取值函数：`getDisplayName`、`getToolIcon`、`getInProgressText`、`getCompletedSummary`、`getActiveLabel`、`getCompletedRowTitle`、`getPreparingText`、`categorizeTool`、`parseTruncatedResult`、`stripLineNumbers`。

**按路径覆写**：`Read` / `Write` / `Edit` 会再经 `classifyAgentPath(file_path)` 重分类成 skill / memory / memo / user-profile —— 读 `.agents/memory/x.md` 显示成"记忆"而不是"读取文件"。

```ts
export type ToolCategory =
  | 'skill' | 'memoryRead' | 'memoryWrite' | 'memo' | 'memoWrite'
  | 'profileRead' | 'profileWrite' | 'code' | 'web' | 'search'
  | 'fileRead' | 'fileEdit' | 'generic';
```

**状态机**（`components/ActivityBlock.tsx`，979 行）

```ts
type LiveState = 'active' | 'completing' | 'completed' | 'failed';
```

在 `buildRenderBlocks.ts` 里赋值：

- `isInProgress` 且（是常驻实时工具 或 流仍开着且年龄 < `MAX_IN_PROGRESS_MS = 15000`）→ `active`
- 刚完成且年龄 < `MIN_LIVE_EXPOSURE_MS = 1800` 且流仍开着 → `completing`（`proc.isFailed` 则 `failed`）
- 其余 → `completed`（折进折叠区）
- `ALWAYS_LIVE_TOOLS = new Set(['TaskOutput','WebFetch'])` 永不因超时被踢出实时区
- `HIDDEN_TOOL_CALL_NAMES = new Set(['TodoWrite','task','Task','SubmitPlan','AskUserQuestion','manage_workspaces','ptc_agent','agent_output','manage_threads','ShowWidget'])` —— 这些有专属 UI 或纯内部

**关键**：`MessageContentSegments` 要按 `nextExpiry` 排一个重算定时器，让 `active → completed` 的迁移**不依赖新的 SSE chunk** 也能发生。

**展开行为**：`ActivityBlock` 把条目分三区 —— `inlineChartItems`（已完成 + 属于 `INLINE_ARTIFACT_TOOLS` + 有 artifact + 非 `_annotationStep`）作为**常驻可见**卡片；`completedItems` 进折叠手风琴；`liveItems` 排在手风琴下方。手风琴标题是**内容感知**的：把分类计数压成"读了 3 个文件 · 搜索了网页"这样的片段，折叠时最多 3 个片段 + "还有更多…"，且 memory/memo/profile 的**写入**要提到最前面。`aria-expanded` / `aria-controls` 把按钮和时间线面板绑起来。`.agents/user/profile/README.md` 的读取在分区层就被丢弃（`shouldHideTimelineItem`）。

点击行 → `onToolCallClick` → `useRightPanel.handleToolCallDetailClick`；但 `FILE_NAV_TOOLS = new Set(['Read','Write'])` 改为跳到文件面板。

**详情视图**（`ToolCallDetailView.tsx`，616 行，装在 `DetailPanel.tsx` 里）：按 artifact 类型分支 —— `stock_prices` / `company_overview` / `market_indices` / `sector_performance` / `market_overview` / `stock_screener` / `sec_filing` / `automations`；外加 WebFetch 的 URL 条、WebSearch 结果列表（`webSearchUtils.ts`）、Task 子代理面板（含 `TaskStatusChip`），兜底是 Markdown / CodeBlock。**面板宽度按内容决定**（`useRightPanel.getDetailPanelWidth`）：`Read` / `sec_filing` = 850，Task = 750，图表/搜索 = 650，`company_overview` / `automations` = 480，plan = 550。

#### 2.1.5 输入区（`components/ui/chat-input.tsx`，929 行）

同目录兄弟模块：`chat-input.types.ts` / `.helpers.tsx` / `.parts.tsx` / `.toolbar.tsx` / `.modelMenu.tsx` / `.models.ts` / `.useMentions.ts` / `.useSlashCommands.ts` / `.useFileAttachments.ts` / `.useToolbarFold.ts` / `.useVoiceInput.ts` / `chat-input.css`。

```ts
export interface FileAttachment {
  id: string; file: File; type: string; preview: string | null;
  uploadStatus: 'pending' | 'uploading' | 'complete'; dataUrl: string | null;
}
export interface MentionedFile {
  path: string; snippet?: string; label?: string;
  lineStart?: number; lineEnd?: number; lineCount?: number; source?: string;
}
export interface SlashCommand { type: string; name: string; skillName?: string; description?: string; aliases?: string[] }
export interface ModelOptions {
  model: string | null; reasoningEffort: string | null; fastMode: boolean;
  marketWatch?: boolean; widgetSnapshots?: WidgetContextSnapshot[];
}
// 回调签名
onSend(message, planMode, attachments, slashCommands, modelOptions)
```

行为清单：

- **附件**：点击选择、拖放（`onDragOver`/`onDragLeave`/`onDrop`）、粘贴（`handlePaste`，**文件粘贴优先于文本粘贴**）
- **@ 提及**：打开文件选择器（数据来自 ChatView 传入的 `workspaceFiles`），产出 `mentionedFiles` pill；片段也可由外部通过命令式句柄注入
- **/ 斜杠命令**：打开 skills/actions；action 类命令（`/compact`、`/offload`）的 `onAction` **在发送时触发而不是在选中时**，并完整清空草稿
- **模型选择器**（`chat-input.modelMenu.tsx`）：优先级 `initialModel`（线程上次用的模型）→ `preferred_model` / `preferred_flash_model`（来自 `other_preference`）；一级菜单展示 `threadModels`，星标模型来自 `other_preference.starred_models`。推理强度和 fast 模式**按模型**存 localStorage：`reasoning_effort:${model}`、`fast_mode:${model}`。`onModelChange` 把选择镜像给 ChatView（驱动 `FallbackSuggestionPill`）
- **开关**：`planMode`、`watchMode`（盯盘，仅 PTC，且受 `useFeatureEnabled('market_watch')` 门控）、语音输入（`useVoiceInput`）、widget 上下文卡组（`widgetSnapshots` + `deckFanned`，由全局 ContextBus 供给）
- **发送/停止**：`isLoading` 或 `isCompacting` 时按钮变成停止（`onStop` + `isStopping` 闩锁）。**运行期间 Enter 仍然发送** —— 这条消息成为 steering 或排队消息
- **键盘**：`Enter` 发送（提及菜单打开时除外）；`Shift+Enter` 换行；提及/斜杠菜单**优先消费**方向键/回车/Esc（`mentionKeyDown` / `slashKeyDown`）；处于语音监听时任意按键停止录音
- **命令式句柄** `ChatInputHandle`：`getModelOptions()`、`addContext({path, snippet, label, lineStart, lineEnd, lineCount, source})` —— 文件面板和 ChatView 的"选中文本加入上下文"气泡都靠它

> **草稿持久化的真相**：**没有**按线程持久化输入框文本。真正持久化的只有三样 —— 按模型的推理强度/fast 模式（localStorage）、深路由会话（`chat_session_restore`，sessionStorage，5 分钟）、每工作区最后线程 id（`workspace_thread_id_<wsId>`，`hooks/utils/threadStorage.ts`）。输入框草稿之所以能"活下来"，纯粹是因为 §2.1.1 的 LRU 缓存没卸载组件。

输入区之上还叠了一串状态条（仅 main tab）：`<TodoDrawer todoData={cards['todo-list-card'].todoData}>`、`<MarketWatchChip>` + 后台任务提示、计划反馈提示（`pendingRejection`）、`<ErrorBanner error={messageError}>`、重连中 / `awaitingReportBack` / `workspaceStarting`（带 HoverCard）/ `isCompacting` / `queuedSend` 各自一行、`<ModelStatusPill>`、`<FallbackSuggestionPill onSwitchModel onDismiss>`。子代理 tab 下则换成 `<SubagentStatusBar agent threadId onInstructionSent>`。

#### 2.1.6 Artifact 与右面板内容

**内联 artifact 卡片**（`components/charts/`）

```ts
export const INLINE_ARTIFACT_TOOLS = new Set([
  'get_daily_prices','get_stock_daily_prices','get_company_overview','get_quote',
  'get_market_overview','get_market_indices','get_sector_performance',
  'get_sec_filing','screen_stocks','check_automations','create_automation',
  'GetPreviewUrl','WebSearch','draw_chart_annotation',
]);

export const INLINE_ARTIFACT_MAP: Record<string, ComponentType<{artifact; onClick?}>> = {
  stock_prices:       InlineStockPriceCard,
  company_overview:   InlineCompanyOverviewCard,
  quote:              InlineQuoteCard,
  market_indices:     InlineMarketIndicesCard,
  sector_performance: InlineSectorPerformanceCard,
  market_overview:    InlineMarketOverviewCard,
  sec_filing:         InlineSecFilingCard,
  stock_screener:     InlineStockScreenerCard,
  automations:        InlineAutomationCard,
  preview_url:        InlinePreviewCard,
  web_search:         InlineWebSearchCard,
  chart_annotation:   InlineChartAnnotationCard,
};
```

文件：`InlineArtifactCards.tsx`（卡片 + 映射表，recharts Area/Bar）、`MarketDataCharts.tsx`（详情面板用的全尺寸版）、`InlineQuoteCard.tsx`、`InlineAutomationCards.tsx`、`AutomationDetailPanel.tsx`、`InlinePreviewCard.tsx`、`InlineChartAnnotationCard.tsx`、`AnnotationPreviewChart.tsx`、`SecFilingViewer.tsx`、`inlineCardsShared.ts`（颜色/尺寸/格式化）。

**图表注解的归并**（`components/chartAnnotationGrouping.ts`）：`planChartAnnotationCards` 在**首次绘制**时为每个图表实例钉住**一张**卡，之后一直喂它最新的累积 artifact 让它就地生长；后续的绘制动作降级成普通的 `_annotationStep` 时间线行。

**右面板内容组件**

| 组件 | 展示什么 | 何时 |
|---|---|---|
| `FilePanel.tsx`（996 行）+ `filePanel/` | 工作区文件树、查看器、上传/编辑/备份、分享链接、系统文件开关（`localStorage['filePanel.showSystemFiles']`） | `tab === 'files'` |
| `MemoryPanel.tsx` | 用户级 + 工作区级记忆条目（`.agents/memory/`），markdown 正文，层级切换 | `tab === 'memory'` |
| `MemoPanel.tsx`（1438 行） | 用户备忘录库：列表/读取/上传/写入/删除/重新生成/下载 | `tab === 'memo'` |
| `SourcesPanel.tsx`（1051 行） | 按来源类型分组的溯源记录 + `Favicon`；"本轮 / 全部来源"切换 | `tab === 'sources'`（仅当有 `sources` target） |
| `StatusPanel.tsx` | 盯盘实时快照（symbol 列表、最后更新、`PulseDot`） | `tab === 'status'`（有 target，或盯盘 symbol 数 > 0） |
| `ExportPreviewModal.tsx`（599 行） | paged.js + react-to-print 的打印预览：纸张尺寸、字体、页边距预设 | 从 `FilePanel` 懒加载 |
| `DetailPanel.tsx` | 包住 `ToolCallDetailView` 或计划数据的外框 | `rightPanelType === 'detail'` |
| `viewers/PreviewViewer.tsx` | 签名沙箱预览 URL 的 iframe，带刷新/重载令牌 | `rightPanelType === 'preview'` |

文件查看器：`viewers/` 下有 Code / Csv / Excel / Pdf / Html / Preview / InlineWidget，`viewers/html/` 单独处理 HTML 沙箱化、`srcdoc` 构建、动作条、全屏。

#### 2.1.7 右面板系统（`components/chatView/useRightPanel.ts`，563 行）

**两个正交轴**，这是本页最容易实现错的地方。

轴一 —— **面板类型**（哪张"表面"占据右列）：

```ts
const [rightPanelType, setRightPanelType] = useState<'file' | 'detail' | 'preview' | null>(null);
```

轴二 —— **面板目标**（`file` 表面被指向什么，`components/RightPanel.tsx`）：

```ts
export type RightPanelTab = 'files' | 'memory' | 'memo' | 'sources' | 'status';

export type PanelTarget =
  | { kind: 'file';   path?: string | null; dir?: string | null }
  | { kind: 'memory'; key: string; tier: MemoryTier }
  | { kind: 'memo';   key: string }
  | { kind: 'sources'; messageId: string }
  | { kind: 'status' };
```

**同一时刻只允许一个 target**；当前 tab、tab 可见性、以及关闭后的回弹全部由 `.kind` 派生。`files` / `memory` / `memo` 三个 tab 常驻；`status` 在 `kind === 'status'` 或盯盘 symbol > 0 时出现；`sources` **只在** `kind === 'sources'` 时出现。`file` / `memory` / `memo` 三种 target 通过 `onTarget*Handled` 回调**自清除**；`sources` / `status` 在自己的 tab 打开期间保留，由一个 `rightPanelType !== 'file'` 的 effect 清掉。

打开器：`handleOpenFileFromChat`（等价于 `handleOpenAgentArtifactFromChat`，经 `utils/agentPaths.computeAgentArtifactRouting` 分流到 file / memory / memo，并可为跨工作区的 flash 链接切换 `filePanelWorkspaceId`）、`handleOpenDirFromChat`、`handleOpenSourcesFromChat(messageId)`、`handleOpenStatusFromChat`、`handleToolCallDetailClick`（`preview_url` 类 artifact 改走预览面板）、`handlePlanDetailClick`、`handleOpenPreview`、`handleToggleFilePanel`。

关闭器：`handleCloseDetailPanel`、`handleClosePreview`、RightPanel 的 `onClose`、移动端拖拽关闭、返回手势。

其它机制：
- **分隔条拖拽**用直接 DOM 写入（`handleDividerMouseDown`），拖拽期间给所有 iframe 加 `pointer-events:none`；`dragJustEndedRef` 让松手后那一帧 `duration: 0`
- 宽度由 `@/lib/panelUtils.clampPanelWidth` 夹取，预览面板上限 `PREVIEW_MAX_RATIO = 0.92`
- 多端口预览缓存 `previewMapRef: Map<number, PreviewData>`
- **移动端返回手势集成**：克隆一个 `history.pushState({_panelSentinel: true})` 哨兵，使物理返回键关闭面板而**不发生路由变化**
- 一次性 `?file=` 深链消费（受 `isActive` 门控），消费后 `replace: true` 抹掉参数
- 溯源 memo：`sourcesRecords`（指定轮次）与 `allSourcesRecords`（全轮次合并，**首次出现者胜**）

#### 2.1.8 侧栏与画廊

**`WorkspaceGallery.tsx`（1058 行）** —— 工作区卡片网格。内部部件：`CardMenu`（置顶 / 重命名 / 升级 spec / 常驻开关 / 复制 / 删除）、`SortableReorderRow`（dnd-kit 自定义排序）、`WorkspaceCard`；配套对话框 `CreateWorkspaceModal.tsx`（555 行）、`RenameWorkspaceDialog.tsx`、`DuplicateWorkspaceDialog.tsx`、`AlwaysOnConfirmDialog.tsx`、`ChangeSpecDialog.tsx`、`WorkspaceImage.tsx`。状态：`sortBy: 'activity' | 'name' | 'custom'`、`reorderActiveId`、`allWorkspaces`、`renameTarget`。
**悬停即预取**：`prefetchThreads(wsId)` → `queryClient.prefetchInfiniteQuery(threadGalleryQuery(wsId, false))` —— **必须用画廊挂载时用的那个 infinite key**，否则预取白做。

**`ThreadGallery.tsx`（881 行）** —— 某工作区的线程列表 + 页内 `ChatInput`（`dropdownDirection="down"`）直接开新线程、工作区文件条、归档筛选、无限滚动（`loadMoreSentinelRef`）、`ThreadCard.tsx` 行、`DeleteConfirmModal` / `RenameThreadModal` / `ArchiveThreadConfirmDialog`、`SandboxSettingsPanel`（1538 行），以及它自己的 `RightPanel` 覆盖层。输入逻辑在 `hooks/useThreadGalleryInput.ts`，查询在 `utils/threadGalleryQuery.ts`。

**`NavigationPanel.tsx`** —— 导航树（工作区 → 线程 → agent），dnd-kit 的 `DndContext` / `SortableContext` / `DragOverlay`。**挂载两次**：桌面的 `AppSidebar` 和 ChatView 的移动抽屉，两者从 `hooks/useNavTreeProps.ts` 拿同一份 prop 包。

**`NavigationRows.tsx`（687 行）** —— 行原语：`AgentRow`、`ThreadTreeRow`、`WorkspaceTreeRow`、`WorkspaceDragChip`，内部件 `SortableWorkspace`、`ThreadRowTitle`、`ThreadRowGlyph`、`ThreadMetaCard`、`workspaceGlyph`。

**`hooks/useNavigationData.ts`（696 行）** —— 用 `useQueries` / `useQuery` 拉工作区 + 每工作区线程，外加一层**稳定排序**，让流式更新和写操作不把行洗乱：`applyStableOrder`、`applyStableOrderBy`、`bumpThreadNavOrder`、`absorbThreadOrder`、`resetStableNavOrder`、`forgetStableNavOrder`、`resetWorkspaceOrderFreeze`、`isEffectivelyPinned`、`partitionPinnedFirst`；还有 `showAllWorkspaces` 开关。展开态在 `components/navExpansionStore.ts`（`toggleWorkspaceExpansion` / `toggleThreadExpansion` / `subscribeNavExpansion` / `getNavExpansionVersion`）。显示偏好在 `components/NavDisplayOptions.tsx` + `utils/navPrefs.ts`（`localStorage['nav.display']`），置顶在 `components/chatView/navPin.ts`（`localStorage['nav.pinned']`）。

#### 2.1.9 关键局部状态

**`hooks/useChatMessages.ts`（2648 行，全站最大文件）** —— 会话引擎。

状态：`messages`、`threadId`、`isLoading`、`isLoadingHistory`、`hasActiveSubagents`、`workspaceStarting: false | 'starting' | 'archived'`、`isCompacting: string | false`、`modelStatus`、`fallbackSuggestion`、`queuedSend: string | false`、`messageError: string | StructuredError | null`、`returnedSteering`、`pendingInterrupt`、`pendingRejection`、`tokenUsage`、`isShared`、`threadModels`、`lastThreadModel`、`reloadTrigger`、`isReconnecting`。

流式协调用的 ref（**都不能改成 state**，否则每个 chunk 触发重渲染）：`currentPlanModeRef`、`contentOrderCounterRef`、`wasStoppedRef`、`backgroundReconnectRef`、`tabSuspendedRef`、`historyLoadingRef`、`newMessagesStartIndexRef`、`isStreamingRef`、`pendingMuxResyncRef`、`historyHasUnresolvedInterruptRef`、`pendingInterruptIdsRef`、`renderedInterruptIdsRef`、`threadIdRef`、`isNewConversationRef`、`recentlySentTrackerRef`、`requestKeyRef`、`toolCallIdToTaskIdMapRef`、`terminalTaskOutcomesRef`、`subagentHistoryRef`、`lastEventIdRef`、`currentRunIdRef`、`mainStreamAbortRef`、`isReconnectingOwnerRef`、`streamingThreadIdRef`。

返回约 45 个成员：上述状态 + `handleSendMessage`、`stopWorkflow`、`stopCompaction`、`handleApproveInterrupt` / `handleRejectInterrupt`、`handleAnswerQuestion` / `handleSkipQuestion`、`handleApprove|RejectCreateWorkspace`、`…StartQuestion`、`…PTCAgent`、`…SecretaryAction`、`handleEditMessage`、`handleRegenerate`、`handleRetry`、`handleThumbUp` / `handleThumbDown`、`feedbackByTurn`、`insertNotification`、`marketWatch`、`awaitingReportBack`、`resolveSubagentIdToAgentId`、`getSubagentHistory`、`hydrateTaskTranscript`、`reconnectIfStaleRun`。

关键类型（`session/types.ts`）：

```ts
export type ModelStatus =
  | { kind: 'retrying'; model: string; attempt: number; maxRetries: number }
  | { kind: 'fallback'; fromModel: string; toModel: string };

export interface FallbackSuggestion { fromModel: string; toModel: string }

interface TokenUsage { totalInput; totalOutput; lastOutput; total; threshold }
interface PendingInterrupt {
  type?; interruptId?; assistantMessageId?; planApprovalId?;
  questionId?; proposalId?; planMode?; actionRequests?; threadId?; toolCallId?
}
interface PendingRejection { interruptId: string; planMode: boolean }
```

**`session/` 引擎拆分**（非 React 逻辑，靠 `runtime.ts` 的依赖契约注入）：

```
runtime.ts   ChatSessionRuntime = SubagentRuntime & HistoryRuntime & StreamRuntime & RecoveryRuntime
stream/processStreamEvent.ts  (996) 实时 SSE 路由器
stream/threadStreamMux.ts     (515) v2 多路复用客户端（见 §4.9）
stream/lifecycle.ts           (608) 所有权 / 刷新后重连 / 重试 / 流终结
stream/steeringRollback.ts          steering_accepted 的 _eventId 作为回滚边界
stream/provenance.ts / mainEventHandlers.ts (650)
history/replayHistory.ts      (935) + history/historyHandlers.ts (772)
interrupts/buckets.ts               HITL 卡片词汇表
interrupts/fromLiveEvent.ts / fromHistoryEvent.ts
subagents/liveEventHandlers.ts (811) / muxSink.ts / projectHistory.ts /
          hydrateTaskTranscript.ts / subagentStatus.ts / subagentMetrics.ts /
          resolveSubagentTelemetry.ts / taskSegmentBuilder.ts / workflowRunState.ts
threadCreation.ts                   创建优先流程（先 POST /threads，真 id 比发送晚一个往返）
```

`processStreamEvent` 处理的事件名全集：`message_chunk`、`tool_calls`、`tool_call_chunks`、`tool_call_result`、`artifact`、`reasoning`、`reasoning_signal`、`interrupt`、`steering` / `steering_accepted` / `steering_delivered` / `steering_returned`、`token_usage`、`context_window`、`compaction_chunk`、`summarize`、`offload`、`model_retry`、`model_fallback`、`market_watch_update`、`provenance`、`todo_list` / `todo_update`、`subagent_task`、`html_widget`、`notification`、`file_operation`、`preview_url`、`chart_annotation`、`navigate_to_workspace`、`ptc_agent`、`metadata`、`error`、`complete`。

**其它有状态 hook**：
- `useCardState.ts` —— `cards: CardsMap` 按 card id 索引，装 `todo-list-card`（`TodoData`）与子代理卡（`SubagentData`）
- `useChatScroll.ts` —— 返回 `scrollAreaRef`、`subagentScrollAreaRef`、`pinToBottom`、`saveScrollPosition`、`withProgrammaticScroll`、`jumpPill: {visible, hasNew, newCount}`、`scrollPositionsRef`、`isNearBottomRef`、`restoredForThreadRef`
- `useSubagentTabs.ts` —— `hiddenAgentIds: Set<string>`；返回 `sidebarAgentRows`、`activeAgent`、`switchAgent`、`handleSelectAgent`、`handleOpenSubagentTask`、`handleRemoveAgent`、`handleSubagentInstruction`、`resolveSubagentTelemetry`、`resolveWorkflowRun`（`MAIN_AGENT` 常量在 `chatView/mainAgent.ts`）
- 其余：`useChatFeedback`、`useMarketWatch`、`useMemo`(备忘录)、`useMemory`、`useWorkspaceFiles`、`useWorkspaceMutation`、`workspaceRowActions`、`usePTCDispatchStatus`、`useReportBackWatch`(718 行)、`useWarmWorkspaceSandbox`、`useNavTreeProps`

**ChatView 自身局部状态**：`agentMode`（`'ptc' | 'flash'`）、`inputModel`、`workspaceName`、`filePanelWorkspaceId`、`activeAgentId`（`'main'` 或 `task:<id>`）、`showSystemFiles`、`wasStopped`、`shareLinkFile`、`msgSelectionTooltip`；ref：`initialMessageSentRef`、`intentionalExitRef`、`isActiveRef`、`containerRef`、`chatInputRef`、`msgAreaRef`、`navWorkspacesRef`。

#### 2.1.10 API 端点（`utils/api/`，由 `utils/api.ts` 桶文件再导出）

| 模块 | 端点 |
|---|---|
| `threads.ts` | `POST /api/v1/threads`、`GET /api/v1/threads/{id}`、`GET /api/v1/threads`（列表带参）、`DELETE /api/v1/threads/{id}`、`PATCH /api/v1/threads/{id}`、`GET\|POST /api/v1/threads/{id}/share`、`POST /api/v1/threads/{id}/summarize?keep_messages=`、`POST /api/v1/threads/{id}/offload`、`GET /api/v1/threads/{id}/market-watch` |
| `messages.ts` | `GET /api/v1/threads/{id}/messages/replay`(SSE)、`POST /api/v1/threads/messages`(新线程,SSE)、`POST /api/v1/threads/{id}/messages`(SSE)、`POST /api/v1/threads/{id}/retry`(SSE)、`POST /api/v1/threads/{id}/cancel`、`GET /api/v1/threads/{id}/status`、`GET /api/v1/threads/dispatches/liveness`、`GET /api/v1/threads/{id}/messages/stream?run_id=&last_event_id=`、`GET /api/v1/threads/{id}/turns`、`GET /api/v1/threads/{id}/stream?contract=v2`(mux)、`POST\|GET /api/v1/threads/{id}/tasks/{taskId}/messages\|status\|history\|cancel`、`GET /api/v1/threads/{id}/watch`(SSE) |
| `workspaces.ts` | `GET\|POST /api/v1/workspaces`、`GET\|PUT\|DELETE /api/v1/workspaces/{id}`、`POST /api/v1/workspaces/flash`、`POST /api/v1/workspaces/reorder`、`POST /api/v1/workspaces/{id}/spec`、`POST …/always-on`、`POST …/duplicate`、`GET /api/v1/workspaces/quota`、`POST …/start`、`streamWorkspaceEvents`(SSE `/events`) |
| `files.ts` | 见 §4.14 |
| `sandbox.ts` | `GET …/sandbox/stats`、`POST …/sandbox/packages`、`POST /api/v1/workspaces/{id}/refresh`、`POST …/sandbox/preview-url`、`POST …/sandbox/preview-health` |
| `feedback.ts` | `POST\|DELETE\|GET /api/v1/threads/{id}/feedback` |
| `memory.ts` | `GET /api/v1/memory/user`、`…/user/read`、`…/workspaces/{id}`、`…/workspaces/{id}/read` |
| `memos.ts` | `GET /api/v1/memo/user`、`…/user/read`、`POST …/user/upload`、`PUT …/user/write`、`DELETE …/user`、`POST …/user/regenerate`、`GET …/user/download` |
| `mcp.ts` | `GET\|POST /api/v1/workspaces/{id}/mcp/servers`、`PUT\|DELETE …/servers/{name}`、`POST …/servers/{name}/enabled\|discover\|promote`、`POST …/servers/import`、`GET\|POST /api/v1/mcp/servers`、`PUT\|DELETE /api/v1/mcp/servers/{name}` |
| `vault.ts` | `GET\|POST /api/v1/workspaces/{id}/vault/secrets`、`PUT\|DELETE …/secrets/{name}`、`GET …/secrets/{name}/reveal`、`GET …/vault/blueprints` |
| `metadata.ts` | `GET /api/v1/skills?mode=`（promise 级缓存）、`GET /api/v1/models`（缓存）；`resetChatApiCaches()` **必须注册进 authResets** |

#### 2.1.11 三个必须照抄的横切模块

- **`utils/agentPaths.ts`** —— 所有 agent 触碰路径的路由大脑：`classifyAgentPath` → `{ kind: 'skill'|'memory'|'memo'|'user-profile'|'file', tier, entity }`，以及 `computeAgentArtifactRouting`、`MEMORY_USER_DIR`、`MEMORY_WORKSPACE_DIR`、`workspaceRelativePath`、`isUserProfileReadmePath`。工具显示名和右面板 tab **都**由它派生。
- **`utils/compactionControl.ts`** —— `routeStopAction`、`compactionErrorCode`、`isUserStoppedCompaction`、`shouldClearCompactingFlag`、`isManualCompactionInFlight`：决定停止按钮取消的是**一轮对话**还是**一次压缩**。
- **`utils/threadRouteGuard.ts`** —— `shouldLeaveThreadRoute(needsLookup, error, accessDenied)` 决定是否重定向回 `/chat`。

---

### 2.2 Dashboard（重点）

#### 2.2.1 路由分流（`DashboardRouter.tsx`）

```
移动端（useIsMobile()）→ 【永远】渲染 Classic，模式切换按钮完全隐藏
桌面端 → 由 preferences.other_preference.dashboard.mode 决定
         经 migrateDashboardPrefs() 解析，mode = parsed?.mode ?? 'classic'
         缺失 / 旧格式 → Classic（零回归）
```

`onModeChange(next)` 有四层防御，**一层都不能省**：

1. `isLoading` 时**拒绝**（冷缓存门禁）—— 服务端会整体替换 `dashboard` 这个 key，从 `{}` 写入等于抹掉已保存的布局。
2. 重新读**最新**缓存 `queryClient.getQueryData<UserPreferences>(queryKeys.user.preferences())`，避免踩掉另一个 tab 的编辑（replay-aware）。
3. `firstFlipToCustom = next === 'custom' && (!baseDashboard.widgets || baseDashboard.widgets.length === 0)` → 播种 `getPreset('morning-brief')`。
4. 通过 `useDashboardPrefsWriter().writeDashboardPrefs` 写入，传 `fallbackOther: preferences === null ? undefined : (rawOther ?? null)` —— **`undefined` 表示冷缓存（写入器拒绝），`null` 表示已加载但为空**。

三个分支（移动 / custom / classic）**都要**挂 `NetworkBanner`。顶部的副作用导入 `import './widgets/index'` 保证任何 preset 工厂运行前 widget 注册表已填满。

#### 2.2.2 Classic 仪表盘（`Dashboard.tsx`）

布局：`DashboardHeader`（吸顶）→ "Market Overview" H1（移动端附自选 pill）→ 可关闭的个性化横幅 → 通栏 `IndexMovementCard` → `grid lg:grid-cols-3`：

- 左 `lg:col-span-2`：`AIDailyBriefCard`、`NewsFeedCard`
- 右 `lg:col-span-1`（仅桌面，`lg:sticky lg:top-24`）：`PortfolioWatchlistCard`、`EarningsCalendarCard`
- `<main>` 内浮动 `ChatInputCard`
- 底部各类弹窗，以及移动端用的 `MobileBottomSheet`（里面重渲染 `PortfolioWatchlistCard` + `EarningsCalendarCard`）

滚动记忆：`useScrollMemory(mainRef, 'page:dashboard')`。

**局部状态**：
```ts
selectedNewsId: string | null
selectedNewsFallbackUrl: string | null
selectedMarketInsightId: string | null
showWatchlistSheet: boolean            // 移动端底部抽屉
brokerDialogOpen: boolean
deleteConfirm: { open, title, message, onConfirm: (() => Promise<void>) | null }
```

**`components/` 全量清单**

| 文件 | 展示 / 行为 | 数据源 |
|---|---|---|
| `DashboardHeader.tsx` + `.css` | 吸顶条：股票搜索（防抖 300ms、全局 `/` 快捷键、12 条结果下拉 → 跳 `/market?symbol=`）、Classic/Custom 分段切换（仅桌面）、编辑布局铅笔（仅 custom）、帮助气泡（读 `import.meta.env.VITE_CONTACT_EMAILS`） | `searchStocks()` |
| `IndexMovementCard.tsx`（406 行） | 5 个指数卡（S&P / NASDAQ / Dow / Russell / VIX）+ Recharts `LineChart` 迷你走势。`forceMobile` prop 切换成 iOS 风格可滑动卡堆（`MarketsOverviewWidget` 在 <640px 时用） | props `indices` |
| `AIDailyBriefCard.tsx`（590 行） | 见下 | `/api/v1/insights/*` |
| `NewsFeedCard.tsx`（419 行） | 3 个 tab（市场/持仓/自选）、ticker 搜索、时间范围 chip（all/1h/6h/24h/7d）、行含图片+favicon+来源+ticker。**【缺陷】它在 `parseRelativeTime` 解析出的相对时间字符串上过滤**，widget 版已改用原始 ISO —— 新实现按 widget 版做 | props |
| `PortfolioWatchlistCard.tsx`（669 行） | 见下 | props（**自己不发任何请求**） |
| `EarningsCalendarCard.tsx`（613 行） | 见下 | `/api/v1/calendar/*` |
| `NewsDetailModal.tsx`（686 行） | 见下 | `/api/v1/news/:id` |
| `InsightDetailModal.tsx`（432 行） | 完整洞察：标题、话题、`content[]` 分节、带 favicon 的 `sources[]`、回形针 → `ContextBus.attach(buildInsightWidgetSnapshot(...))`。移动端走 `MobileBottomSheet` | `getInsightDetail()` |
| `ChatInputCard.tsx` | 浮动聊天，见 2.2.6 | `useChatInput()` |
| `AddWatchlistItemDialog.tsx`（365 行） | 两页对话框：①关键词搜索 ②复核 + 备注 + `alert_settings.price_above/below`。产出 `{symbol, instrument_type, exchange, name, notes, alert_settings}` | `searchStocks`、`getStockPrices` |
| `AddPortfolioHoldingDialog.tsx`（350 行） | 两页：搜索 → 数量 / 平均成本 / 币种 / 账户名 / 备注 / 首次买入日 | `searchStocks` |
| `ConnectBrokerDialog.tsx`（355 行） | IBKR Flex Query 连接：`query_id` / `token` / `account`，先测试再同步。凭据缓存进 `localStorage['kairos_ibkr_flex_credentials']` | `POST /api/v1/brokers/ibkr/test`、`POST /api/v1/brokers/ibkr/sync` |
| `ConfirmDialog.tsx` | 通用确认框 | — |
| `NetworkBanner.tsx` | 吸顶离线横幅（`useNetworkStatus`），`role="status" aria-live="polite"`；在线时返回 `null` | — |
| `RowAttachButton.tsx` + `.css` | 列表行悬停显现的回形针。调 `getWidgetContextSnapshot(instanceId, rowId)` → `ContextBus.attach`。带 `widget-drag-cancel` 类，需配 `.row-attach-host` 包裹层 | 上下文注册表 |
| `TopicBadge.tsx` | `#文本` 药丸，按 `trend: 'up'\|'down'\|'neutral'` 着色 | — |
| `Portfolio.tsx` / `Watchlist.tsx` / `TopNews.tsx` / `TopResearch.tsx` | **死代码**：写死的 AAPL/UBER 样例数组，`Dashboard.tsx` 根本没引用。**新实现直接删** | — |

**`PortfolioWatchlistCard.tsx` 细节**

单卡 + Watch/Holdings tab 切换，`maxHeight: clamp(300px, calc(100vh - 420px), 800px)`。

- **持久化走 localStorage（不是服务端）**：`portfolio_active_tab`（`'watchlist' | 'portfolio'`）、`portfolio_values_hidden`（`'true' | 'false'`）
- 自选行：代码、价格、绝对涨跌、百分比徽章、盘前盘后副行（`getExtendedHoursInfo`，盘前 `#fbbf24` + `Sunrise`，盘后 `#3b82f6` + `Sunset`）。**`quoteAvailable === false` 渲染 `N/A`，绝不渲染假的 `0.00`**
- 持仓行：代码、股数、市值、价格、未实现盈亏 %、同样的盘前盘后副行。`valuesHidden` 用 `******` / `***` / `********` 遮蔽
- 汇总卡（仅 `hasRealHoldings` 时）：**按币种分组**的净值（`utils/portfolioSummary.ts` 的 `summarizePortfolioByCurrency`）、每币种盈亏药丸、Eye/EyeOff 开关
- 行交互：点击 → `navigate('/market?symbol=...')`；桌面右键 `ContextMenu`（自选=删除；持仓=编辑+删除）；移动端显性 `MoreVertical` `DropdownMenu`
- 底部 CTA：watch tab 是 `AddNewButton`；holdings tab 是 `PortfolioAddActions`（两个虚线按钮："手工添加交易" + "连接券商"）
- 动画：tab 切换用 framer-motion `AnimatePresence mode="wait"`；行逐个 `delay: index * 0.05`

**`EarningsCalendarCard.tsx` 细节**

两个 tab：`type EventTab = 'earnings' | 'macro'`，标题 `dashboard.majorEvents.title`，"查看全部" 开弹窗。

- 取数用**朴素 `useEffect` + `useState`，不是 React Query**。窗口 `from = 今天 − 5天`，`to = 今天 + 60天`（**故意开这么宽**：yfinance 返回的是每个 symbol 的"下一次"财报日）：
  `Promise.all([ getEarningsDates({from,to}), getMacroCalendar({from,to}) ])`
- 预览逻辑：earnings 取最多 3 条未来 + 用过去的补齐，上限 6；macro 取最多 4 条未来 + 补齐，上限 6。用 `SectionLabel` 分"最近"/"即将"，过去的行 `opacity: 0.6`
- `SYMBOL_DISPLAY_NAME` 映射外国代码：`'000660.KS' → 'SK Hynix'`、`'005930.KS' → 'Samsung Electronics'`
- 宏观行按重要度着色圆点 `{high:'#ef4444', medium:'#f59e0b', low:'#9ca3af'}`；`i18n.language.startsWith('zh')` 时标签用 `label_zh`
- 弹窗走 `createPortal(..., document.body)`，`z-[1030]`，Esc 可关：`EarningsModal`（横向日期 tab 条：星期 / "Mon D" / 条数，默认今天或最近的未来日，1/2/3 列网格）、`MacroModal`（纯时间序列表）

**`AIDailyBriefCard.tsx` 细节**

- **模块级缓存** `let insightsCache: Insight[] | null` —— 跨导航存活，刷新即清。导出读取器 `getCachedInsights()` 供 `InsightBriefWidget` 的快照序列化用
- `getTodayInsights()`（`GET /api/v1/insights/today`）只加载一次。`latest = insights[0]`，`older = insights.slice(1)`
- 渲染：眉标徽章、`text-3xl` 标题、摘要、`MobileTopicRow`（用 `ResizeObserver` 测量，移动端塌成一行 + "+N more"）
- CTA：**读全文** → `onReadFull(market_insight_id)`；**生成个性化** → `POST /api/v1/insights/generate` 然后**每 5 秒轮询 `getInsightDetail`，最多 120 次（10 分钟）**，直到 `status === 'completed' | 'failed'`。错误映射：409 → "正在生成中"，429 → "额度用尽"，其它 → 通用；轮询耗尽 → 超时提示
- `TYPE_CONFIG` 强调色：`pre_market` → profit 色，`market_update` → accent-primary，`post_market` → `#a78bfa`，`personalized` → `#f59e0b`
- `older.length > 0` 时折叠态在卡后面画两层假的堆叠阴影（`translateY(8px) scale(0.98)` / `translateY(16px) scale(0.96)`）。点卡片正文（不是按钮/链接）切换 `expanded`；展开用 `AnimatePresence height 0→auto` 显示历史洞察时间线（时间 / 圆点 / 类型药丸 / 标题），widget 模式下每行还带 `RowAttachButton`

**`NewsDetailModal.tsx` 细节**

Props `{ newsId, onClose, fallbackUrl?, fallback? }`。`fallbackUrl` 是 Classic 的旧路径（只有 URL），`fallback: NewsFallback` 是 `NewsFeedWidget` 用的富路径。

- 取数策略：先用 `fallbackToArticle(fallback)` 立刻播种。**若种子已含 `description`，完全跳过网络请求**；否则 `getNewsArticle(newsId)` 作为可选增强；失败且无种子 → `fetchFailed` 空态 + "打开原文" 按钮
- **安全**：`safeHttpUrl()` 在 URL 进入 `<a href>` 之前拒掉非 `http:`/`https:` 的东西 —— **React 不会拦 `javascript:`**
- 正文分节：主图（无图来源如 TickerTick 用标题兜底块）、元信息行（作者 / 日期 / 加入聊天上下文 / 原文链接）、**相关话题**（`#keyword` chip）、**要点摘要**（`description`）、**个股影响**（最多 5 张情绪卡，点击开 `z-[60]` 的嵌套覆盖弹窗展示完整 `reasoning`；无情绪数据时降级成纯 ticker chip）
- 附加上下文：`attachArticleToContext()` → `ContextBus.attach(buildNewsWidgetSnapshot({ instanceId: 'news.detail', rowId: articleId, article }))`
- 响应式：移动端 `MobileBottomSheet` 92vh；桌面 framer-motion 居中对话框 `max-w-5xl max-h-[90vh]`、`z-50`、Esc 关闭

#### 2.2.3 Custom 仪表盘 / widget 框架

`DashboardCustom.tsx` 的包裹层：
```tsx
<DashboardDataProvider><MarketDataWSProvider><CustomInner/></MarketDataWSProvider></DashboardDataProvider>
```
（`MarketDataWSProvider` 是给 `ChartWidget` 的实时 tick 用的。）

**局部状态**：`editMode` / `addOpen` / `presetsOpen` / `resetConfirmOpen`（布尔）、`settingsFor: string | null`（widget 实例 id）。`Escape` 退出编辑模式（document keydown）。派生：`hasAgentWidget = widgets.some(w => w.type === 'agent.conversation')`、`showFloatingChat = !hasAgentWidget && !editMode`、`bottomGutter = editMode ? '6rem' : showFloatingChat ? '8rem' : '0'`。滚动记忆 key `'page:dashboard-custom'`。

**注册表**（`widgets/framework/WidgetRegistry.ts`，模块级 `Map`）

```ts
const registry = new Map<string, WidgetDefinition<unknown>>();
export function registerWidget<C>(def: WidgetDefinition<C>): void
export function getWidget(type: string): WidgetDefinition<unknown> | undefined
export function listWidgets(): WidgetDefinition<unknown>[]
export function listWidgetsByCategory(): Record<string, WidgetDefinition<unknown>[]>
```

**靠副作用导入填充**：`widgets/index.ts` 按固定顺序 import 全部 33 个定义模块，再 re-export 三个读取函数。**陷阱**：直接 import `migrations.ts` 的测试必须同时 `import 'widgets/index'`，否则 `sanitizeConfig` 静默变成空操作。

**核心类型**（`widgets/types.ts`，原样照抄）

```ts
export type WidgetCategory = 'markets' | 'intel' | 'personal' | 'agent' | 'workspace';
export interface WidgetSize { w: number; h: number; }

export interface WidgetRenderProps<C = unknown> {
  instance: WidgetInstance<C>;
  updateConfig: (patch: Partial<C>) => void;
}
export interface WidgetSettingsProps<C = unknown> {
  config: C; onChange: (patch: Partial<C>) => void; onClose: () => void;
}

export interface WidgetDefinition<C = unknown> {
  type: string;
  titleKey: string;
  descriptionKey?: string;
  category: WidgetCategory;
  icon: LucideIcon;
  component: ComponentType<WidgetRenderProps<C>>;
  defaultConfig: C;
  defaultSize: WidgetSize;
  minSize: WidgetSize;
  maxSize?: WidgetSize;
  settingsComponent?: ComponentType<WidgetSettingsProps<C>>;
  singleton?: boolean;
  fitToContent?: boolean;          // 放弃手动竖向拉伸，高度锁到内容测量值
  source?: 'tradingview';
  initConfig?: (ctx: DashboardDataContextValue) => C;
  configSchema?: z.ZodType<C>;
}

export interface WidgetInstance<C = unknown> { id: string; type: string; config: C; }

export interface RGLItem {
  i: string; x: number; y: number; w: number; h: number;
  minW?: number; minH?: number; maxW?: number; maxH?: number; static?: boolean;
}

export type BreakpointKey = 'lg' | 'md';

export interface DashboardPrefs {
  version: 1;
  mode: 'classic' | 'custom';
  widgets: WidgetInstance[];
  layouts: Partial<Record<BreakpointKey, RGLItem[]>>;
  lastBreakpoint?: BreakpointKey;
  history?: Array<{ widgets: WidgetInstance[]; layouts: Partial<Record<BreakpointKey, RGLItem[]>> }>;
}
export const DASHBOARD_PREFS_VERSION = 1 as const;
```

**网格常量**（`gridConstants.ts`）

```ts
export const BREAKPOINTS_PX = { lg: 1024, md: 0 } as const;
export const COLS_PER_BP    = { lg: 12,   md: 12 } as const;
export const COLS = 12;
export const BREAKPOINT_KEYS: readonly BreakpointKey[] = ['lg', 'md'] as const;
export const ROW_HEIGHT = 8;   MARGIN_Y = 16;   MARGIN_X = 16;   FIT_PADDING_PX = 0;
// 每行单位 = 8 + 16 = 24px
pxToRows(px) = Math.max(1, Math.ceil((px + MARGIN_Y) / (ROW_HEIGHT + MARGIN_Y)))
```

**`DashboardGrid.tsx`（481 行）**

```tsx
<ResponsiveGridLayout
  width={width}                                   // useContainerWidth
  className={`widget-grid ${editMode ? 'widget-grid--edit' : ''}`}
  layouts={rglLayouts} breakpoints={BREAKPOINTS_PX} cols={COLS_PER_BP}
  rowHeight={ROW_HEIGHT} margin={[MARGIN_X, MARGIN_Y]} containerPadding={[0, 0]}
  dragConfig={{ enabled: editMode, handle: '.widget-drag-handle',
                cancel: '.widget-drag-cancel, .react-resizable-handle' }}
  resizeConfig={{ enabled: editMode }}
  onLayoutChange onDragStart onDragStop onResizeStart onResizeStop onBreakpointChange
/>
```

关键行为：
- **拖拽/缩放只在编辑模式开启**。编辑模式下整张 `WidgetFrame` 带 `widget-drag-handle`（卡片任意位置可抓），头部动作按钮带 `widget-drag-cancel`
- **手势批处理**：`onLayoutChange` 每帧都触发；用 `isGesturingRef` + `pendingLayoutsRef` 把提交推迟到 `onDragStop` / `onResizeStop`。`if (!editMode) return;` 在浏览模式下彻底屏蔽提交
- **reconcile 回写**：一个 effect 按断点比对"已存布局"与 `reconcileLayouts()` 输出，注册表的 min/max 边界变化时把修正后的布局刷回 prefs（**不是只在挂载时闩一次**）
- **fit-to-content**：`fitHeightRef.current(id, totalCellPx)` → `targetRows = clamp(pxToRows(px + FIT_PADDING_PX), def.minSize.h, def.maxSize?.h)`，把 `{h, minH, maxH} = targetRows` 写进**每个**断点。手势期间跳过（`commitLayouts` 在手势结束时重读 `fittedHeightRef`）
- **每 widget 的稳定回调**缓存在 `callbackCacheRef: Map<id, {onFitHeight, updateConfig}>`，widget 消失时清理。**两层 `useMemo`**：`widgetBodies` 只依赖 `prefs.widgets`，`widgetChildren` 再加 `editMode` —— 这样纯布局的 prefs 更新不会重渲染约 20 棵 widget 子树
- `WidgetErrorBoundary`（class 组件）隔离单个 widget 崩溃；`Suspense` + `WidgetFallback` 供懒加载 widget（ChartWidget）
- `handleRemove(id)` 同时过滤 widget 和布局项；`handleDuplicate(id)` 克隆 config、`singleton` 则拒绝、放到 `maxY`

**`reconcile.ts`**：`reconcileLayouts(widgets, layouts)` —— 丢弃孤儿布局项、给缺失 widget 自动放到底部（用 `defaultSize`，兜底 `{w:4,h:3}`）、夹取 `w`/`h` 并从定义盖上 `minW/minH/maxW/maxH`。**`fitToContent` widget 的 `minH`/`maxH` 保留上次写入值而不是重置成 `def.minSize.h`**。`placeAtBottom(layouts, newId, w, h)` 往每个断点追加 `{i, x:0, y:maxY, w: Math.min(w, COLS), h}`。

**`WidgetFrame.tsx`（286 行）+ `.css`（386 行）**

外框 = 头部（`GripVertical` 拖拽柄、`t(definition.titleKey)` 标题、动作区）+ 主体。浏览模式下头部由 CSS 折叠（`.widget-frame:not(.widget-frame--edit) .widget-frame__header`），只在悬停时显现回形针；编辑模式显现设置齿轮 + `MoreVertical` 菜单（复制 —— singleton 隐藏 —— 和移除）。

回形针 → `getWidgetContextSnapshot(instance.id)` → `ContextBus.attach(snapshot)` + toast。以下情况禁用：`definition.source === 'tradingview'`（跨域 iframe，像素和 DOM 都够不着）、或没有注册导出器（`useHasWidgetContextExporter`）。

**fit-to-content 测量**：内层 div 上挂 `ResizeObserver`，`total = inner.offsetHeight + bodyPaddingV + headerH`。**变高立即提交，变矮走 120ms 尾防抖** —— 注释说明这是为了避免 `AIDailyBriefCard` 300ms 抽屉动画期间出现滚动条，以及两个 fit widget 之间来回震荡。

CSS 要点：`.widget-grid--edit .widget-frame__body iframe` 编辑期间失活；`.react-grid-item:has(.widget-frame--fit)` 关掉 RGL 的高度过渡；`.widget-frame a[href*="utm_medium=lwc-link"]` 隐藏 lightweight-charts 水印（TradingView 的署名通过 `.tv-attribution` 白名单保留 —— **这是许可要求，不许一起删**）。

**`AddWidgetDialog.tsx`（561 行）**

两栏对话框 `!max-w-[1080px] w-[96vw] h-[86vh]`，`grid-cols-[280px_minmax(0,1fr)]`。

左栏：眉标 + 标题、搜索框（自动聚焦）、带彩点和计数的分类导航，底部一个提示框。
```ts
CATEGORY_META = {
  markets:  { order: 1, dot: '#E0B341' },
  intel:    { order: 2, dot: '#5BA47F' },
  personal: { order: 3, dot: '#C4A36B' },
  agent:    { order: 4, dot: '#C4574F' },
  workspace:{ order: 5, dot: '#5DA372' },
}
```
搜索同时匹配 `w.type`（**保留英文原文**，方便老手直接打 `chart.symbol`、`tv.movers`）、翻译后的标题、翻译后的描述。

右栏：`WidgetCard` 网格（`md:grid-cols-2`）—— 图标块、标题、singleton/多实例徽章、`source === 'tradingview'` 时的 TV 徽章、描述、元信息行 `` `${w}w × ${h}h · ${configurable|noSettings}` ``。非搜索态先列当前分类，再用分隔标题列其余分类。

选中模型：自动选中第一个可用 widget；单击选中，**双击添加**，Enter 添加（焦点在 INPUT 里时除外）。底部 Cancel + "添加 {title} →"（选中的是已存在的 singleton 时禁用）。

**预设**（`widgets/presets.ts`，313 行）

```ts
export type PresetId = 'morning-brief' | 'researcher' | 'trader' | 'trader-tv'
                     | 'portfolio-steward' | 'agent-desk';
export interface PresetMeta {
  id: PresetId; popular?: boolean;
  nameKey; tagKey; descriptionKey; bestForKey; pillKeys: string[];
}
```

每个预设是一个返回 `Pick<DashboardPrefs, 'widgets'|'layouts'|'version'>` 的工厂；`makePrefs` 设 `layouts: { lg: layoutsLg, md: layoutsLg }`（两个断点相同）。id 每次调用由 `newWidgetId(prefix)` 现生成。

| 预设 | 组成 |
|---|---|
| `morning-brief`（默认、`popular`） | markets.overview、insight.brief、personal.portfolioWatchlist、news.feed(market)、calendar.earnings |
| `researcher` | 再加 agent.conversation；news.feed 做成右侧高栏；calendar.earnings `{window:'2w', tickers:'all'}` |
| `trader` | markets.overview + 4 个 `chart.symbol`（NVDA 5min/30min/1day 蜡烛+面积、SPY 1day 面积）+ portfolioWatchlist + news.feed |
| `trader-tv` | tv.ticker-tape、tv.stock-heatmap、tv.symbol-spotlight、tv.movers、tv.economic-events、tv.technicals、markets.miniChartGrid(`DEFAULT_BLUE_CHIPS`) |
| `portfolio-steward` | markets.overview、chart.symbol(NVDA 1day)、portfolioWatchlist、agent.conversation、news.feed(portfolio)、threads.recent |
| `agent-desk` | insight.brief、agent.conversation、workspace.picker、threads.recent、automations.list、portfolioWatchlist（**故意没有大盘概览** —— 这个预设是"上下文优先"不是"行情优先"） |

`PresetsDialog.tsx` + `presets/Thumbnails.tsx`（712 行）：`Thumbnails` 是**纯 SVG 模拟渲染器** —— `getPreset(id)` → 把每个 `RGLItem` 画成矩形，配色写死成纸感调色板（`C.paper #FDFBF6`、`C.gain #7A9A67`、`C.loss #B3362F`、`C.highlight #F7D76A`…），按 `TYPE_VISUALS[type] → {bg, stroke}` 上色，再叠一个 `WidgetGlyph` 图形符号（迷你蜡烛、列表行、行情带）。

**添加/设置流程**（`DashboardCustom.tsx`）：`handleAddWidget(type)` → `def.initConfig ? def.initConfig(ctx) : {...def.defaultConfig}` → `{id: newWidgetId(), type, config}` → `update(prev => singleton 守卫; placeAtBottom(...))`。设置对话框由外壳渲染（**不是由 frame 渲染**）：`settingsFor` id → 找到实例 → `def.settingsComponent` → `onChange(patch)` 合并进该实例 config。`applyPreset` / `resetToDefault` 之前必须先 `setSettingsFor(null)`，避免悬空 id。

#### 2.2.4 widget 全量清单（`widgets/definitions/`）

尺寸单位是网格单元（12 列，每行 24px）。

**原生 widget**

| type | 文件 | 渲染 | 数据源 | 尺寸 默认/最小/最大 | 标记 |
|---|---|---|---|---|---|
| `markets.overview` | `MarketsOverviewWidget.tsx` | 包 `IndexMovementCard`，`ResizeObserver` 在 `COMPACT_WIDTH_PX = 640` 以下翻 `forceMobile` | `useDashboardContext().dashboard.indices` | 12×11 / 3×11 / 12×11 | singleton |
| `insight.brief` | `InsightBriefWidget.tsx` | 包 `AIDailyBriefCard`（传 `instanceId` + `onReadFull = modals.openInsight`），快照导出器读 `getCachedInsights()` | insights 模块缓存 | 8×18 / 4×15 / 12×44 | singleton, **fitToContent** |
| `news.feed` | `NewsFeedWidget.tsx`（573 行） | 见下 | context | 8×29 / 4×18 | — |
| `calendar.earnings` | `EarningsCalendarWidget.tsx` | 未来财报按 `today/tomorrow/week/next/later` 分桶。`useQuery(['earnings-calendar', todayStr, toStr])` → `getEarningsCalendar`，`staleTime 5min`；过滤掉含 `.` 的代码；窗口 `1w/2w/1m` → `WINDOW_DAYS`。**用本地日期串**，UTC 以东用户不会丢"今天" | `GET /api/v1/calendar/earnings` | 4×26 / 3×15 | — |
| `watchlist.list` | `WatchlistWidget.tsx` | 只有自选行 | context `watchlist` | 4×26 / 3×15 | — |
| `portfolio.holdings` | `PortfolioWidget.tsx` | 持仓 + 净值 + `valuesHidden` | context `portfolio` | 4×26 / 3×15 | — |
| `personal.portfolioWatchlist` | `PortfolioWatchlistWidget.tsx` | 组合 tab 卡的 widget 原生重实现（`defaultTab` + `valuesHidden` 存在 **widget config 里，不是 localStorage**） | context | 4×30 / 3×18 | — |
| `automations.list` | `AutomationsWidget.tsx` | 自动化行 + 启停 + 立即运行 | `useAutomations` / `useAutomationMutations` | 4×22 / 3×14 | — |
| `agent.conversation` | `ConversationWidget.tsx` | 内联 `ChatInput` 控制台 + 最近线程列表 | `useWorkspaces({limit:100})`、`getWorkspaceThreads` | 12×18 / 8×12 / 12×44 | singleton, **fitToContent** |
| `workspace.picker` | `WorkspacePickerWidget.tsx` | 工作区磁贴（置顶/flash 徽章、相对时间）→ 导航 + `clearChatSession` | `useWorkspaces` | 6×22 / 3×15 | — |
| `threads.recent` | `RecentThreadsWidget.tsx` | 最近线程，`workspaceId: 'all' \| 'current' \| <uuid>` + `limit` | `getRecentThreads` / `getWorkspaceThreads` | 6×22 / 3×15 | — |
| `markets.miniChartGrid` | `MiniChartGridWidget.tsx` | N 个迷你走势格 + 实时报价。`initConfig` 优先从自选取前 12 个，否则 `DEFAULT_BLUE_CHIPS` | `useQuotes` + bars query | 12×16 / 6×10 | 有设置 |
| `chart.symbol` | `ChartWidget.register.tsx` → 懒加载 `ChartWidget.tsx` | 见下 | REST bars + WS | 6×22 / 3×15 | 有设置, 懒加载 |

`_holdingsHelpers.ts` + `_holdingsPrimitives.tsx` 是 `portfolio.holdings` 和 `personal.portfolioWatchlist` 共用的行/汇总原语。

**TradingView widget**（`definitions/tv/`，全部 `source: 'tradingview'`，全部带 `settingsComponent`）

| type | defaultConfig | 尺寸 默认/最小/最大 |
|---|---|---|
| `tv.ticker-tape` | `{symbols: [], displayMode:'adaptive'}` | 12×6 / 6×3 / 12×12，**fitToContent**；`initConfig` **故意保持 `symbols: []`**，让行情带在渲染时从默认+自选+持仓（去重）实时播种 |
| `tv.stock-heatmap` | `{dataSource:'SPX500', blockSize:'market_cap_basic', blockColor:'change'}` | 12×22 / 6×12 / 12×40 |
| `tv.crypto-heatmap` | `{dataSource:'Crypto', blockSize:'market_cap_calc', blockColor:'24h_close_change\|5'}` | 12×20 / 6×12 / 12×40 |
| `tv.forex-heatmap` | `{currencies: DEFAULT_CURRENCIES}`（9 个币种） | 12×18 / 6×10 / 12×32 |
| `tv.etf-heatmap` | `{dataSource:'AllUSEtf', blockSize:'aum', blockColor:'change', grouping:'asset_class'}` | 12×20 / 6×12 / 12×40 |
| `tv.economic-events` | `{importanceFilter:'-1,0,1', countryFilter:'us,eu,jp,gb,cn'}` | 6×24 / 4×14 / 12×48 |
| `tv.economic-map` | `{region:'global', metric:'gdp', hideLegend:false}` | 12×18 / 6×18 / 12×32 —— **唯一走 web component 的**（`<tv-economic-map>`） |
| `tv.technicals` | `{symbol:'NASDAQ:NVDA', interval:'1D'}` | 6×22 / 4×22 / 12×32（最小高度调高过，否则仪表盘被裁） |
| `tv.movers` | `{exchange:'US', dataSource:'AllUSA'}` | 6×22 / 4×14 / 12×40 |
| `tv.symbol-spotlight` | `{symbol:'NASDAQ:NVDA', range:'12M'}` | 6×22 / 4×14 |
| `tv.single-ticker` | `{symbol:'NASDAQ:NVDA'}` | 3×4 / 2×3 / 6×6 |
| `tv.symbol-info` | `{symbol:'NASDAQ:NVDA'}` | 6×8 / 4×6 / 12×12 |
| `tv.company-profile` | `{symbol:'NASDAQ:NVDA'}` | 6×18 / 4×12 / 12×32 |
| `tv.company-financials` | `{symbol:'NASDAQ:NVDA', displayMode:'regular'}` | 6×24 / 4×14 / 12×48 |
| `tv.screener` | `{market:'america', defaultColumn:'overview', defaultScreen:'general'}` | 12×24 / 6×14 / 12×48 |
| `tv.crypto-screener` | `{defaultColumn:'overview', defaultScreen:'general'}` | 12×24 / 6×14 / 12×48 |
| `tv.top-stories` | `{feedMode:'market', market:'stock', symbol:'NASDAQ:NVDA', displayMode:'regular'}` | 6×22 / 4×14 / 12×40 |

除 `tv.economic-map` 外，所有 TV 嵌入都走 `TradingViewEmbed`：往 iframe 注入 `<script src="https://s3.tradingview.com/external-embedding/embed-widget-*.js">`，**200ms 重建防抖 + 10s 看门狗 → `EmbedFallback`（带重试按钮）**。`tvConfig.getTVCommonConfig()` 提供 `{autosize, width:'100%', height:'100%', isTransparent:true, backgroundColor:'rgba(0,0,0,0)', locale: mapLocaleForTV(i18n.language), support_host, largeChartUrl}`。

**`ChartWidget.tsx`（1468 行，全 Dashboard 最大文件）**

**必须懒注册**，让 `lightweight-charts`（约 200KB gzip）不进 dashboard chunk：

```ts
const LazyChartWidget   = lazy(() => import('./ChartWidget'));
const LazyChartSettings = lazy(() => import('./ChartWidget').then(m => ({default: m.ChartSettings})));
type ChartConfig = {
  symbol: string;
  interval: '1min'|'5min'|'15min'|'30min'|'1hour'|'1day';
  chartType: 'candle'|'area'|'line';
};
const DEFAULT_CONFIG = { symbol: 'NVDA', interval: '1day', chartType: 'candle' };
```

- 基于 `lightweight-charts`（`createChart` / `ColorType` / `CrosshairMode` / `LineStyle` / `LineType`）
- **整套复用 MarketView 的机器**：`@/lib/bars` 的 `INTERVALS` / `INTERVAL_SECONDS` / `WS_FOLD_INTERVALS` / `fetchStockData` / `centerLatestBarView` / `computeInitialLoadRange` / `dedupeMergeByTime` / `rangeBeforeOldest` / `foldMinuteBar` / `useLiveBars` / `useCurrencyDisplay`，以及 `@/pages/MarketView/utils/chartConstants` 的 `STAGE2_BACKFILL_DAYS` / `SCROLL_CHUNK_DAYS` / `SCROLL_LOAD_THRESHOLD` / `RANGE_CHANGE_DEBOUNCE_MS` / `EXTENDED_HOURS_INTERVALS` / `TARGET_BAR_SPACING` / `computeExtendedHoursRegions` + `ExtendedHoursBgPrimitive`
- `4hour` 被 `EXCLUDED_INTERVALS` 排除（yfinance 档位不支持）
- 加载路径：先按 (symbol, interval) 做首次 `fetchStockData`（**每条取数路径一个 `AbortController`**），然后让出主线程做**第二阶段后台回填**；向左滚动触发 `fetchAndPrepend(SCROLL_CHUNK_DAYS[interval])`，由 `fetchingRef` 守卫
- 实时 tick 走 `useMarketDataWSContext()`（这就是 `DashboardCustom` 要包 `MarketDataWSProvider` 的原因）：1min 原生消费 WS 秒级 tick；5min~1hour 用 `foldMinuteBar` 折进 REST 播种的成形桶；**1day 在 widget 里只轮询不吃 WS**。缺口检测走一次性 REST 桥接，最多重试 3 次
- 时间戳用"场馆墙钟当 UTC"的约定（格式化器传 `timeZone: 'UTC'`），这样月份/星期名会本地化但时钟读数是市场本地时间
- 工具条：`WIDGET_INTERVALS` 周期按钮、`ZoomIn`/`ZoomOut`/`RotateCcw` 重置、`Maximize2`、`ExternalLink` → `/market`。代码选择器用 `searchStocks`。重新取数只显示角落的低调 spinner
- 主题响应：`useTheme()` + `getChartTheme()`；**图表状态用 `createValueStore` + `useSyncExternalStore` 放在 React 之外**
- 通过 `useWidgetContextExport` + `serializeOhlcvToMarkdown` / `summarizeOhlcv` 把 OHLCV 导给 agent

**`NewsFeedWidget.tsx`（573 行）**

```ts
type NewsFeedSource = 'top' | 'market' | 'portfolio' | 'watchlist';
type NewsFeedConfig = { source?: NewsFeedSource; limit?: number };
type DateRangeKey = 'all' | '1h' | '6h' | '24h' | '7d';
```

- 四个 tab 全部来自 `useDashboardContext()`：`top → dashboard.curatedItems`（TickerTick，游标分页）、`market → dashboard.newsItems`、`portfolio → portfolioNews.items`、`watchlist → watchlistNews.items`
- **tab 选择持久化进 widget config**：`switchTab` 调 `updateConfig({source: key})` 并重置所有筛选
- **无限滚动只在 Top tab**：哨兵元素上挂 `IntersectionObserver`，`root: scrollRef.current`、`rootMargin: '500px'`；分页状态通过 `pageStateRef` 读取，避免每页重建 observer
- 筛选：ticker 搜索（子串、转大写）、发布方 `<select>`（**从筛选前的列表构面**）、时间范围 chip。**时间过滤用原始 `publishedAt` ISO，不是显示字符串** —— 这是相对 Classic 卡片的明确修复
- 行：16×12 缩略图、情绪圆点（`isHot → --color-profit`）、favicon、来源、`Clock` + 相对时间、2 行截断标题、最多 4 个 ticker chip + "+N"
- 点击 → `modals.openNews(item.id, {title, source, publishedAt, tickers, articleUrl, author, description, keywords, sentiments, imageUrl, favicon})` —— 这份富 fallback 让 `NewsDetailModal` 跳过按 id 取数
- 每行还带 `RowAttachButton`；widget 同时注册 `full` 导出器（**筛选后**列表的 markdown 表 + tab/筛选属性）和 `rows(rowId)` 导出器（→ `buildNewsArticleSnapshot`）

#### 2.2.5 `utils/api.ts` 端点全表（630 行）

| 函数 | 方法 + 路径 | 说明 |
|---|---|---|
| `getIndex(symbol)` | `GET /api/v1/market-data/intraday/indexes/:symbol` | 日内 bar → 迷你走势；过滤到 09:30–16:00 ET 正常时段，取**确实有 RTH 数据点**的最新日期（VIX 隔夜 bar 守卫） |
| `getIndices(symbols)` | 组合调用 | `Promise.all([getSnapshotIndexes(list), ...每个 symbol 的 getIndex])` → `{indices, failedCount}` |
| `getStockCompanyNames(symbols)` | `POST /api/v1/market-data/stocks/names` | 失败吞掉返回 `{}` |
| `getStockPrices(symbols)` | 经 `getSnapshotStocks` | → `StockPrice[]`（`snapshotToStockPrice`） |
| `fetchHello()` | `GET /hello`（`responseType:'text'`） | 健康检查 |
| `createUser` / `getCurrentUser({refresh_tier?})` / `updateCurrentUser` | `POST\|GET\|PUT /api/v1/users[/me]` | |
| `getPreferences` / `updatePreferences` / `clearPreferences` | `GET\|PUT\|DELETE /api/v1/users/me/preferences` | **仪表盘布局的持久化路径**；`getPreferences` 404 返回 `null` |
| `uploadAvatar(file)` | `POST /api/v1/users/me/avatar`（multipart） | |
| 自选清单 | `GET\|POST /api/v1/users/me/watchlists`、`PUT\|DELETE …/watchlists/:id` | |
| 自选条目 | `GET\|POST /api/v1/users/me/watchlists/:wid/items`、`PUT\|DELETE …/items/:id` | `getWatchlistItems()` 是**废弃 shim** → `listWatchlistItems('default')`，新实现删掉 |
| 持仓 | `GET\|POST /api/v1/users/me/portfolio`、`PUT\|DELETE …/portfolio/:id` | |
| Codex OAuth | `POST /api/v1/oauth/codex/device/initiate\|poll`、`GET …/status`、`DELETE /api/v1/oauth/codex` | 设备码流程 |
| Claude OAuth | `POST /api/v1/oauth/claude/initiate\|callback`、`GET …/status`、`DELETE /api/v1/oauth/claude` | PKCE 粘贴回填 |
| `getNews({tickers,limit=20,cursor,provider})` | `GET /api/v1/news` | `tickers` 逗号连接。**失败要重新抛出** —— 让 React Query 重试并保住上一份好数据 |
| `getNewsArticle(articleId)` | `GET /api/v1/news/:articleId` | |
| `getTodayInsights()` | `GET /api/v1/insights/today` | 返回 `data.insights ?? []`，吞错误 |
| `getInsightDetail(id)` / `generatePersonalizedInsight()` | `GET /api/v1/insights/:id` / `POST /api/v1/insights/generate` | |
| `getEarningsCalendar({from,to})` | `GET /api/v1/calendar/earnings` | 宽口径日历，吞错误 → `{data:[],count:0}` |
| `getEarningsDates({from,to})` | `GET /api/v1/calendar/earnings/dates` | **用户自己跟踪的**代码（持仓 + 自选）的财报 |
| `getMacroCalendar({from,to})` | `GET /api/v1/calendar/macro` | FRED 宏观事件 `MacroEvent{date,event,label_zh,importance,category,source,country}` |
| 再导出 | — | `getAvailableModels` / `getUserApiKeys` / `updateUserApiKeys` / `deleteUserApiKey`（来自 `@/api/model`）；`getSnapshotIndexes` / `getSnapshotStocks`（来自 `@/lib/quotes/snapshotApi`） |

`ConnectBrokerDialog.tsx` 另有 `POST /api/v1/brokers/ibkr/test`、`POST /api/v1/brokers/ibkr/sync`。

导出常量：`INDEX_SYMBOLS = ['GSPC','IXIC','DJI','RUT','VIX']`、`INDEX_NAMES`、`DEFAULT_WATCHLIST_SYMBOLS = ['AAPL','MSFT','NVDA','AMZN','TSLA']`、`DEFAULT_WATCHLIST_NAMES`、`buildIndexData`、`fallbackIndex`、`normalizeIndexSymbol`。

其它 `utils/`：`insightFetch.ts`、`newsArticleFetch.ts`、`newsItem.ts`（`DashboardNewsItem`、`mapNewsResults`、`NEWS_POLL_INTERVAL_MS`、`NEWS_STALE_MS`）、`portfolioSummary.ts`、`sourceColor.ts`、`workspace.ts`（`ensureFlashWorkspace`）。

#### 2.2.6 Dashboard 的 hook

| Hook | 契约 |
|---|---|
| `useDashboardData.ts` | `marketStatus`（`['dashboard','marketStatus']`，60s 轮询 / 30s stale）。指数走共享 `useQuotes(INDEX_SYMBOLS,{isIndex:true})` + 独立的走势查询 `['dashboard','indexSparklines',…]`，**自适应轮询：开盘 30s / 休市 60s**。`newsItems` = `['dashboard','news']` + `getNews({limit:50})`。`curatedItems` = `useInfiniteQuery(['dashboard','curatedNews'])` + `getNews({provider:'tickertick',limit:50,cursor})`，`getNextPageParam: lastPage.next_cursor`，且 **加载超过 1 页后停止轮询**（第 2 页起绕过服务端缓存）。所有轮询 `refetchIntervalInBackground: false` |
| `useWatchlistData.ts` | `['watchlistData']` —— 先列清单再取条目；用 `localStorage['watchlist_last_id']` 做**投机性并行取数**，该 key 通过 `registerAuthReset` 在登出时清除。报价走 `useQuotes(symbols)`。暴露 `rows, loading, modalOpen, setModalOpen, currentWatchlistId, fetchWatchlist, handleAdd, handleDelete`。409 / "already exists" 有专门的 toast |
| `usePortfolioData.ts` | `['portfolioData']`，60s 轮询 / 30s stale。计算 `marketValue = qty*price`、`unrealizedPlPercent = (price-avgCost)/avgCost*100`、`quoteAvailable` 遮蔽。**`handleDelete(id)` 返回一个确认配置而不是直接删**；编辑表单状态 `editRow` / `editForm{quantity,averageCost,notes}` / `openEdit` / `handleUpdate`（带 `>0` 校验）。`NumericValueOutOfRange` 映射到专门的 toast |
| `useTickerNews.ts` | `['dashboard','tickerNews', cacheKey, provider ?? null, sortedTickerCsv]`，`enabled: tickers.length > 0`。**Classic 不传 provider，Custom 传 `'tickertick'`** —— 两套 feed 因此不会互相串数据 |
| `useChatInput.ts` | `mode: 'fast' \| 'ptc'`（默认 `'ptc'`；用户一个非 flash 工作区都没有时**自动**回落 `'fast'`，直到用户显式选择）、`selectedWorkspaceId`、`isLoading`。`handleSend` 导航到 `/chat/t/__default__`（fast 模式经 `getFlashWorkspace` 拿 flash 工作区），载荷放在 location state 里；`MAX_LOCATION_STATE_BYTES = 5MB` 是 structured-clone 的安全网；并把 `ContextBus` 的 widget 快照经 `widgetSnapshotsToContexts` 合并进去 |
`DashboardDataContext.tsx`（仅 Custom）把上述 hook 聚成一个 provider，值为 `{dashboard, watchlist, portfolio, portfolioNews, watchlistNews, portfolioHandlers, watchlistHandlers, brokerModal, modals}`。`modals` 暴露 `openNews(id, fallback?)`、`closeNews`、`openInsight`、`closeInsight`、`deleteConfirm`、`requestDeleteConfirm`、`runDeleteConfirm`、`cancelDeleteConfirm`。**每一块都要 memo**，否则 provider 的无关重渲染会让所有消费者失效。`useDashboardContext()` 在 provider 外调用直接 throw。

#### 2.2.7 浮动聊天框与编辑工具条

**浮动聊天（`ChatInputCard.tsx`）**

- 桌面：`fixed bottom-8 left-[var(--sidebar-width)] right-0 z-40 flex justify-center pointer-events-none`，内层 `pointer-events-auto w-full max-w-2xl px-4`，类名 `sidebar-tracking`。**理由**：fixed 定位无视 `<main>` 的侧栏内边距，所以它要往内容列缩进而不是往视口缩进（这正是 §1.3 把 `--sidebar-width` 发布在 `documentElement` 上的原因）
- 建议气泡：4 个 chip，**只在 `focused` 时挂载**（否则不进 DOM / 无障碍树 / tab 顺序），`animationDelay: i*60ms` 错开，`onMouseDown preventDefault` 使点击不会让输入框失焦，点击 → `chatInputRef.current.setValue(label)`
- 焦点追踪用 `onFocus`/`onBlur` + `!e.currentTarget.contains(e.relatedTarget)`
- 移动：`MobileFabChat` 折成一个 logo 悬浮球，`bottom: calc(var(--bottom-tab-height, 0px) + 8px)`；发送后收起
- Custom 模式下，画布上有 `agent.conversation` 或处于编辑模式时**隐藏**（会重叠）

**编辑工具条（`DashboardCustom.tsx`）**

仅 `editMode` 时渲染。`fixed left-1/2 sidebar-tracking z-40 bottom: 1.5rem`，圆角药丸 + 边框 + 阴影。居中用 `transform: translateX(calc(-50% + var(--sidebar-width) / 2))` —— **用 transform 而不是改 `left`**，因为盒子是 `width:auto`，改 `left` 会把整行压扁。

按钮：**+ 添加 widget**（主色填充）→ `AddWidgetDialog`；**预设**（`Layers`）→ `PresetsDialog`；**重置**（`RotateCcw`）→ `ConfirmDialog` → `resetToDefault()`（= `applyPreset('morning-brief')`）；分隔线；**完成**（`X`）→ `setEditMode(false)`。

布局补偿：`<main>` 加 `paddingBottom: bottomGutter`（编辑 `6rem` / 显示聊天卡 `8rem` / 否则 `0`）—— **放在滚动区内部**，这样 widget 停在覆盖层上方，又不会在视口底部露出外层容器背景。

#### 2.2.8 布局持久化

**存在服务端的用户偏好里，布局本身不用 localStorage。**

存储路径：`UserPreferences.other_preference.dashboard`（一个 `DashboardPrefs` 形状的 JSON blob），经 `PUT /api/v1/users/me/preferences` 写入。

**`dashboardPrefsWriter.ts` —— 唯一的写入器**，三件事：

1. **最小载荷**：PUT 只带 `{ other_preference: { dashboard: next } }`。后端对 `other_preference` 顶层键做**浅合并**（JSONB `||`），兄弟键（theme / locale / onboarding / providers）在服务端自然存活；把它们一起重发反而会用旧值回放覆盖别的 tab 的新写入。
2. **跨 tab 广播**：`export const BROADCAST_CHANNEL = 'dashboard-prefs'`，成功后 post `{type:'updated'}`。每个 hook 实例一个 `BroadcastChannel`，卸载时 `close()`。
3. **冷缓存保护**：`if (fresh === undefined && opts?.fallbackOther === undefined) return false;` 返回 `boolean`（接受 / 拒绝）。

**`useDashboardPrefs.ts` —— 读写 hook**

- `HISTORY_CAP = 3`，`DEBOUNCE_MS = 800`
- 读：`usePreferences()` → `migrateDashboardPrefs(raw) ?? emptyPrefs()`；另留一份 `local` 供乐观编辑，用 `ownWriteInFlightRef` 防止**自己**那次写入触发的服务端回程把本地状态砸掉
- `update(patch | fn, {immediate?})` —— `isLoading` 时**整个丢弃**（冷缓存门禁）；否则 800ms 防抖，或立即刷写
- `flush(next)` —— **仅开发环境的 20KB 体积陷阱**（`new Blob([JSON.stringify(next)]).size > 20_000` 就 `console.warn`），再调 `writeDashboardPrefs`；`onError` 清守卫 + 破坏性 toast "无法保存仪表盘 / 已恢复上次保存的布局"
- 跨 tab 接收：`BroadcastChannel('dashboard-prefs')` 的 `onmessage` 失效 `queryKeys.user.preferences()`，**但只在没有排队防抖、也没有飞行中写入时**；否则设 `replayPendingRef`，等当前编辑落定后再排空（`runReplay`）。没有 `BroadcastChannel` 的环境（Safari < 15.4）静默降级 —— `staleTime: 0` + `refetchOnWindowFocus` 已经能覆盖切窗口的场景
- `setMode(mode)` —— 立即写；首次翻到 `'custom'` 且零 widget 时播种 `morning-brief`
- `applyPreset(presetId)` —— 立即写；替换前先把 `{widgets, layouts}` 压进 `history`（上限 3）。`resetToDefault()` = `applyPreset('morning-brief')`
- 返回 `{prefs: local, stored, isLoading, setMode, update, applyPreset, resetToDefault}`

> `history` 被写入并限长，但**这个目录里没有任何 UI 读它** —— 撤销功能尚未实现。新实现要么补上撤销 UI，要么删掉这个字段，别留半截。

**`migrations.ts` —— 加载边界**（`migrateDashboardPrefs(raw) → DashboardPrefs | null`）

- `TYPE_RENAMES = { 'agent.input': 'agent.conversation' }`，**在 sanitize 之前**应用（否则找不到正确的定义/schema）
- `isValidWidgetInstance` 丢弃"不是对象 / `id`·`type` 不是字符串 / `config` 不是非空对象"的条目 —— `widgets: [null, "garbage", {id:'x'}]` 曾经直接把渲染器打崩
- `sanitizeConfig` 跑定义自带的 `configSchema.safeParse`；逐字段 `.catch()` 恢复单个字段，整体失败则重置成 `{...def.defaultConfig}` 并在开发环境 warn
- 逐断点过滤布局：任何 `layouts[bp]` 不是真数组就丢掉（否则 `reconcileLayouts` 会崩）
- 永远盖上 `version: DASHBOARD_PREFS_VERSION`

**Dashboard 目录实际用到的 localStorage key**

| key | 归属 | 内容 |
|---|---|---|
| `portfolio_active_tab` | `PortfolioWatchlistCard.tsx` | `'watchlist' \| 'portfolio'`（**仅 Classic**，widget 版存 config） |
| `portfolio_values_hidden` | 同上 | `'true' \| 'false'` |
| `watchlist_last_id` | `useWatchlistData.ts` | 投机取数用的自选清单 id；`registerAuthReset` 清除 |
| `kairos_ibkr_flex_credentials` | `ConnectBrokerDialog.tsx` | JSON `{provider,query_id,token,account}` |

滚动位置另经 `useScrollMemory` 存在 `'page:dashboard'` / `'page:dashboard-custom'`。

---

### 2.3 MarketView

入口 `MarketView.tsx`（809 行）导出 `MarketView = <MarketDataWSProvider><MarketViewInner/></MarketDataWSProvider>`。

**功能**：K 线工作台 —— 图表（蜡烛 + 均线 + 成交量 + RSI + 盘前盘后底色 + 财报/评级/目标价覆盖层）、自选/持仓侧栏、公司概况抽屉，以及一个侧边聊天面板，agent 可以**直接往图上画注解**，用户也可以**把选中的图表区域/价格位加上批注交给 agent**。

**主要交互**

- **代码搜索**：复用 `pages/Dashboard/components/DashboardHeader` 的 `onStockSearch`；`handleStockSearch(symbol, searchResult)` 设 `selectedStock` + `selectedStockDisplay` 覆写，清掉 `chartMeta`/`showOverview`。侧栏点击和 URL `?symbol=` 走同一条路
- **周期**：`INTERVALS`（来自 `@/lib/bars/chartConstants`）；只有 `1min` 和 `1day` 是行内按钮（`PRIMARY_INTERVAL_KEYS`），其余进下拉。存 `localStorage['market-chart:interval']`，**非法值回落 `1day`**
- **图表工具条**：指标下拉（MA 5/10/20/50/100/200、RSI 7/14/21、覆盖层 Earn/Grade/PT）、工具（百分比刻度、磁吸 `M`、基线 `B`、注解 `T`）、视图（对数、缩放 ±、自动适配、全览、回到实时）、模式切换 **Light**（自绘 LWC）/ **Advanced**（`TradingViewWidget`）、选区工具 `SquareDashedMousePointer`（区域）+ `Ruler`（价格位）。工具条按 `ResizeObserver` 对照 `TOOLBAR_WIDTH_BREAKPOINTS = [1180, 880, 710, 560]` 折叠成 `toolbarLevel: 0..4`
- **平移加载**：`subscribeVisibleLogicalRangeChange` 防抖 `RANGE_CHANGE_DEBOUNCE_MS = 300`；距左缘 `SCROLL_LOAD_THRESHOLD = 20` 根 bar 以内时，往前补 `SCROLL_CHUNK_DAYS[interval]` 天
- **图表截图**：命令式句柄 `MarketChartHandle { captureChart(): Promise<Blob|null>; captureChartAsDataUrl(): Promise<string|null>; getChartMetadata(): Record<string,unknown>|null }`（html2canvas）。`handleCaptureChart` 下载 PNG；`handleCaptureChartForContext` 产出 `chartImage`（data URL）+ `chartImageDesc`（多行文本：代码/名称/交易所、图表模式、周期、日期区间 + bar 数、均线说明、`RSI(n)`、最新 OHLCV、52 周高低、实时价）
- **选区 → agent（确认后才加入）**：在图上拖拽 → `chartSelectionStore.beginDraft()`（状态 `pending`，内联批注框打开）→ 点"添加" → `confirm(id, note)`（显示 chip + 图钉）→ 随下一次发送带出 → `clearAll()`。拖拽距离小于 `MIN_DRAG_PX = 4` 算点击。区域选区最多携带 `MAX_SELECTION_BARS = 300` 根 OHLCV（超出降采样，**必须 ≤ 服务端 500 的上限**）+ 一张裁剪 JPEG
- **聊天面板宽度**：拖 `.market-resize-handle`，夹在 `300 … min(700, 40vw)`，存 `localStorage['market-chat-width']`
- **快捷提问**：8 条模板（`QUICK_QUERIES`），每个代码随机抽 2 条 + 换一批按钮；点击先截图再预填输入框
- **fast ↔ ptc 模式**：`fast` 在面板内联流式；`ptc` 导航到 `/chat/t/__default__` 并带 router state `{ workspaceId, initialMessage, planMode, additionalContext, skills: ['chart-annotation'], chartSelections, attachmentMeta, model, reasoningEffort }`
- **自动跳转**：`subscribeLiveAnnotationAdd` **只在新鲜的 SSE `add` 时触发**；若 agent 画在了和当前屏幕不同的 `symbol:timeframe` 上，`handleJumpToChart` 把图表切过去
- **移动端**：`useIsMobile()` 换成 `MobileFabChat` + `MobileBottomSheet`（概况）+ 右侧抽屉（自选）
- **URL 参数消费一次即剥离**：`symbol`、`returnTo`、`ws`、`mode`、`tf`（`thread` 保留给聊天面板）

**API 端点**（`MarketView/utils/api.ts`）

| 方法 | 路径 |
|---|---|
| GET | `/api/v1/market-data/snapshots/stocks/{symbol}` |
| GET | `/api/v1/market-data/snapshots/indexes?symbols=`（指数分支，key 归一化） |
| GET | `/api/v1/market-data/stocks/{symbol}/overview` |
| GET | `/api/v1/market-data/stocks/{symbol}/analyst-data` |
| POST | `/api/v1/threads/messages`（新 flash 线程，SSE） |
| POST | `/api/v1/threads/{threadId}/messages`（已有线程，SSE） |
| DELETE | `/api/v1/threads/{threadId}` |
| GET | `/api/v1/workspaces` |
| DELETE | `/api/v1/workspaces/{workspaceId}` |
| WS | `{wsBase}/ws/v1/market-data/aggregates/{market}?interval={second\|minute}` |

其它：`utils/flashWorkspace.ts` → `POST /api/v1/workspaces/flash`（**模块级 promise 缓存**，失败时清、登出时清）；`hooks/useChartAnnotationSync.ts` → `GET /api/v1/workspaces/{workspace_id}/chart-annotations?symbol=X`；bars 走 `@/lib/bars`（`GET /api/v1/market-data/bars/{instrument}?schema=ohlcv-1m[&after=]`，legacy 回落 `GET /api/v1/market-data/daily|intraday/{market}/{symbol}`）。PTC 路径复用 ChatAgent 的 `getFlashWorkspace` / `getPreviewUrl` / `summarizeThread` / `offloadThread`。

**关键状态**

`MarketViewInner`：`selectedStock`（偏好 `symbol`，默认 `GOOGL`）、`selectedStockDisplay`、`chartMeta`、`marketPhase`、`selectedInterval`（偏好 `interval`）、`chartImage` / `chartImageDesc`、`showOverview`、`mobileTab`、`chatExpanded`、`prefillMessage`、`mode: 'fast'|'ptc'`（偏好）、`workspaces`、`selectedWorkspaceId`（偏好）、`quickQueries`、`chatPanelWidth`、`flashWorkspaceId`、`chatReturnPath`。派生 `activeWorkspaceId = mode === 'fast' ? flashWorkspaceId : selectedWorkspaceId`。偏好读写走 `utils/prefs.ts`，localStorage 前缀 `market-chart:`。

`MarketChart` 局部状态：`loading`、`scrollLoading`、`error`、`rsiValue`、`activeRange`、`enabledMaPeriods`（默认 `[20,50]`）、`rsiPeriod`(14)、`maValues`、`chartMode`（`custom|tradingview`）、`priceScaleMode`、`magnetMode`、`showBaseline`、`annotationsVisible`、`selectMode: 'off'|'region'|'price_level'`、`overlayVisibility`、`toolbarLevel`、4 个下拉开关；以及约 25 个 ref（图表/序列句柄、RSI 的 Wilder 平滑状态、WS 缺口补齐记账、`allDataRef`、`oldestDateRef`）。

**两个 store —— agent↔图表契约的两半**

`stores/chartAnnotationStore.ts`（567 行）—— **agent 画了什么**。模块单例 + `useSyncExternalStore`，按 `(workspace_id, chart_id)` 索引，`chart_id = "{SYMBOL}:{timeframe}"`。

```ts
export type AnnotationType =
  | 'price_line' | 'trendline' | 'marker' | 'vertical_line'
  | 'rectangle' | 'text' | 'event' | 'fib_retracement';
export const VALID_TIMEFRAMES: ReadonlySet<string> =
  new Set(['1min','5min','15min','30min','1hour','4hour','1day']);
export const DEFAULT_TIMEFRAME = '1day';
export function makeChartId(symbol, timeframe) { return `${symbol.toUpperCase()}:${timeframe}` }
```

API：`add / remove / clear / setAll / setChartsForSymbol(ws, symbol, charts, sinceSeq) / clearDisplay / restoreDisplay / isDisplayCleared / getMutationSeq`；hook：`useAnnotationsForView(wsId, symbol, tf)`、`useDisplayCleared(...)`。SSE 入口 `applyAnnotationArtifact(artifactType, payload)` 处理 `op: 'add' | 'remove' | 'clear'`，**必须校验** `KNOWN_ANNOTATION_TYPES` 和 `VALID_MARKER_SHAPES`（`arrowUp|arrowDown|circle|square`）—— 一个坏 marker 会让 LWC 的 `setMarkers()` 抛异常并**清空整个共享标记层**。两处并发守卫要照抄：`mutationSeq` / `keyMutatedAt` 配对（陈旧的同步响应不能覆盖实时的 add）、`MAX_CLEARED_KEYS = 200` 给"显示已清除"集合封顶。

`stores/chartSelectionStore.ts`（298 行）—— **用户选了什么**。同样是模块单例而**不是 Context**：桌面面板和移动 FAB 不共享 provider，但发送时两边都要 `getConfirmedFor`。

```ts
export type SelectionType   = 'region' | 'price_level';
export type SelectionStatus = 'pending' | 'confirmed';
interface ChartSelection {
  id; symbol; timeframe; selectionType; timeStart?; timeEnd?;
  priceLow; priceHigh; bars: SelectionBar[]; barsTruncated; croppedImage?; comment; status;
}
```

API：`beginDraft / confirm / setComment / remove / openEditor / closeEditor / clearAll / getConfirmedFor`；辅助 `isConfirmedFor`、`toSelectionSnapshot`、`promoteSelectionComment`（用户没打字时，唯一选区的批注升格成消息正文）。

**绘制工具**

- `utils/agentAnnotationsPrimitive.ts`（676 行）—— LWC v4 序列 primitive，画 LWC 没有原生 API 的四种：**rectangle / vertical_line / text / fib_retracement**。price line / trendline / marker 用原生 `createPriceLine` / `addLineSeries` / `setMarkers`。标签渲染成随主题的磨砂 chip，带一次去重叠处理
- `utils/selectionPrimitive.ts` —— 画用户进行中的选区。**两个来源**：拖拽期间的像素空间 `setDraft`（此时禁用平移，原始像素稳定）和每帧按时间/价格换算的 `setCommitted`。青色强调色，**刻意与 agent 的板岩蓝区分**
- `utils/extendedHoursBg.ts` + `chartConstants.ts` 的 `computeExtendedHoursRegions` —— 盘前琥珀 `#fbbf24`、盘后蓝 `#3b82f6`；间隔超过 `EXT_REGION_MAX_GAP_SEC = 2h` 强制断开区域
- `components/AgentEventOverlay.tsx` / `SelectionCommentOverlay.tsx` —— `chart-wrapper` 内的 DOM 覆盖层（canvas 上的 chip 接不了 hover/click），用图表坐标 API 定位，在平移/缩放/尺寸变化时重定位。**只在 Light 模式有** —— TradingView iframe 没有可覆盖的表面

**聊天面板**

`useMarketChat.ts`（633 行）是 **Fast 模式引擎**，`useChatMessages` 的简化版：SSE 事件经 `pendingUpdatesRef` 队列按 `BATCH_FLUSH_INTERVAL_MS = 150` 批量刷新；处理 `metadata`（latch `run_id`）、`message_chunk`（`content_type: text | reasoning | reasoning_signal`）、`tool_calls`、`tool_call_result`、`artifact`（→ `applyAnnotationArtifact`）、`error`。消息形状 `MarketChatMessage { contentSegments, reasoningProcesses, toolCallProcesses }`，`threadIdRef` 起始 `'__default__'`。

`MarketChatPanel.tsx`（1021 行）是**桌面**表面，**不用** `useMarketChat` —— 它直接驱动 ChatAgent 的完整 `useChatMessages`，所以工具调用、子代理、预览、停止/中断全都可用。线程连续性按 `(workspace, symbol)` 存（`utils/threadPersistence.ts`，`localStorage['marketview_thread_id_{ws}_{SYMBOL}']`）。两条发送路径都在第一轮注入同一份上下文：

```ts
{ type: 'skills', name: 'chart-annotation', instruction: marketViewAnnotationContext(sym, tf) }
```

---

### 2.4 Automations

入口 `Automations.tsx`（170 行）。**功能**：定时/触发式 agent 任务的 CRUD + 生命周期控制 + 执行历史钻取。

**CRUD 流程**

1. `AutomationTemplateCards` —— 5 个模板 `TemplateId = 'price_alert' | 'morning_briefing' | 'weekly_review' | 'earnings_watch' | 'custom'`。点一张切换 `selectedTemplate`；再点同一张则关闭表单
2. `AutomationInlineForm`（581 行）在 `<AnimatePresence mode="wait">` 里打开，**key = `editingAutomation?.automation_id ?? selectedTemplate ?? 'form'`** —— 切模板/切编辑对象时重新挂载拿到干净状态
3. `formInitialValues` 优先级：`automationToFormState(editing)` › `applyTemplate(selectedTemplate)` › `INITIAL_FORM`
4. 提交 → 编辑时 `mutations.update(id, payload)`，否则 `mutations.create(payload)`；成功后 `selectedTemplate` 和 `editingAutomation` 都清空
5. 从行进入编辑 → 关闭详情覆盖层、设 `editingAutomation`、强制 `selectedTemplate = 'custom'`、`topRef.scrollIntoView({behavior:'smooth'})`
6. 删除 → `ConfirmDeleteDialog` → `mutations.remove(id)`
7. 深链 `?id=<automation_id>` 自动打开详情覆盖层一次（`deepLinkHandledRef`）后剥离参数

**表单模型**（`utils/templates.ts`）

```ts
FormState = { name, description, trigger_type, cron_expression, timezone, next_run_at,
  agent_mode, workspace_id, instruction, thread_strategy, max_failures, delivery_method,
  price_symbol, price_condition_type, price_value, price_reference,
  price_retrigger_mode, price_cooldown_minutes }

INITIAL_FORM 默认: trigger_type:'cron', agent_mode:'flash', thread_strategy:'new',
  max_failures:3, price_condition_type:'price_above', price_reference:'previous_close',
  price_retrigger_mode:'one_shot',
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone ?? 'America/New_York'

PRICE_CONDITION_TYPES = ['price_above','price_below','pct_change_above','pct_change_below']
PRICE_REFERENCE_OPTIONS = ['previous_close','day_open']
RETRIGGER_MODES = ['one_shot','recurring']
```

三条触发分支决定条件区块：`price`（代码自动补全 + 条件 + 阈值 + 参考基准 + 重触发模式 + 冷却分钟）、`cron`（`CronScheduleBuilder`）、`once`（`next_run_at` 日期时间）。高级区（可折叠）放 `agent_mode` tab（**选 `ptc` 时展开必填的工作区选择，`handleSubmit` 里要校验**）、`thread_strategy`、`max_failures`、`delivery_method`。

内嵌的 `TickerAutocomplete` 把静态指数列表（`SPX, DJI, COMP, NDX, RUT, VIX`，`isIndexSymbol()`）立即展示，再合并防抖的 `searchStocks()` 结果。

`CronScheduleBuilder.tsx`（271 行）—— cron ↔ UI **双向**。`type Frequency = 'minutes'|'hourly'|'daily'|'weekdays'|'weekly'|'monthly'|'custom'`；`parseCron(expr)` 识别 `*/N * * * *`、`M * * * *`、`M H * * *`、`M H * * 1-5`、`M H * * D`、`M H N * *`，其余落到 `custom` 保留原串。显示侧的 `cronToHuman()` 在 `utils/cron.ts`。

**子组件**：`AutomationsHeader`（汇总统计）、`AutomationTemplateCards`、`AutomationInlineForm`、`AutomationsTable`、`AutomationRow`、`AutomationDetailOverlay`（308 行 —— 暂停/恢复/立即运行/编辑/删除 + `ExecutionHistoryTable`）、`ExecutionHistoryTable`、`StatusBadge`、`ConfirmDeleteDialog`。

**API**（`utils/api.ts`，全是薄 axios 包装，返回 `Promise<AxiosResponse>`）

```
GET    /api/v1/automations                  ?limit=100&offset=0[&status]
GET    /api/v1/automations/{id}
POST   /api/v1/automations
PATCH  /api/v1/automations/{id}
DELETE /api/v1/automations/{id}
POST   /api/v1/automations/{id}/pause | resume | trigger
GET    /api/v1/automations/{id}/executions  ?limit=20&offset=0
GET    /api/v1/workspaces
```

**状态**：`useAutomations()` = key `['automations', status]`、`refetchInterval 30000`、`refetchIntervalInBackground false`、`staleTime 5000`；`useExecutions(id)` = key `['executions', id]`、`refetchInterval 15000`、`enabled: !!id`；`useAutomationMutations(refetch)` 共用一个 `run()` 包装（单个 `loading` 标志、成功 toast、`await refetch()`、错误 toast 取 `err.response.data.detail ?? err.message` 后**重新抛出**）。页面局部：`selectedAutomation`、`selectedTemplate`、`editingAutomation`、`deleteTarget`；`selectedFresh` 把覆盖层的对象重新对着轮询列表解析一遍，保证它一直是活的。

---

### 2.5 Settings

入口 `Settings.tsx`（113 行）。四个 tab 同步到 `?tab=`（`userInfo` 默认 / `preferences` / `model` / `experiments`），滚动记忆 key `page:settings`，`useUser().isLoading || usePreferences().isLoading` 时门禁。

| 文件 | 配置什么 |
|---|---|
| `panels/types.ts`（7 行） | `interface Preferences { risk_preference?, investment_preference?, agent_preference?, other_preference? }`，全是 `Record<string, unknown>` |
| `panels/UserInfoTab.tsx`（414 行） | 头像上传、显示名、时区（分组 select）、语言（同时写 locale cookie）、主题偏好、字号（`FONT_SCALES`）、语音输入开关、带 `ConfirmDialog` 的登出。名字/时区/语言经 `useDebouncedSave` 自动保存 |
| `panels/PreferencesTab.tsx`（328 行） | 投资偏好摘要、agent **输出格式**（`agent_preference.output_format`：`null` = markdown 默认，`'html'` = HTML 报告）、引导重放/重置入口（`useOnboarding().replayGuides / resetOnboarding`）、破坏性的"重置偏好"流程 |
| `panels/ModelTab.tsx`（459 行） | 默认模型 + flash 模型、星标模型选择器（带搜索）、高级路由（压缩模型、取数模型、回退模型、`CompactionProfileName`）、搜索提供方 + 搜索深度、自定义模型（`CustomModelEntry[]`）、BYOK 提供方列表。内嵌 `<ConnectedAccounts/>`。防抖保存 |
| `panels/ConnectedAccounts.tsx`（649 行） | 模型 tab 的子区。两条 OAuth 流程，各自前面挡一个免责声明对话框：**Codex 设备码**（initiate → 展示 `user_code` + `verification_url` → 按 interval 轮询）与 **Claude PKCE 粘贴回填**（initiate → 打开授权 URL → 用户粘贴回调串 → 提交）。挂载时同时拉两边状态 |
| `panels/ExperimentsTab.tsx` | **完全数据驱动**：渲染 features API 里所有 `gate === 'opt_in' \|\| 'opt_out'` 的开关 —— **新增开关前端零改动** |

API：`GET|PUT /api/v1/users/me`、`POST /api/v1/users/me/avatar`、`GET|PUT|DELETE /api/v1/users/me/preferences`、`GET /api/v1/models`、`GET|PUT /api/v1/users/me/api-keys`、`DELETE /api/v1/users/me/api-keys/{provider}`、`GET /api/v1/features`、`PUT /api/v1/features/{key}`，以及 §2.2.5 里的 Codex/Claude OAuth 八个端点。

---

### 2.6 Setup

入口 `SetupWizard.tsx`（248 行），挂在 `/setup/*`。嵌套 `<Routes>`，六步**全部 `React.lazy`**。

```ts
const ROUTES = ['/setup/method','/setup/provider','/setup/connect',
                '/setup/models','/setup/defaults','/setup/ready'] as const;
const STEPPER_KEYS = ['stepMethod','stepProvider','stepModels','stepReady'] as const;
```

6 个微路由折成 4 个进度点（`stepperDot(routeIdx)`）：`method→0`、`provider→1`、`connect→1`、`models→2`、`defaults→2`、`ready→3`。framer `AnimatePresence mode="wait"` 按路由序号做滑动转场。退出按钮在 `canExit = hasProvider || user.access_tier >= 0 || !isPlatformMode` 时显示 → `skipSetup(); navigate('/dashboard')`。

| 步骤 | 文件 | 做什么 |
|---|---|---|
| 1 方式 | `MethodStep.tsx`（493 行） | 选 `AccessType`：`oauth` / `coding_plan` / `api_key`（+ 平台专属和 OSS 专属卡）。展示已配置的提供方及断开操作（`deleteUserApiKey`、`disconnectCodexOAuth`、`disconnectClaudeOAuth`）。**邀请码兑换框**也在这里 → `POST /api/auth/invitations/redeem { code }`。用 router state 把 `{ method }` 传下去 |
| 2 提供方 | `ProviderStep.tsx`（233 行） | 按 `access_type === method` 过滤 `GET /api/v1/models` 返回的 `provider_catalog`；再加上用户 `other_preference.custom_providers` 里已有的自定义提供方和一张 `__custom__` "新增"卡 |
| 3 连接 | `ConnectStep.tsx`（34 行） | 纯分发器，按 router state → `ExistingCustomConnect` / `CustomProviderConnect` / `OAuthConnect` / `ApiKeyConnect`。**state 缺失（刷新页面）时重定向回 `/setup/method`** |
| 4 模型 | `ModelPickStep.tsx`（703 行） | 选哪些模型进入已配置集合。持久化成偏好里的 **`starred_models`**（DefaultsStep 和 Settings 的快速选择器共用同一份）。支持自定义模型增删；辅助 `slugifyModelName.ts`、`mergeCustomModelsForSlug.ts`、`modelSlotCleanup.ts`（`computeSlotCleanup`） |
| 5 默认值 | `DefaultsStep.tsx`（197 行） | 经 `ModelTierConfig` 设 `preferred_model` + `preferred_flash_model`（**两个都必填才能继续**），外加高级的 `compaction_model` / `fetch_model` / `fallback_models` |
| 6 就绪 | `DoneStep.tsx`（134 行） | 只读汇总：`useApiKeys()` 里的提供方名、`other_preference` 里的主模型 + flash 模型 |

`steps/connectStep/`：`shared.tsx`（210 行 —— `API_FORMATS`、`getApiFormatKey(sdk, useResponseApi)`、密钥测试 → `POST /api/v1/keys/test`、`ProcessStep`、`DisclaimerBox`、`CopyButton`、`useModalityState()`）、`ApiKeyConnect.tsx`（310 行）、`OAuthConnect.tsx`（399 行，`oauthPhase: 'disclaimer'|'connecting'|'active'`）、`CustomProviderConnect.tsx`（483 行，父模型发现 `GET /api/v1/providers/{parent}/visible-models`）、`ExistingCustomConnect.tsx`（368 行，`GET /api/v1/providers/{provider}/models`）。

所有偏好写入统一走 `useUpdatePreferences()` → `PUT /api/v1/users/me/preferences`。

---

### 2.7 SharedChat

`SharedChatView.tsx`（628 行）+ `api.ts`（238 行）。路由 `/s/:shareToken`，**在认证外壳之外**。

**功能**：公开、免登录、只读地重放一段被分享的会话。布局完全镜像 ChatView，所有交互操作禁用（`READ_ONLY_MESSAGE_ACTIONS`），并**复用** ChatAgent 的 `MessageList`、`FilePanel` 和 `historyEventHandlers` —— 分享出去的线程渲染结果和实时线程一模一样。

**交互**：拉元数据 → 重放 SSE 流 → 渲染转录；切换右侧**文件面板**（仅当 `permissions.allow_files === true`）；打开/读取/预览/下载文件；拖分隔条（默认宽 `750`，拖拽清理注册在 `dragCleanupRef` 上，拖到一半卸载也不会漏掉 document 监听器）。

**权限分两档**：`allow_files`（浏览 + 内联图片/预览，走 **serve** 端点）与 `allow_download`（显式"另存一份"）。**预览刻意走 `/files/serve` 而不是 `/files/download`** —— 复制链接的分享只授予 `allow_files`，走 download 路径会 403。

**API**（`SharedChat/api.ts`，**裸 `fetch`，不带 Bearer**）

```
GET /api/v1/public/shared/{shareToken}
GET /api/v1/public/shared/{shareToken}/replay          (SSE)
GET /api/v1/public/shared/{shareToken}/files?path=
GET /api/v1/public/shared/{shareToken}/files/read?path=
GET /api/v1/public/shared/{shareToken}/files/download?path=
    buildSharedServeUrl(shareToken, path) → …/files/serve
```

```ts
interface SharedThreadMetadata { thread_id; title; msg_type; created_at; updated_at;
                                 workspace_name; permissions: Record<string, unknown> }
interface SharedFileListResponse { path; files: string[]; source }
interface SharedFileReadResponse { path; content; mime; offset; limit; truncated }
type DownloadMode = 'download' | 'blob' | 'arraybuffer';
// downloadSharedFileAs 按 mode 重载：'blob'→string、'arraybuffer'→ArrayBuffer、'download'→void
```

**状态**：`metadata`、`messages: MessageRecord[]`、`loading`、`error`、`showFilePanel`、`files`、`filesLoading`、`filePanelTargetFile`、`rightPanelWidth`。重放重建用 `assistantMessagesByPair: Map<number,string>` + `pairStateByPair: Map<number, PairState>`（`PairState = { contentOrderCounter, reasoningId, toolCallId }`），另有一个形状匹配 `useChatMessages` 的 `sharedRefs` 垫片，让共享的历史处理器可以原样复用。

---

### 2.8 Onboarding（引导引擎）

**不是路由** —— 是挂在认证外壳里的 provider + host。公开面刻意收窄（`index.ts`）：

```ts
export { OnboardingProvider, useOnboarding } from './OnboardingProvider';
export { OnboardingHostGate } from './OnboardingHostGate';
// OnboardingHost 【故意不导出】——它会拖进沉重的 modal + 插画依赖图
```

**三个界面，弹窗类同时只允许一个**：

```ts
export type OnboardingPhase = 'idle' | 'pageIntro' | 'whatsNew';
```

`useOnboardingOrchestrator` 只在 `idle` 时运行，严格顺序：硬抑制（`suppress`，或页面上有任何 `[role="dialog"][data-state="open"]`）→ 当前路由的页面 intro → What's New（每会话一次，`whatsNewShownRef`）。

**持久化**

```ts
export const ONBOARDING_PREFS_VERSION = 1 as const;
interface OnboardingPrefs {
  version;
  pageIntrosSeen: Record<string, number>;
  gettingStartedDoneAt: Record<string, number>;
  gettingStartedDismissedAt: number | null;
  lastSeenReleaseVersion: string | null;
  firstRunAt: number | null;
}
```

服务端家：`user_preferences.other_preference.onboarding`（搭同一个 `PUT /api/v1/users/me/preferences` 的车）。`mirror.ts` 是一份**只用于抑制**的 localStorage 投影。

**注册表类型**（`registry/types.ts`）

```ts
interface AnnouncementDef { key; releaseVersion; modalTitleKey; modalBodyKey }
// releaseVersion 是 CalVer 'YYYY.MM.DD[.N]'；新条目必须【严格大于】当前最大值，否则永远不会出现

type IntroVisualId = 'twoModes'|'workspaceGrid'|'flashAnswer'|'ptcSandbox'
  |'createWorkspace'|'filePanel'|'memory'|'memo'
  |'dashboardGrid'|'dashboardCustomize'|'dashboardAttach';

interface PageIntroDef { id; matchRoute: (pathname) => boolean;
                         steps: [PageIntroStepDef, ...PageIntroStepDef[]] }   // 非空元组
interface PageIntroStepDef { id; titleKey; bodyKey; visual: IntroVisualId }

interface GettingStartedTaskDef { id; titleKey; descKey; to;
  interview?; external?; platformOnly?;
  visitRoute?: (pathname, search) => boolean;
  doneWhen?: 'hasStocks' | 'hasPreferences' | 'hasWorkspace' }
```

`engine/introVisuals.tsx`（1020 行）把自己的 `Record` 类型标成 `IntroVisualId` —— **加了 id 却没有画面就是编译错误**。

**Context 值**

```ts
{ phase, unseen: AnnouncementDef[], activeIntro, dismissPageIntro,
  gettingStarted: { visible, tasks, doneCount, dismiss, completeTask },
  acknowledgeWhatsNew, replayGuides(): boolean, resetOnboarding(): boolean }
```

`replayGuides` / `resetOnboarding` 在持久化写入被拒（冷缓存）时返回 `false`，好让 `Settings/panels/PreferencesTab` 跳过一个它兑现不了的"完成"toast。

清单完成有三种模式：路由访问自动盖章（`visitRoute`）、点击盖章（`external`）、派生信号（`doneWhen`）—— `hasStocks` 和 `hasWorkspace` 一旦为真就盖章（让查询停止重跑），而 **`hasPreferences` 永不盖章**，所以重置偏好会让它重新变成未完成。

`OnboardingHostGate` 是**闩锁式**的：一旦需要任何界面就整会话保持挂载；**完全走完引导的用户根本不会下载 `OnboardingHost` 这个 chunk**。

读取来源：`usePreferences`、`useUser`、`useWorkspaces`，以及 `Dashboard/utils/api` 的 `listWatchlists` / `listWatchlistItems` / `listPortfolio`（供派生信号用）。

---

### 2.9 Login

入口 `LoginPage.tsx`（548 行），登出状态下渲染在 `APP_ENTRY_PATH`。左右分屏：左认证、右行情带视觉。

```ts
type LoginView = 'method' | 'login' | 'signup' | 'magic-link' | 'forgot-password' | 'check-inbox';
```

状态：`view`、`loginEmail`/`loginPassword`、`signupEmail`/`signupPassword`/`signupConfirm`/`signupName`、`magicEmail`、`forgotEmail`、`sentKind: CheckInboxKind`、`sentEmail`、`isSubmitting`、`error: AuthErrorInfo | null`、`visualHidden`（`matchMedia('(max-width: 900px)')`）。
处理器：`handleLogin`、`handleSignup`、`handleMagicLink`、`handleForgotPassword`、`handleResendConfirmation`、`handleOAuth('google' | 'github')`。

认证全部走 `contexts/AuthContext`（Supabase）—— **本页没有任何 `/api/v1` 调用**；错误文案由 `lib/authErrors.authErrorMessage` 映射。

子组件：`PasswordInput`、`PasswordStrength`（`scorePassword → 0|1|2|3`）、`passwordRequirements.ts`（`validatePasswordPair`、`MIN_PASSWORD_LENGTH`）、`CheckInbox`、`AccountRecoveryHint`（三个 `<Trans>` 内嵌的恢复动作，"凭据错误"和"邮箱已存在"两种情况共用）、`EmailOnlyView`（魔法链接和重置请求共用）、`useResendCooldown(60)`。

配套路由：`AuthConfirm.tsx`（`/auth/confirm` —— 等客户端把 `?code=` 换完，再 `broadcastAuthComplete()` 让打开着的登录 tab 拿到 session cookie）、`ResetPassword.tsx`（`/reset-password` —— 同时处理 `?token_hash=&type=recovery` 和 PKCE `?code=`）。

装饰画布层（**完全自包含，不碰 React state 也不发请求**）：`MarketScanlines.tsx`（627 行，半调标普行情带）、`spxSeries.ts`（501 行烘焙好的日收盘）、`emberBall.ts`（347 行，悬停特效；`onBurst` 播种 `EdgeGrain`）、`EdgeGrain.tsx`（236 行）、`loginPaper.ts`（共享的运行时调色板推导，让美术跟随主题）、`WavesBackground.tsx`。

---

### 2.10 Detail / Legal / OAuth

**Detail** —— `NewsDetailPage.tsx`（214 行），路由 `/news/:id`。用 `useParams().id` 调 `getNewsArticle(id)`（`GET /api/v1/news/{articleId}`）。状态 `article: NewsArticle | null` / `loading` / `error`，**全部由一个 `cancelled` 标志守卫**。渲染返回按钮（→ `/dashboard`）、标题、发布时间/作者/带 favicon 的来源、ticker 药丸、主图、描述、**情绪分析**区（`sentiments: { ticker, sentiment, reasoning }[]`，按 profit/loss/neutral 着色）、外链"阅读全文"。图片加载失败经 `onError` 自隐藏。

**Legal** —— 两个独立静态页，在 `App.tsx` 里懒加载，渲染在外壳之外，各自带滚动容器和 `← 返回` 的 `<Link to="/">`。`Legal.tsx`（124 行，`/legal`）放开源许可与第三方致谢（含 TradingView widget 署名）；`PrivacyPolicy.tsx`（304 行，`/privacy`）用编号 `<Section title>` 子组件，`const EFFECTIVE_DATE = 'April 18, 2026'`。**无状态、无 API、无 props，可逐字重建。**

**OAuth** —— `CodexCallback.tsx` 仅 14 行**死代码**，被设备码流程取代，`App.tsx` 也没引用它。**新实现直接删除整个目录。**

<!-- SECTION-2-END -->

## 3. 状态管理

状态分五类，**每类只允许一个归属**，混用即缺陷：

| 类别 | 归属 | 判据 |
|---|---|---|
| 服务端数据 | React Query | 有 HTTP 来源、可失效重取 |
| 跨树共享的**派生/回调** | React Context | 无网络来源，纯粹是"避免 prop 钻透" |
| **高频外部事件流**（SSE 推送、鼠标移动、token 计数） | 模块级 store + `useSyncExternalStore` | 更新频率高于 React 渲染节奏，或活得比组件长 |
| 组件私有 UI 状态 | `useState` / `useReducer` | 只有本组件看 |
| 用户偏好（本地） | `localStorage` / `sessionStorage` | 需要跨刷新存活，但不值得上服务端 |

**铁律**：服务端数据**永远不许**镜像进 `useState`。需要"编辑中的草稿"就单独存一份草稿态，提交后 `invalidateQueries`，不要把 query 结果 `useEffect` 拷进 state。

### 3.1 React Query 全局配置

`main.tsx` 里构造唯一的 `QueryClient`：

```ts
new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: true,   // 回到 tab 自动刷新
      retry: 1,                     // 失败重试一次
      staleTime: 1000 * 60 * 2,     // 默认新鲜 2 分钟
    },
  },
})
```

`gcTime` 全局用默认值（5 分钟），**只有一处显式覆盖**：平台模型列表 `gcTime: Infinity`（模型清单在一次会话内不会变，且被下拉框反复挂载/卸载）。

### 3.2 queryKey 工厂（`lib/queryKeys.ts`）

**唯一真源，禁止在任何调用点内联 key 数组** —— 内联会破坏前缀失效（`['threads','detail',id]` 手写成 `['thread',id]` 就永远收不到失效）。

层级式设计：每一层建立在父层之上，使 `invalidateQueries({ queryKey: 父层 })` 能一次扫掉整个子树。

```ts
export const queryKeys = {
  user: {
    all: ['user'],
    me:          () => [...queryKeys.user.all, 'me'],
    preferences: () => [...queryKeys.user.all, 'preferences'],
    apiKeys:     () => [...queryKeys.user.all, 'api-keys'],
  },
  models:   { all: ['models'] },
  features: { all: ['features'], list: () => [...] },
  platform: { all: ['platform'], models: () => [...] },
  oauth:    { all: ['oauth'], codex: () => [...], claude: () => [...] },
  workspaces: {
    all: ['workspaces'],
    lists:  () => [...all, 'list'],
    list:   (params) => [...lists(), params],     // 参数进 key，分页/排序各自成条目
    detail: (id) => [...all, 'detail', id],
    flash:  () => [...all, 'flash'],
    quota:  () => [...all, 'quota'],
  },
  threads: {
    all: ['threads'],
    byWorkspace: (wsId) => [...all, 'workspace', wsId],
    // 画廊的无限列表【关键】：故意挂在 byWorkspace 前缀之下，
    // 这样生命周期 feed 的前缀失效能够到它；后缀对象把它和
    // 侧栏的有限分页条目区分开（后者装不下 InfiniteData）。
    gallery: (wsId, archived) => [...byWorkspace(wsId), { view: 'gallery', archived }],
    detail:  (threadId) => [...all, 'detail', threadId],
    recentAll: () => [...all, 'recent'],          // 失效打这个前缀
    recent:  (limit) => [...recentAll(), limit],
    status:  (threadId) => [...all, 'status', threadId],
    dispatchLivenessAll: () => [...all, 'dispatch-liveness'],
    // 【关键】key 建在 SORTED id 集合上，注册顺序不同不会churn 出新缓存条目
    dispatchLiveness: (ids) => [...dispatchLivenessAll(), [...ids].sort()],
  },
  workspaceFiles: { all: ['workspaceFiles'], byWs: (wsId, opts?) => [...] },
  memory: { all: ['memory'], user, userRead, workspace, workspaceRead },
  memo:   { all: ['memo'], list, read },
  mcp:    { all: ['mcp'], catalog: () => [...], workspace: (wsId) => [...] },
  marketData: { all: ['marketData'], bars: (symbol, interval) => [...] },
  quote:  { all: ['quote'], detail: (symbol) => [...all, symbol] },  // 每个 symbol 一条，全站共享
};
```

两个设计点必须照抄：

1. **`threads.gallery` 挂在 `byWorkspace` 之下**。画廊用 `useInfiniteQuery`（数据是 `{pages:[...]}`），侧栏用有限分页（数据是 `{threads:[...]}`）。两者共前缀才能被同一次失效覆盖，靠后缀对象区分形状。所有读取列表数据的公共代码（如 §3.7 的列表播种）必须**同时**处理这两种形状。
2. **`dispatchLiveness` 的 id 集合排序后入 key**。多个卡片注册顺序不定，不排序会为同一批 id 生成 N 个缓存条目。

### 3.3 `CACHE_ONLY_META` —— "我故意不自己取数"

```ts
export const CACHE_ONLY_META = { cacheOnly: true } as const;
export function isCacheOnlyMeta(meta: QueryMeta | undefined): boolean {
  return meta?.cacheOnly === true;
}
```

标记一个 query 是"参数完整、queryFn 能成功，但按自己的节奏取数是错的"（典型：侧栏导航树的 page-0 列表，靠 SSE 推送驱动刷新，不该自己轮询）。这类 query 挂 `enabled: false` + `meta: CACHE_ONLY_META`。

`invalidateQueries` **只会重取有启用观察者的 query**，所以生命周期 feed 需要一段补刀逻辑（§3.7 的 `refetchCacheOnlyLists`）：遍历被失效的 query，若 `state.isInvalidated && !query.isActive() && 有观察者带 cacheOnly meta` → 手动 `query.fetch()`。

**严禁**给"因为参数缺失而 disabled"的 query 挂这个 meta —— 那种 queryFn 会在缺 id 时 throw，被当成真实失败读走。曾经一个没有 id 的 thread 查询因此把用户从所有 `/chat` 路由踢出去。

### 3.4 staleTime / 轮询约定表

| 数据 | staleTime | 其它 | 理由 |
|---|---|---|---|
| 默认（未指定） | 2 min | — | `main.tsx` 全局 |
| `user.me` / `workspace.detail` / `models` / `apiKeys` / 平台模型 | 5 min | 平台模型另加 `gcTime: Infinity` | 几乎不变 |
| `features`（功能开关） | 5 min | `retry: false`，**失败当 false**（fail closed） | 加载中/出错时受控界面不许先闪出来 |
| `preferences` | 有 `BroadcastChannel` → 60s；否则 **0** | `retry: false` | 有广播通道就靠跨 tab 同步；Safari < 15.4 没有，只能靠聚焦重取 |
| `workspaces.list` | 30s | `placeholderData: keepPreviousData` | 翻页/换排序不闪白 |
| 导航树线程/工作区列表 | 30s | `enabled:false` + `CACHE_ONLY_META` | 由 SSE feed 驱动 |
| `memory` / `memo` / `workspaceFiles` | 30s | memo 带条件 `refetchInterval` | |
| MCP 有效服务器列表 | 15s | 处于 settling 态时 `refetchInterval: 2.5s` | 连接握手期需要跟进 |
| MCP catalog | 60s | | |
| 行情 quote | 30s | `refetchInterval: 60s`，`refetchIntervalInBackground: false` | 后台 tab 不烧配额 |
| 自动化任务 / 执行记录 | 5s | `refetchInterval: 30s`，`refetchIntervalInBackground: false` | |
| PTC dispatch liveness | 2s | **自适应 `refetchInterval`**：读 `query.state.data` 算聚合状态，用 `dataUpdateCount` 去重后推进窗口计数器，交给 `nextDispatchPollInterval(聚合态, 累计轮数, 连续 starting 轮数)` 决定下次间隔 | 起步密、稳定后疏 |

**约定**：`refetchIntervalInBackground` 一律保持默认 `false`。任何轮询都要能在 tab 隐藏时停下来。

### 3.5 失效策略

三种手段，按"能不能拿到新值"选：

1. **`invalidateQueries(前缀)`** —— 默认手段。写操作成功后失效它影响的前缀。因为 key 是层级式的，`queryKeys.threads.byWorkspace(ws)` 一发就覆盖该工作区下的侧栏列表 + 画廊无限列表。
2. **`setQueryData` 直接改缓存** —— 当**服务端已经把新值还给你**时用，省一个往返：
   - `useSetFeatureOverride`：PUT 返回完整 flag 列表 → 直接 `setQueryData(features.list(), features)`；`onError` 才退回 `invalidateQueries`。
   - `useUpdatePreferences`：PATCH 返回更新后的 preferences → `setQueryData` 瞬间传播给所有 `usePreferences()` 消费者。
   - `AuthContext.syncUser`：`/api/v1/auth/sync` 的响应里带 preferences → 播种缓存。**但绝不播种 `user.me()`** —— sync 响应缺 `access_tier` 等字段，会覆盖掉正在飞行中的 `GET /users/me` 的正确值。
   - `quoteBatcher`：批量快照响应按 symbol 扇出 `setQueryData(queryKeys.quote.detail(sym), row)`。
3. **跨 key 变体的行级补丁** —— 一行线程同时活在多个 key 变体里（侧栏分页 key、画廊无限 key、`recent`、`detail`、dashboard widget key）。所以 `lib/threadListCache.ts` + `lib/threadRowActions.ts` 用 `getQueriesData(前缀)` 遍历**所有**匹配 key，逐个 `setQueryData` 改写，并且统一处理有限/无限两种数据形状。提供：
   - `insertOptimisticThread`：新建线程立刻插进列表头（`headOnly: true`，无限列表只动第 0 页；`skipKey: isArchivedThreadsKey` 跳过归档视图）。
   - `patchThreadTitle(qc, tid, title, updatedAt?)`：改标题；**用 `updated_at` 做版本**，迟到的自动生成标题不许覆盖已落地的手动改名。空标题是合法载荷（清空标题就是发 `""`），判定用 `!= null` 而不是真值。
   - `patchThreadRows(qc, 前缀, fn, opts)`：通用行改写，支持 `headOnly` / `skipKey`。
   - 快照 + 回滚：`threadRowActions` 会先把改动前的 `[key, data]` 收进 snapshot，失败时逐条 `setQueryData` 还原。

### 3.6 乐观更新用在哪

只在**三类**地方用乐观更新，别的地方一律等服务端：

| 场景 | 做法 | 回滚 |
|---|---|---|
| MCP 服务器启停开关 | `onMutate`：`cancelQueries` → 快照 `previous` → `setQueryData` 同时改 `enabled` **和** `status`（关→`'disabled'`，开→乐观 `'connected'`） | `onError` 用 `context.previous` 还原；`onSettled` 一律 `invalidateQueries` |
| 线程行操作（置顶/归档/删除/重命名） | `patchThreadRows` 直接改所有 key 变体 | 快照全量回滚 |
| 新建线程 | `insertOptimisticThread` 插入列表头 | 由后续 reconcile refetch 纠正（无回滚） |
| 未读点消失（"已看"） | `raiseSeenToEffective(threadId)` 本地抬水位 | 无回滚：`/seen` POST 丢了也没关系，下次打开会自愈 |

**MCP 开关那处的关键细节**：同一次乐观写里把 `status` 也一并调好，否则行会先闪 "Verifying…/Applying…" 再稳定。切换 `enabled` 不改变发现指纹，重新启用会直接从缓存 schema 重连，不需要重新校验。

### 3.7 Context 清单

Context 只用来**避免 prop 钻透**和**把高频叶子更新从 memo 树里摘出去**，从不承载服务端数据。

| Context | 位置 | 管什么 | 为什么不是 Query |
|---|---|---|---|
| `AuthContext` | `contexts/AuthContext.tsx` | `userId` / `isInitialized` / `isLoggedIn` + 全部认证动作（邮箱登录、注册、OAuth、magic link、密码重置、验证 OTP、改密、登出） | Supabase session 不是 HTTP 资源；且它要在 Query 之上（登出要清 Query） |
| `ThemeContext` | `contexts/ThemeContext.tsx` | `preference: 'light'\|'dark'\|'auto'`、解析后的 `theme`、`setTheme`、`toggleTheme`（三态循环 dark→light→auto→dark） | 纯本地偏好 |
| `WorkspaceContext` | `pages/ChatAgent/contexts/` | `{ workspaceId, downloadFile }` | 只是把两个值递到 markdown 渲染的最深处 |
| `ChartSurfaceContext` | `pages/ChatAgent/contexts/` | `{ chartPresent, activeSymbol?, activeTimeframe?, onJumpToChart? }` | 同一套聊天引擎在两个"表面"渲染（ChatAgent 独立页 / MarketView 侧边聊天面板），图表注解卡的行为要按表面切换：`chartPresent:false` 渲染可点开的迷你实时图；`true` 折叠成一行确认 chip（真图就在旁边） |
| `MessageActionsContext` | `pages/ChatAgent/components/messageList/` | 转录区的**全部动作回调**（打开子代理任务/文件/来源/目录、工具详情、批准/拒绝计划、回答/跳过提问、批准/拒绝各类提案…） | 之前这些 handler 要穿三层完全相同的 pass-through prop 列表（MessageList → MessageBubble → MessageContentSegments）才能到达第五层的卡片。**value 必须 identity 稳定**：每个 host 用 `useStableHandler` 包装后 memo 成一个对象——否则每个 chunk 都会重渲染所有已定型的气泡。四个 host 显式提供：ChatView、它的子代理转录、MarketChatPanel、SharedChat 的只读适配器。空默认值只给测试用 |
| `SubagentTelemetryContext` | `pages/ChatAgent/components/` | 一个 resolver：`(subagentId) => SubagentTelemetry \| undefined` | 在叶子（`SubagentTaskMessageContent`）消费，让 token 实时跳动的重渲染**绕过** memo 化的 MessageBubble / MessageContentSegments 树，而不是每个 SSE 事件都把 memo 打穿 |
| `WorkflowRunContext` | 同上 | resolver：`(subagentId) => WorkflowRunState` | 同上，服务 `WorkflowRunCard` 的 `workflow_lifecycle` 心跳 |
| `CitationMetadataContext` | `pages/ChatAgent/components/` | `Map<url, CitationMeta>`，由 `toolCallProcesses` 里的 WebSearch 结果 memo 派生 | 是**派生**数据不是取来的；markdown 里任意深度的链接都要查它 |
| `DashboardDataContext` | `pages/Dashboard/widgets/framework/` | 把 `useDashboardData` / `useWatchlistData` / `usePortfolioData` / `useTickerNews` 的返回值 + 删除确认弹窗状态 + 新闻弹窗状态聚合成一份，供任意 widget 消费 | 底下**仍然是** Query；Context 只负责让网格里任意位置的 widget 不必各自再挂一遍 hook |
| `MarketDataWSContext` | `pages/MarketView/contexts/` | `useMarketDataWS()` 的整个返回值（prices / connectionStatus / subscribe / unsubscribe…） | **一个页面只能有一条 WebSocket**；用 Context 保证 provider 唯一。缺 provider 时 `useMarketDataWSContext()` 直接 throw |
| `OnboardingProvider` | `pages/Onboarding/` | 引导编排：公告、页面 intro、getting-started 任务、版本 What's New | 组合了多个 Query + 本地 mirror + 一个外部快照存储 |

### 3.8 模块级 store（既不是 Context 也不是 Query）

用于"更新频率高于 React 渲染节奏"或"活得比 React 树长"的状态。统一用 `useSyncExternalStore` 订阅。

| 模块 | 作用 |
|---|---|
| `lib/emitter.ts` | `createEmitter()`：最小订阅/通知原语，所有 store 的底座 |
| `lib/valueStore.ts` | `createValueStore<T>(initial)`：`get/set/subscribe`，`Object.is` 相等即不发通知。用于 input 频率的状态（如 per-mousemove 的十字光标读数）——只有显示它的那个叶子重渲染，拥有它的组件树不动 |
| `lib/contextBus.ts` | widget → 聊天输入框的"上下文附件"总线。事件：`{type:'attach', snapshot}` / `{type:'detach', widgetId}` / `{type:'clear'}`。**为什么手写 bus 而不是 Context**：聊天输入框和溢出提示 pill 可能在不同的 React 树里（modal / portal / hero 卡 vs 网格内 widget），需要订阅同一份状态而无法提升共同祖先。派发时先 `[...handlers]` 快照集合（handler 可能在派发中退订），单个 handler 抛错只 log 不中断其余 |
| `lib/threadLifecycle/store.ts` | 线程运行态状态机，见 §3.10 |
| `lib/navThreadsStore.ts` | 导航树的 "Show more" 已展开线程列表 |
| `pages/ChatAgent/components/navExpansionStore` | 导航面板展开态 |
| `pages/ChatAgent/hooks/useNavigationData` 的 stable order | 导航排序的稳定化快照 |
| `pages/MarketView/utils/flashWorkspace` | flash 工作区缓存 |
| `pages/MarketView/stores/chartAnnotationStore.ts` | 图表注解 |
| `pages/ChatAgent/session/stream/threadStreamMux.ts` 的 `muxByThread` | 每线程一个 mux 实例的注册表 |
| `lib/threadLifecycle/feedClient.ts` 的 `client` | 用户级 SSE feed 的连接状态 |


### 3.9 登出重置（必须实现，否则串号）

模块级单例活得比 React 长，而登出**不刷新页面**，所以一个用户的数据会漏进下一个用户的会话。

```ts
// lib/authResets.ts
const resets = new Set<() => void>();
export function registerAuthReset(fn: () => void): void { resets.add(fn); }
export function runAuthResets(): void { for (const fn of resets) fn(); }
```

**依赖倒置是刻意的**：模块在自己 init 时注册重置函数，`AuthContext` 只调 `runAuthResets()`，从不静态 import 那些重量级页面模块——没被加载过的模块本来也没东西要重置。

`AuthContext` 的 `onAuthStateChange` 里有两条重置路径，**动作完全相同**：

- **登出**（`sess` 为空）
- **账号切换**（`sess.user.id !== lastUserIdRef.current`，比如 A 登录着却打开了 B 的邮件链接）

两条都执行：
```
queryClient.clear()
clearFlashWorkspaceCache()
resetNavPanelExpansion()
resetStableNavOrder()
resetSharedWorkspaceThreads()
runAuthResets()          // 注册表里的其余 store（含 threadLifecycle）
// 仅登出路径额外：
setTokenGetter(() => Promise.resolve(null))
setTokenRefresher(() => Promise.resolve(null))
```

**新实现要求**：任何新增的模块级单例，必须在自己模块顶部 `registerAuthReset(...)`，这是硬约定。

### 3.10 线程生命周期状态机（`lib/threadLifecycle/`）

这是整个前端最需要照抄的一块。它回答一个问题：**在任意时刻，任意线程行上该不该显示转圈 / 等待输入 / 未读点。**

#### 3.10.1 状态集合

```ts
export type PublicRunStatus =
  | 'idle' | 'queued' | 'running' | 'stopping' | 'recovering'
  | 'completed' | 'interrupted' | 'failed' | 'cancelled';

const LIVE_STATUSES     = new Set(['queued', 'running', 'stopping', 'recovering']);
const TERMINAL_FAMILY   = new Set(['completed', 'failed', 'cancelled']);
// 'interrupted' 既不 live 也不 terminal，单独一档（等待人工输入）
```

> 这是后端 `LIVE_PUBLIC_STATUSES` / `TERMINAL_PUBLIC_STATUSES` 的**手工镜像**。契约变更**先改后端再改前端**，两边都要有契约测试。

#### 3.10.2 核心设计：保留式观测（retained observations），不是"当前状态"

每个线程保留**两层观测**，而不是一个状态字段：

```ts
interface LifecycleObservation {
  runId?: string;
  runSeq?: number;                 // 后端 nextval() 分配的单调序号
  status: PublicRunStatus;
  interruptReason?: string | null;
}

interface ThreadEntry {
  local?: LifecycleObservation;    // 本 tab 自己的聊天 SSE 流看到的
  feed?: LifecycleObservation;     // 用户级 feed + 快照 + 列表行播种看到的
  lastSeenSeq: number;             // 已读游标
  liveSuppressedBelowSeq: number;  // 「活跃」抑制水位
  seenSuppressedBelowSeq: number;  // 「未读」抑制水位
}
```

**UI 显示的三个指标全是派生的，一个都不作为标志位存储**：

```
effective   = 两层里 seq 最新的那个观测
spinner     = effective.status ∈ LIVE_STATUSES        （受 liveSuppressedBelowSeq 门控）
needsInput  = effective.status === 'interrupted'      （同一门控）
unseen      = effective.status ∈ TERMINAL_FAMILY
              && runSeq > lastSeenSeq
              && runSeq > seenSuppressedBelowSeq
              && threadId !== activeThreadId
```

#### 3.10.3 `effective()` —— 两层合并规则（必须逐条照抄）

```
若只有一层  → 返回那一层
两层都有 seq：
  seq 不等 → 大的赢
  seq 相等 → terminal 类（含 interrupted）赢 live 类
local 无 seq（聊天流不带 run_seq）：
  两边 runId 相同 → feed 权威（terminal 赢 live）。理由：卡死的本地流不该
                    把一个已经崩溃恢复并结算的 run 的转圈一直挂着
  runId 不匹配：
    local 是 LIVE  → local 赢（哪怕还没 runId：id 要到响应头才latch，
                     一个过期的 feed terminal 不许掐掉刚发出去的转圈）
    local 是 TERMINAL → 让位给带 seq 的 feed（别的 tab 可能已起了新 run）
local 有 seq 而 feed 没有 → 信带 seq 的那层（即 local）
```

#### 3.10.4 seq-aware 单调合并

`mergeFeedObservation(tid, obs)`：**旧观测永远不能覆盖新观测**。

```
若当前 feed 观测有 seq：
  新观测无 seq            → 丢弃
  新观测 seq < 当前 seq    → 丢弃
  seq 相等 且 当前是 terminal 且 新的不是 → 丢弃（live 不许把 terminal 降级）
否则写入
```

`applySeenCursors` / `seedFromListRows` 里的游标同理，**只升不降**（`if (seen > entry.lastSeenSeq)`）。

#### 3.10.5 快照的"缺席推理"—— 两个水位缺一不可

快照帧：

```ts
interface SnapshotFrame {
  as_of_seq: number;
  // 0 = unseen 集合是完整的；低于此 seq 的缺席不证明任何事
  oldest_included_unseen_seq: number;
  live: SnapshotEntry[];    // 【无上限】——缺席即证明"不活跃"
  unseen: SnapshotEntry[];  // 【有截断】——缺席只在 cutoff 之上才证明"已读"
}
```

应用规则（**单调，绝不整体替换**）：

- **在场的条目**：`mergeFeedObservation` 按 seq 合并；`lastSeenSeq` 取 max。
  并且要**释放**之前的缺席推断：`run_seq` 由 `nextval()` 分配、稍后才提交，所以一个 run 可以提交在更早快照的 `as_of` 水位**之下**。若不释放，水位会永久压住它（抑制只会升）。释放条件加一道"最新已知"守卫：`entry.run_seq >= (e.feed?.runSeq ?? 0)` 时才把两个水位下调到 `run_seq - 1`。
  > 之所以敢在"在场"时释放：在场条目一定是该线程的**最新** run —— 三条不变式撑着（快照 SQL 每线程 LATERAL LIMIT 1、同一连接的帧按序投递、fork 原子截断 run 链）。
- **缺席的条目**（遍历 store 里所有线程，跳过在场的；**两层都要看**）：
  - 观测无 seq 或 `runSeq > as_of_seq` → 跳过（快照还没看到它）
  - 观测是 LIVE 或 interrupted → `liveSuppressedBelowSeq = max(它, as_of_seq)`
  - 观测是 TERMINAL **且** `runSeq >= oldest_included_unseen_seq` → `seenSuppressedBelowSeq = max(它, as_of_seq)`

**为什么必须两个水位**：单个"状态无关"的标量是 v5 的 bug。live 集合无上限、缺席即证明，所以它只能关掉活跃指示；而 unseen 集合被截断过，一个仅仅因为超出上限而被裁掉的 terminal 未读态**不能**被它抹掉。

**绝不能**用"删掉 feed 层"来处理缺席——那样底下会重新暴露一个陈旧的 local `running`。

#### 3.10.6 三个写入源

| 源 | 写哪层 | 入口 |
|---|---|---|
| 本 tab 的聊天 SSE 流 | `local` | `publishLocalRunning(tid, runId?)` / `publishLocalSettled(tid, runId?)` / `clearLocalObservation(tid)` |
| 用户级 feed 事件 | `feed` | `applyFeedEvent({type:'run_started'\|'run_settled', ...})` |
| feed 快照帧 | `feed` + 两个水位 | `applySnapshot(frame)` |
| 任意线程列表响应的行 | `feed` + `lastSeenSeq` | `seedFromListRows(rows)`（读行上的 `latest_run_id/latest_run_seq/run_status/interrupt_reason/last_seen_run_seq`） |

**`clearLocalObservation` 与 `publishLocalSettled` 的区别是核心语义**：卸载 / LRU 淘汰 / id 翻转走前者（**只丢 local 层**，底下的 feed 观测保留），**绝不**铸造一个"完成"；只有流真正跑到自然终点才走后者。

#### 3.10.7 已读游标

- `setActiveThread(tid | null)`：用户正在看的线程；unseen 派生把它排除。
- `raiseSeenToEffective(tid)`：乐观已读——打开线程即清点，即使 `/seen` POST 丢了；持久游标下次打开自愈。
- `applySeenCursors(tid, { last_seen_run_seq })`：来自 `/seen` POST 的权威游标。
- feed 里 `run_settled` 且 `thread_id === getActiveThreadId()` → 直接 `markThreadSeen`，用户正看着的完成永远不冒点。

#### 3.10.8 订阅：按线程订阅，绝不订阅整个集合

```ts
useThreadRunning(tid)   / useThreadNeedsInput(tid)
useThreadUnseen(tid)    / useThreadRunStatus(tid)
useThreadFlags(tid) → { isRunning, needsInput, isUnseen, status }   // 推荐
isThreadRunning(tid)    // 命令式读取，供事件处理器用；【禁止在 render 期调用】
```

`useThreadFlags` 把四个指标**打包成一个整数快照**（bit0 running / bit1 needsInput / bit2 unseen / bit3+ status 序号），一次 `useSyncExternalStore` 订阅搞定，再 `useMemo` 解包。分开读要付四次订阅、四次快照比较。

**硬性要求**：行组件必须订阅**单个线程**，绝不订阅整个 Set —— 否则任何一个 run 事件会重渲染所有已挂载列表里的所有行。

#### 3.10.9 `recompute()` 的 notify 门槛

每次写入后全量重算四个派生结构，然后**只在真的变了才 notify**：`running`/`needsInput`/`unseen` 用 `setsEqual` 逐项比；`effStatus` 用 size + 逐项比（不物化数组——这个函数在每个 feed 事件/快照上都跑）。

#### 3.10.10 用户级 feed 连接（`feedClient.ts`）

**每个 tab 一条**，挂在 `AuthenticatedShell` 上（`<ThreadLifecycleFeed />`），`GET /api/v1/users/me/thread-events`。这是让"后台调度的 run"在没有任何聊天视图挂载时也可见的推送通道。

- **连接策略：永久重订阅**。活过 ≥30s 记为健康 → 立刻重连（覆盖服务端 600s 时长上限）；短于 30s 走指数退避 1s → 30s，带 0~30% 抖动。
- **生命周期事件**：`pagehide` / `freeze` → abort（**绝不把 socket 带进 bfcache**）；`pageshow` / `visibilitychange(visible)` → 恢复。
- **重启安全：generation 计数器**。`start()`/`stop()` 都 `generation += 1`，循环在**每一个挂起点之后**重新检查 `client.generation === gen`。这样认证抖动（登出→登录）不会留下一个僵尸循环在活循环旁边重连；将死的循环也不能 abort 或清空活循环的 controller（controller 是循环局部的，按 identity 释放）。
- **每次挂接都置 `resyncOnSnapshot = true`**：该连接的第一个 snapshot 帧把失效升级为整个 `threads` 前缀 —— 断开期间（冻结 tab、后端重启、600s 上限）可能漏事件，而这条 feed **没有重放**。
- **失效去抖 300ms**：并发 run 的结算风暴合并成"每个受影响工作区一次 + recent 列表一次"。resync 时一次前缀级全量失效吞掉所有分片失效。
- **事件处理**：
  | 事件 | 动作 |
  |---|---|
  | `snapshot` | `applySnapshot`；首帧→全量 resync，否则按帧中出现的 workspace 分片失效（裸 `scheduleInvalidate()` 只够到 recent 列表，在聊天页等于 no-op） |
  | `thread_lifecycle` / `run_started`·`run_settled` | `applyFeedEvent` + 分片失效；settled 且是活跃线程 → `markThreadSeen` |
  | `thread_lifecycle` / `thread_title` | `patchThreadTitle`（带 `updated_at` 版本） |
  | `thread_lifecycle` / `thread_pinned` | 就地改 `is_pinned` 标志（快照帧没有这个字段，不打补丁则别的 tab 的置顶永远到不了侧栏），再失效拿服务端的置顶优先排序 |
  | `thread_lifecycle` / `thread_deleted`·`thread_archived` | 从缓存列表移除该行（归档跳过归档视图 key）+ `pruneThread(tid)` + 失效 |
  | `thread_lifecycle` / `thread_unarchived` | 反向：从归档视图移除；活跃列表靠失效重取恢复 |
  | `timeout` | 忽略，循环自会重连 |
  | 未知 type | **忽略**（向前兼容） |
- **缓存播种**：订阅 `queryCache`，任何 key 以 `'threads'` 开头的 success 更新都喂给 `seedFromListRows`（同时处理 `{threads}` 和 `{pages:[{threads}]}` 两种形状）；启动时先扫一遍已有缓存。

<!-- SECTION-3-END -->

## 4. API 客户端层（含 SSE / WebSocket）

四条传输通道，**互不共用实现**：

| 通道 | 载体 | 用途 |
|---|---|---|
| 普通 REST | `axios` 单例 | 所有 CRUD、状态查询、文件读写 |
| **SSE** | **裸 `fetch` + `ReadableStream`**（**不是 `EventSource`**） | 聊天流、历史重放、线程 watch、多路复用流、用户级生命周期 feed |
| WebSocket | 原生 `WebSocket` | 实时行情聚合 bar |
| 文件 | axios（`FormData` 上传 / `blob`·`arraybuffer` 下载） | 沙箱文件 |

### 4.1 axios 实例（`api/client.ts`）

```ts
const baseURL = import.meta.env.VITE_API_BASE_URL ?? '';   // 空 = 同源
export const api = axios.create({
  baseURL,
  headers: { 'Content-Type': 'application/json' },
});
```

**没有全局 timeout**（刻意的：部分接口本来就慢）。需要限时的调用点自己传，比如 `cancelWorkflow` 传 `timeout: 5000` —— 否则网络层挂起会一直等到浏览器默认的 ~60s，把"停不下来"的提示拖后几分钟。

#### 4.1.1 token 注入是**依赖倒置**的

`api/client.ts` **不 import** Supabase。它只暴露两个 setter，由 `AuthContext` 在拿到 session 时注入：

```ts
let _getAccessToken: (() => Promise<string | null>) | null = null;
let _refreshToken:   (() => Promise<string | null>) | null = null;
export function setTokenGetter(fn)    { _getAccessToken = fn; }
export function setTokenRefresher(fn) { _refreshToken = fn; }
```

登出时 `AuthContext` 把两者都设成 `() => Promise.resolve(null)`。

#### 4.1.2 请求拦截器

```ts
api.interceptors.request.use(async (config) => {
  if (_getAccessToken) {
    try {
      const token = await _getAccessToken();
      if (token) config.headers.Authorization = `Bearer ${token}`;
    } catch { /* 拿不到就不带认证继续 */ }
  }
  return config;
});
```

**取 token 抛错必须吞掉**：一个坏掉的 session 不该让所有请求在客户端就崩，让服务端返 401 去走统一的失败路径。

#### 4.1.3 响应拦截器 —— 429 归一 + 401 单次刷新重放

```ts
api.interceptors.response.use(
  (r) => r,
  async (error) => {
    // ① 429：把结构化限流信息挂到 error 上
    if (error.response?.status === 429) {
      const detail = error.response.data?.detail || {};
      error.status = 429;
      error.rateLimitInfo = typeof detail === 'object' ? detail : {};
      error.retryAfter = parseInt(error.response.headers?.['retry-after'], 10) || null;
    }
    // ② 401：强制刷新一次并重放（单次守卫 config._retry）
    const config = error.config;
    if (error.response?.status === 401 && config && !config._retry && _refreshToken) {
      config._retry = true;
      try {
        const token = await _refreshToken();
        if (token) {
          config.headers.Authorization = `Bearer ${token}`;
          return api(config);            // 重放
        }
      } catch { /* 刷新失败 → 落到下面 reject 原始错误 */ }
    }
    return Promise.reject(error);
  },
);
```

**401 重放的由来**：iOS Safari 从冻结的 tab 回来时，Supabase 的自动刷新定时器还没跑，缓存 session 已经过期，重取立刻 401。强制刷新一次再重放即可。`config._retry` 是**单次**守卫，防止刷新后仍 401 时无限循环。

**错误归一约定**：错误对象上统一挂 `status`（数字）、可选 `rateLimitInfo`、`retryAfter`、`errorInfo`。UI 层只读这几个字段，不去挖 `error.response.data.detail.xxx`。

### 4.2 SSE：为什么不用 `EventSource`

`EventSource` 有三个致命限制，这个应用全踩：

1. **只能 GET**。而发消息是 `POST /threads/{id}/messages`，请求体带 messages/模型/上下文，响应直接是 SSE 流。
2. **不能自定义请求头**。Bearer token 加不上（只能塞 query string，会进日志）。
3. **重连策略不可控**。这里需要"带 `last_event_id` 精确续点 + 指数退避 + generation 守卫"。

所以**全部走 `fetch` + `res.body.getReader()` 手工分帧**。

### 4.3 SSE 认证头（`pages/ChatAgent/utils/api/transport.ts`）

```ts
export async function getAuthHeaders(): Promise<Record<string, string>> {
  if (!supabase) return {};                       // oss 模式：无认证
  const { data } = await supabase.auth.getSession();
  const session = data.session;
  let token = session?.access_token;
  // 【关键】提前刷新：后台 tab 的 Supabase 自动刷新定时器是冻结的，
  // 恢复时缓存 session 可能已过期。过期或距过期 ≤60s 就强制刷新，
  // 免得 SSE 重连拿着死 token 打出 401。expires_at 单位是【秒】。
  if (session && token && typeof session.expires_at === 'number') {
    const nowSec = Math.floor(Date.now() / 1000);
    if (session.expires_at - nowSec <= 60) {
      try {
        const { data: refreshed } = await supabase.auth.refreshSession();
        if (refreshed.session?.access_token) token = refreshed.session.access_token;
      } catch { /* 刷新失败 → 沿用旧 token */ }
    }
  }
  return token ? { Authorization: `Bearer ${token}` } : {};
}
```

**这个函数永不 throw**。SSE 里 axios 的响应拦截器帮不上忙（不走 axios），所以刷新逻辑必须前置到这里。

### 4.4 `streamFetch` —— 唯一的 SSE 读取器

签名与返回值：

```ts
async function streamFetch(
  url: string,
  opts: RequestInit,
  onEvent: (event: Record<string, unknown>) => void,
  onHeaders?: (contentLocation: string | null) => void,
): Promise<{ disconnected: boolean; aborted: boolean; contentLocation: string | null }>
```

#### 4.4.1 建连阶段

```
fetch(`${baseURL}${url}`, opts)
  catch: err.name === 'AbortError'  →  return { disconnected:false, aborted:true, contentLocation:null }
         （用户在响应头到达前就点了停止；AbortError 是 DOMException，
           【不一定是 Error 实例】，必须按 name 匹配而不是 instanceof）
         其它 → throw
```

**响应头一到就立刻回调 `onHeaders(contentLocation)`**，早于任何 SSE 字节。这样 `run_id` 能在第一个 `metadata` 事件之前 latch 住，关掉"清除旧 run_id"与"新一轮首个 metadata 帧"之间的重连竞态窗口。`contentLocation` 在读 body 之前就快照下来，即使后面 4xx 中止也能拿到规范重连 URL。

#### 4.4.2 HTTP 错误分支（顺序固定）

| 状态 | 行为 |
|---|---|
| 429 | 读 JSON，`err.status=429`、`err.rateLimitInfo=detail`、`err.retryAfter=Retry-After`，throw |
| 413 | throw "Files too large. Try smaller files or fewer attachments."，`err.status=413` |
| 404 且 url 含 `/replay` | throw `HTTP error! status: 404`（新线程重放是**预期**的 404，调用方靠这个消息识别） |
| 其它 | 读 body 文本 → 试 JSON.parse：若 `body.detail` 是带 `message` 的对象则挂 `err.errorInfo` 并取 `detail.message` 当消息；否则字符串化。throw 时挂 `err.status` |

#### 4.4.3 分帧与解析

按 `\n` 逐行处理，跨 chunk 用 `buffer` 拼接：

```ts
const reader = res.body!.getReader();
const decoder = new TextDecoder();
let buffer = '';
let ev: { id?: string; event?: string } = {};

const processLine = (line: string) => {
  if (line.startsWith('id: '))          ev.id = line.slice(4).trim();
  else if (line.startsWith('event: '))  ev.event = line.slice(7).trim();
  else if (line.startsWith('data: ')) {
    try {
      const d = JSON.parse(line.slice(6));
      if (ev.event)      d.event = ev.event;      // 事件名合并进对象
      if (ev.id != null) d._eventId = parseInt(ev.id, 10) || ev.id;  // 数字优先，非数字保留原串
      onEvent(d);
    } catch (e) { console.warn('[api] SSE parse error', e, line); }   // 【解析失败不中断流】
    ev = {};                       // data 行落地即重置帧头
  } else if (line.trim() === '') ev = {};          // 空行 = 帧边界
};

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const lines = buffer.split('\n');
  buffer = lines.pop() || '';        // 最后一段可能是半行，留到下次
  lines.forEach(processLine);
}
buffer.split('\n').forEach(processLine);   // 收尾残留
```

**约定**：
- 每个事件对象最终形如 `{ ...payload, event: '<事件名>', _eventId: <数字或串> }`。下游全部靠 `event.event` 分派、靠 `event._eventId` 排序去重。
- `TextDecoder` 必须带 `{ stream: true }`，否则多字节 UTF-8 被 chunk 切开会解码出乱码。
- **单条 `data:` 行解析失败只 warn，不终止读取循环**。
- 这个读取器只支持**单行 `data:`**（后端契约保证）。`watchThread` 那条路径额外做了多行 `data:` 合并（见 §4.7）。

#### 4.4.4 读取阶段的异常分类（三分支，必须照抄）

```ts
catch (error) {
  if (error?.name === 'AbortError') {
    aborted = true;               // 用户主动停止 → 调用方跳过重连/报错/二次清理
  } else if (error instanceof Error && error.name === 'TypeError') {
    disconnected = true;          // 传输层断流 → 走重连路径
  } else {
    throw error;                  // 真异常
  }
}
```

**为什么 `TypeError` 一律当断流**：iOS Safari 冻结后台 tab 时会拆掉连接，`reader.read()` 抛 "Load failed" / "The network connection was lost."，两者都不可靠地含有 "network" 字样——旧的字符串匹配守卫会把它重新抛出，界面停在一个没有重连的死错误横幅上。按 Streams/Fetch 规范，`reader.read()` **只在传输层网络错误时**抛 `TypeError`；循环体内（decode/split/processLine，且 `JSON.parse` 已被自己 try 住）不会另外抛出 `TypeError`。

返回的三元组 `{ disconnected, aborted, contentLocation }` 是调用方分流的唯一依据：

- `aborted` → 用户停止，`stopWorkflow` 已经接管清理，直接 return
- `disconnected` → 抛 "stream disconnected"，进入重连退避循环
- 都为 false → 正常结束，走 finalize

### 4.5 `Content-Location` 头：run_id / thread_id 的 latch

后端在 SSE 响应头返回：`Content-Location: /api/v1/threads/{tid}/messages/stream?run_id={uuid}`

两个纯函数解析它（**都必须非抛出、失败返回 `null`**）：

```ts
parseRunIdFromContentLocation(v)    // 取 ?run_id=；用 URLSearchParams
parseThreadIdFromContentLocation(v) // 正则 /\/threads\/([^/?]+)\// 再 decodeURIComponent
                                    // 【注意】畸形百分号编码（"%ZZ"）会让 decodeURIComponent 抛
                                    // URIError，必须 try/catch 返回 null
```

**为什么 thread_id 也要从头里 latch**：新建会话在第一个 SSE 事件到达前，路由里的 `threadId` 还是 `'__default__'`。若用户此时就点停止，没有真实 id 就取消不了。从响应头 latch 住就能取消。

### 4.6 三条发送路径共用的 `postSSEStream`

```ts
async function postSSEStream(path, body, { onEvent, onRunIdResolved, signal }) {
  const authHeaders = await getAuthHeaders();
  return streamFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...authHeaders },
    body: JSON.stringify(body),
    ...(signal ? { signal } : {}),
  }, onEvent,
     onRunIdResolved ? (cl) => {
       const runId = parseRunIdFromContentLocation(cl);
       if (runId) onRunIdResolved(runId, parseThreadIdFromContentLocation(cl));
     } : undefined);
}
```

三条调用路径只差 path + body：

| 路径 | HTTP | body 要点 |
|---|---|---|
| 新建/继续会话 | 新线程 `POST /api/v1/threads/messages`；已有线程 `POST /api/v1/threads/{tid}/messages` | `workspace_id, messages[], agent_mode, plan_mode, locale, timezone` + 可选 `request_key / additional_context / checkpoint_id / fork_from_turn / llm_model / reasoning_effort / fast_mode / platform`。**checkpoint 重放（重新生成/重试）时 messages 传空数组** |
| 重试上一次失败的 run | `POST /api/v1/threads/{tid}/retry` | `{ workspace_id }` + 模型选项 + `request_key`。后端自己校验目标仍是最新 attempt、自己解析重试 checkpoint —— **客户端不取 checkpoint、不 fork、不截断** |
| HITL 恢复 | `POST /api/v1/threads/{tid}/messages` | `{ workspace_id, messages: [], hitl_response, plan_mode, agent_mode }` + 模型选项 |

`request_key` 是幂等键，网络重发不会产生两个 run。

### 4.7 断线重连与 `last_event_id` 去重

#### 4.7.1 重连请求怎么带游标

```ts
export async function reconnectToWorkflowStream(threadId, runId, lastEventId, onEvent, signal) {
  const params = new URLSearchParams();
  if (runId)             params.set('run_id', String(runId));
  if (lastEventId != null) params.set('last_event_id', String(lastEventId));
  const authHeaders = await getAuthHeaders();
  return streamFetch(
    `/api/v1/threads/${threadId}/messages/stream${params.toString() ? '?' + params : ''}`,
    { method: 'GET', headers: { ...authHeaders }, ...(signal ? { signal } : {}) },
    onEvent,
  );
}
```

> **注意**：走的是 **query 参数 `last_event_id`，不是 `Last-Event-ID` 请求头**。原因就是 §4.2 —— 不用 `EventSource`，也就不享受浏览器自动补 `Last-Event-ID` 头的机制；而手写 fetch 里放 header 也可以，但后端契约选了 query 参数（便于日志/回放调试），前端必须跟。带 `run_id` 时后端定位到精确的 per-run Redis 流键 `workflow:stream:{tid}:{rid}`；不带则回落到线程最新 run。

#### 4.7.2 游标怎么维护

```
lastEventIdRef: Ref<number | string | null>
```

- 只在 `processStreamEvent` 里推进，且**只有主干事件**才写：`if (event._eventId != null && !isSubagent) rt.lastEventIdRef.current = event._eventId;`
  子代理事件带的是**每任务自己的 seq**，绝不能污染主游标。
- **历史重放（`/replay`）绝不写这个游标**。重放的 `_eventId` 属于另一套序号空间，写进去会让后续重连从错误位置续。重放只在开始时把它清成 `null`。
- **`resetCursor` 语义**：凡是"（重新）挂接到线程当前活跃 run"的调用方（线程加载、跨线程导航、HITL 恢复后、report-back）都要传 `runId` 并且 `resetCursor: true` —— 否则 `currentRunIdRef` / `lastEventIdRef` 还指着**上一个线程**的流，会挂到一个死键上（零实时事件，内容只在后续重取时才出现）。
  **中途断线**的重连则**两个都不传**，保留进行中的游标。

#### 4.7.3 重连退避循环

`attemptReconnectAfterDisconnect`：最多 **5 次**，延迟 `1s, 2s, 4s, 8s, 16s`（`BASE_DELAY * 2^(attempt-1)`，第 0 次不等）。每次先 `GET /threads/{tid}/status`：

- `can_reconnect === false` → 放弃，跳出循环
- 否则 `reconnectToStream({ activeTasks: status.active_tasks, snapshotAtMs: Date.now(), resetSubagentProjection: false })`

重连目标是**latch 住的线程 id（`threadIdRef`）而不是路由 prop** —— 首轮回答期间 prop 还是 `'__default__'`（"提问→切走 tab→回来"这个最常见时刻）。整个 ~31s 的重试循环把 id 快照一次，保持钉住同一个 run。

耗尽后：`setIsReconnecting(false)` → `cleanupAfterStreamEnd` → `setReloadTrigger(n => n+1)` 触发整段会话重载，从持久化历史补齐完整回答。

#### 4.7.4 重连后如何去重合并

四层去重，各管一段：

1. **服务端游标续点**：`last_event_id` 让后端只发游标之后的条目。这是主力。
2. **子代理投影重置策略**（`resetSubagentProjection`）：
   - **history 支撑的重连**（线程加载 / 跨线程导航 / HITL 恢复后）会重放整个 run → 必须 `clearSubagentCards()` 并清空 `terminalTaskOutcomesRef`，否则缓存 + Redis 重叠会把卡片翻倍。
   - **report-back 挂接**没有子代理重放（只推一条合成通知轮次）→ 传 `false` 保留实时投影。清了反而会删掉一个仍在跑的兄弟任务的详情卡，若它正卡在长工具调用里就没人重建，详情视图永远停在 "Initializing" 而 chip 显示 "Running"。
   - **中途断线重连**用保留的游标、不重放 → 也传 `false`（清了会让本轮早先 spawn、尚未持久化的任务失去重建来源）。
3. **中断段剥离**：历史重放填充的 interrupt 段要在重连流重新投递前剥掉，否则同一个问题/提案渲染两次（一次在历史气泡、一次在重连气泡）。**重连流对实时中断状态是权威的**。剥离时必须**同步**把被剥 id 从 `renderedInterruptIdsRef` 里释放 —— 剥完之后重连流的重投是**唯一一份**，残留条目会把它压掉，导致中断卡哪都没有、无法回答，只能整页刷新。同时把 `unresolvedHistoryInterruptRef` 里的 `assistantMessageId` 重定向到新气泡。
4. **空气泡清理**：重连创建的占位助手消息若最终没有任何 contentSegments 和 content，在 `finally` 里删掉。**但用户主动停止时跳过**（`finalizeStreamingMessage` 刚给这个气泡盖了 `stopped: true` 和 "⏹ Stopped" chip，删掉就抹掉了停止标记）。

另外，重连时若上一条消息是历史重放产出的**空的**助手消息，用新的重连气泡**替换**它而不是追加，避免重复气泡。

#### 4.7.5 空闲看门狗（仅 report-back 路径）

per-run 流没有终止哨兵（摘要之后大约 8s 握手，卡死的 run 则永远不结束），所以传 `idleAbortMs` 的调用方要装看门狗。**静默 ≠ 终止**，超时后必须先探测：

```
GET /threads/{tid}/status?fields=report_back
  探测失败 或 signal === 'unknown' → 重新武装（最多 REPORT_BACK_IDLE_MAX_REARMS 次），耗尽则强制关闭
  report_back_run_id 存在且 !== attachedRunId，或 signal === 'idle' → 队列已排空/被更新的 head run 取代 → 真的完成，关闭
  否则（pending/none、同一个或没有 run id） → 只是慢，重新武装
```

**任何事件到达都把重新武装预算 `idleRearms` 清零** —— 健康的 run 偶尔有一个超过 `idleAbortMs` 的空档，不该累计到"卡死"上限里。探测的 await 之后必须**重新检查 ownership**（`rt.mainStreamAbortRef.current === abortController`），期间可能已被更新的重连/finalize/stop 接管。

`pending_report_back` 是**三态** `boolean | null`（true=待处理 / false=已排空 / null=后端自己的 Redis 读失败），必须用 `decodeReportBackSignal()` 解码，**绝不能直接对原始布尔分支**。

### 4.8 流所有权契约

`isStreamingRef` 和 `streamingThreadIdRef` 是**同一个不变式**（"一条重连流恰好属于一个线程，或者不属于任何线程"），只能通过这两个函数改：

```ts
acquireStreamOwnership(rt, tid) { rt.isStreamingRef.current = true;  rt.streamingThreadIdRef.current = tid; }
releaseStreamOwnership(rt)      { rt.isStreamingRef.current = false; rt.streamingThreadIdRef.current = null;
                                  if (rt.pendingMuxResyncRef.current) { … setReloadTrigger(n=>n+1); } }
```

清理阶段的 ownership 守卫（必须逐条实现）：

- `stillActive = rt.mainStreamAbortRef.current === abortController` —— 导航可能已经用新流取代了本流，此时跑清理会把新线程的状态砸掉。
- 但**清空 abort ref 时必须用实时再检查，不能用上面的 `stillActive` 快照**：`cleanupAfterStreamEnd → onStreamEnd` 可能**同步**链式挂接下一个排队的 report-back run，它会在自己第一个 await 之前注册一个新的 `AbortController`；用陈旧快照去 null 掉那个新注册，会让流变成孤儿（停不掉、清理被跳过、`isLoading` 和 `isStreamingRef` 永远卡在 true）。
- `isReconnectingOwnerRef`：只有拥有 spinner 的那条流才能清掉 spinner。
- 内容一开始流动就 `markReconnected()` 关掉 "Reconnecting…" spinner（实时 run 的 reader 整轮都开着，否则 token 都在往外冒了 spinner 还在转）；`isLoading`（停止按钮）保持开启。

### 4.9 多路复用线程流（v2 契约）

`GET /api/v1/threads/{id}/stream?contract=v2` —— 一条 socket 承载**每个开放 run 一条 run 域信道**（主干 lane + 每个子代理任务 run）外加 watch 中继。

#### 4.9.1 传输层：`openThreadMuxStream`

**故意不复用 `streamFetch`** —— mux 客户端要自己解析 SSE 块，它需要 `run:<run_id>#<entry_id>` 这种游标 id 行，而 `streamFetch` 的解析器会把它 `parseInt` 弄坏。所以这个 helper 只负责传输：baseURL、认证头、abort、**按行切分**，把每一行原样交给 `onLine`。

```
GET /api/v1/threads/{id}/stream?contract=v2
    &cursors=run:<runId>#<entryId>,run:<runId2>#<entryId2>   （URL 编码）
    &since_age_s=<秒>                                        （>0 时才带）
```

#### 4.9.2 信道状态

```ts
interface RunChannel {
  runId: string;
  lane: string;          // "main" | "task:<taskId>"
  cursor: string | null; // 最后收到的 entry id —— 重连续点
  applied: string | null;// 最后【已交付给 sink】的 entry id —— 去重高水位
  closed: boolean;
  outcome: string | null;
  drain: boolean;        // drain 模式打开：被取代的前驱的积压
  startedAt: number;     // 服务端声明的 run 开始时刻（epoch ms，账本行真相）
}
```

**投递是 at-least-once**：信道以 replay 模式重新推送（新 socket / resync / reconcile 重扫）时**从 0 重发**，靠 per-run 的 `applied` 高水位把已交付的丢掉。entry id 形如 `"1784-3"`（Redis 流 id），比较用主次号数值比较（`entryAfter`），**不能字符串比较**。

#### 4.9.3 关闭是"正向"的

信道关闭**只来自服务端的 `run_end` / `chan_close {reason:"terminal"}`（账本行真相）**，**绝不**从 socket 丢失或重试耗尽推断。

`chan_close` 的四种 reason：

| reason | 动作 |
|---|---|
| `terminal` | `closed=true`，记录 outcome；若该 task 没有其它开放 run 则 `onTaskRunClosed(taskId, outcome)` |
| `resync_required` | 游标指向一个已丢失的头部 → `closed=true`、`cursor=null`、`forceReconnect=true`、`poisonHorizon()`、abort。信道以 replay 模式从 0 重挂，保留的 `applied` 高水位把重放变成去重 —— 最坏是一个有界的空洞，绝不会重复内容 |
| `unknown_run` | 直接 `runs.delete(runId)` |
| 其它 | 忽略 |

**outcome 归属按"服务端声明的 run 开始时刻"排序，不按关闭顺序**：批量读取下前驱的重放积压可能在一个短命的后继之后才关闭，且一次故障之后每条信道都以 drain 重开。所以 `latestRunOutcome: Map<taskId, {startedAt, outcome}>` 只在 `chan.startedAt >= 已记录的 startedAt` 时才更新。前驱关闭而后继还开着**不算**任务终止。

#### 4.9.4 知识视野（`knownAt`）与 `since_age_s`

- `knownAt`（epoch ms）：由 `attach(sink, snapshotAtMs)` 用状态/历史快照时刻播种（取 `min`），之后**每收到一行 SSE（含 keepalive）就刷新成 `Date.now()`** —— 任何处理过的行都证明控制 lane 在那一刻是活的。
- 连接时算 `since_age_s = (Date.now() - knownAt)/1000`，服务端据此加宽"已结算 run"的追赶窗口。
- **`poisonHorizon()`**：mux 自己撕连接时会丢弃本连接缓冲区里的行——包括那些"我们从此对其一无所知"的控制通告。触发撕裂那一行的接收时刻**不是**它们的有效水位，所以要把 `knownAt` 回滚到**连接开始时的地板** `connStartKnownAt`（所有被丢弃的行都产生于连接开始之后）；若连地板都没有，置 `horizonUntrusted = true`，下次连接声明一个超上限的 age（`24 * 3600` 秒），逼服务端回一个线程级 resync。
- `horizonUntrusted` 是**粘性**的：只有 resync 真正送达 sink（`onResyncRequired` 被调用）才解除；连接中途死掉或 EOF 就在下次尝试重新声明。

#### 4.9.5 abort 后的行必须全部丢弃

`onLine` 和 `flushBlock` 开头都要 `if (this.controller?.signal.aborted) return;`。已撕连接的剩余缓冲行是死的：它们不能推进知识视野（被丢弃的控制通告根本没被应用，声称应用了会让那个 run 永远发现不了），它们的帧也不能被组装 —— 尤其一个 `run_end` 会**确认并越过**一个 sink 从未应用的条目。

#### 4.9.6 sink 抛错 = 毒化本连接

```ts
try {
  this.sink?.onTaskEvent(ev);
  chan.applied = entryId;   // 【只有成功交付之后】才推进
  chan.cursor  = entryId;
} catch (e) {
  this.forceReconnect = true;
  this.poisonHorizon();
  this.controller?.abort();
}
```

游标和高水位**只在成功交付之后推进**：独占续点的游标不能确认一个 sink 从未应用的帧，去重也必须让它保持可被 replay 重新投递。这样一个确定性的 sink bug 退化成"退避有界的重连循环"，而不是**静默丢内容**。

#### 4.9.7 重连退避

`min(1000 * 2^(retry-1), 16000)`。正常 HTTP 结束会把 `retry` 归零，**但 `transport_error` 控制帧例外**：服务端因为传输失败（Redis 故障）关闭连接时 HTTP 请求是正常结束的，若归零会让一次故障变成 1 rps 的猛敲，所以用 `connFailed` 标记住、让退避继续增长。

HTTP 400~404（除 401）是**确定性拒绝**，重试治不好 → 直接 `dispose()`，让之后的 attach 从头开始，而不是留一个惰性注册表条目。**401 保持可重试**：后台 tab 会带着过期 token 重连，而每次重试都会重新读 session。

#### 4.9.8 帧到 v1 事件的投影

task lane 的内容帧 payload 是捕获记录 `{seq, event, data}`，投影成 hook 消费的 v1 SSE 形状：

```ts
const ev = { ...record.data, event: ftype, thread_id: this.threadId };
if (typeof record.seq === 'number') ev._eventId = record.seq;
if (chan.drain) { ev._drain = true; if (chan.startedAt) ev._runStartedMs = chan.startedAt; }
```

`_drain` 让 sink 跳过已经从历史投影过的内容；`_runStartedMs` 让它在 **run 粒度**上做这件事 —— 一份陈旧的持久化欠账 drain 不许重放进一个被后继保持存活的 task。

`main` lane 的帧只推游标不投递（前台的 POST 拥有主 lane）。

#### 4.9.9 注册表

`muxByThread: Map<threadId, ThreadStreamMux>`；`getThreadMux(tid)` 取或建，`peekThreadMux(tid)` 只取不建（被动读）。`dispose()` 时按 identity 从表里摘除（`if (muxByThread.get(tid) === mux) delete`）。

### 4.10 线程 watch 流（`watchThread`）

`GET /api/v1/threads/{tid}/watch`，Redis pub/sub 支撑，用来发现"这个线程上起了新的工作流"。**这条路径自己解析 SSE，不用 `streamFetch`**，因为它必须按**完整帧**（以空行 `\n\n` 结尾）反应：

> 一个帧可能跨多次 `read()` 到达，若一看到事件名就反应，会撞上只读到一半的 `data:` 行、解析出残缺 JSON —— 丢掉 `run_id`，逼调用方退回 `/status`，而对一个快速的 report-back 来说那时它已经被拆掉了。

多行 `data:` 按 SSE 规范用 `\n` 连接后再 `JSON.parse`（当前后端都是单行，这是防御性的）。

两种事件：

| 事件名 | 含义 |
|---|---|
| `watch_snapshot` | 每次订阅后端发一次的**挂接即状态**帧，JSON 与 `/status?fields=report_back` 相同 → `onSnapshot(status)`。让每次（重）订阅都无缝，且省一次 `/status` 往返 |
| `workflow_started` | report-back 唤醒，payload `{ run_id, needs_input, cleared }` → `await onWorkflowStarted(payload)` |

其余帧（keepalive ping / timeout）跳过。

**关键行为**：
- **持久订阅**：收到第一个唤醒后**不要**取消并返回 —— N 个已调度的 PTC 会分别唤醒，重新订阅会丢掉第 2 个及之后的唤醒。`await onWorkflowStarted(...)`（它会阻塞到那个 run 的流跑完）天然把链路串行化。
- 循环内重试 `MAX_RETRIES = 2`，延迟 `1000 * (attempt+1)`。
- 三个生命周期回调，语义不重叠：
  - `onResubscribed()`：**重试**（非首次订阅）成功拿到响应之后才触发。pub/sub 无重放，这段空隙里发布的唤醒**丢了**，调用方要跑一次追赶拉取。放在响应确认 OK 之后触发，所以一个硬失败的端点（直接 `return`）绝不会报告幻觉恢复。
  - `onClosed()`：**非主动**的最终关闭（后端超时 / 掉线 / 重试耗尽）恰好触发一次；调用方主动 abort **不触发**（它自己已经拆干净了）。
  - 返回 `{ abort: AbortController }`，调用方 `abort.abort()` 停止监听。

### 4.11 用户级生命周期 feed

见 §3.10.10 —— 复用 `streamFetch` + `getAuthHeaders`。

> **分层瑕疵（刻意保留）**：`lib/threadLifecycle/feedClient.ts` 反向 import 了 `pages/ChatAgent/utils/api/transport`。理由：那里是 SSE 解析器 + 提前刷新 token 的**唯一实现**，在 `lib/` 里复制这段微妙逻辑比一次 lib→pages 的 import 更糟。新实现可以把 `transport.ts` 提到 `lib/http/sse.ts`，但**绝不允许出现第二份 SSE 解析器**。

### 4.12 WebSocket 接入（`pages/MarketView/hooks/useMarketDataWS.ts`）

**全站只有这一处 WebSocket**，且必须由 `MarketDataWSProvider` 唯一持有（Context 包一层，见 §3.7）。

#### 4.12.1 URL 与握手

```ts
function getMarketDataWSUrl(market = 'stock', interval = 'second') {
  const wsBase = baseURL
    ? baseURL.replace(/^http/, 'ws')
    : `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}`;
  return `${wsBase}/ws/v1/market-data/aggregates/${market}?interval=${interval}`;
}
// token 从 supabase session 取；oss 模式返回 null
const url = token ? `${base}${sep}token=${token}` : base;   // 走 query，WS 不能带自定义头
```

**首次连接前先做 HTTP 预检**：把 ws URL 改写成 `http(s)://…/ws/v1/market-data/status`，`fetch` 带 5s 超时，`data.enabled !== false` 才继续。目的是避免后端关闭该特性时浏览器控制台刷一片握手失败的红字。预检失败 → `disabledRef = true`，状态置 `'disabled'`，**不再重连**。

连接状态机：`'connecting' | 'connected' | 'disconnected' | 'reconnecting' | 'disabled'`。

#### 4.12.2 订阅协议

文本 JSON 帧：

```jsonc
// 客户端 → 服务端
{ "action": "subscribe",   "symbols": ["AAPL", "MSFT"] }
{ "action": "unsubscribe", "symbols": ["AAPL"] }

// 服务端 → 客户端（两种形状都要认）
{ "ev": "AM", "sym": "AAPL", "o":…, "h":…, "l":…, "c":…, "v":…, "s":…, "e":… }
{ "type": "aggregate", "symbol": "AAPL", "data": { open|o, high|h, low|l, close|c, volume|v, time|timestamp|s|e } }
```

无法识别的帧（status / keepalive）**静默丢弃**，不报错。

**引用计数订阅**：`subscribedRef: Map<symbol, refCount>`。多个 widget 订阅同一 symbol 只发一次 `subscribe`；`unsubscribe` 只在计数归零时才真正发出。symbol 一律 `toUpperCase()`。**`onopen` 时用 `[...subscribedRef.keys()]` 整批重新订阅**（重连后服务端不记得任何订阅）。

#### 4.12.3 重连退避与保活

| 参数 | 值 |
|---|---|
| `INITIAL_BACKOFF_MS` | 1000 |
| `MAX_BACKOFF_MS` | 30000 |
| `BACKOFF_MULTIPLIER` | 2 |
| 抖动 | `Math.random() * 500` |
| `STALE_TIMEOUT_MS` | 45000 —— 45s 无消息就 `ws.close(4000, 'Stale connection')` |
| `HIDDEN_CLOSE_DELAY_MS` | 60000 —— 页面隐藏 60s 后主动断开 |

- 每收到一条消息就重置 stale 定时器；`onopen` 成功后 `backoff` 归 `INITIAL`。
- `onclose` 分流：已 disabled → `'disabled'`；**code 1008（认证失败）→ 标记 disabled 且不再重连**；`intentionalCloseRef` → `'disconnected'`；其余 → `'reconnecting'` + `scheduleReconnect()`。
- `onerror` 里**什么都不做** —— 它后面必定跟一个 `onclose`，由 `onclose` 统一处理。
- 可见性恢复时若未连接且未禁用，把 `backoff` 设成 `INITIAL * 2` 再连（这个值不等于 `INITIAL`，正好**跳过预检**）。
- `mountedRef` 守卫：所有 await 之后都要检查，卸载后不许再 setState / 建连。

#### 4.12.4 写穿到共享行情缓存

每条聚合帧除了更新本地 `prices: Map<symbol, PriceUpdate>`，还要 `writeQuoteFromWs(queryClient, symbol, { price, change, changePercent })` 写穿进 `['quote', SYMBOL]`。**只合并实时字段**；`name` / `previous_close` / 日内 OHLC 保留 REST 快照的值；**未被关注的 symbol 绝不播种**（避免 WS 给缓存塞进半截数据）。

时间戳换算必须和 REST bars **完全一致**：`utcMsToChartSec(tsMs, timezoneForSymbol(symbol))`，否则图表的按时间归并会裂开。

`change%` 优先用快照的 `previous_close`；判定用**真值**而不是 `!= null` —— `previous_close` 为 0 会除出 `Infinity` 并毒化共享缓存。

### 4.13 行情快照的批量合并层（`lib/quotes/`）

不是新传输通道，但属于客户端层的约定：组件按 symbol 请求，`quoteBatcher` 把 ~50ms 窗口内的请求**合并成一次批量快照请求**（股票和指数分开批，端点不同），响应按 symbol 扇出 `setQueryData(queryKeys.quote.detail(key), row)`。

- 缓存 key = 大写的 legacy symbol 拼写（指数去掉前导 `^`）。批量/单只/WS 三条路径必须塌缩到**同一个 key**。
- 窗口内已在飞行的 symbol 不重复请求，返回 pending promise。
- 批量响应里被丢掉的未知 symbol 解析成 `null`，**绝不 throw** —— 消费方看到 `quote === undefined` 而不是崩溃。

### 4.14 文件上传 / 下载

全部走 axios（要进度条、要 blob、要 `Content-Type` 协商）。

| 操作 | 实现 |
|---|---|
| 列目录 | `GET /api/v1/workspaces/{ws}/files`，params `{ path, include_system, auto_start, wait_for_sandbox }`（`auto_start` 同时也决定 `wait_for_sandbox`）→ `{ workspace_id, path, files:[] }` |
| 读文本 | `GET …/files/read?path=` → `{ content, mime, truncated }` |
| 读全文（编辑模式） | 同上加 `unlimited: true`（跳过行数分页） |
| 写 | `PUT …/files/write?path=`，body `{ content }` |
| 删 | `DELETE …/files`，**paths 走 `config.data`**：`api.delete(url, { data: { paths } })` |
| 下载（blob URL） | `GET …/files/download?path=`，`responseType: 'blob'` → `URL.createObjectURL(res.data)` |
| 下载（解析用） | 同上 `responseType: 'arraybuffer'`（Excel/PDF 客户端解析） |
| 触发浏览器下载 | 拿 blob URL → 建 `<a href download=fileName>` → `appendChild` → `click()` → `removeChild` → **`URL.revokeObjectURL(blobUrl)`**（必须回收，否则内存泄漏） |
| 上传 | `POST …/files/upload`，`FormData` append `'file'`，params `{ path }`（可选目标路径），headers `Content-Type: multipart/form-data`，`onUploadProgress: e => onProgress(Math.round(e.loaded*100/(e.total||1)))` |
| 备份到 DB | `POST …/files/backup` → `{ synced, skipped, deleted, errors, total_size }` |
| 备份状态 | `GET …/files/backup-status` → `{ persisted_files: {path: hash}, total_size }` |

上传的 413 由 `streamFetch`/axios 各自归一（发消息带附件走 SSE 路径，见 §4.4.2）。

### 4.15 API 模块划分

```
api/                          跨页共享
  client.ts                   axios 实例 + 拦截器 + token setter
  features.ts                 GET /features、PUT /features/{key}（都解 { features } 信封）
  model.ts                    模型清单
lib/bars/barsClient.ts        GET /market-data/bars/{instrument}?schema=&after=&before=&asset_class=
                              404 或无响应 → 抛 BarsNotAvailableError，调用方回落 legacy 全量拉取
                              （其它 HTTP 错误原样上抛；abort 原样上抛）
lib/quotes/snapshotApi.ts     批量快照端点
pages/ChatAgent/utils/api/    transport / messages / threads / workspaces / files / mcp /
                              memory / memos / metadata / sandbox / vault / feedback / errors
pages/<Page>/utils/api.ts     页面私有端点
```

**分层铁律**（重复 §0.6 第 1 条）：组件里不准出现 `fetch` / `axios`。唯一例外是 `AuthContext` 里对 `/api/v1/auth/sync` 的裸 `fetch` —— 它必须在 axios 的 token getter 接线**之前**跑，用的是刚拿到的 session token。

<!-- SECTION-4-END -->

## 5. 组件体系

> 本节描述**目标形态**。原实现的 `components/ui/` 是"shadcn CLI 起了个头、之后大量手改"的产物，很多约定名存实亡（见 §5.7 的偏差清单和 §8）。新实现应按这里写的来，而不是照抄原文件。

### 5.1 依赖现状：只装了 7 个 Radix 包

原实现只装了 `@radix-ui/react-` 的 `context-menu` / `dialog` / `dropdown-menu` / `hover-card` / `popover` / `toast` / `tooltip`。**没有** `slot`、`select`、`checkbox`、`switch`、`tabs`、`separator`、`accordion`、`avatar`、`progress`、`slider`、`radio-group`。也**没有** `sonner`、`cmdk`、`react-hook-form`、`date-fns` / `react-day-picker`。

这不是遗漏，是一条明确的边界：**只有"焦点管理 / 键盘导航 / 碰撞检测很难做对"的那几类才用 Radix，其余自己写。** 新实现可以保留这条边界，但要为下面这几个"自己写但写漏了 a11y"的补课（见 §8）。

### 5.2 `components/ui/` 基础组件清单

#### （一）Radix 支撑的浮层原语

| 文件 | 包装 | 关键 API |
|---|---|---|
| `dialog.tsx` | `react-dialog` | `Dialog / DialogPortal / DialogOverlay / DialogClose / DialogTrigger / DialogContent / DialogHeader / DialogFooter / DialogTitle / DialogDescription`。`DialogContent` 带 `variant?: 'default' \| 'centered'` —— **移动端底部抽屉 vs 桌面居中弹窗的分叉就在这里面**（见 §5.4） |
| `dropdown-menu.tsx` | `react-dropdown-menu` | `Root / Trigger / Content / Item / Label / Separator / Group / Sub / SubTrigger / SubContent`。`Content` 额外接一个 **`container?: HTMLElement \| null`** 转给 `Portal container`（移动端把菜单 portal 进聊天容器而不是 body）。`Item` 有 `variant?: 'default' \| 'destructive'`。高度用 `max-h-[var(--radix-dropdown-menu-content-available-height)]` 封顶 |
| `context-menu.tsx` | `react-context-menu` | `Root / Trigger / Content / Item / Separator / Group`，同样的 destructive item 变体 |
| `popover.tsx` | `react-popover` | `Popover / Trigger / Content / Anchor`，默认 `align="center"`、`sideOffset=4`、`w-72`，底色 `bg-popover` |
| `tooltip.tsx` | `react-tooltip` | `Tooltip / Trigger / Content / Provider`，`sideOffset=4`、`collisionPadding=8`，底色走内联 `var(--color-bg-elevated)` |
| `hover-card.tsx` | `react-hover-card` | `HoverCard / Trigger / Content`，`w-72`，底色 `bg-popover` |
| `toast.tsx` | `react-toast` | `ToastProvider / Viewport / Toast / Title / Description / Close / Action` |

#### （二）react-aria-components 支撑的

| 文件 | 包装 | 说明 |
|---|---|---|
| `field.tsx` | `Label` / `Text` / `FieldError` / `Group` | 导出 `Label`、`labelVariants`、`FieldGroup`、`fieldGroupVariants`（`variant: 'default' \| 'ghost'`）、`FieldError`、`FormDescription` |
| `aria-select.tsx` | `Select` / `ListBox` / `Button` / `SelectValue` / `Popover` | 导出各分件 + 一个便利封装 `JollySelect<T>`（`label` / `description` / `errorMessage: string \| (validation) => string` / `items` / render-prop children）。popover 用 `w-[--trigger-width]` 对齐触发器宽度 |
| `aria-popover.tsx` | react-aria `Popover` / `Dialog` / `DialogTrigger` | `Popover / PopoverTrigger / PopoverDialog`。和 Radix 的 `popover.tsx` 是两套 |
| `list-box.tsx` | react-aria `ListBox` 系 | 选中项渲染 `Check` 图标，`data-[selection-mode]:pl-8`，`data-[empty]` 空态样式 |

> **【缺陷】这一整支基本是死代码**：`aria-select` 只有一个消费方，`field.tsx` 只被 `aria-select` 自己 import。**新实现必须二选一** —— 要么把表单字段全部收敛到 react-aria（能白拿字段级校验和 a11y），要么整支删掉。不要两套并存。

#### （三）纯手写的

| 文件 | 实质 | 变体 |
|---|---|---|
| `button.tsx` | 裸 `<button>` + `forwardRef` | `default \| destructive \| outline \| secondary \| ghost \| link`；尺寸 `default (h-10 px-4 py-2) \| sm (h-9 px-3) \| lg (h-11 px-8) \| icon (h-10 w-10)`。`default` 是**中性色**（`--color-btn-primary-bg`），**不是琥珀强调色** |
| `badge.tsx` | `<div>` | `default \| success \| warning \| destructive \| muted \| info` —— **注意没有 `outline`/`secondary`**，这是一套盈亏 / 状态词汇（`/10` 填充 + `/30` 边框），不是通用徽章 |
| `card.tsx` | 标准 shadcn 结构 | `Card / CardHeader / CardFooter / CardTitle / CardDescription / CardContent` |
| `input.tsx` | 标准 shadcn `<input>` | `h-10`、`text-base sm:text-sm`（挡 iOS 自动放大） |
| `textarea.tsx` | 标准 shadcn `<textarea>` | `min-h-[80px]` |
| `select.tsx` | **原生 `<select>`** 包在 `relative` 容器里 + 绝对定位的 `ChevronDown`；`className`/`style` 施加在**外层容器**上 | 无 |
| `switch.tsx` | 导出 **`ToggleSwitch`**：`<button role="switch" aria-checked>` 药丸，`h-5 w-9`，`checked` + `onChange: () => void`（无参） | 无 |
| `checkbox-02.tsx` | 导出 **`PremiumCheckbox`**：`sr-only` 原生 checkbox + framer-motion 的 `w-7 h-7` 自定义视觉（弹簧勾入） | 无 |
| `scroll-area.tsx` | **不是 Radix ScrollArea** —— 就是 `relative overflow-hidden` 外层套 `h-full w-full overflow-auto` 内层，没有滚动条样式、没有 viewport/thumb 分件 | 无 |
| `animated-tabs.tsx` | `AnimatedTabs({ tabs, value, onChange, layoutId })`，framer-motion `layoutId` 滑动药丸，靠追踪 `prevValue` 避免父级重排时误触发动画 | 弹簧 `{ bounce: 0.2, duration: 0.6 }` |
| `mobile-bottom-sheet.tsx` | `MobileBottomSheet` —— framer-motion 手写抽屉，**不是 Radix** | `sizing: 'auto' \| 'fixed'`，`height` 默认 `80vh` |
| `error-banner.tsx` | `ErrorBanner({ error: string \| StructuredError \| null })` —— 应用里的内联"警告条"（没有 `alert.tsx`）。识别 `err.kind === 'upstream' \| 'internal'`，渲染 i18n 提示列表；内含 `ErrorLink`：相对 / 同源 URL 走应用内跳转，跨源才 `target=_blank` | — |

#### （四）加载态组件族

`loader.tsx`（盲文点阵 `⠋⠙⠹…`）、`dot-loader.tsx`（7×7 点阵帧动画）、`lissajous-loading.tsx`（rAF 驱动的 SVG 李萨如曲线，聊天流式指示器）、`logo-loading.tsx`（品牌标 stroke-dash 描边）、`text-shimmer.tsx`、`pulse-dot.tsx`、`morph-loading.tsx`（**已废弃**）。

`loader.tsx` 里有一个值得照搬的设计：**同一节奏的所有 loader 共用一个模块级 `setInterval`**（模块级 `tickers` Map + `useSyncExternalStore`）。SSE 事件爆发时页面上可能同时有几十个 loader，各自开定时器既费性能又会各转各的相位。

> **【缺陷】7 个加载态组件里 3 个只有 1 处使用、1 个 0 处使用。** 新实现应收敛到 **2 个**：一个通用 spinner（`Loader`）+ 一个流式生成指示器，其余删掉。

#### （五）业务性较强但住在 `ui/` 的

`stepper-track.tsx`（agent 计划 / todo 轨道，状态 `pending \| in_progress \| completed \| stale`，亮暗两套调色板）、`token-usage-ring.tsx`（16px SVG 环 + Popover，阈值 >0.85 loss / >0.60 warning / 否则 success）、`mobile-fab-chat.tsx`、`morphing-page-dots.tsx`、`ContextOverflowPill.tsx`。

**`chat-input.*` 是 `ui/` 里最大的一块**（13 个文件、约 2400 行）：`chat-input.tsx` + `.css` + `.helpers.tsx` / `.modelMenu.tsx` / `.models.ts` / `.parts.tsx` / `.toolbar.tsx` / `.types.ts`，加 5 个 hook（`useFileAttachments` / `useMentions` / `useSlashCommands` / `useToolbarFold` / `useVoiceInput`）。默认导出 `React.memo(forwardRef<ChatInputHandle, ChatInputProps>(...))`，命令式句柄：

```ts
interface ChatInputHandle {
  getModelOptions(): ModelOptions;
  addContext(ctx: ContextItem): void;
  addWidgetSnapshot(snapshot: WidgetContextSnapshot): void;
  setValue(text: string): void;
  setModel(model: string): void;
}
```

**这个拆法值得学**：一个复杂组件拆成"主壳 + 展示分件 + 每种交互一个 hook"，而不是一个 2000 行的巨型文件。但它**不该住在 `ui/`** —— 它不是设计系统原语，是业务组件（见 §5.6）。

#### （六）刻意不存在的原语

`separator` / `avatar` / `progress` / `slider` / `calendar` / `date-picker` / `command` / `combobox` / `accordion` / `tabs`(Radix) / `table` / `alert` / `sheet` / `drawer` / `skeleton` / `toggle` / `radio-group` / `breadcrumb` / `pagination` —— **一个都没有**，import 站点为 0。

替代关系：tabs → `animated-tabs`；sheet/drawer → `mobile-bottom-sheet` + `DialogContent variant="default"`；alert → `error-banner`；table → 9 个页面组件里各写各的裸 `<table>`；skeleton → 散落的 `animate-pulse` div。

> **【缺陷】`table` 和 `skeleton` 必须补。** 裸 `<table>` 重复 9 次、`animate-pulse` 骨架屏重复约 15 次，是这个代码库里最明显的两块重复。

### 5.3 Toast 方案

**Radix Toast + shadcn 的 `use-toast` reducer 模式，不是 sonner。** 三个文件：

- `use-toast.ts` —— 模块级 `memoryState` + `listeners` 数组 + reducer（`ADD_TOAST` / `UPDATE_TOAST` / `DISMISS_TOAST` / `REMOVE_TOAST`）。`TOAST_LIMIT = 3`，`TOAST_REMOVE_DELAY = 5000`。同时导出 `useToast()` 和**可在 React 之外调用的 `toast()`**（hook 和 mutation 回调里大量用）。
- `toast.tsx` —— 原语 + `toastVariants`（`default` / `destructive`）。
- `toaster.tsx` —— `<Toaster />`，`ToastProvider duration={3000}`，viewport **移动端在顶部、桌面端在右下**（`fixed top-0 … sm:bottom-0 sm:right-0 md:max-w-[420px]`）。

**挂载点在 `main.tsx`，是 `<App />` 的兄弟节点**（不在 `App.tsx` 里）—— 这样路由切换、认证态变化都不会把 toast 队列连根拔掉。

```ts
import { toast } from '@/components/ui/use-toast';
toast({ title, description, variant: 'destructive' });  // 返回 { id, dismiss, update }
```

> **【缺陷】`use-toast.ts` 的 `memoryState` 是活得比 React 长的模块级单例，但没有注册进登出重置表**（`lib/authResets.ts`）。切账号时上一个用户的 toast 会残留。新实现必须把它注册进去（§3.6 的铁律）。
>
> **【缺陷】toast viewport 是 `z-[100]`，而弹窗层是 `z-[1030]`** —— 弹窗开着时触发的 toast 会渲染在遮罩后面，用户完全看不见。而弹窗里的操作恰恰是最需要 toast 反馈的场景。**新实现里 toast 必须是整个 z 轴的最顶层。**

### 5.4 弹层方案

#### z-index 阶梯（原实现全是硬编码的 Tailwind 任意值，没有令牌）

| 层 | z | 出处 |
|---|---|---|
| Toast viewport | `z-[100]` | `toast.tsx` |
| MobileBottomSheet 遮罩 | `z-[1010]` | `mobile-bottom-sheet.tsx` |
| MobileBottomSheet 面板 / 图片灯箱 | `z-[1020]` | — |
| **Dialog 遮罩 + 内容、dropdown、context-menu、popover、tooltip** | `z-[1030]` | 所有 Radix 封装 |
| hover-card、aria-popover | `z-50`（**离群值**） | `hover-card.tsx` / `aria-popover.tsx` |

> **【缺陷】必须重做**。新实现按 §7.3 定义 `--z-*` 令牌，顺序为：`base < sticky < dropdown < overlay < modal < popover < toast`，并禁止裸数字。当前 `hover-card` 的 `z-50` 会被底部抽屉（1010/1020）盖住，是明显的 bug。

#### Portal 策略

所有 Radix 浮层走各自原语的 `Portal` → `document.body`。唯一的逃生舱是 `DropdownMenuContent` 的 `container` prop：移动端把模型选择菜单 portal 进聊天容器而不是 body（`container={isMobile ? chatContainerRef.current : undefined}`），因为移动端 body 上有 `position: fixed`（治 iOS 键盘），portal 到 body 会定位错乱。

#### 「弹窗还是抽屉」的判断在 `DialogContent` 内部

**没有独立的 `Sheet` / `Drawer` 组件**，`DialogContent` 运行时分叉：

```tsx
const isMobile = useIsMobile();                       // matchMedia('(max-width: 767px)')
const swipeEnabled = isMobile && variant === 'default';
```

- **走抽屉**：`fixed left-0 bottom-0 … flex flex-col … rounded-t-3xl max-h-[90dvh] … slide-in-from-bottom`。**两层结构**：外层负责定位 + 拖拽 `translate`，内层 `flex-1 min-h-0 overflow-y-auto` 负责滚动和触摸。顶部加拖拽把手条，另加一个**隐藏的 `DialogPrimitive.Close`** —— 滑动手势就是靠 `closeRef.current?.click()` 触发 `onOpenChange(false)`（不能直接调，Radix 的关闭动画和焦点归还都挂在这条路径上）。
- **走居中**：`fixed left-[50%] top-[50%] … translate-x-[-50%] translate-y-[-50%] … max-h-[85vh] overflow-y-auto` + 右上角可见的 `X`。
- `variant="centered"` 强制移动端也居中（用于宽内容，例如图表）。

两个必须照搬的细节：

1. **用 state 承载容器节点**（`useState` + 合并回调 ref），不是普通 ref —— Radix 在开关周期之间会重挂 portal 内容，普通 ref 拿不到重挂后的节点，拖拽订阅会断。
2. **拖拽位移用 CSS `translate` 属性，不用 `transform`** —— `data-[state]` 的进出场动画类占用了 `transform`，两者会打架。

`DialogContent` 还统一设 `aria-describedby={undefined}` 压掉 Radix 缺 Description 的告警。

#### 焦点陷阱 / ESC / 点外关闭

全部用 Radix 默认行为：没有任何 `onEscapeKeyDown` 覆写、没有 `FocusScope` 定制、没有 `modal={false}` 用在 Dialog 上。

唯一的点外关闭覆写在 MarketView 的详情弹窗：payload 是 `preview` 时 `preventDefault()` 掉 `onInteractOutside` —— 因为预览器渲染在 iframe 里，iframe 内的点击会冒泡成"点了外面"。

`modal={false}` 只用在 **DropdownMenu**（9 处），目的是菜单开着时页面仍可滚动、不上 body scroll lock。

#### 确认弹窗与自定义包装

**没有通用的 `Modal` 组件**，而是约 28 个各自组合 `ui/dialog` 的具名弹窗。

> **【缺陷】五个各写各的确认弹窗**：`ConfirmDialog`（还住在 `pages/Dashboard/components/`）、`ConfirmDeleteDialog`、`DeleteConfirmModal`、`AlwaysOnConfirmDialog`、`ArchiveThreadConfirmDialog`。**新实现必须只有一个** `components/ui/confirm-dialog.tsx`：
> ```ts
> interface ConfirmDialogProps {
>   open: boolean; onOpenChange(open: boolean): void;
>   title: string; message: ReactNode;
>   confirmLabel?: string; cancelLabel?: string;
>   destructive?: boolean;
>   onConfirm(): void | Promise<void>;   // await 完再关，期间按钮 loading
> }
> ```

也有绕过封装的合理场景：聊天里的图表标注弹窗直接组合 `DialogPortal` + `DialogOverlay` + 裸 `DialogPrimitive.Content`，为的是拿到 75vw/80vh，而 `DialogContent` 的 `max-w-lg` 会跟它打架。**这说明 `DialogContent` 应该开一个 `size` 维度**（`sm | md | lg | full`），而不是逼调用方拆封装。

#### 手势层

`hooks/useSwipeToDismiss.ts` → `{ contentRef, handleRef, dragY }`。用原生滚动，**只在 `scrollTop === 0` 且向下拉时接管触摸**（或触摸起点就在把手上）。关闭阈值：**速度 > 300 px/s 或位移 > 120 px**，否则弹回。`MobileBottomSheet` 和 `DialogContent` 共用。

> **【缺陷】`MobileBottomSheet` 没有焦点陷阱、没有 ESC 处理、没有 `role="dialog"`**，却在 7 个地方当移动端模态用，跟可访问的 `DialogContent` 抽屉路径并存。**新实现必须删掉它，统一走 `DialogContent variant="default"`。**

### 5.5 表单方案

**没有 `react-hook-form`，没有 zod resolver，没有 `Form`/`FormField`/`FormItem`/`FormMessage`。全是受控 `useState`。**

典型形态：一个 `FormState` 对象放局部 state，字段类型定义在 `utils/templates` 之类的地方，提交时过一层 `formStateToPayload(...)` 再交给 React Query mutation。"字段包装"只有一个 `const labelClass = 'form-label'` 和一个共享的 `inputStyle` 对象。

**校验错误几乎全靠 toast** —— mutation 的 `onError` 里 `toast({ variant: 'destructive', ... })`，加上服务端 / 流式错误用 `ErrorBanner` 内联展示。**没有任何字段级错误渲染机制。**

> **【缺陷】表单方案必须重做。** 全站表单不多（约 16 处 `onSubmit`），但"校验失败只弹 toast、不指出是哪个字段错"是很差的体验，且完全没有 `aria-invalid` / `aria-describedby` 关联。新实现二选一：
> - **推荐**：`react-hook-form` + `zodResolver` + 一套 `Field` / `FieldLabel` / `FieldError` 包装，错误就地渲染并正确接 aria 属性；
> - 或者把已有的 react-aria `field.tsx` + `JollySelect` 路线做完（react-aria 自带 `isInvalid` / `errorMessage` 的 aria 接线）。
>
> 无论选哪条，**都要真正用起来，不能像现在这样留一支死代码**。

**Zod 的用武之地是另一件事**（见 §0.6 铁律 4 与 §6.2）：只在"持久化 / 用户输入"边界，只用 `safeParse` + 逐字段 `.catch()`，**从不 `.parse()`、从不 throw**。原实现只有 5 个文件用 zod：dashboard widget 配置 schema 与其类型、onboarding 偏好 schema、MCP 配置 schema、UUID 校验。

### 5.6 图表组件

#### lightweight-charts（K 线）：**没有共享封装**

原实现里 `createChart()` 被三个大组件各自命令式地调用，生命周期代码重复三份：

1. **`pages/MarketView/components/MarketChart.tsx`**（约 2400 行）—— 主实现。`forwardRef` + `useImperativeHandle`：
   ```ts
   export interface MarketChartHandle {
     captureChart(): Promise<Blob | null>;          // 走 html2canvas
     captureChartAsDataUrl(): Promise<string | null>;
     getChartMetadata(): Record<string, unknown> | null;
   }
   ```
   承载均线 / RSI / 成交量分栏、盘前盘后底色、WebSocket 实时 tick、向左滚动加载历史、标注、区间选择。
2. **`pages/Dashboard/widgets/definitions/ChartWidget.tsx`** —— dashboard widget 版，懒注册（见 §6.6）。它**直接跨页 import** MarketView 的常量和辅助模块。
3. **`pages/ChatAgent/components/charts/MarketDataCharts.tsx`**（约 1500 行）—— 对话流里的图表，**同一个文件里混用 lightweight-charts 和 recharts**。

**真正的抽象在共享模块层，不在组件层：**

- `lib/bars/`（barrel）—— `fetchStockData`、`useLiveBars`、`foldMinuteBar`、`applyQuoteToDailyBar`、`deriveMarketSession`、`formatPrice`、`useCurrencyDisplay`、`timezoneForSymbol`、`rangePresets`、`marketProtocol.ts`。
- `lib/quotes/`（barrel）—— `useQuote` / `useQuotes`。
- `pages/MarketView/utils/chartConstants.ts` —— `getChartTheme(theme)`、`INTERVALS`、`MA_CONFIGS`、`DEFAULT_ENABLED_MA = [20, 50]`、`RSI_PERIODS = [7,14,21]`、`SCROLL_CHUNK_DAYS`、`SCROLL_LOAD_THRESHOLD = 20`、`RANGE_CHANGE_DEBOUNCE_MS = 300`、`TARGET_BAR_SPACING`。
- `lib/themeTokens.ts` —— 令牌到颜色字符串的桥（见 §7.3）。
- **lightweight-charts v4 的 series primitive 插件**：`extendedHoursBg.ts`（`ExtendedHoursBgPrimitive`）、`selectionPrimitive.ts`、`agentAnnotationsPrimitive.ts`、`annotationGeometry.ts`。
- 图表相关 hook：`useChartAnnotations` / `useChartOverlays` / `useAgentAnnotations` / `useChartAnnotationSync` / `useStockBars` / `useMarketDataWS`；外部 store：`chartAnnotationStore` / `chartSelectionStore`（都建在 `lib/valueStore.ts` 的 `createValueStore` 上）。
- **可复用复合体**：`MarketChartSurface.tsx` —— 自带 `MarketDataWSProvider` 的完整图表面板（StockHeader + MarketChart + CompanyOverviewPanel），设计目标就是"能扔到 MarketView 之外的任何地方"。聊天里的标注弹窗用的就是它。

> **【缺陷】新实现应该把这层做成真正的封装组件。** 目标形态：
> ```
> components/charts/
>   PriceChart.tsx        ← 唯一持有 createChart 生命周期的组件
>                            props: bars / interval / chartType / overlays / primitives / onRangeChange
>   usePriceChart.ts      ← 图表实例 + ResizeObserver + 主题令牌订阅，一处实现
> ```
> 三个调用点各传各的 props，而不是各写一遍 `createChart` / `remove` / `applyOptions` / ResizeObserver。缩放监听尤其不该重复 —— 现在每个图表各 `new ResizeObserver` 一次。

#### recharts（统计图）：也没有封装

只有 3 个 import 站点。用到 `BarChart / Bar / XAxis / YAxis / CartesianGrid / Tooltip / ResponsiveContainer / PieChart / Pie / Cell / Legend / LabelList / LineChart / Line / ReferenceLine`。

主题靠约定：**recharts 是 SVG，直接吃 `var(--color-*)` 字符串**（`GRID_COLOR = 'var(--color-border-default)'`、`PIE_COLORS`、按 'Strong Buy'/'Buy'/… 索引的 `ANALYST_COLORS`）；同一个页面上的 lightweight-charts 则必须走 `createThemeResolver`（canvas 读不了 CSS 变量）。**这条差异要在新实现里写进注释，否则很容易搞混。**

> **【缺陷】tooltip 每处重写一遍。** 每个调用点都手写一个 render-prop 的 `<div className="rounded-lg px-2.5 py-1.5 text-xs shadow-lg border" style={{ backgroundColor: 'var(--color-bg-card)', … }}>`。新实现要有 `components/charts/ChartTooltip.tsx` + `ChartLegend.tsx` 两个共享件。

### 5.7 业务组件的组织与复用边界

原实现是**按页分组，不是按 feature 分组**：

```
src/components/          ← 只放应用外壳 + 真正全局的（ui/ 之外仅 19 个文件）
  ui/                    ← 设计系统原语
  Main/Main.tsx          ← 路由表，每路由 React.lazy + preloadRouteChunk()
  Sidebar/               ← AppSidebar / AccountMenu / sidebarWidth.ts / useChatRoute.ts
  BottomTabBar/          ← 移动端底部导航
  PageLoading/           ← 品牌化的 Suspense fallback
  nav/                   ← navItems.ts / useNavActive.ts（侧栏与底部 tab 共用）
  model/                 ← ModelSelector / ProviderCard / ProviderManager / ApiKeyInput /
                             ModelTierConfig（设置页与 Setup 向导共用）

src/pages/<Page>/        ← 12 个页面：Automations / ChatAgent / Dashboard / Detail / Legal /
                             Login / MarketView / OAuth / Onboarding / Settings / Setup / SharedChat
  components/  hooks/  utils/  contexts/  stores/  constants/  __tests__/

src/features/            ← 只有一个目录，且是"刻意未接入"的演示组件
```

**判据（新实现照此执行）**：

| 放哪 | 条件 |
|---|---|
| `components/ui/` | 无业务语义的设计系统原语。**不能 import `pages/` 里的任何东西** |
| `components/` | 应用外壳（导航、加载、错误）**或**被 ≥2 个页面组用到的业务组件 |
| `pages/<Page>/components/` | 只服务这一个页面组 |
| `lib/` | 纯逻辑，不含 JSX |

**`features/` 在原实现里等于不存在**（唯一的 `analyst-standalone/AnalystCard.tsx` 带着"未接入路由，勿在线上页面 import"的注释）。**新实现要么认真建立 feature 层并定义清楚它和 `pages/` 的关系，要么干脆不要这个目录** —— 留一个空壳约定只会让人困惑。

> **【缺陷】跨页深 import 取代了"提升到 `components/`"。** 实例：`ChartWidget.tsx`（Dashboard）import `@/pages/MarketView/utils/chartConstants` 和 `@/pages/MarketView/contexts/MarketDataWSContext`；`MarketChart.tsx`（MarketView）反过来 import `@/pages/Dashboard/widgets/framework/TradingViewAttribution`。**Dashboard 和 MarketView 互相依赖，成了环。**
>
> 更糟的是 **`components/ui/` 向上依赖 `pages/`**：`ui/error-banner.tsx` → `@/pages/ChatAgent/utils/parseErrorMessage`；`ui/chat-input.types.ts` → `@/pages/Dashboard/widgets/framework/contextSnapshot`。这把分层彻底倒置了。
>
> **新实现必须用 ESLint 的 `import/no-restricted-paths`（或 dependency-cruiser）把方向锁死**：`ui/` 不许 import `pages/` 和 `components/` 的非 ui 部分；`pages/A` 不许 import `pages/B`——共享的东西一律提升到 `lib/` 或 `components/`。这条规则**必须进 CI 门禁**，靠文档约定已经被证明无效。

**Barrel 导出只有 6 个，且 `ui/` 一个都没有**（全部按显式路径 import，如 `@/components/ui/button`）。真正的 barrel：`lib/bars/index.ts`、`lib/quotes/index.ts`、`types/index.ts`、`pages/Onboarding/index.ts` 及其 `registry/index.ts`；外加一个**副作用注册 barrel** `pages/Dashboard/widgets/index.ts`（30 行裸 `import './definitions/…'` 后再 re-export 注册表函数）。**这个尺度是对的** —— 给 `ui/` 加 barrel 只会破坏 tree-shaking 和按需加载。

**命名约定**：`components/` 下用 `Dir/Dir.tsx` 的 PascalCase；`components/ui/` 用 kebab-case 文件名（shadcn 约定）；页面组件 PascalCase，同名 `.css` 就近放；测试一律在同级 `__tests__/`。

### 5.8 横切组件

**`PageLoading`** —— 品牌化的"研究终端预热"效果：用确定性的种子 LCG 生成 120 行 × 560 字符的假行情墙（`AAPL 227.15 ▲1.24%` …），一道余烬色扫描带从中扫过。**全部纯 CSS**，这样它留在主包里、路由 chunk 还在下载时就能立刻画出来。props：`variant?: 'screen' | 'pane'`（`screen` 铺满视口带页面底色，用于认证门禁和顶层 Suspense；`pane` 透明填满父容器，用于外壳内的 Suspense）。`role="status"`，行情墙 `aria-hidden`。

**ErrorBoundary** —— 原实现只有两个局部的：文档查看器的 `DocumentErrorBoundary`，和 dashboard 里的 `WidgetErrorBoundary`（每块砖一个，按 `widgetType` 标识，渲染 i18n 内联提示）。

> **【缺陷】没有应用级 ErrorBoundary。** 任何一处渲染抛错 = 整页白屏，用户只能刷新。**新实现必须有三层**：应用根一层（渲染"出错了 + 重载"页并上报）、每路由一层（只崩一个页面，外壳和导航还在）、以及已有的 widget 级一层。

**EmptyState** —— 无组件，每处内联。新实现应有一个 `EmptyState({ icon, title, description, action })`。

**Skeleton** —— 无组件，三套散落的写法（`.map` 里重复 `<div className="h-14 rounded-lg animate-pulse" style={{ backgroundColor: 'var(--color-bg-card)' }} />`、结构化占位树、以及一处纯 CSS 的）。新实现补 `components/ui/skeleton.tsx`。

**虚拟列表** —— **完全没有**。没装 `react-window` / `@tanstack/react-virtual`，长列表（线程画廊、消息列表、文件面板）全量渲染。见 §8。

**无限滚动** —— 全站没有 `useInfiniteQuery`。IntersectionObserver 有 6 处，但都用于别的目的（聊天输入是否在视口、小地图、新闻 widget 的曝光）。图表的历史加载走的是 lightweight-charts 的**可视区间监听**（离左边缘 20 根 K 线时触发，300ms 防抖，按周期决定拉取跨度），并分两阶段：先补可视区间，再静默回填。

**共享 hook**（`src/hooks/`）：`useIsMobile`（`useSyncExternalStore` 包 `matchMedia('(max-width: 767px)')`，另导出一个可在渲染外调用的同步 `getIsMobileSnapshot()`）、`useSwipeToDismiss`、`useSwipeBack`、`useOnClickOutside`、`useNarrowContainer`、`useNetworkStatus`、`useDebouncedSave`、`useStableArray`、`useStableHandler`、`useTitleFade`、`useSetupGate`，以及一批 React Query 数据 hook。

### 5.9 变体方案：统一用 cva

原实现装了 `class-variance-authority`，文档也声称 `ui/` 用它，**但实际只有 2 个文件 import 了 cva**（`toast.tsx` 和 `field.tsx`）。`button.tsx` / `badge.tsx` / `dropdown-menu.tsx` / `context-menu.tsx` 全是裸的 `Record<Variant, string>` 查表。

> **【缺陷】必须统一。** 用 `Record` 的直接后果是：
> - 拿不到 `VariantProps<typeof buttonVariants>`，props 类型只能手写、容易和实现漂移；
> - `buttonVariants({ variant: 'ghost', size: 'sm' })` 这个 shadcn 最常用的惯用法用不了 —— 别的组件想"长得像个 ghost 按钮"只能复制类名字符串。
>
> 新实现：**所有带变体的组件一律 cva**，props 一律 `VariantProps<typeof xVariants>` 推导。
>
> **【缺陷】`Button` 没有 `asChild` / Slot**（`@radix-ui/react-slot` 根本没装），所以想让 `<Link>` 长成按钮只能复制一遍类名。新实现要装 `react-slot` 并支持 `asChild`。
>
> **【缺陷】`checkbox-02.tsx` 硬编码 `bg-white` / `bg-black` / `border-gray-700`** —— 是 `ui/` 里唯一无视主题令牌体系的组件（而这个代码库有专门的测试在强制令牌纪律，见 §7.3）。

---


## 6. Dashboard widget 系统

Dashboard 是整个前端里唯一一个**用户可自由编排**的页面，也是最值得单独立规格的子系统。它有两套并存的形态，由 `DashboardRouter` 分流。

### 6.0 两种形态：Classic vs Custom

| | **Classic**（经典） | **Custom**（自定义） |
|---|---|---|
| 布局 | 写死的 JSX：指数卡 / AI 日报卡 / 新闻流卡 / 持仓自选卡 / 财报日历卡 / 聊天输入卡 | 用户拖出来的 12 列网格，元素来自 widget 注册表 |
| 配置来源 | 无（组件内 state + 少量 localStorage） | 服务端 `user_preferences.other_preference.dashboard` |
| 移动端 | **唯一形态** | **永不渲染** |

**`DashboardRouter` 分流规则（新实现必须一致）**

1. `useIsMobile()`（媒体查询 `(max-width: 767px)`）为真 → **无条件 Classic**，且顶部的形态切换按钮在 `md` 断点以下隐藏。移动端不做响应式网格，直接不给 Custom。
2. 桌面端读 `prefs.other_preference.dashboard.mode`；缺失 / 旧版 / 结构非法 → 一律回落 `'classic'`（零回归默认值）。
3. 两个分支都先渲染 `<NetworkBanner />`。
4. `prefs` 仍在 `isLoading` 时**拒绝切换**（按钮 disabled）。
5. 切换回调 `onModeChange` 必须**重新读 query cache**（`queryClient.getQueryData(queryKeys.user.preferences())`）而不是信任 render 期的快照 —— 否则跨 tab 编辑或尚在 debounce 中的改动会被覆盖。
6. **第一次翻到 custom 且 widget 列表为空 → 自动播种 `morning-brief` 预设**（不要给用户一块空白画布）。

### 6.1 widget 类型清单（30 个）

分五个 category：`'markets' | 'intel' | 'personal' | 'agent' | 'workspace'`。

尺寸单位说明：`w` 是 12 列网格的列数，`h` 是行数，**一行只有 8px**（见 §6.3），所以 `h` 数值普遍很大。

#### 原生 widget（13 个）

| type id | category | 用途 | 默认 w×h | 最小 w×h | 特性 |
|---|---|---|---|---|---|
| `markets.overview` | markets | 大盘指数总览（指数涨跌 + 迷你走势） | 12×11 | 3×11 | 单例；`maxSize` 也是 12×11（高度锁死） |
| `markets.miniChartGrid` | markets | 一组标的的迷你走势网格 | 12×16 | 6×10 | 有 `initConfig`（按自选播种）、有设置面板 |
| `chart.symbol` | markets | 单标的 K 线图 | 6×22 | 3×15 | **懒加载**组件 + 懒加载设置面板 |
| `insight.brief` | intel | AI 每日简报 | 8×18 | 4×15 | 单例；`fitToContent`；max 12×44 |
| `news.feed` | intel | 新闻流 | 8×29 | 4×18 | 无设置弹窗，用**内联** source tab 切换 |
| `calendar.earnings` | intel | 财报日历 | 4×26 | 3×15 | — |
| `watchlist.list` | personal | 自选列表 | 4×26 | 3×15 | — |
| `portfolio.holdings` | personal | 持仓列表 | 4×26 | 3×15 | — |
| `personal.portfolioWatchlist` | personal | 持仓 + 自选合并的 tab 卡 | 4×30 | 3×18 | — |
| `automations.list` | personal | 自动化任务列表 | 4×22 | 3×14 | — |
| `agent.conversation` | agent | 嵌在 dashboard 里的对话入口 | 12×18 | 8×12 | 单例；`fitToContent`；max 12×44 |
| `workspace.picker` | workspace | 工作区选择器 | 6×22 | 3×15 | — |
| `threads.recent` | workspace | 最近会话 | 6×22 | 3×15 | — |

#### TradingView 嵌入 widget（17 个）

全部 `category: 'markets'`、`source: 'tradingview'`、**全部带设置弹窗**。

`tv.ticker-tape`（12×6，min 6×3，`fitToContent`，有 `initConfig`）、`tv.stock-heatmap`（12×22）、`tv.crypto-heatmap`（12×20）、`tv.forex-heatmap`（12×18）、`tv.etf-heatmap`（12×20）、`tv.economic-events`（6×24）、`tv.economic-map`（12×18，唯一走 Web Component `<tv-economic-map>` 的）、`tv.technicals`（6×22，min 4×22）、`tv.movers`（6×22）、`tv.symbol-spotlight`（6×22）、`tv.single-ticker`（3×4，min 2×3，max 6×6）、`tv.symbol-info`（6×8）、`tv.company-profile`（6×18）、`tv.company-financials`（6×24）、`tv.screener`（12×24）、`tv.crypto-screener`（12×24）、`tv.top-stories`（6×22）。

TV widget 的实现方式：**注入 `https://s3.tradingview.com/external-embedding/` 的 `<script>`**，数据完全由 TV 的 iframe 自己拿，不经过 React Query。加载器按 `scriptSrc` 去重；config / 主题 / 语言变化时重建 iframe，且**必须 debounce**（否则 17 块砖会因为一次主题切换或一次键入同时重建）。

> **【缺陷】跨源 iframe 的代价**：TV widget 既读不到像素也读不到 DOM，因此"把这块内容附到聊天上下文"的功能对它们一律禁用（要给出解释性 tooltip，不能只是灰掉）。新实现若能用自绘图表覆盖这些场景，应优先自绘。

### 6.2 配置 schema 与校验边界

**所有 Zod schema 集中在一个文件里**（`widgets/framework/configSchemas.ts`），**不要**散进 30 个 widget 定义文件 —— 集中的目的是让 schema 变更是一次有意识的动作，而不是埋在某个 widget 里。widget 定义只通过可选字段 `configSchema?: z.ZodType<C>` 引用它。

复用的原语：

```ts
const TV_SYMBOL_RE = /^[A-Z0-9._\-:!/^&]+$/i;
const tvSymbol     = (def: string) => z.string().min(1).regex(TV_SYMBOL_RE).catch(def);
const intInRange   = (def: number, min: number, max: number) =>
                       z.number().int().min(min).max(max).catch(def);
const looseString  = (def: string) => z.string().min(1).catch(def);
```

各 widget 的配置字段（字段名必须一致，它们已经躺在生产用户的偏好里）：

| schema | 字段 |
|---|---|
| `TickerTapeConfigSchema` | `symbols: string[]`（filter + dedupe 的 transform，不是逐元素 `.catch()`）、`displayMode: 'adaptive'\|'regular'\|'compact'` |
| `StockHeatmapConfigSchema` | `dataSource`（默认 `'SPX500'`）、`blockSize`（`'market_cap_basic'`）、`blockColor`（`'change'`） |
| `CryptoHeatmapConfigSchema` | `dataSource`（`'Crypto'`）、`blockSize`（`'market_cap_calc'`）、`blockColor`（`'24h_close_change|5'`） |
| `ETFHeatmapConfigSchema` | `dataSource`（`'AllUSEtf'`）、`blockSize`（`'aum'`）、`blockColor`（`'change'`）、`grouping`（`'asset_class'`） |
| `ForexHeatmapConfigSchema` | `currencies: string[]`，逐个匹配 `/^[A-Z]{3}$/`；**过滤后为空则整体回落**默认表 `['USD','EUR','GBP','JPY','CHF','CAD','AUD','NZD','CNY']` |
| `EconomicEventsConfigSchema` | `importanceFilter`（`'-1,0,1'`）、`countryFilter`（`'us,eu,jp,gb,cn'`） |
| `EconomicMapConfigSchema` | `region`（7 值枚举）、`metric`（`gdp\|ur\|gdg\|intr\|iryy`）、`hideLegend: boolean` |
| `TechnicalsConfigSchema` | `symbol`、`interval`（`'1D'`） |
| `MoversConfigSchema` | `exchange`（`'US'`）、`dataSource`（`'AllUSA'`） |
| `SymbolSpotlightConfigSchema` | `symbol`、`range`（`'12M'`） |
| `CompanyProfileConfigSchema` / `SingleTickerConfigSchema` / `SymbolInfoConfigSchema` | `symbol` |
| `CompanyFinancialsConfigSchema` | `symbol`、`displayMode`（`regular\|compact\|adaptive`） |
| `TopStoriesConfigSchema` | `feedMode`（`all_symbols\|market\|symbol`）、`market`（7 值）、`symbol`、`displayMode`（`regular\|compact`） |
| `StockScreenerConfigSchema` | `market`（`'america'`）、`defaultColumn`（`'overview'`）、`defaultScreen`（`'general'`） |
| `CryptoScreenerConfigSchema` | `defaultColumn`、`defaultScreen` |
| `ChartConfigSchema` | `symbol`（`'NVDA'`）、`interval` ∈ `['1min','5min','15min','30min','1hour','1day']`、`chartType` ∈ `['candle','area','line']` |
| `MiniChartGridConfigSchema` | `symbols: string[]`（不做正则；空数组时 widget 回落自选 / 蓝筹） |
| `PortfolioConfigSchema` | `valuesHidden?: boolean` |
| `AutomationsConfigSchema` | `limit?` int 1–100，默认 8 |
| `PortfolioWatchlistConfigSchema` | `defaultTab?: 'watchlist'\|'portfolio'`、`valuesHidden?: boolean` |
| `WorkspacePickerConfigSchema` | `limit?` 1–100，默认 12 |
| `RecentThreadsConfigSchema` | `workspaceId?`（`'all'\|'current'\|<UUID>`）、`limit?` 1–100 默认 15 |
| `EarningsConfigSchema` | `window?: '1w'\|'2w'\|'1m'`、`tickers?: 'all'\|'portfolio'` |
| `InsightBriefConfigSchema` | `variant?: 'latest'\|'personalized'` |
| `MarketsOverviewConfigSchema` | `indices?: string[]` |
| `NewsFeedConfigSchema` | `source?: 'top'\|'market'\|'portfolio'\|'watchlist'`、`limit?` 1–200 默认 50 |
| `WatchlistConfigSchema` / `ConversationConfigSchema` | `z.object({}).catch({})`（无配置，但保留占位以便将来加字段不动 migration） |

**校验边界只有一个：偏好「读取」路径。**

- `migrations.ts` 的 `sanitizeConfig()` 对每个实例调 `def.configSchema.safeParse(w.config)`。
- 逐字段 `.catch()` 修单个坏值；整体 parse 失败才整块回落 `def.defaultConfig`，并在 DEV 下 `console.warn`。
- **没有 schema 的 widget 原样放行**，不报错。
- **写入不校验** —— 设置弹窗的 patch 直接进偏好，schema 只在下次读取时重新归一化。这是有意的：设置 UI 本身就只产出合法值，写路径再校验一遍徒增复杂度和 throw 风险。
- 枚举元组要单独 `export ... as const`（如 `TICKER_TAPE_DISPLAY_MODES`），让设置下拉框跟 schema 共用同一个真源 —— Zod v4 在 `.catch()` 之后就取不到 `.options` 了。

> **【缺陷】隐式依赖**：`sanitizeConfig` 内部调 `getWidget(type)` 查注册表，而注册表靠副作用 import 填充。任何直接 import `migrations.ts` 的测试如果没同时 import `widgets/index`，sanitize 会**静默变成 no-op**（查不到定义 = 放行）。新实现应把注册表作为显式参数传进 sanitize，而不是靠模块副作用。

### 6.3 布局系统

基于 `react-grid-layout` 的 `ResponsiveGridLayout`。常量集中在 `gridConstants.ts`：

```ts
export const BREAKPOINTS_PX = { lg: 1024, md: 0 } as const;
export const COLS_PER_BP    = { lg: 12,   md: 12 } as const;
export const COLS = 12;
export const BREAKPOINT_KEYS = ['lg', 'md'] as const;
export const ROW_HEIGHT   = 8;   // px —— 故意做得很细，让高度接近连续
export const MARGIN_Y     = 16;
export const MARGIN_X     = 16;
export const FIT_PADDING_PX = 0;
```

**只有两个断点、且都是 12 列。** 断点存在的意义不是"手机上换成 1 列"（手机根本不进 Custom），而是让用户可以在宽屏 / 窄屏各存一套排布。

- 网格只在 `mounted && containerWidth > 0` 时渲染（`useContainerWidth()`），`containerPadding={[0, 0]}`。
- 拖拽 / 缩放**只在编辑模式开启**：
  ```tsx
  dragConfig={{ enabled: editMode, handle: '.widget-drag-handle',
                cancel: '.widget-drag-cancel, .react-resizable-handle' }}
  resizeConfig={{ enabled: editMode }}
  ```
  编辑模式下整个卡片框都是拖拽把手（`widget-drag-handle`），头部的操作按钮加 `widget-drag-cancel` 排除。
- 像素转行数：`pxToRows(px) = Math.max(1, Math.ceil((px + MARGIN_Y) / (ROW_HEIGHT + MARGIN_Y)))`。

**手势批处理（性能必备）**：RGL 在拖动过程中每帧都触发 `onLayoutChange`。必须把布局暂存在 ref 里（`pendingLayoutsRef`），拖拽 / 缩放开始时置 `isGesturingRef`，**只在 `onDragStop` / `onResizeStop` 提交一次**。`editMode === false` 时 `onLayoutChange` 直接 no-op。

**`fitToContent`（内容自适应高度）** —— `insight.brief`、`agent.conversation`、`tv.ticker-tape` 三个用。`WidgetFrame` 挂 `ResizeObserver` 量内层内容 + body padding + header 高度，回报 `onFitHeight`，网格把该实例的 `h === minH === maxH` 锁成量出来的行数（纵向缩放禁用，横向仍可拉）。

- **长高立即提交，变矮走 120ms 尾防抖** —— 否则内容抖一下就会来回跳。
- CSS 上要对 `.react-grid-item:has(.widget-frame--fit)` 去掉 height 过渡，不然测量和动画会打架。

**布局对账（`reconcile.ts`）**：每次读到偏好都要过一遍 `reconcileLayouts()`：

1. 丢弃没有对应 widget 实例的孤儿 layout 项；
2. 给缺 layout 的 widget 自动排到底部（兜底尺寸 4×3）；
3. 从注册表重新盖章 `minW/minH/maxW/maxH`，并按之做 `w`/`h` 的 clamp。

`placeAtBottom()` 把新 widget 放到**两个断点各自**的 `y = max(y + h)`。如果对账过程中真的 clamp 了什么，effect 要把修正后的布局**写回偏好**（不然每次进页面都重算一遍）。

**每块砖单独兜底**：每个 widget 外面包一层 `WidgetErrorBoundary`（class 组件）+ `<Suspense>`。**一块砖崩掉不能带走整个 dashboard。**

> **【缺陷】拖拽完全不可键盘操作**。`react-grid-layout` 只认指针事件，编辑模式下没有任何键盘等价路径（移动/缩放/换位）。新实现至少要为每块砖提供一组"上移/下移/变宽/变窄"的按钮或快捷键，作为拖拽的可访问替代。

### 6.4 配置持久化

**存在服务端，不在 localStorage。** 路径是 `user_preferences.other_preference.dashboard`（嵌一层是为了塞进后端偏好表固定的四列结构）。

| 操作 | 端点 |
|---|---|
| 读 | `GET /api/v1/users/me/preferences`（404 → `null`） |
| 写 | `PUT /api/v1/users/me/preferences` |
| 清 | `DELETE /api/v1/users/me/preferences` |

Query key：`queryKeys.user.preferences()` = `['user', 'preferences']`。`usePreferences` 的 `staleTime` **按能力分叉**：支持 `BroadcastChannel` 的浏览器用 60s（跨 tab 有推送，不需要频繁轮），不支持的用 0（退化成靠 focus refetch）。

**唯一写入者：`useDashboardPrefsWriter()`。** 所有 dashboard 偏好写入必须从这里走，它保证三件事：

1. **最小载荷** —— 只发 `{ other_preference: { dashboard: next } }`。后端对 `other_preference` 做顶层 key 的浅合并（JSONB `||`），所以同级的 theme / locale / onboarding 不需要从可能已经过期的本地缓存里回放一遍就能存活。
2. **跨 tab 广播** —— 频道名 `'dashboard-prefs'`，写成功后 post `{ type: 'updated' }`。
3. **冷缓存拒写** —— 偏好缓存是冷的、且调用方没给 `fallbackOther` 哨兵时，直接返回 `false` 并跳过写入。**这条是数据安全红线**：服务端是整块替换 `dashboard` 键，用 `{}` 拼出来的写会不可逆地抹掉用户的整套布局。

**写入时机**：`DEBOUNCE_MS = 800`。`update(patch, { immediate: true })` 绕过防抖（`setMode` 和 `applyPreset` 用）。卸载时清定时器。DEV 下序列化超过 **20,000 字节**要告警（偏好表不是拿来存大对象的）。

**冲突处理**：

- `ownWriteInFlightRef`：抑制本 tab 自己那次写引发的"服务端 → 本地"回灌。
- 收到 BroadcastChannel 消息时**只有在没有排队的 debounce 定时器、也没有 pending mutation 时**才立刻 invalidate；否则把 invalidate 推迟到当前编辑落定后再跑（`replayPendingRef` / `runReplay`）。**不能让别的 tab 的更新打断用户正在拖的这一下。**
- `isMutatingRef` 镜像 `isPending`，让 channel 回调读到最新状态而不必重建 channel（重建会丢消息）。
- 写失败：清守卫 + 弹 destructive toast（文案大意"没能保存 dashboard，最近一次改动没同步，已恢复到上次保存的布局"），然后交给 refetch 对账。
- `isLoading` 期间 `update()` 直接 early return。

**版本与迁移**：`DASHBOARD_PREFS_VERSION = 1`。读取边界统一走 `migrateDashboardPrefs(raw)`，顺序固定：

```ts
const TYPE_RENAMES: Record<string, string> = { 'agent.input': 'agent.conversation' };
```

1. 过滤结构非法的实例 —— `isValidWidgetInstance` 要求 `id` 是 string、`type` 是 string、`config` 是非 null 对象；
2. 按 `TYPE_RENAMES` 改名；
3. `sanitizeConfig`（Zod）。

layouts 逐断点过滤，值不是真数组的断点整个丢掉。`mode` 除非严格等于 `'custom'` 否则一律强制 `'classic'`。整体畸形 → 返回 `null` → 上层用 `emptyPrefs()`。

`history` 字段：最多 3 份 `{ widgets, layouts }` 快照，**只有 `applyPreset` 会往里 push**。

> **【缺陷】`history` 被持久化、被迁移逻辑小心保留，但没有任何 UI 读它 —— 撤销功能根本不存在。** 新实现要么把撤销做出来（"应用预设"是唯一会一次性抹掉全部排布的操作，最需要撤销），要么把这个字段删干净，别留半成品占着偏好体积。

**Dashboard 里其他用 localStorage 的地方**（都不是布局）：个性化提醒的 snooze 时间戳、最后使用的自选表 id、券商连接凭据、以及 Classic 专用的 `portfolio_active_tab` / `portfolio_values_hidden`。后两个在 Custom 形态里被 `personal.portfolioWatchlist` 的服务端字段 `defaultTab` / `valuesHidden` 取代 —— 也就是同一个偏好在两种形态下存两个地方。

> **【缺陷】券商凭据写进 localStorage**（ConnectBrokerDialog 的 IBKR Flex token）。任何 XSS 都能捞走。新实现必须让它只走后端，前端不落盘。

### 6.5 widget 数据获取：共享 + 自取的混合

**不是"每块砖各自拉"，也不是"全部集中拉"，而是明确分两类。**

**（一）共享数据 provider** —— `DashboardDataProvider` 包住整个 Custom dashboard（和 `MarketDataWSProvider` 并列），内部只跑一次这些 hook：

```
useDashboardData()   → indices, newsItems, curatedItems, marketStatus
useWatchlistData()
usePortfolioData()
useTickerNews(portfolio.rows, 'portfolio', 'tickertick')
useTickerNews(watchlist.rows,  'watchlist', 'tickertick')
```

外加弹层状态（`openNews` / `openInsight` / `deleteConfirm`）与增删回调。**context value 必须 `useMemo`**，否则 provider 自身的任何 re-render 都会把所有消费者一起刷掉。

消费共享 context 的：`markets.overview`、`news.feed`、`portfolio.holdings`、`watchlist.list`、`personal.portfolioWatchlist`、`insight.brief`（只用弹层）、`markets.miniChartGrid`（用自选）、`tv.ticker-tape`（用自选 + 持仓来播种符号）。

判据很简单：**多块砖会用到同一份数据 → 进 provider；一块砖专属 → 自己查。**

**（二）自取数据的 widget**

| widget | query key | 缓存策略 |
|---|---|---|
| `calendar.earnings` | `['earnings-calendar', todayStr, toStr]` | staleTime 5min |
| `markets.miniChartGrid` | `['mini-chart-grid', symbols.join(',')]` | staleTime 60s，`refetchInterval: 120_000`，`refetchIntervalInBackground: false`；另叠 `useQuotes(symbols, { staleTime: 30_000, refetchInterval: 60_000 })` |
| `threads.recent` / `agent.conversation` | `queryKeys.threads.*` | staleTime 30s；另用 `useWorkspaces({ limit: 100 })` |
| `workspace.picker` | `useWorkspaces({ limit: 100 })` | — |
| `automations.list` | `useAutomations()` | — |

**行情走统一的 quote 层**：`queryKeys.quote.detail(symbol)` = `['quote', SYMBOL]`（符号大写归一）。同一个指数在 dashboard 和 MarketView 共用同一条缓存记录 —— 这是 key 工厂必须唯一真源的直接理由。

**轮询节奏**（都设 `refetchIntervalInBackground: false`，标签页在后台一律停轮）：

- 市场状态 `['dashboard','marketStatus']` —— 60s 轮，staleTime 30s；
- 指数 —— **自适应：开盘 30s / 收盘 60s**，`useQuotes(INDEX_SYMBOLS, { isIndex: true, staleTime: 10_000, refetchInterval: indexRefetch })`；
- 指数迷你走势 `['dashboard','indexSparklines', INDEX_SYMBOLS]` —— 同一套自适应节奏；
- 新闻 `['dashboard','news']` —— 固定间隔；
- 精选无限流 `['dashboard','curatedNews']` —— **只在 `pages.length <= 1` 时轮**，翻到第 2 页之后置 `false`（第 2 页起绕过服务端缓存，轮询会打穿后端）。

TradingView 那 17 块砖不经过 React Query，数据在 TV 自己的 iframe 里。

### 6.6 注册表与懒加载

**`WidgetRegistry`** 刻意做得极小：一个模块级 `Map<string, WidgetDefinition<unknown>>`，加 `registerWidget` / `getWidget` / `listWidgets` / `listWidgetsByCategory`。里面没有 React，也没有懒加载逻辑。

填充靠 `widgets/index.ts` 的**副作用 import**（见 §6.2 的缺陷说明）。

**懒加载是 per-widget、opt-in 的**，目前只有 `chart.symbol` 用，方式是拆一个独立的注册文件，把重模块挡在 dashboard chunk 之外：

```tsx
// 重图表模块（含 lightweight-charts 约 200KB gzip）不进 dashboard 路由 chunk，
// 只在真的渲染出一块图表 widget 时才加载
const LazyChartWidget   = lazy(() => import('./ChartWidget'));
const LazyChartSettings = lazy(() => import('./ChartWidget').then(m => ({ default: m.ChartSettings })));
```

`<Suspense>` 边界统一放在网格层（组装 widget 内容的那个 memo 里），所以任何 widget 想改成懒加载都不需要额外接线。

**实例 id 生成**：`newWidgetId(prefix = 'w')` —— 有 `crypto.randomUUID` 就用 `w_<uuid>`，否则 `<prefix>_<ts36>_<seq36>_<rand36>`，其中 seq 是**进程生命周期内单调递增**的计数器。理由很实在：预设工厂会在同一毫秒内连造 4 个以上 id，纯时间戳会撞。

**两层 memo**：内容层按 `prefs.widgets` 记忆，外壳层再叠 `editMode`。因为编辑模式的表现（拖拽把手、操作按钮）全在框架层，切换编辑模式只重算卡片外壳，重量级 widget 子树保持挂载不动 —— 实测差别是每次提交 15–40ms 降到 1–3ms。

### 6.7 增删改的交互流程

**进入编辑模式**：Custom 形态下桌面端顶栏的铅笔按钮。`Escape` 退出。编辑模式下底部浮出一条药丸工具条（`bottom: 1.5rem`，用 `translateX(calc(-50% + var(--sidebar-width) / 2))` 对齐到内容列中心 —— 这就是 §1.3 里 `--sidebar-width` 必须发布在 `documentElement` 上的原因之一）。工具条上四个动作：**添加 widget / 预设 / 重置 / 完成**。`<main>` 底部留白随状态切换（编辑 `6rem`、有浮动聊天框 `8rem`、否则 `0`）。

**添加**（`AddWidgetDialog`）：1080 宽 × 86vh 的双栏弹窗。

- 左栏：搜索框 + 分类导航（带颜色圆点和数量），底部一张提示卡。分类色：markets `#E0B341`、intel `#5BA47F`、personal `#C4A36B`、agent `#C4574F`、workspace `#5DA372`。
- 右栏：两列卡片，每张显示图标、标题、`单例`/`可多开` 徽章、TradingView 徽章、描述，以及 `{w}w × {h}h · 可配置|无设置`。
- **搜索同时匹配原始 `type` 字符串（英文，给熟手用）和译后的标题 / 描述。**
- 自动选中第一个可用项；**回车**添加当前选中，**双击**卡片添加。
- 已经在画布上的单例渲染成 disabled，透明度 0.55。

添加逻辑：查定义 → 有 `initConfig(ctx)` 就调它做上下文播种（例如 ticker-tape 用用户自选表播种），否则浅拷贝 `defaultConfig` → 生成 id → **在 setState 的 updater 内部再查一次单例约束**（不能只在渲染时查，会有竞态）→ `placeAtBottom` 到两个断点。

**删除 / 复制**：卡片头部的 `⋮` 菜单，仅编辑模式可见。单例的"复制"要同时**在 UI 上隐藏**并**在处理函数里拒绝**。复制出来的实例沿用源实例的 `w`/`h`，落到底部。

**设置**：齿轮图标只在 `definition.settingsComponent` 存在**且**处于编辑模式时出现。上层持有 `settingsFor: string | null`，渲染一个 `sm:max-w-md` 的 Dialog。`onChange(patch)` 浅合并进该实例的 config，走防抖 `update()`。设置面板的字段原子统一复用：`EnumField` / `SymbolField` / `SymbolListField` / `SettingsDoneButton` / `TradingViewSettingsFooter`。

**30 个 widget 里只有 19 个有设置弹窗**（17 个 TV + `chart.symbol` + `markets.miniChartGrid`）。其余 11 个原生 widget 走**内联配置**（通过 render prop 拿到 `updateConfig`）：`news.feed` 的 source tab、`portfolio.holdings` 的隐藏金额眼睛、`personal.portfolioWatchlist` 的 tab + 眼睛、`chart.symbol` 的周期胶囊。**这是有意的分工：高频切换的选项内联，低频的一次性配置进弹窗。**

**预设**：6 套 —— `morning-brief`（默认 / 推荐）、`agent-desk`、`researcher`、`trader`、`trader-tv`、`portfolio-steward`。每个工厂生成全新 id，返回 `{ version, widgets, layouts: { lg, md } }`（两个断点用同一份数组）。应用时先把当前状态 push 进 `history`（上限 3），然后**立即写**（不防抖）。**重置** = 在确认弹窗后应用 `morning-brief`。这两条路径都要**先清空 `settingsFor`**，否则设置弹窗会引用一个已经被换掉的实例 id。

**附到聊天上下文**：每块砖上有个回形针按钮（视图模式下 hover 才显形，触屏设备常驻），点击把一份 `WidgetContextSnapshot` 发布到 ContextBus。底层是一个 `Map` + `useSyncExternalStore` 的注册表，widget 通过 `useWidgetContextExport(instanceId, { full, rows? })` 主动登记自己能导出什么。TV widget 该按钮禁用并给出解释。

**浮动聊天框**：画布上存在 `agent.conversation` widget 时、或处于编辑模式时，隐藏浮动聊天输入卡（避免两个入口打架）。

---


## 7. 国际化、主题、样式方案

### 7.1 i18n

**库**：`i18next` + `react-i18next`，**不装** `i18next-browser-languagedetector` —— 检测逻辑手写在 `lib/locale.ts`，因为要让 cookie 优先且要能在没启动 i18n 的单测里单独用。

初始化（`src/i18n.ts`，副作用 import，在 `main.tsx` 里排在 `./index.css` **之前**）：

```ts
i18n.use(initReactI18next).init({
  resources: {
    'en-US': { translation: enUS },
    'zh-CN': { translation: zhCN },
  },
  lng: detectLocale(),
  fallbackLng: 'en-US',
  interpolation: { escapeValue: false },   // React 自己转义
});
```

**不分 namespace**，只有隐式的 `translation` 一个。语言包全量打进主包（各约 11.5 万字节）—— 两个语言、总量可控，按需加载的复杂度不划算。

#### 语言持久化：cookie，不是 localStorage

这是个有意的决策：**cookie 服务端 / 边缘可读**，localStorage 不行。同时因此放弃了跨 tab 的 `storage` 事件同步（可接受：换语言是低频操作）。

```ts
export const SUPPORTED_LOCALES = ['en-US', 'zh-CN'] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];
export const isSupported: (v: string | null | undefined) => v is Locale;
export function getLocaleCookie(): Locale | null;
export function setLocaleCookie(locale: string): void;
export function detectLocale(): string;
```

- Cookie 属性：`locale=<v>; path=/; max-age=31536000; samesite=lax`，`VITE_COOKIE_DOMAIN` 有值时加 `domain=`，`https:` 下加 `secure`。
- 读取正则 `/(?:^|;\s*)locale=([^;]+)/`；**`decodeURIComponent` 失败必须 try/catch 吞掉当作"没有"** —— 这段代码在模块加载期（i18n init）跑，抛出去就是白屏。
- 读和写**两侧都校验** `isSupported`（`zh-TW` 直接当没有）。
- **检测顺序**：cookie → `navigator.language` 精确匹配 → `navigator.language` 前缀匹配（`zh` → `zh-CN`）→ `'en-US'`。

#### 与后端偏好的同步

`useSyncUserLocale()` 在 `Main` 里调一次。规则：

```ts
if (!isSupported(stored)) return;
synced.current = true;          // 即使什么都没做也要上锁
if (getLocaleCookie()) return;  // cookie 优先，服务端值不覆盖用户当前选择
if (i18n.language !== stored) i18n.changeLanguage(stored);
setLocaleCookie(stored);
```

两个要点：**（一）只在没有 cookie 时才用服务端 `user.locale` 播种**；**（二）用 ref 上锁，且"没做事"也要上锁** —— 否则后续任何一次 `/users/me` refetch 都会把用户刚选的语言冲掉。

`POST /api/v1/auth/sync` 的 body 里带 `email` / `name` / `avatar_url` / `timezone`（`Intl.DateTimeFormat().resolvedOptions().timeZone || null`），**故意不带 `locale`** —— 只有设置页的下拉框有权写它。

#### 切换机制

设置页 › 用户信息 tab：

```ts
const handleLocaleChange = (newLocale: string) => {
  setLocale(newLocale);
  if (isSupported(newLocale)) {
    i18n.changeLanguage(newLocale);
    setLocaleCookie(newLocale);
  }
  userInfoRef.current = { ...userInfoRef.current, locale: newLocale };
  flushUserInfoSave();     // 立刻 PATCH，不走防抖
};
```

同一个面板里，**姓名走 800ms 防抖保存，语言 / 时区走立即保存**（离散选择没有"还在输入"的状态）。面板卸载时若仍 dirty 要 flush。

#### 语言包组织

21 个顶层 namespace，两个语言包结构完全对应：`common` / `auth` / `sidebar` / `account` / `nav` / `settings`(153) / `dashboard` / `workspace` / `thread` / `share` / `chat`(105) / `context` / `filePanel` / `rightPanel` / `memoryPanel` / `memoPanel` / `automation`(95) / `toolArtifact`(155) / `setup`(196) / `marketView` / `onboarding`。

**必须有一个 key 齐备性测试**（`locales/__tests__/keys.test.ts`）：遍历整棵 `src`，用三条正则抽出所有用到的 key ——

- `T_CALL`：`\bt\(\s*['"]([a-zA-Z0-9_.]+)['"]`
- `KEY_PROP`：`titleKey|descriptionKey|nameKey|tagKey|bestForKey|labelKey|blurbKey`（数据驱动的配置对象里的 key 字段）
- `KEY_VALUE`：裸的 `'dashboard.*'` 字符串

再解析复数变体（`_one`/`_other`/`_zero`/`_two`/`_few`/`_many`），断言发现的 key 数 > 200，且每个 key 在两个语言包里都解析得到。未翻译的槽位约定写成 `__pending: <english>`。

> 注意这个测试**只校验"被引用到的" key**，所以语言包里可能残留只在英文包有的死 key（原实现里 `chat`/`filePanel`/`memoPanel`/`toolArtifact` 各多 1–9 条）。新实现应额外加一条"反向检查"：列出从未被引用的 key 并在 CI 里告警。

#### 日期 / 数字格式化

**唯一真源是 `lib/format.ts`，禁止在别处写 `Intl.*`。**

```ts
export function createFormatter(opts: Intl.NumberFormatOptions): (n: number) => string;
export function createDateFormatter(opts: Intl.DateTimeFormatOptions): (d: Date | number) => string;
export const compactNumber = createFormatter({ notation: 'compact', maximumFractionDigits: 1 });
export const relativeTime: (d: Date | number | string | null | undefined) => string;
```

- 工厂函数按 `i18n.language` **记忆 `Intl` 实例** —— 每次语言切换构造一次，不是每次调用构造一次（`Intl` 构造是重操作）。
- `safeNumberFormat` / `safeDateFormat` / `safeRelativeFormat` 把构造包在 try/catch 里，失败回落 `new Intl.XFormat(undefined, opts)`，**并且照样更新 `lastLocale`**（否则一个坏 locale 会让每次调用都重试构造一次）。理由很实际：不做这层保护，一个畸形 locale 会让 dashboard 上所有格式化过的组件同时崩。
- `relativeTime` 用 `new Intl.RelativeTimeFormat(lang, { numeric: 'auto', style: 'narrow' })`，**带符号**（未来时间读作未来），步长表 `[year 31536000, month 2592000, week 604800, day 86400, hour 3600, minute 60]`，不足一分钟落到 `fmt.format(0, 'second')`。**null / 空 / `NaN` 一律返回 `''`，绝不返回一个看起来合理的"刚刚"。**
- `compactNumber` 在 zh-CN 下自动出 `万`/`亿`。

> **【硬约定，重写必须原样保留】** 用 `createFormatter` / `createDateFormatter` 的组件**必须同时调 `useTranslation()`**，否则语言切换时它不会重渲染，会一直显示旧语言格式。这条在原实现里靠注释和文档约束 —— **新实现应该把它做成机制**：把格式化包成 `useFormatter()` 之类的 hook，内部自己订阅 i18n，从根上消除这个陷阱。

#### 时区

**没有全局硬编码的"市场时区"**，而是"美东为默认、按交易所解析"。相关函数在 `lib/utils.ts`：

```ts
export const dateStrInTz  = (d: number | Date, tz: string): string   // 'en-CA' → YYYY-MM-DD
export const utcMsToETDate = (ms: number): string
export const utcMsToETTime = (ms: number): string                     // HH:MM 24h
export function utcMsToChartSec(utcMs: number, tz = 'America/New_York'): number
export function chartSecToDateStr(sec: number): string
export function utcOffsetLabel(tz: string, at?: Date): string         // 'UTC+8' / 'UTC-4' / 'UTC+5:30' / 'UTC'
```

- `utcMsToChartSec` 的做法：**用交易所墙上时钟的各字段拼一个"假 UTC 时间戳"**（lightweight-charts 一律按 UTC 渲染）。用一个按 tz 缓存的 `Intl.DateTimeFormat('en-US', { timeZone: tz, year/month/day/hour/minute/second: '2-digit', hour12: false })` 取字段。
- 对称地，`chartSecToDateStr` **必须按 UTC 解码** —— 用任何真实时区格式化都会二次偏移。
- `utcOffsetLabel` 用 `timeZoneName: 'longOffset'` 再正则 `/GMT([+-])(\d{2}):(\d{2})/`。要处理 ICU/CLDR 版本漂移：零偏移可能拼成 `GMT+00:00` 也可能是裸 `GMT`，两者都要映射成 `'UTC'`。formatter 按时区缓存（调用方每个时钟 tick 都会调它）。
- `lib/bars/exchanges.ts`：`US_MARKET_TZ = 'America/New_York'` 是未知交易所的兜底；`timezoneForSymbol(symbol)` 按后缀映射 `.L`→Europe/London、`.HK`→Asia/Hong_Kong、`.T`→Asia/Tokyo、`.TO`→America/Toronto、`.SS`→Asia/Shanghai、`.AX`→Australia/Sydney、`.KS`→Asia/Seoul。**`BRK.B`、`^GSPC` 这类必须正确回落到美股**（不能把 `.B` 当交易所后缀）。

#### 货币

**故意不走 `Intl`** —— 图表坐标轴和十字光标要求确定性、无千分位的短格式：

```ts
const CURRENCY_SYMBOLS = { USD:'$', GBP:'£', HKD:'HK$', EUR:'€', JPY:'¥', CNY:'CN¥' };
export function currencySymbol(code?: string | null): string;   // 未知 → 'AUD '，缺失 → '$'
export function formatPrice(value: number, code?: string | null, decimals = 2): string;  // toFixed，无分组
export function resolveDisplayCurrency(symbol, meta?): { code: string; decimals: number };
```

协议里的元数据（`price_currency` / `display_decimals`）优先于后缀启发式。`useCurrencyDisplay` 持有一对 `{ state + 镜像 ref }`，让"建 series 时创建的价格格式化闭包"能跟上货币变化而不必重建 series。

### 7.2 主题

```ts
type ThemePreference = 'light' | 'dark' | 'auto';
export type ResolvedTheme = 'light' | 'dark';
export interface ThemeContextValue {
  theme: ResolvedTheme;                       // 解析后的
  preference: ThemePreference;                // 用户选的
  setTheme: (value: ThemePreference) => void;
  toggleTheme: () => void;                    // dark → light → auto → dark
}
```

- **持久化**：`localStorage['theme']` 存**偏好**（三值），默认 `'auto'`。不是 cookie、不进后端 —— 主题必须在首帧之前就可用，网络往返来不及。
- **应用方式**：`<html data-theme="light|dark">` **属性**，不是 class。整套主题走 CSS 自定义属性，不依赖 Tailwind 的 `dark:` 变体。
- **跟随系统**：`window.matchMedia('(prefers-color-scheme: light)')` + `addEventListener('change')`，卸载时清理。注意查询写的是 **light**（不是惯例的 dark），语义是"**暗色是默认，只有系统明确说亮才亮**"。
- **必须用 `useLayoutEffect` 而不是 `useEffect` 盖属性**。理由：effect 是子先父后，被动 effect 会让 `data-theme` 落在所有后代的 effect 之后，于是那些从 `data-theme` 解析令牌来画布的模块（见 §7.3）在每次切换时都会有一帧读到旧主题的颜色。
- 同一个 effect 里顺手换 favicon（亮 `/logo-favicon.svg` / 暗 `/logo-favicon-dark.svg`）。
- Provider 挂在 `main.tsx` 的 `BrowserRouter` 与 `AuthProvider` 之间。

#### FOUC 防护（`index.html` 内联脚本，必须在任何样式表之前）

```js
var stored = localStorage.getItem('theme');
var t = (stored === 'light' || stored === 'dark') ? stored
      : (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
d.setAttribute('data-theme', t);

var s = parseFloat(localStorage.getItem('fontScale'));
if (s >= 0.9 && s <= 1.1) d.style.setProperty('--app-font-scale', String(s));
```

**关键点：不能把存的值原样盖上去。** 存的可能是 `'auto'`，它两个选择器都不匹配，结果是亮色系统的用户先画一屏暗色再被 ThemeProvider 纠正。字号那行用**区间检查**而不是成员检查，因为 `FONT_SCALES` 常量在 bundle 里、而这段脚本跑在 bundle 之前。

另有一段内联 `<style>` 定义"上色之前的底色"：

```css
@view-transition { navigation: auto; }
html { background: #191919; color-scheme: dark; }
html[data-theme="light"] { background: #FFFFFF; color-scheme: light; }
```

这两个值**必须和 `--color-bg-page` 完全一致** —— 差一点点，每次刷新都会闪一下错色的底。`<body>` 里还有第二段内联脚本，在 React 挂载前按已盖上的 `data-theme` 设好 favicon 的 href。

#### 字号缩放

```ts
export const FONT_SCALES = [0.9, 0.95, 1, 1.05, 1.1] as const;
export function getFontScale(): FontScale;
export function setFontScale(value: FontScale): void;   // 写 --app-font-scale + localStorage['fontScale']
```

**故意不做成 context**：初始值必须在首帧前盖上（由 `index.html` 负责），改变时也不需要任何组件重渲染 —— CSS 变量自己就够了。单写单读（只有设置页写、只有 CSS 读）。

根节点用 `html { font-size: calc(100% * var(--app-font-scale, 1)) }`，**乘在浏览器 / 系统偏好之上**，两者可叠加（不是覆盖）。

### 7.3 CSS 变量与令牌

`styles/tokens.css` 里**并存两套系统**，写在同一个 `:root` 块：

1. **语义化 hex / rgba 令牌**（`--color-*`）—— 给内联 `style={{}}` 和画布画笔用；
2. **Tailwind / shadcn 的 HSL 三元组**（裸值，不带 `hsl()` 包裹）—— `--background` / `--card` / `--primary` …

第 (1) 套**从第 (2) 套派生**，改调色板只动一处：

```css
--color-bg-page:    hsl(var(--background));
--color-bg-canvas:  var(--color-bg-page);
--color-bg-card:    hsl(var(--card));
--color-bg-input:   hsl(var(--input));
--color-bg-popover: hsl(var(--popover));
```

暗色写在 `:root`，亮色写在 `[data-theme="light"]` 覆盖。

#### 背景令牌的"角色表"（按表面选，不要按色值凑）

| 令牌 | 用在哪 |
|---|---|
| `bg-page` | 应用的地面：聊天页、设置页、登录页 |
| `bg-canvas` | 卡片网格底下的地面（dashboard）；暗色下等于 page |
| `bg-card` | 落在 page / canvas 上的卡片、面板 |
| `bg-tool-card` | 对话流里的 agent 工具 / 产物卡 |
| `bg-elevated` | 浮在卡片之上的 chrome：菜单、tooltip、悬浮片 |
| `bg-input` | 输入框、文本域、长得像字段的只读展示 |
| `bg-popover` | Radix/shadcn 的 popover + select 自绘时用的 `--popover` 的 hex 孪生 |

> **【缺陷】浮层填充色没统一**：popover/select 用 `--popover`，tooltip 和菜单用 `bg-elevated`，还有四个 dialog 停在 `bg-page`。原实现明确把统一推迟了。**新实现应该一次性收敛成一个 `bg-elevated`**，别把这个半成品继承过来。

#### 其余令牌分组

- **字体**（3）：`--font-ui`（`'Sora','Geist','Noto Sans SC',-apple-system,system-ui,sans-serif`）、`--font-content`（`'Geist','Noto Sans SC',…`）、`--font-mono`（`'JetBrains Mono','Menlo',ui-monospace,SFMono-Regular,monospace`）。
- **布局**：`--sidebar-width: 80px`、`--bottom-tab-height: 78px`。
- **边框**：`--color-border-muted / -default / -elevated / -subtle / -input`。
- **文字**：`--color-text-primary / -secondary / -tertiary / -quaternary / -muted`、`--color-icon-muted`；`--color-text-on-accent` **两个主题都是 `#241505`** —— 白字压在琥珀色上对比度不到 3:1。
- **强调色（burnished amber）**：`--color-accent-primary: hsl(var(--primary))`、`-soft` / `-disabled` / `-overlay`（暗 0.75 / 亮 0.65；低到 0.5 左右会发浑成脏棕）、`-gradient`（**故意是一个平的 `linear-gradient(90deg, #E9954A 0%, #E9954A 100%)`**，保留 gradient 类型只是因为调用点当 `background-image` 用）。
- **金融语义色**：`--color-profit`（`#3FB950` / `#1A7F37`）、`--color-loss`（`#F85149` / `#CF222E`）及各自的 `-soft` / `-muted` / `-border`；`--color-warning`、`--color-info`、`--color-success` 及 `-soft`。
- **按钮**：`--color-btn-primary-bg`（`#ECECEA` / `#1F1D1A`）、`-primary-text`、`-stop-hover-bg/-fg`、`-danger`、`-danger-pressed`。
- **HSL 块（暗）**：`--background: 0 0% 9.8%`、`--card: 220 4% 14.5%`、`--popover: 220 5% 12.4%`、`--primary: 28 78% 60%`、`--destructive: 3 93% 63%`、`--border: 0 0% 100% / 0.08`、`--ring: 0 0% 100% / 0.45`、`--radius: 0.5rem`、`--shadow-card: 0 1px 2px rgba(0,0,0,.35), 0 4px 16px rgba(0,0,0,.25)`。亮色：`--background: 0 0% 100%`、`--primary: 28 63% 51%`、`--ring: 60 2% 12% / 0.4`。
- **焦点环恒为中性色** —— 琥珀色永远不套在控件上（设计规范：强调色只做批注，不做 CTA 填充、不做 hover 浸染、不做焦点环、不做辉光）。

#### 没有令牌化的东西（新实现应该补上）

- **没有间距刻度**（无 `--space-*`），全靠 Tailwind 原子类；
- **没有阴影刻度** —— 只有一个 `--shadow-card`（设计规范："卡片阴影只有这一个"，这条可以保留）；
- **没有圆角刻度** —— 只有 `--radius`，但仍有 CSS 文件硬编码 `border-radius: 8px`；
- **【缺陷】没有 z-index 层级令牌** —— 从 1 到 1010 的裸数字散在 25 个 CSS 文件里，没有任何排序文档。最脆的一对是侧栏 / 底部 tab（1000）对 CreateWorkspaceModal（1010）。**新实现必须先定义一套 `--z-*` 层级（base / sticky / dropdown / overlay / modal / popover / toast），然后禁止裸数字。**

#### 基础层与工具层

- `@layer base`：`* { box-sizing: border-box; @apply border-border }`、`body { @apply bg-background text-foreground; font-family: var(--font-ui); height: 100vh; min-width: 320px; overflow: hidden }`。
- 移动端块 `@media (max-width: 767px)`：全局 `-webkit-tap-highlight-color: transparent`、`body { position: fixed }`（治 iOS 键盘顶起）、`.mobile-scroll-contain`，以及
  ```css
  input, select, textarea { font-size: max(16px, 1rem) !important; }
  ```
  用 `max()` 而不是写死 16px —— 既挡住 iOS 的自动放大，又让输入框仍随根字号缩放。
- `@layer utilities`：`.font-content`、`.bg-app`、`.panel`、`.fin-card` / `.fin-card-inner`、`.text-muted-fin`、`.text-up` / `.text-down`、`.bg-up-soft` / `.bg-down-soft`、`.ring-accent`、`.tabular-nums`。
- 导入顺序有讲究：`tokens.css` 和 `animations.css` 必须排在 `@tailwind` **之前**。

#### 画布颜色桥（`lib/themeTokens.ts`）

lightweight-charts 和裸 2D canvas 只吃**颜色字符串**，读不了 CSS 变量。这层负责把令牌解析成字符串：

```ts
export function readTokens(names: readonly string[], theme: ResolvedTheme): Record<string, string>;
export function resolveTokenMap<K extends string>(sources, fallbacks, theme): Record<K, string>;
export function createThemeResolver<K extends string>(sources, fallbacks): (theme: ResolvedTheme) => Record<K, string>;
export function useThemeTokens<K extends string>(resolve, theme): Record<K, string>;
export function clearThemeTokenCache(): void;
```

四条关键机制，新实现必须照搬：

1. **`readTokens` 在 `root.getAttribute('data-theme') !== theme` 时直接拒读**。这把"渲染阶段的竞态"从"画出另一个主题的颜色"降级成"回落到字面量"—— 后者只是不够精确，前者是肉眼可见的错。
2. **`normalizeColor`**：非 `#` / 非 `rgb` 开头的值要塞进一个隐藏 `<span>` 的 `color` 属性再读回来，拿浏览器规范化后的 `rgb()/rgba()`。因为声明成 `hsl(var(--card))` 的令牌读回来是 `hsl(220 4% 14.5%)`，有些库解析不了。同时要防住 CSSOM 悄悄丢弃无法解析的值、返回继承来的黑色。
3. **`createThemeResolver` 每个主题返回稳定的对象引用**。图表 effect 是按引用比较的，每次调用返回新对象 = 每次渲染都拆了重建整个图表。
4. jsdom 下所有自定义属性都算成 `''` → 返回 `{}` → 走纯字面量路径（测试环境自然可用）。

约定：source 里 `--` 开头的当令牌名，其他一律当字面量一次性色值。

**强制约束**：一条测试（`styles/__tests__/tokenRefs.test.ts`）遍历 `src` 下所有 `.ts/.tsx/.js/.jsx/.css`，抽出每个 `var(--color-…)`，只要名字没在 `tokens.css` 里声明过就失败（声明正则 `/(--color-[\w-]+)\s*:/g`），只对 `--radix-*` 前缀放行。**这条测试必须保留** —— 原实现是在积攒了十五个悬空令牌之后才补上的。

图表色的两个调用点：MarketView 的 `CHART_SOURCES`（21 个槽位，`bg→--color-bg-card`、`upColor→--color-profit`、`downColor→--color-loss` 等走令牌；`volumeUp/Down` 的 0.3/0.25 透明度、盘前琥珀底 / 盘后蓝底、水印、RSI 上下轨、baseline 渐变止点这些走字面量，理由是"没有任何 `-soft`/`-border` 令牌带着那个透明度"），以及聊天里的 `CANVAS_SOURCES`（7 个槽位；暗色阳线用 `#0FEDBE` 的终端薄荷绿——系统里别处没有这个色，所以没有令牌可指）。均线配色 `MA_CONFIGS`（MA5 `#22d3ee` / MA10 `#34d399` / MA20 `#fbbf24` / MA50 `#3b82f6` / MA100 `#a78bfa` / MA200 `#f59e0b`）是裸 hex，默认开 `[20, 50]`。

### 7.4 Tailwind 配置要点

```js
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: { extend: { fontFamily, colors, borderRadius, keyframes, animation } },
  plugins: [],
}
```

- **`colors`**：12 项，全部 `hsl(var(--x))` —— `border` / `input` / `ring` / `background` / `foreground`，以及 `primary` / `secondary` / `destructive` / `muted` / `accent` / `popover` / `card` 的 `DEFAULT` + `foreground` 对。**profit / loss / warning / info 没有进 Tailwind**，只以 `--color-*` 形式存在，通过内联样式或 `.text-up` / `.text-down` 工具类使用。
- **`fontFamily.mono`** 必须显式扩展成 JetBrains Mono 栈 —— 否则 `font-mono` 会静默解析到 `ui-monospace`。**`sans` 故意不扩展**：正文字体来自 `body { font-family: var(--font-ui) }`，因此 `font-sans` 会掉回 Tailwind 默认栈（这是个坑，新实现应把 `sans` 也指到 `var(--font-ui)`）。
- **`borderRadius`**：`lg: var(--radius)`、`md: calc(var(--radius) - 2px)`、`sm: calc(var(--radius) - 4px)`。
- **`keyframes`/`animation`**：只有一个 `fade-in`（opacity 0→1 + translateY 4px→0，`0.2s ease-out`）。其余动画（`fade-up-enter`、`morph-0..3`）都写在普通 CSS 里。
- **`plugins: []`** —— 没装 `@tailwindcss/typography`、`forms`、`tailwindcss-animate`，尽管代码里存在 shadcn 组件（它们的 `data-[state=open]:animate-in` 这类类名因此实际不生效）。
- `postcss.config.js` = `{ tailwindcss: {}, autoprefixer: {} }`。

> **【缺陷】`darkMode: ["class"]` 与实际用的 `data-theme` 属性不匹配** —— 代码里从来没有人给 `<html>` 加过 `dark` 类，所以**今天写下的任何 `dark:` 工具类都静默失效**。新实现必须改成 `darkMode: ['selector', '[data-theme="dark"]']`（或干脆统一改用 class），二选一，不能两套并存。
>
> **【缺陷】`components.json` 里 `"tsx": false`** 而实际全是 `.tsx` —— shadcn CLI 加组件会生成 `.jsx`。新实现要改成 `true`。

### 7.5 字体加载与 CJK

`index.html` 里**只有一次 Google Fonts 请求**：

```html
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400..700&family=JetBrains+Mono:wght@400;500;600&family=Noto+Sans+SC:wght@400..700&family=Sora:wght@400..700&display=swap"
      rel="stylesheet" media="print" onload="this.media='all'" />
```

策略是 **`media="print"` + `onload` 提升**：浏览器以低优先级抓取、不阻塞渲染，加载完再切成 `all`；CSS 侧 `display=swap`。family 必须按字母序（css2 的要求）。四个字族分工：**Sora**（展示 / UI 语气）、**Geist**（阅读正文）、**Noto Sans SC**（中日韩）、**JetBrains Mono**（数据 / 数字）。

> **【缺陷】没有 `<noscript>` 兜底**，禁用 JS 的用户只有系统字体；也没有对字体文件本身 `preload`。

**CJK 策略全在字体栈里**：`--font-ui` 和 `--font-content` 都把 `'Noto Sans SC'` **排在拉丁字族之后**，靠逐字形回落 + Google Fonts 的 `unicode-range` 切片 —— 拉丁字形永远轮不到它，而中文在所有操作系统上渲染一致（不会一台机器 PingFang、一台机器 YaHei），且大体积的 SC 子集只在真的渲染到中文码位时才下载。

代码里没有 `:lang(zh)` 规则、没有 CJK 专属的 `line-height` / `letter-spacing` / `word-break` 覆盖。CSS 之外只有两处语言相关分支：语音输入的 `recognition.lang = i18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'`，以及财报日历从接口数据里挑 `label_zh` 字段。

### 7.6 RTL：没有，且当前是合理的

没有 `dir` 管理、没有 `[dir="rtl"]` 选择器、没有 `rtl:` 变体、没有逻辑属性（`margin-inline` 等）。两个支持的语言都是 LTR，所以这是自洽的 —— **但新实现在写布局时应当优先用逻辑属性**（`padding-inline-start` 而不是 `padding-left`），这几乎零成本，能把未来接入 RTL 的代价从"重写全部样式"降到"加一个 `dir` 开关"。

> **【缺陷】`<html lang="en">` 是写死的，切换语言时从不更新。** 屏幕阅读器会用英文语音念中文内容。新实现必须在语言切换时同步 `document.documentElement.lang`。

---


## 8. 已知问题清单（新实现该修的）

前面各节已经就地标了 `【缺陷】`。本节把它们汇总成一张**可执行的检查表**，按"修不修得起"和"不修的后果"排序。规模参照：原实现 `src/` 下 **885 个源文件**（454 个 `.ts` + 431 个 `.tsx`）、约 8.9 万行（含测试），**270 个测试文件**，**60 个文件超过 500 行**。

### 8.1 P0：必须在动第一行业务代码之前定好的

这四条是"结构性"的 —— 等代码铺开再改，成本是现在的十倍。

#### （1）分层方向没有机械约束，已经彻底倒置

- `components/ui/error-banner.tsx` → import `@/pages/ChatAgent/utils/parseErrorMessage`
- `components/ui/chat-input.types.ts` → import `@/pages/Dashboard/widgets/framework/contextSnapshot`
- `pages/Dashboard/.../ChartWidget.tsx` → import `@/pages/MarketView/utils/chartConstants` + `MarketDataWSContext`
- `pages/MarketView/components/MarketChart.tsx` → import `@/pages/Dashboard/widgets/framework/TradingViewAttribution`

设计系统层向上依赖业务页，两个页面组互相依赖成环。AGENTS.md 里写了规则，但**规则不进 CI 就等于没有**。

**修法**：`eslint-plugin-import` 的 `no-restricted-paths`（或 dependency-cruiser）锁死方向，**并且 lint 对这条规则必须是 error 且进 CI**（其余 lint 规则可以继续保持"软"）。

```
lib/       → 不许 import components/、pages/
components/ui/ → 不许 import pages/、components/ 的非 ui 部分
components/    → 不许 import pages/
pages/A    → 不许 import pages/B
```

#### （2）没有 z-index 层级体系

从 `1` 到 `1010` 的裸数字散在约 25 个 CSS 文件和一堆 Tailwind 任意值里，没有任何排序文档。已经产生的实际 bug：

- **toast（`z-[100]`）在 dialog（`z-[1030]`）下面** —— 弹窗里的操作反馈用户根本看不见，而这恰恰是最需要反馈的场景；
- **`hover-card` 和 `aria-popover` 是 `z-50`**，会被底部抽屉（`z-[1010]`/`z-[1020]`）盖住。

**修法**：先定 `--z-*` 令牌（`base / sticky / dropdown / overlay / modal / popover / toast`），加一条测试禁止裸 `z-index` 数字（照 `tokenRefs.test.ts` 的样子写）。

#### （3）`darkMode: ["class"]` 与实际的 `data-theme` 属性不匹配

`tailwind.config.js` 声明的是 class 模式，但代码里从来没有人给 `<html>` 加过 `dark` 类 —— **今天写下的任何 `dark:` 工具类都静默失效**，而且不会有任何报错。同理，`plugins: []` 意味着 `tailwindcss-animate` 没装，那些从 shadcn 抄来的 `data-[state=open]:animate-in` 类名也全是空转。

**修法**：`darkMode: ['selector', '[data-theme="dark"]']`，并补齐真正用到的插件（或把用不到的动画类清掉）。顺带把 `components.json` 里的 `"tsx": false` 改成 `true`（否则 shadcn CLI 会生成 `.jsx`）。

#### （4）活得比 React 长的模块级单例没有全部登记

`lib/authResets.ts` 已经有了正确的注册表机制（模块自己 `registerAuthReset`，`AuthContext` 在登出 / 换账号时统一跑），但 **`components/ui/use-toast.ts` 的 `memoryState` 没有注册进去** —— 换账号时上一个用户的 toast 会残留。

**修法**：新实现里**每一个模块级可变单例都必须在定义处同一个文件里注册重置**，并加一条测试：扫描 `src` 里所有模块级 `let` / `new Map()` / `new Set()`，白名单之外必须能在同文件找到 `registerAuthReset`。

### 8.2 性能

| 问题 | 具体表现 | 修法 |
|---|---|---|
| **完全没有列表虚拟化** | 没装 `react-window` / `@tanstack/react-virtual`。线程画廊、消息列表、文件面板全量渲染。一条长会话（几百条消息，每条还带 markdown + katex + 代码高亮）会把主线程钉死 | 消息列表、线程画廊、文件树三处上虚拟化。消息列表要注意"高度不定 + 流式增长"，用动态测量的虚拟化方案 |
| **没有 `useInfiniteQuery`** | 全站零处。长列表要么一次拉完，要么手写分页 | 新闻流、线程列表、执行历史改用 `useInfiniteQuery` + IntersectionObserver 触底 |
| **`useEffect` 用量 381 处，`memo`/`useMemo` 仅 36 处** | 比例严重失衡。React 19 有编译器可以缓解，但前提是代码本身没有副作用滥用 | 开 React Compiler；把"从 props 派生 state"的 effect 全部改成渲染期计算；剩下的 effect 逐个问"这是在同步外部系统吗" |
| **78 处组件内 `setTimeout`/`setInterval`、23 处 `window.addEventListener`** | 每个都是一次潜在的泄漏（清理漏写 / 依赖数组错） | 封成 `useTimeout` / `useInterval` / `useEventListener`，清理逻辑只写一遍。`loader.tsx` 的"同节奏共用一个 ticker"模式值得推广 |
| **图表生命周期重复三份** | `MarketChart` / `ChartWidget` / `MarketDataCharts` 各写一遍 `createChart` + `remove` + `applyOptions` + `new ResizeObserver` | 抽 `usePriceChart()`，见 §5.6 |
| **60 个文件超 500 行、20 个超 800 行** | 最大的 `useChatMessages.ts` **2648 行**、`MarketChart.tsx` **2406 行**、`ChatView.tsx` **1938 行**。这类文件改一行就要重跑整个 chunk 的 HMR，也没法被 tree-shake | 参照 `chat-input.*`（主壳 + 展示分件 + 每种交互一个 hook）和 `pages/ChatAgent/session/*`（stream / history / subagents 分目录）的拆法，给全部 800 行以上的文件定拆分方案 |

**做对了、要保留的性能设计**（不要在重写时丢掉）：

- 侧栏拖拽调宽**每次 `pointermove` 只写 DOM 自定义属性、不 setState**（§1.3）；
- Dashboard 网格的**手势批处理**：拖拽过程中把布局堆在 ref 里，只在 `onDragStop`/`onResizeStop` 提交一次（§6.3）；
- Dashboard 的**两层 memo**：切编辑模式只重算卡片外壳，重量级 widget 子树保持挂载（15–40ms → 1–3ms）；
- 所有轮询一律 `refetchIntervalInBackground: false`；指数轮询**开盘 30s / 收盘 60s 自适应**；精选新闻**只在第一页轮**；
- 路由 chunk 预热与 `/users/me` 门禁请求**并行**而非串行（§1.2）；
- `lightweight-charts` 靠独立注册文件挡在 dashboard chunk 之外（约 200KB gzip）。

### 8.3 可访问性

这是原实现最薄弱的一块。具体缺口：

| 缺口 | 数据 / 位置 | 修法 |
|---|---|---|
| **`MobileBottomSheet` 无焦点陷阱、无 ESC、无 `role="dialog"`** | 却在 7 处当移动端模态用，和可访问的 `DialogContent` 抽屉路径并存 | **删掉它**，统一走 `DialogContent variant="default"` |
| **`CreateWorkspaceModal` 是手写 div 遮罩** | `.cwm-overlay` / `.cwm-modal` + `onClick` 关闭，没有焦点陷阱、没有 ESC、没有 body scroll lock | 改用 `ui/dialog` |
| **`FilePanel` 手写右键菜单** | 三个 `<div className="file-panel-context-menu-item" onClick=...>`，不可 Tab、不可方向键、无 `role="menuitem"` | 改用已有的 `ui/context-menu` |
| **`react-grid-layout` 的拖拽完全不可键盘操作** | 编辑模式下移动 / 缩放 / 换位没有任何键盘等价路径 | 每块砖提供"上移/下移/变宽/变窄"按钮或快捷键 |
| **`<html lang="en">` 写死，切语言不更新** | 屏幕阅读器会用英文语音念中文 | 语言切换时同步 `document.documentElement.lang` |
| **没有 skip link** | 全站 0 处 | 加"跳到主内容" |
| **约 20 处 `outline-none` 没有配套 focus ring** | 87 处 `outline-none`/`outline: none` vs 67 处 `focus-visible` | 逐处补 `focus-visible:ring-2 focus-visible:ring-ring`；令牌里的 `--ring` 是中性色（琥珀色永远不做焦点环），照用 |
| **表单没有字段级错误 + aria 关联** | 校验失败只弹 toast，没有 `aria-invalid` / `aria-describedby` | 见 §5.5 |
| **7 处 `<div onClick>` 无 `role`/`tabIndex`** | 见上面两条的具体位置 | — |
| **没有应用级 ErrorBoundary** | 全站只有 `DocumentErrorBoundary` 和 `WidgetErrorBoundary` 两个局部的；`main.tsx` 里根本没包 | 补三层：应用根 / 每路由 / widget |
| **字体加载没有 `<noscript>` 兜底** | `media="print" onload` 策略在禁用 JS 时永远不会提升成 `all` | 加 `<noscript><link rel="stylesheet" …></noscript>` |

**做对了、要保留的 a11y 设计**：

- 全局 `<MotionConfig reducedMotion="user">` + CSS 里 15 处 `prefers-reduced-motion`；
- `PageLoading` 的 `role="status"` + 装饰性行情墙 `aria-hidden`；
- 21 处 `aria-live` / `role="status"` / `role="alert"`；
- 移动端 `input, select, textarea { font-size: max(16px, 1rem) }` —— 用 `max()` 而不是写死 16px，既挡 iOS 自动放大又不破坏字号缩放；
- `--color-text-on-accent` 在两个主题都是深色（白字压琥珀不到 3:1）；侧栏选中态用"字形加深"而不是只靠强调色（强调色单独作图形指示器达不到 3:1）；
- 弹窗自绘头部时用视觉隐藏的 `DialogTitle` 保住可访问名。

### 8.4 类型不安全的地方

代码库本身已经是全 TypeScript（`src/` 下没有 `.js`/`.jsx`），`tsc --noEmit` 是构建和 CI 的硬门禁。**但"能过 tsc"和"类型是可信的"是两回事** —— 门禁是靠断言逃逸维持的：

| 症状 | 数量 | 集中在哪 |
|---|---|---|
| `: any` / `as any` / `any[]`（不含测试） | **86 处** | `MessageContentSegments.tsx`（14 处）、`ThreadGallery.tsx`、`WorkspaceGallery.tsx`、`SandboxSettingsPanel.tsx`、`MarketChart.tsx` |
| `as unknown as`（双重断言，绕开类型系统的终极手段） | **83 处** | 同上 |
| `// TODO: type properly` | **38 处** | 同上 |
| 非空断言 `x!.y`（.tsx，不含测试） | **42 处** | 分散；`main.tsx` 的 `document.getElementById('root')!` 是其中之一 |
| `@ts-expect-error` | 3 处 | **全部在测试里**，都是刻意构造非法输入，合理 |
| `eslint-disable` 系 | 55 处 | 分散 |

TODO 注释本身已经写清了根因，按频次归类就是三条：

1. **类型没导出** —— `// TODO: type properly — ActivityItem[] not exported` / `PlanData not exported` / `QuestionData not exported` / `ProposalData not exported`。组件内部定义了 props 类型却没 `export`，调用方只能 `as any`。**修法：所有组件的 props 类型必须导出**，加 lint 规则强制。
2. **catch 块** —— `} catch (err: any) {` 出现在至少 8 处。**修法**：`useUnknownInCatchVariables`（`strict` 已含）会强制 `unknown`，配一个 `toAppError(e: unknown): AppError` 收窄函数统一处理，禁止在 catch 里写 `any`。
3. **后端契约没有类型** —— `// TODO: type properly once backend API schema is formalized`（新闻详情页）、`once overview API response shape is formalized`（MarketView）、`depends on backend preferences shape`（设置页）。**修法**：从后端 OpenAPI 生成 TS 类型（`openapi-typescript`），让 `02-api-layer.md` 的契约变成编译期约束。注意这**不违反** §0.6 铁律 4 —— 生成类型是编译期的 `interface`，不是运行时 zod 校验。
4. **第三方库泛型复杂** —— `lightweight-charts series types are complex generics`、`ExcelJS CellValue union is complex`。**修法**：在 `types/` 下写一个薄的适配层，把复杂泛型收窄成本项目实际用到的形状，`as` 只出现在那一个文件里，而不是散在 20 个组件里。

**门禁强化建议**：新实现开 `noUncheckedIndexedAccess`、`exactOptionalPropertyTypes`，并加一条 CI 检查"`any` / `as unknown as` 的出现次数不得增加"（棘轮式，允许存量、禁止新增）。

### 8.5 状态管理的坑

| 坑 | 说明 | 修法 |
|---|---|---|
| **格式化函数的重渲染陷阱** | 用 `createFormatter` / `createDateFormatter` 的组件**必须同时调 `useTranslation()`**，否则切语言时不重渲染、一直显示旧语言格式。原实现靠注释和文档约束 —— 这是把机制问题降级成了纪律问题 | 把格式化包成 `useFormatter()` hook，内部自己订阅 i18n，**从根上消除陷阱** |
| **`Intl.*` 散落在 6 处** | 规则写着"禁止 ad-hoc `Intl.*`"，实际 `lib/utils.ts`（2）、`Automations/utils/time.ts`、`Dashboard/utils/portfolioSummary.ts`、`Login/emberBall.ts`、`Login/MarketScanlines.tsx` 都各建各的。`NewsDetailPage.tsx` 甚至直接 `new Date(...).toLocaleString()` | 同上，规则要靠 lint（`no-restricted-globals` / `no-restricted-syntax`）而不是文档 |
| **`streamFetch` 有两份实现** | `pages/ChatAgent/utils/api/transport.ts` 和 `pages/MarketView/utils/api.ts` 各一份 | 提升到 `lib/` 一处 |
| **`dashboard.history` 是半成品** | 字段被持久化、被迁移逻辑小心保留（上限 3），但**没有任何 UI 读它** —— 撤销功能不存在 | 要么把撤销做出来（"应用预设"是唯一会一次性抹掉全部排布的操作），要么删干净 |
| **同一偏好存两个地方** | `portfolio_active_tab` / `portfolio_values_hidden` 在 Classic 走 localStorage，在 Custom 走服务端的 `defaultTab` / `valuesHidden` | Classic/Custom 合并后自然消失；若保留两形态，偏好读写要统一走一个 adapter |
| **`sanitizeConfig` 依赖模块副作用** | 它内部 `getWidget(type)` 查注册表，注册表靠副作用 import 填充。忘了 import `widgets/index` 的话 sanitize **静默变成 no-op** | 把注册表作为显式参数传进去 |
| **Zod 只在读路径校验，写路径不校验** | 这是**有意的**，不是缺陷 —— 设置 UI 本身只产出合法值，写路径再校验只增加 throw 风险。**新实现要保留这个决策**，但要在代码里写清理由 | — |

**做对了、必须原样保留的状态设计**：

- **`isSafeRedirect()` 用"相对 origin 解析后比对 origin"而不是前缀匹配**（同时挡住 `//evil.com/x`、跨域绝对 URL、`/\evil.com` 反斜杠绕过）；
- **Dashboard 偏好写入的三重保障**：最小载荷（靠后端 JSONB 浅合并保住同级键）、跨 tab 广播、**冷缓存拒写**（服务端整块替换，用 `{}` 拼的写会不可逆抹掉布局）；
- **跨 tab 消息的延后重放**：本 tab 有排队的防抖或 pending mutation 时，把 invalidate 推迟到编辑落定后 —— 不能让别的 tab 打断用户正在拖的这一下；
- **`useSyncUserLocale` 的 ref 上锁"即使 no-op 也要锁"**；
- **`useLayoutEffect` 盖 `data-theme`**（被动 effect 会让画布画笔有一帧读到旧主题）；
- **`readTokens` 在 `data-theme` 与请求主题不一致时拒读**，回落字面量而不是画错颜色；
- **`createThemeResolver` 每个主题返回稳定对象引用**（图表 effect 按引用比较，新对象 = 每次渲染重建图表）；
- **`newWidgetId` 的进程级单调计数器**（预设工厂同一毫秒连造 4 个以上 id）。

### 8.6 重复代码

| 重复项 | 现状 | 目标 |
|---|---|---|
| **确认弹窗** | 5 个各写各的：`ConfirmDialog`（还住在 `pages/Dashboard/components/`）、`ConfirmDeleteDialog`、`DeleteConfirmModal`、`AlwaysOnConfirmDialog`、`ArchiveThreadConfirmDialog` | 1 个 `components/ui/confirm-dialog.tsx` |
| **表格** | 9 个页面组件各写裸 `<table>` | 1 个 `components/ui/table.tsx` |
| **骨架屏** | 约 15 处 `animate-pulse`，三套不同写法（`.map` 内联 div、结构化占位树、纯 CSS 类） | 1 个 `components/ui/skeleton.tsx` |
| **recharts tooltip** | 每个调用点手写一遍 render-prop div | `components/charts/ChartTooltip.tsx` + `ChartLegend.tsx` |
| **图表生命周期** | 3 份 `createChart` + ResizeObserver | `usePriceChart()` |
| **加载态组件** | 7 个，其中 3 个只 1 处使用、1 个 0 处使用（`morph-loading` 注释里自己写着"已被 LissajousLoading 取代"） | 2 个：通用 spinner + 流式指示器 |
| **Select** | 2 套并存：原生 `ui/select.tsx`（6 处使用）和 react-aria `ui/aria-select.tsx`（1 处使用，且 `ui/field.tsx` 只被它 import） | 1 套 |
| **底部抽屉** | 2 套并存：`MobileBottomSheet`（7 处，无 a11y）和 `DialogContent variant="default"`（有 a11y） | 1 套 |
| **`streamFetch`** | 2 份 | 1 份，在 `lib/` |
| **变体定义方式** | cva 2 个文件 vs 裸 `Record` 4+ 个文件 | 全 cva |

**死代码清点**（可直接不迁移）：`ui/card.tsx` **0 处使用**、`ui/checkbox-02.tsx` **0 处使用**、`ui/morph-loading.tsx` **0 处使用**；`ui/field.tsx` / `ui/badge.tsx` / `ui/textarea.tsx` / `ui/tooltip.tsx` / `ui/aria-select.tsx` / `ui/list-box.tsx` / `ui/aria-popover.tsx` / `ui/dot-loader.tsx` / `ui/lissajous-loading.tsx`(注：这个是聊天流式指示器，实际在用) / `ui/logo-loading.tsx` / `ui/morphing-page-dots.tsx` **各只 1 处使用**；`features/analyst-standalone/` 整个目录带着"未接入路由，勿在线上页面 import"的注释。语言包里也有约 20 条只在英文包存在、从未被引用的死 key。

> **注意**："只 1 处使用"不等于该删 —— `tooltip.tsx` 只有 1 处使用恰恰说明**该多用**（很多图标按钮没有可访问名）。要区分"抽象过早"和"抽象没用起来"。

### 8.7 工程门禁的三条建议

原实现的门禁配置本身有个结构性问题：**类型是硬门禁、lint 是软门禁**，于是所有"靠 lint 才能机械执行"的架构规则（分层方向、禁止裸 `Intl.*`、禁止内联 query key、禁止裸 z-index）全部只能靠文档约定，而文档约定被证明是无效的 —— 上面 §8.5 那几条"规则写了但实际违反"就是证据。

1. **拆成两档 lint**：架构规则（分层方向、一处真源、禁裸数字/裸 `Intl`）设为 `error` 且进 CI；风格规则保持 `warn` 不门禁。
2. **保留并扩展"用测试当门禁"的做法**。原实现里有两条很好的：`styles/__tests__/tokenRefs.test.ts`（任何 `var(--color-*)` 没在 `tokens.css` 声明就失败 —— 这条是在积攒了 15 个悬空令牌之后才补的）和 `locales/__tests__/keys.test.ts`（所有被引用的 i18n key 必须在两个语言包都解析得到）。新实现照此再加：z-index 令牌检查、模块级单例的登出重置检查、`any` 计数棘轮、以及**反向的 i18n 死 key 检查**（现有的只查"引用到的 key 在不在"，查不出永远不会被引用的 key）。
3. **`pnpm build` 保持 `tsc --noEmit && vite build`** —— 类型检查是构建的前置，不是并行的可选步骤。

### 8.8 安全

| 问题 | 修法 |
|---|---|
| **券商凭据写进 localStorage** —— `ConnectBrokerDialog` 把 IBKR Flex 凭据存在 `localStorage['kairos_ibkr_flex_credentials']`，任何 XSS 都能捞走 | 只走后端，前端不落盘 |
| Markdown 渲染链已含 `rehype-sanitize`，但同时也用了 `rehype-raw` | 保留 `rehype-sanitize` **排在 `rehype-raw` 之后**的顺序，并加测试固化；`dangerouslySetInnerHTML` 当前全站 0 处，保持 |
| `isSafeRedirect()` 的实现是对的 | **原样保留**，并为它写测试（`//evil.com`、`/\evil.com`、跨域绝对 URL 三个用例） |

