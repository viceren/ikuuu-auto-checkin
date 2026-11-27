# ikuuu 账号密码登录改造 — 可行性评估

> 评估时间：2026-06-18
> 评估对象：将 `ikuuu-auto-checkin` 从"手动获取 Cookie"改造为"账号密码自动登录"

## 结论

在 GitHub Actions 里做"纯账号密码全自动登录"**基本不可行**；但有两条务实路径能把"手动复制 Cookie"的痛点降到最低。核心卡点不在代码，而在**验证码类型**。

## 一、当前架构回顾

| 文件 | 角色 | 关键点 |
|------|------|--------|
| `checkin.py` | CI 签到主力 | 纯 `requests` + Cookie 打 `/user/checkin`，无浏览器，适合 Actions |
| `refresh_cookie.py` | 本地半自动取 Cookie | Playwright 有头浏览器，自动填账号密码，人手动过验证码 |
| `debug_captcha.py` | 验证码探针 | 检测 `.cf-turnstile` / reCAPTCHA / hCaptcha 等元素 |
| `checkin.yml` | Actions 调度 | 每天 06:00 跑，读 `IKUUU_COOKIE` Secret，失败开 issue |

登录能力已具备（`refresh_cookie.py` 能填账号密码），唯一缺的是"自动过人机验证"。问题本质是：**能否自动过 ikuuu 的人机验证**。

## 二、核心障碍：验证码类型不对，OCR 这条路不适用

基于代码分析，ikuuu 的人机验证是 **Cloudflare Turnstile（托管式行为挑战）**，不是图像验证码。

| 维度 | ikuuu | 内网系统（对照） |
|------|-------|------------|
| 验证码类型 | Cloudflare Turnstile | 加法验证码（图像） |
| 本质 | 行为/指纹挑战 | 图像字符识别 |
| 有没有"字符"可识别 | 没有，不展示图片 | 有，160×60 红字手写体 |
| ddddocr/OCR 是否有效 | 完全无效 | 有效，能解 |
| IP 风控 | Cloudflare 对数据中心 IP 评分低 | 内网 IP 无风控 |

Turnstile 不展示图片让你认字，而是通过浏览器指纹、IP 信誉、交互行为判断是否真人。因此"ddddocr 识别 → 提交答案"的打法在这里**原理上不成立**。

## 三、CI 环境下两个叠加的不利因素

1. **GitHub Actions 是数据中心 IP**：Cloudflare 对这类 IP 段评分极低，Turnstile 几乎必然触发严格挑战甚至直接拦截。IP 层面，代码改不了。
2. **headless Chromium 指纹易识别**：`navigator.webdriver` 等自动化特征。即使用 `playwright-stealth`，Turnstile 托管模式检测也在持续升级。

> 第三方打码服务（2captcha / capsolver 等）虽声称支持 Turnstile，但 token 有时效性和域名/会话绑定，跨环境传递常失效，且付费、违反 ToS。不建议作为唯一依赖。

## 四、可行的改进方案（带权衡）

| 方案 | 原理 | 可行性 | 代价 |
|------|------|--------|------|
| **A. self-hosted runner（家庭网络）** | Actions 仍定时触发，但浏览器跑在自己机器上，住宅 IP 信誉好，Turnstile managed 模式常能无感通过 | 中高 | 机器需常开 + 装 runner |
| **B. 本地半自动 + API 回写 Secret** | 增强 `refresh_cookie.py`：点完验证码登录后，自动调 GitHub API 把新 Cookie 写回 `IKUUU_COOKIE` Secret | 高 | Cookie 过期时需本地点一下 |
| **C. 失效告警闭环** | `checkin.py` 已有 Cookie 失效检测，配合 workflow 失败开 issue，形成"失效→通知→本地一键刷新"闭环 | 高 | B 的自然延伸 |
| ❌ 纯 requests 模拟登录 POST | Turnstile token 是必填参数，没合法 token 直接 403 | 不可行 | — |
| ❌ Actions 跑 headless 硬刚 | 大概率被 Turnstile 拦死，浪费 CI 额度 | 不可行 | — |

