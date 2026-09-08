"""
GitHub Actions Secret 回写工具

把刷新后的 Cookie 推送到仓库的 IKUUU_COOKIE Secret，
免去手动登录 GitHub Actions 配置页粘贴 Cookie 的步骤。

权限要求（任选其一）：
  - Classic PAT：勾选 `repo` scope
  - Fine-grained PAT：对该仓库授予 `Actions` (Read and write) 权限

配置来源（按优先级）：
  1. 环境变量 GH_TOKEN / GITHUB_TOKEN
  2. config.json 的 github.token_env 指向的环境变量名
  3. config.json 的 github.token 明文（不推荐）

仓库来源（按优先级）：
  1. config.json 的 github.owner + github.repo
  2. 环境变量 GH_REPOSITORY（格式 "owner/repo"）
  3. 环境变量 GITHUB_REPOSITORY（GitHub Actions 内置）
  4. 从本地 git remote origin 自动推断
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger("sync_secret")

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
GITHUB_API = "https://api.github.com"
DEFAULT_SECRET_NAME = "IKUUU_COOKIE"


# ═══════════════════════════════════════════════════════════
#  配置解析
# ═══════════════════════════════════════════════════════════


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("读取 config.json 失败: %s", e)
    return {}


def get_token(config: dict) -> Optional[str]:
    """按优先级解析 GitHub token。"""
    # 1. 标准环境变量
    for env_name in ("GH_TOKEN", "GITHUB_TOKEN"):
        val = os.environ.get(env_name, "").strip()
        if val:
            return val

    gh_cfg = config.get("github", {}) or {}
    # 2. token_env 指向另一个环境变量
    token_env = gh_cfg.get("token_env")
    if token_env:
        val = os.environ.get(token_env, "").strip()
        if val:
            return val

    # 3. 明文 token（不推荐，但允许）
    plain = gh_cfg.get("token", "").strip()
    if plain:
        return plain

    return None


def get_repo_slug(config: dict) -> Optional[tuple[str, str]]:
    """解析 owner/repo。返回 (owner, repo) 或 None。"""
    gh_cfg = config.get("github", {}) or {}

    # 1. config 显式指定
    owner = gh_cfg.get("owner", "").strip()
    repo = gh_cfg.get("repo", "").strip()
    if owner and repo:
        return owner, repo

    # 2. 环境变量 GH_REPOSITORY
    slug = os.environ.get("GH_REPOSITORY", "").strip()
    if "/" in slug:
        o, r = slug.split("/", 1)
        if o and r:
            return o, r

    # 3. GitHub Actions 内置变量
    slug = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" in slug:
        o, r = slug.split("/", 1)
        if o and r:
            return o, r

    # 4. 从 git remote origin 推断
    return _infer_from_git_remote()


def _infer_from_git_remote() -> Optional[tuple[str, str]]:
    """从本地 git remote origin URL 推断 owner/repo。"""
    patterns = [
        # git@github.com:owner/repo.git
        re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
        # https://github.com/owner/repo.git
        re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
    ]
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(Path(__file__).resolve().parent),
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    url = result.stdout.strip()
    for pat in patterns:
        m = pat.search(url)
        if m:
            return m.group("owner"), m.group("repo")
    return None


# ═══════════════════════════════════════════════════════════
#  GitHub API 调用（用标准库 urllib，避免引入 requests 依赖）
# ═══════════════════════════════════════════════════════════


def _api_request(
    method: str,
    path: str,
    token: str,
    body: Optional[dict] = None,
) -> tuple[int, dict, bytes]:
    """调用 GitHub API。返回 (status, json_headers, raw_body)。"""
    url = f"{GITHUB_API}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ikuuu-auto-checkin/sync_secret",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(req, timeout=20) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _get_repo_public_key(owner: str, repo: str, token: str) -> tuple[str, str]:
    """获取仓库 Actions 公钥。返回 (key_id, base64_public_key)。"""
    status, _, body = _api_request(
        "GET", f"/repos/{owner}/{repo}/actions/secrets/public-key", token
    )
    if status != 200:
        raise RuntimeError(
            f"获取仓库公钥失败 (HTTP {status}): {body.decode('utf-8', 'replace')[:200]}"
        )
    data = json.loads(body)
    return str(data["key_id"]), str(data["key"])


def _encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    """用仓库公钥（libsodium sealed box）加密 secret，返回 base64。"""
    try:
        from nacl import encoding, public
    except ImportError as e:
        raise RuntimeError(
            "缺少 PyNaCl 依赖，请运行: pip install pynacl"
        ) from e

    pub_key = public.PublicKey(
        public_key_b64.encode("utf-8"), encoding.Base64Encoder()
    )
    sealed_box = public.SealedBox(pub_key)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def _put_secret(
    owner: str,
    repo: str,
    secret_name: str,
    encrypted_b64: str,
    key_id: str,
    token: str,
) -> None:
    """写入（或更新）仓库 Secret。"""
    body = {"encrypted_value": encrypted_b64, "key_id": key_id}
    status, _, resp_body = _api_request(
        "PUT", f"/repos/{owner}/{repo}/actions/secrets/{secret_name}", token, body
    )
    # 201 = 新建，204 = 更新
    if status not in (201, 204):
        raise RuntimeError(
            f"写入 Secret 失败 (HTTP {status}): "
            f"{resp_body.decode('utf-8', 'replace')[:300]}"
        )


# ═══════════════════════════════════════════════════════════
#  对外入口
# ═══════════════════════════════════════════════════════════


def push_cookie_secret(
    cookie_str: str,
    config: Optional[dict] = None,
    secret_name: str = DEFAULT_SECRET_NAME,
) -> tuple[bool, str]:
    """把 Cookie 推送到 GitHub Secret。

    Args:
        cookie_str: 完整 Cookie 字符串
        config: 已加载的配置字典（None 时自动读取 config.json）
        secret_name: Secret 名称，默认 IKUUU_COOKIE

    Returns:
        (success, message)
    """
    if not cookie_str or not cookie_str.strip():
        return False, "Cookie 为空"

    if config is None:
        config = load_config()

    token = get_token(config)
    if not token:
        return False, (
            "未配置 GitHub Token。请设置环境变量 GH_TOKEN，"
            "或在 config.json 的 github.token_env 中指定 token 所在的环境变量名"
        )

    slug = get_repo_slug(config)
    if not slug:
        return False, (
            "无法确定目标仓库。请在 config.json 配置 github.owner + github.repo，"
            "或设置环境变量 GH_REPOSITORY='owner/repo'"
        )
    owner, repo = slug

    logger.info("目标仓库: %s/%s", owner, repo)

    # 1. 获取仓库公钥
    logger.info("获取仓库 Actions 公钥...")
    try:
        key_id, pub_key_b64 = _get_repo_public_key(owner, repo, token)
    except RuntimeError as e:
        return False, str(e)

    # 2. 加密 Cookie
    logger.info("加密 Secret...")
    try:
        encrypted_b64 = _encrypt_secret(pub_key_b64, cookie_str)
    except RuntimeError as e:
        return False, str(e)

    # 3. 写入 Secret
    logger.info("写入 Secret: %s", secret_name)
    try:
        _put_secret(owner, repo, secret_name, encrypted_b64, key_id, token)
    except RuntimeError as e:
        return False, str(e)

    return True, f"✓ 已将 Cookie 推送到 {owner}/{repo} 的 Secret `{secret_name}`"


def check_prerequisites(config: Optional[dict] = None) -> tuple[bool, str]:
    """快速检查回写前置条件是否就绪（只查本地配置，不发网络请求）。

    返回 (ready, hint)。
    """
    if config is None:
        config = load_config()

    if not get_token(config):
        return False, "未配置 GitHub Token（环境变量 GH_TOKEN）"

    if not get_repo_slug(config):
        return False, "未配置目标仓库（config.json github.owner/repo 或 GH_REPOSITORY）"

    try:
        import nacl  # noqa: F401
    except ImportError:
        return False, "缺少 PyNaCl 依赖（pip install pynacl）"

    return True, "前置条件就绪"


def verify_github_access(config: Optional[dict] = None) -> tuple[bool, str, list[str]]:
    """真实调用 GitHub API 验证 token 有效性与仓库 secrets 权限。

    比 check_prerequisites 更进一步：会实际发请求，确认
      1. token 能被 GitHub 接受（未过期/未撤销）
      2. 能访问目标仓库
      3. 能读取仓库 Actions 公钥 —— 这是写入 Secret 的必要条件
         （不具备时通常意味着 token 缺少 repo / Actions 写权限）

    注意：本函数只读取，不写入任何数据。

    Returns:
        (ok, 结论消息, 明细行列表)
    """
    details: list[str] = []
    if config is None:
        config = load_config()

    token = get_token(config)
    if not token:
        return False, "未配置 GitHub Token", ["设置环境变量 GH_TOKEN，或 config.json 的 github.token_env"]

    slug = get_repo_slug(config)
    if not slug:
        return False, "无法确定目标仓库", ["配置 config.json 的 github.owner/repo，或 GH_REPOSITORY"]
    owner, repo = slug
    details.append(f"目标仓库: {owner}/{repo}")

    try:
        import nacl  # noqa: F401
        details.append("PyNaCl 依赖: 已安装")
    except ImportError:
        return False, "缺少 PyNaCl 依赖", ["运行: pip install pynacl"]

    # 1) 验证 token + 仓库可达
    status, _, body = _api_request("GET", f"/repos/{owner}/{repo}", token)
    if status == 401:
        return False, "Token 无效（401：已过期或被撤销）", ["重新生成 PAT 并更新 GH_TOKEN"]
    if status == 403:
        return False, "Token 无权访问该仓库（403）", ["确认 PAT 具备 repo 权限（或 Actions 读写）"]
    if status == 404:
        return False, f"仓库不存在或无权访问（404）: {owner}/{repo}", ["检查 owner/repo 拼写"]
    if status != 200:
        return False, f"仓库访问失败（HTTP {status}）", [body.decode("utf-8", "replace")[:200]]
    details.append("Token 有效，仓库可访问 ✓")

    # 2) 读取 Actions 公钥 —— 写入 Secret 的必要条件
    status, _, body = _api_request(
        "GET", f"/repos/{owner}/{repo}/actions/secrets/public-key", token
    )
    if status == 200:
        data = json.loads(body)
        details.append(f"可读取 Actions 公钥（key_id={data.get('key_id')}）✓")
        details.append("具备写入 Secret 的权限 ✓")
        return True, "GitHub 回写链路就绪", details
    if status in (401, 403):
        return False, (
            "Token 缺少写入 Actions Secret 的权限（HTTP %d）" % status
        ), [
            "Classic PAT 需勾选 repo；Fine-grained PAT 需授予 Actions(读写)",
            "另确认仓库 Settings → Actions → Workflow permissions 为 Read and write",
        ]
    return False, f"读取仓库公钥失败（HTTP {status}）", [body.decode("utf-8", "replace")[:200]]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="GitHub Actions Secret 回写工具",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只做配置与权限预检（真实调用 GitHub API，不写入任何数据）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = load_config()

    if args.check:
        print()
        print("=" * 56)
        print("   GitHub 回写链路预检")
        print("=" * 56)
        print()
        ok, msg, details = verify_github_access(cfg)
        for line in details:
            print(f"  · {line}")
        print()
        if ok:
            print(f"  ✓ {msg}")
            print()
            print("  可以正常使用 refresh_cookie.py 的自动回写功能。")
        else:
            print(f"  ✗ {msg}")
            print()
            print("  修复建议：")
            for tip in details:
                print(f"    - {tip}")
            print()
            print("  本地 Cookie 刷新仍可使用，只是不会自动回写到 GitHub。")
        print("=" * 56)
        print()
        raise SystemExit(0 if ok else 1)

    # 默认：读取 config.json 的 cookie 字段并推送
    cookie = cfg.get("cookie", "")
    if not cookie:
        logger.error("config.json 中没有 cookie 字段")
        raise SystemExit(1)

    ok, msg = push_cookie_secret(cookie, cfg)
    if ok:
        logger.info(msg)
    else:
        logger.error(msg)
        raise SystemExit(1)
