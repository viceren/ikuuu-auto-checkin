# ikuuu 自动签到

## 原理

使用 Cookie 直接向 `https://ikuuu.win/user/checkin` 发送 POST 请求完成签到，无需浏览器自动化。

Cookie 需要从浏览器手动获取并配置，过期后需手动更新。

## 使用方式

### 方式一：本地运行

1. **获取 Cookie**：在浏览器登录 ikuuu，打开开发者工具（F12）→ Application → Cookies → `ikuuu.win`，复制所有 Cookie 拼成字符串（格式：`key1=value1; key2=value2`）

2. **配置 Cookie**：在 `config.json` 中填入 Cookie：
```json
{
  "cookie": "email=xxx; session=xxx; ..."
}
```

3. **安装依赖**：
```bash
pip install requests
```

4. **运行签到**：
```bash
python checkin.py
```

### 方式二：GitHub Actions 自动签到

1. **Fork 或推送**本仓库到 GitHub

2. **设置 Secret**：在 GitHub 仓库 Settings → Secrets and variables → Actions → New repository secret：
   - `IKUUU_COOKIE` — 浏览器登录后复制的完整 Cookie 字符串

3. **启用 Actions**：仓库的 Actions 标签页 → 启用

4. 之后每天北京时间 06:00 会自动签到

> **Cookie 过期后**需要重新从浏览器获取并更新 `IKUUU_COOKIE` Secret。

## 文件说明

| 文件 | 说明 |
|------|------|
| `checkin.py` | 签到主程序（纯 Cookie 签到） |
| `config.json` | 本地配置文件（已加入 .gitignore，存 Cookie） |
| `.github/workflows/checkin.yml` | GitHub Actions 自动签到配置 |
| `README.md` | 本文件 |

## 常见问题

**Q: 签到返回 "您似乎已经签到过了"**
A: 说明今天已经签到成功，无需重复操作。

**Q: 提示 Cookie 已失效怎么办？**
A: 重新在浏览器登录 ikuuu，从开发者工具复制新的 Cookie，更新到 `config.json` 或 GitHub Secrets 中的 `IKUUU_COOKIE`。

**Q: 如何获取 Cookie？**
A: 浏览器登录 ikuuu → F12 打开开发者工具 → Application（应用）标签 → 左侧 Cookies → `ikuuu.win` → 选中所有条目，拼成 `name1=value1; name2=value2` 格式的字符串。