## 五、建议

- **短期（立刻见效）→ 方案 B**：给 `refresh_cookie.py` 加"登录成功后自动回写 GitHub Secret"收尾。本地跑一下、点一下验证码，CI 端 Cookie 自动更新。改动小、最稳。
- **中期（想更接近全自动）→ 方案 A**：在常开家庭机器上挂 self-hosted runner，住宅 IP 下 Turnstile 托管模式通过率明显更高，账号密码可自动填，基本无人值守。
- **不建议在 CI 上硬刚 Turnstile**：投入产出比极低。

> 注：以上基于代码静态分析判断为 Cloudflare Turnstile。要 100% 确认是 Turnstile 还是 reCAPTCHA/hCaptcha，本地跑 `debug_captcha.py` 即可。综合 `checkin.py` 的 Cloudflare 拦截检测，基本可锁定 Turnstile。

---

# 复测更新（2026-11-27）

> 起因：评估「能否让 GitHub Actions 在 Cookie 过期后自动登录、自动获取 Cookie」。
> 结论：**免费路径（Playwright 点「一键验证」）实测不成立**。以下内容覆盖上文已过时的部分，原文保留作历史记录。

## 与 2026-06-18 评估的差异

| 项目 | 2026-06-18 的判断 | 2026-11-27 实测 |
|------|------------------|----------------|
| 验证码类型 | Cloudflare Turnstile | **Geetest v4**（2026-04 起），九宫格语义点选 |
| 页面渲染 | — | JS 混淆动态渲染，`requests` 抓 `/auth/login` 返回 200 但表单/验证码特征全为 0，只能真实浏览器 |
| 社区声称的免费路径 | — | `ShadowHunt-yi/ikuuu`（2026-07）称点一下即放行；**今日不复现** |
| 「点我开始验证」按钮 | — | 实测是 `.geetest_btn_click`；社区代码用的 `.geetest_radar_btn` 在 ikuuu 页面上不存在 |
| 放行判定 | — | `window.Captcha.isReady()`；注意 `.geetest_tip` 只是状态条（出题时仍显示"验证中"），题干在 `.geetest_text_tips` / `.geetest_title` |

## 7 组对照实验（本机住宅 IP，结果一律为「弹出九宫格」）

| # | 变量 | 结果 |
|---|------|------|
| 1 | Playwright 默认无头（headless shell）+ 伪装 UA | 出题 |
| 2 | 新版无头 `channel="chromium"` | 出题 |
| 3 | 系统真实 Edge 159 | 出题 |
| 4 | 系统真实 Chrome 158 | 出题 |
| 5 | 无头 + UA 与 Client Hints 版本自洽（去掉 `HeadlessChrome` 字样） | 出题 |
| 6 | **有头** + 真实 Chrome + 真实 Intel GPU | 出题 |
| 7 | 第 6 组 + 人类鼠标轨迹（贝塞尔 + 抖动变速）+ 逐字输入 | 出题 |

题干示例：「选 3 个符合右图的图片」（九张图里选出 3 张轮胎）。第 6、7 组的浏览器指纹与操作方式
与真人已无实质差别，仍直接出题，说明拦截点是**站点策略（必须作答）**而非指纹。由此：

- 上文「方案 A：self-hosted runner（家庭网络）」中"住宅 IP 下 Turnstile 托管模式常能无感通过"**不再成立**——住宅 IP 的有头浏览器同样出题。
- xvfb + 有头、stealth 加固、UA / 指纹对齐等方向**无需再投入**。
- GitHub Actions 数据中心 IP 评分更低，只会更严。

## 剩余路径

