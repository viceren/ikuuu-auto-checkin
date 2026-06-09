# ikuuu 自动签到

## 原理

使用 Playwright 浏览器自动化登录 ikuuu，自动获取 Cookie 后通过 requests 库向 `https://ikuuu.win/user/checkin` 发送 POST 请求完成签到。

Cookie 过期时脚本会自动重新登录，无需手动更新。

## 使用方式

### 方式一：本地运行

1. **配置账号**：在 `config.json` 中填入账号密码：
   ```json
   {
       "email": "your@email.com",
       "password": "your_password"
   }
   ```

2. **安装依赖**：
   ```bash
   pip install requests playwright
   python -m playwright install chromium
   ```

3. **运行签到**：
   ```bash
   python checkin.py
   ```

### 方式二：GitHub Actions 自动签到

1. **Fork 或推送**本仓库到 GitHub

2. **设置 Secrets**：在 GitHub 仓库 Settings → Secrets and variables → Actions → New repository secret：
   - `IKUUU_EMAIL` — 登录邮箱
   - `IKUUU_PASSWORD` — 登录密码

3. **启用 Actions**：仓库的 Actions 标签页 → 启用

4. 之后每天北京时间 06:00 会自动登录并签到，Cookie 过期也会自动重新登录

### 兼容旧版 Cookie 方式

如果仍然希望使用 Cookie（不存密码），可以：
- 配置 `IKUUU_COOKIE` Secret（或本地 `config.json` 的 `cookie` 字段）
- 脚本会优先使用已有 Cookie，过期后自动回退到账号密码登录

## 文件说明

| 文件 | 说明 |
|------|------|
| `checkin.py` | 签到主程序（集成自动登录） |
| `login.py` | Playwright 自动登录模块 |
| `config.json` | 本地配置文件（已加入 .gitignore，存 email/password） |
| `.github/workflows/checkin.yml` | GitHub Actions 自动签到配置 |
| `README.md` | 本文件 |

## 常见问题

**Q: 签到返回 "您似乎已经签到过了"**
A: 说明今天已经签到成功，无需重复操作。

**Q: 登录失败怎么办？**
A: 脚本会自动尝试定位登录页的输入框和按钮。如果网站改版导致登录失败，请检查 `login.py` 中的选择器是否需要更新。如果登录页有验证码（Cloudflare Turnstile 等），需要在本地有头模式下手动完成验证。

**Q: 可以在本地不装浏览器运行吗？**
A: 装好 `playwright` 和 `chromium` 即可。如果不想装浏览器，也可以用旧版 Cookie 方式（`config.json` 中填 `cookie` 字段），但 Cookie 过期后需要手动更新。
