# ikuuu 自动签到

## 原理

使用 Cookie 认证，通过 requests 库向 `https://ikuuu.win/user/checkin` 发送 POST 请求完成签到。

## 使用方式

### 方式一：本地运行

1. **获取 Cookie**：登录 `https://ikuuu.win/user` 后，在浏览器 F12 → Console 运行：
   ```javascript
   copy(document.cookie)
   ```
   （会自动复制到剪贴板）

2. **更新 config.json**：将获取到的 Cookie 粘贴到 `config.json` 的 `cookie` 字段

3. **运行签到**：
   ```bash
   python checkin.py
   ```

### 方式二：GitHub Actions 自动签到

1. **Fork 或推送**本仓库到 GitHub

2. **设置 Secrets**：在 GitHub 仓库 Settings → Secrets and variables → Actions → New repository secret：
   - Name: `IKUUU_COOKIE`
   - Value: 从浏览器获取的完整 Cookie 字符串

3. **启用 Actions**：仓库的 Actions 标签页 → 启用

4. 之后每天北京时间 08:00 会自动签到

### 更新 Cookie

Cookie 会过期，过期后需要重新获取并更新：
- 本地：更新 `config.json`
- GitHub Actions：更新 Secret `IKUUU_COOKIE`

## 文件说明

| 文件 | 说明 |
|------|------|
| `checkin.py` | 签到主程序 |
| `config.json` | 本地配置文件（已加入 .gitignore） |
| `.github/workflows/checkin.yml` | GitHub Actions 自动签到配置 |
| `README.md` | 本文件 |

## Cookie 过期判断

脚本会自动检测 Cookie 是否过期，如果过期会输出错误提示并退出。

## 常见问题

**Q: 签到返回 "您似乎已经签到过了"**
A: 说明今天已经签到成功，无需重复操作。

**Q: Cookie 过期了怎么办？**
A: 重新登录网站，在浏览器 Console 执行 `copy(document.cookie)` 获取新 Cookie 更新即可。