| 方案 | 可行性 | 代价 |
|------|--------|------|
| 本地半自动 + Secret 回写（现状） | 高，已验证 | Cookie 约每 7 天手动过一次验证 |
| 付费打码平台（CapSolver / AntiCaptcha 解 Geetest v4） | 中高 | 约 $0.0016/次，最低充 $6；数据中心 IP 登录可能还需 IMAP 读邮箱验证码；**违反站点 ToS** |
| 免费自动登录 | **不可行** | — |

## 复测方法

`python auto_login.py --dry-run` —— 不提交登录、不需要真实账号，约 20 秒给出「放行 / 出题（含题干）」的结论。
站点策略变化后可直接复测，无需重做上述实验。该模块的"打开登录页 → 填表 → 触发极验 → 等待结果"流程
可作为日后接入打码平台的底座。

---

# 自动更新 Cookie 路径全量评估（2026-11-27 第二轮）

> 问题：Cookie 约 7 天过期，是否存在**任何**自动更新（= 自动重新登录）的方式？
> 结论：**不能说"确实没有"**。被排除的是「免费 + 无状态 + 纯自动过极验」这一类；
> 仍有 **2 条未验证的免费可能**（B、C）+ **1 条确定可行但付费**（A）。

## 前置原理（硬约束）

SSPanel 的登录 Cookie 由 `uid + email + expire_in + key (+ ip)` 组成：

- `key = hash(uid, email, expire_in, 站点 secret)` —— **secret 在服务端，客户端无法伪造**；
- `expire_in` 是登录时写入的绝对时间戳，若服务端不在校验时重签，**客户端无法延长**。

因此「自动更新 Cookie」在技术上**只能等价于「自动完成一次登录」**——除非服务端本身支持滑动续期（见路径 C）。

## 已实测排除

| 路径 | 实测结果 |
|------|---------|
| 无状态浏览器自动过极验 | 7 组对照全部出题（见上节）。题型还会**轮换**：九宫格语义点选 / **文字点选**（"请在下图依次点击"）/ 滑块 |
| 复用浏览器 profile / 极验 gid | 同一 profile 连开两次，localStorage 为空、行为完全一致 → **未答题**的 profile 不带来信任 |
| API 登录 | `/api/v1/*` 全部 **404**（社区项目亦称「iKuuu 已不支持账号密码 API 登录」） |
| Telegram 登录 | 页面有「使用 Telegram 登录」按钮，但 `/auth/telegram`、`/telegram/callback` 均 **404** → 死功能 |
| 忘记密码链路 | `/auth/forget` 404；真实路径 `/password/reset` **有极验**（`cap=true`）→ 同样挡死 |
| 邮箱验证码 / 2FA 当免验证通道 | `login-email-code`、`2fa-code` 均为隐藏字段（`w=0,h=0`），是登录后的**第二因素**，非独立入口 |

另：登录表单 `action=javascript:void(0)`、由前端 JS 提交，纯 `requests` 模拟登录还必须构造 CSRF 与极验参数，不可行。

## 仍然存在（未排除）

| # | 路径 | 状态 | 成本 / 风险 |
|---|------|------|------------|
| **A** | **付费打码平台**（CapSolver / AntiCaptcha 解 Geetest v4） | 技术确定可行 | ~$0.0016/次，最低充 $6；每月约 4 次登录 ≈ ￥0.04/月。风险：违反 ToS；题型轮换需平台支持；数据中心 IP 可能还需 IMAP 读邮箱验证码 |
| **B** | **极验设备信任**：真人答对一次后，同一 profile / gid 是否免验证 | **未验证，唯一可能免费** | 需主人手动过一次（本来每月也要做一次）。若成立，配合本机持久化 profile + 固定住宅 IP 可近乎无人值守 |
| **C** | **Cookie 滑动续期**：服务端校验时是否重签 `expire_in` | **未验证** | 零成本。若成立则「签到即续命」，问题彻底消失 |
| D | 自研 VLM 解九宫格/文字点选 + 模拟点击 | 技术可能 | 开发+维护成本高；极验校验轨迹与环境，"答对仍判机器"常见；强对抗、违反 ToS |

