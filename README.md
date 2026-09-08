# ikuuu 自动签到

## 原理

核心签到使用 Cookie 直接向 `https://ikuuu.win/user/checkin` 发送 POST 请求完成，无需浏览器。

Cookie 可通过**半自动刷新工具**获取：脚本打开浏览器、自动填表，你手动过人机验证，登录后自动提取 Cookie。配置好 GitHub Token 后，刷新的 Cookie 会**自动回写到 GitHub Actions Secret**，CI 端无需手动更新。

## 使用方式

### 方式一：半自动 Cookie 刷新 + 签到（推荐）

1. **安装依赖**（含 Playwright 浏览器）：
```bash
pip install -r requirements.txt
playwright install chromium
```

2. **配置账户**：在 `config.json` 中填入邮箱和密码：
```json
{
  "email": "your@email.com",
  "password": "your_password",
  "cookie": ""
}
```

3. **刷新 Cookie**（打开浏览器，手动过人机验证）：
```bash
python refresh_cookie.py
```

4. **运行签到**：
```bash
python checkin.py
```

### 方式二：纯手动获取 Cookie

1. **获取 Cookie**：在浏览器登录 ikuuu，打开开发者工具（F12）→ Application → Cookies → `ikuuu.win`，复制所有 Cookie 拼成字符串（格式：`key1=value1; key2=value2`）

2. **配置 Cookie**：在 `config.json` 中填入 Cookie：
```json
{
  "cookie": "email=xxx; session=xxx; ..."
}
```

3. **安装依赖**：
```bash
pip install -r requirements.txt
```

4. **运行签到**：
```bash
python checkin.py
```

### 方式三：GitHub Actions 自动签到

1. **Fork 或推送**本仓库到 GitHub

2. **设置 Secret**：在 GitHub 仓库 Settings → Secrets and variables → Actions → New repository secret：
   - `IKUUU_COOKIE` — 浏览器登录后复制的完整 Cookie 字符串

3. **启用 Actions**：仓库的 Actions 标签页 → 启用

4. 之后每天北京时间 06:00 会自动签到

> **Cookie 过期后**需要重新从浏览器获取并更新 `IKUUU_COOKIE` Secret。

### 方式四：半自动刷新 + 自动回写 Secret（最省心）

把"手动复制 Cookie 到 GitHub 配置页"那一步也省掉：本地刷新 Cookie 后，脚本自动调 GitHub API 把新 Cookie 写入 `IKUUU_COOKIE` Secret，CI 端下次签到自动生效。

**一次性配置：**

1. **创建 GitHub Token**：
   - Classic PAT：勾选 `repo` scope
   - 或 Fine-grained PAT：对本仓库授予 `Actions` (Read and write) 权限
   - 在 https://github.com/settings/tokens 创建

2. **设置环境变量**（不要写进 config.json 明文）：
```bash
# Windows CMD
set GH_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# PowerShell
$env:GH_TOKEN = "ghp_xxxxxxxxxxxxxxxxxxxx"

# Linux/macOS
export GH_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

3. **配置 config.json**（仓库可由 git remote 自动推断，owner/repo 可省略）：
```json
{
  "email": "your@email.com",
  "github": {
    "owner": "viceren",
    "repo": "ikuuu-auto-checkin",
    "token_env": "GH_TOKEN",
    "auto_sync": false,
    "secret_name": "IKUUU_COOKIE"
  }
}
```

> **安全建议**：密码**不要**写进 `config.json`（明文落盘）。改为设置环境变量：
> ```bash
> # Windows CMD
> set IKUUU_PASSWORD=your_password
>
> # PowerShell
> $env:IKUUU_PASSWORD = "your_password"
>
> # Linux/macOS
> export IKUUU_PASSWORD=your_password
> ```
> 或者干脆不配密码——脚本会只填邮箱，密码由你在浏览器里手输。

4. **先自检**（不启动浏览器、不消耗登录，验证 token 与权限是否真的就绪）：
```bash
python sync_secret.py --check
```
```
========================================================
   GitHub 回写链路预检
========================================================

  · 目标仓库: viceren/ikuuu-auto-checkin
  · PyNaCl 依赖: 已安装
  · Token 有效，仓库可访问 ✓
  · 可读取 Actions 公钥（key_id=568250167242549743）✓
  · 具备写入 Secret 的权限 ✓

  ✓ GitHub 回写链路就绪
========================================================
```
若 token 失效/权限不足，会明确指出原因（如 `Token 无效（401）`）并给出修复建议。
这一步能避免"点完验证码才发现推不上去"。

5. **运行刷新**：
```bash
python refresh_cookie.py
```
脚本会在**启动浏览器之前**先做一次链路预检，有问题时提前告警；
登录成功后询问是否回写，确认后 Cookie 自动同步到 GitHub Secret。
设置 `"auto_sync": true` 可跳过确认直接推送。

6. **也可单独推送**已存在 config.json 中的 Cookie：
```bash
python sync_secret.py
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `checkin.py` | 签到主程序（纯 Cookie 签到） |
| `refresh_cookie.py` | 半自动 Cookie 刷新工具（Playwright，需手动过人机验证；登录后可自动回写 GitHub Secret） |
| `sync_secret.py` | GitHub Actions Secret 回写工具（用 PyNaCl 加密后通过 REST API 推送） |
| `config.json` | 本地配置文件（已加入 .gitignore，存账户/Cookie） |
| `config.example.json` | 配置示例 |
| `.github/workflows/checkin.yml` | GitHub Actions 自动签到配置 |
| `FEASIBILITY_ASSESSMENT.md` | 账号密码登录改造可行性评估 |
| `README.md` | 本文件 |

## 常见问题

**Q: 签到返回 "您似乎已经签到过了"**
A: 说明今天已经签到成功，无需重复操作。

**Q: 提示 Cookie 已失效怎么办？**
A: 重新运行 `python refresh_cookie.py` 刷新；若已配置 GH_TOKEN，新 Cookie 会自动回写到 GitHub Secret，CI 下次签到自动生效。若未配置，需手动更新 `config.json` 或 GitHub Secrets 中的 `IKUUU_COOKIE`。

**Q: 如何获取 Cookie？**
A: 浏览器登录 ikuuu → F12 打开开发者工具 → Application（应用）标签 → 左侧 Cookies → `ikuuu.win` → 选中所有条目，拼成 `name1=value1; name2=value2` 格式的字符串。

**Q: 为什么不做成完全自动登录？**
A: ikuuu 使用 Cloudflare Turnstile 行为验证，不是图像验证码（OCR 无效），且 GitHub Actions 数据中心 IP 被 Cloudflare 风控，纯自动化在 CI 不可行。详见 `FEASIBILITY_ASSESSMENT.md`。本地半自动 + Secret 回写是最务实的折中方案。

**Q: GitHub Token 权限报错？**
A: 写入 Actions Secret 需要对仓库有写入权限。Classic PAT 至少勾选 `repo`；Fine-grained PAT 需授予 `Actions` (Read and write)。若仓库是 fork 来的，还需在 fork 仓库 Settings → Actions → General 开启 workflow 权限。

