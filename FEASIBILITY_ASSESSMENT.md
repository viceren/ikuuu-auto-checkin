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