## 两条免费路径的验证方法（各只需一次）

**C（最高优先级，零成本）**：下次刷新 Cookie 后记下 Cookie 里的 `expire_in` 时间戳，连续签到 2~3 天后再看是否变化。
- 不变 → 硬过期，C 排除（回到 A/B）；
- 变大 → 签到即续期，本项目**根本不需要**自动登录。

**B**：把 `refresh_cookie.py` 改为使用 Playwright **持久化 profile**（`launch_persistent_context`，目录固定），并勾选「记住我」。
- 手动登录一次 → 关浏览器；
- 隔日用同 profile 自动打开 `/auth/login`，观察是否仍出题；
- 若免验证 → 自动登录可行（但**必须在同一 IP / 机器**上，CI 数据中心 IP 会重置信任）。
- 注：`remember` 复选框默认**未勾选**（实测 `rememberChecked=false`），建议下次刷新时勾上；对有效期的影响待确认。

## 结论

- 「免费 + 无状态 + 纯自动」过关极验：**确实没有**，已充分实测。
- 「自动更新 Cookie」整体：**尚有 2 条未验证的免费路径（C、B）+ 1 条确定可行的付费路径（A）**。
- 在验证 C 之前，**不应断言「没有任何自动方式」**。

---

# 第三轮：设备信任排除 + 视觉解题实测可行（2026-11-27）

## 结论速览

| 路径 | 结果 |
|------|------|
| 移动端 UA 是否换策略 | **排除**：iPhone UA + 移动视口仍出题（文字点选） |
| 旧版 / 备用登录入口 | **排除**：`/login`、`/user/login`、`/signin`、`/auth/login/` 全 404 |
| **B 极验设备信任**（答对一次后免验证） | **排除**：实测答对并通过后，同一 persistent profile 复测**仍出题**；profile 内 localStorage / cookie 均为空 |
| **D 视觉解题 + 模拟点击** | **实测可行**：九宫格语义题一次点击命中，`isReady()` 变 true，**1 轮通过** |
| C Cookie 滑动续期 | 仍未验证（需有效 Cookie） |

## D 的实测细节

- 题型：`选 3 个符合右图的图片`；9 格均有 DOM 元素（`.geetest_item`，每格 92×80），可精确取中心坐标。
- 关键难点是**干扰项**：本次 9 格中"螺丝刀"实际有 4 个候选——索引 2 是一排彩色**刷子/刮刀**，
  3 是 4 把黑柄螺丝刀，4 是黄红柄螺丝刀，7 是蓝柄螺丝刀；正确解是 **3 / 4 / 7**。
  必须正确识别"刷子 ≠ 螺丝刀"，纯规则或模板匹配做不到，需要语义级视觉理解。
- 点击方式：贝塞尔曲线移动 + 抖动 + 变速 + 按下/抬起（人类轨迹）。
- 结果：点击 `[3,4,7]` 后约 8 秒 `isReady()=true`，未出现多轮验证。
- 样本：n=1，通过率待更多样本；但机制清晰（语义识别 + 行为拟人），不是偶然。

## 工程化路径（若走 D）

1. **在本机（住宅 IP）跑定时任务**，不要放 GitHub Actions——Actions 跑不动 7B 级 VLM，且 IP 评分更差。
2. 本地 VLM（Ollama + Qwen2.5-VL 等）识别题干 + 9 格图并输出 3 个索引；文字点选题型需 OCR 给出字坐标。
3. Playwright 按坐标模拟人类轨迹点击 → 过极验 → 填账号密码登录 → 取 Cookie → 回写 GitHub Secret。
4. 风险：违反站点 ToS（加极验就是为防自动化）；VLM 误判会导致验证失败，需重试与告警；题型会变，需维护。

## 仍然最省事的两条

- **C（零成本，优先验证）**：Cookie 里的 `expire_in` 是否随每日签到变化。若变化 → 签到即续命，以上全不需要。
- 现状：本地半自动刷新（约每 7 天一次）。
