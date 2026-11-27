"""
ikuuu 极验 v4 解题器 — 题型检测 / 视觉识别 / 人类轨迹点击

定位
----
供 ``auto_login.py`` 在本机（住宅 IP）自动登录时调用。**不要放进 GitHub Actions**：
云端 runner 跑不动视觉模型，且数据中心 IP 评分更差。

支持范围
--------
- ✅ 九宫格语义点选（``选 3 个符合右图的图片``）：本模块的主力题型，实测可一轮通过。
- ⚠️ 文字点选（``请在下图依次点击``）：靠 VLM 给出归一化坐标，精度有限；失败会明确报错。
- ❌ 滑块：未实现（ikuuu 上暂未观察到）。

VLM 可插拔（环境变量）
----------------------
    IKUUU_VLM_PROVIDER   ollama | openai | manual | auto（默认 auto）
    IKUUU_VLM_MODEL      模型名，如 qwen2.5vl:3b / gpt-4o-mini
    IKUUU_VLM_BASE_URL   Ollama 默认 http://127.0.0.1:11434；OpenAI 兼容端点填完整 base
    IKUUU_VLM_API_KEY    OpenAI 兼容端点用；未设时回退 OPENAI_API_KEY
    IKUUU_VLM_TIMEOUT    单次识别超时秒数，默认 300（核显跑 3B 需要耐心）

auto 的选择顺序：Ollama 在跑 → 用 Ollama；有 OPENAI_API_KEY → 用 OpenAI 兼容；
否则 manual（截图 + 终端输入序号）。

注意：本模块是**与站点风控对抗**的实现，违反 ikuuu 的服务条款，存在封号风险。
仅用于账号所有者在本机维护自己的签到。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("captcha_solver")

# ── 页面结构（2026-11-27 实测）──────────────────────────────────────────────
# 「点我开始验证」按钮
GEETEST_BTN_SEL = ".geetest_btn_click"
# 题干在 text_tips / title；.geetest_tip 只是状态条（出题时仍显示"验证中"）
GEETEST_STATE_JS = """() => {
    const vis = (s) => { const e = document.querySelector(s); if (!e) return false;
        const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const txt = (s) => { const e = document.querySelector(s);
        return e ? (e.innerText || '').trim() : ''; };
    const C = window.Captcha || {};
    // 注意：.geetest_item 在 ikuuu 上是不可见占位层（bbox 常为 0），
    // 真正可见且带背景图的是 .geetest_imgs0..8，优先用它取坐标。
    const items = [];
    for (let i = 0; i < 9; i++) {
        const el = document.querySelector('.geetest_imgs' + i)
                   || document.querySelectorAll('.geetest_item')[i];
        if (!el) continue;
        const r = el.getBoundingClientRect();
        items.push({i, x: Math.round(r.x), y: Math.round(r.y),
                    w: Math.round(r.width), h: Math.round(r.height)});
    }
    // 9 格图片是 CSS background-image（异步加载）。直接拿 URL 下载原图，
    // 可以绕过"截图时图片还没渲染完"导致的空白/错乱。
    const item_urls = [];
    for (let i = 0; i < 9; i++) {
        const el = document.querySelector('.geetest_imgs' + i);
        if (!el) { item_urls.push(''); continue; }
        const bg = getComputedStyle(el).backgroundImage || '';
        const m = bg.match(/url\\(["']?(.*?)["']?\\)/);
        item_urls.push(m ? m[1] : '');
    }
    const win = document.querySelector('.geetest_window');
    const wr = win ? win.getBoundingClientRect() : null;
    return {
        ready: typeof C.isReady === 'function' ? !!C.isReady() : false,
        window: vis('.geetest_window'),
        question: txt('.geetest_text_tips') || txt('.geetest_title') || txt('.geetest_ques_tips'),
        nine: vis('.geetest_nine') || items.length > 0,
        slide: vis('.geetest_slice_bg') || vis('.geetest_arrow'),
        items: items,
        item_urls: item_urls,
        window_box: wr ? {x: Math.round(wr.x), y: Math.round(wr.y),
                          w: Math.round(wr.width), h: Math.round(wr.height)} : null,
    };
}"""

SCREENSHOT_DIR = Path(__file__).resolve().parent / ".captcha_shots"
CLIP_MODEL_PATH = (Path(__file__).resolve().parent / "models"
                   / "clip_vit_b32_vision_int8.onnx")
CLIP_FALLBACK_MODEL_PATH = (Path(__file__).resolve().parent / "models"
                            / "mobileclip_s0_vision_int8.onnx")
GRID_PICKS = 3          # 九宫格要求选几个（题干含"选 N 个"时以题干为准）
SOLVE_ROUNDS = 4        # 最多几轮（极验可能连续出题）


# ═══════════════════════════════════════════════════════════
#  数据结构
# ═══════════════════════════════════════════════════════════


@dataclass
class Item:
    i: int
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


@dataclass
class Challenge:
    ready: bool = False
    kind: str = "none"          # none | nine | text | slide | unknown
    question: str = ""
    items: list[Item] = field(default_factory=list)
    item_urls: list[str] = field(default_factory=list)
    window_box: Optional[dict] = None


def _kind_of(state: dict) -> str:
    if state.get("ready"):
        return "none"
    if state.get("slide"):
        return "slide"
    if state.get("nine"):
        return "nine"
    if state.get("window"):
        return "text"
    return "unknown"


async def detect(page) -> Challenge:
    """读取当前极验状态。"""
    state = await page.evaluate(GEETEST_STATE_JS)
    return Challenge(
        ready=bool(state.get("ready")),
        kind=_kind_of(state),
        question=(state.get("question") or "").strip(),
        items=[Item(**it) for it in state.get("items") or []],
        item_urls=list(state.get("item_urls") or []),
        window_box=state.get("window_box"),
    )


async def _find_reference_box(page) -> tuple[int, int, int, int] | None:
    """定位题干里的示例小图（位于九宫格上方、小尺寸、带背景图或 img）。

    找不到返回 None，ClipProvider 会退回「9 格聚类」策略。
    """
    try:
        box = await page.evaluate("""() => {
            const items = [...document.querySelectorAll('.geetest_item')];
            if (!items.length) return null;
            const minY = Math.min(...items.map(e => e.getBoundingClientRect().y));
            const cands = [];
            for (const e of document.querySelectorAll('*')) {
                const r = e.getBoundingClientRect();
                if (r.width < 18 || r.height < 18 || r.width > 180 || r.height > 180) continue;
                if (r.y > minY - 5) continue;
                const bg = getComputedStyle(e).backgroundImage || '';
                const src = e.getAttribute && e.getAttribute('src');
                if (!((bg && bg !== 'none') || src)) continue;
                const cls = (e.className || '').toString();
                const pri = /ques/i.test(cls) ? 2 : (/item|tips|title/i.test(cls) ? 1 : 0);
                cands.push({x: r.x, y: r.y, w: r.width, h: r.height,
                            pri, gap: minY - r.y});
            }
            if (!cands.length) return null;
            cands.sort((a, b) => (b.pri - a.pri) || (a.gap - b.gap));
            const b = cands[0];
            return {x: Math.round(b.x), y: Math.round(b.y),
                    w: Math.round(b.w), h: Math.round(b.h)};
        }""")
        if box and box.get("w", 0) >= 18 and box.get("h", 0) >= 18:
            logger.debug("示例图位置: %s", box)
            return (box["x"], box["y"], box["w"], box["h"])
    except Exception:  # noqa: BLE001
        pass
    return None


def wanted_picks(question: str, default: int = GRID_PICKS) -> int:
    """从题干里读「选 N 个」，读不到用默认值。"""
    m = re.search(r"选\s*(\d+)\s*个", question or "")
    if m:
        try:
            return max(1, min(9, int(m.group(1))))
        except ValueError:
            pass
    return default


# ═══════════════════════════════════════════════════════════
#  拼图（给 VLM 一张带编号的清晰大图）
# ═══════════════════════════════════════════════════════════


def _load_font(size: int):
    from PIL import ImageFont

    for path in (
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build_grid_image(screenshot_path: str, ch: Challenge, out_path: str,
                     scale: int = 2) -> str:
    """把九宫格区域裁剪放大，并在每格左上角标注索引号。

    整张 1280×900 截图直接给模型，单格只有 92×80，容易看错；
    裁剪到验证码区域再放大 2 倍，编号也画在格子上，识别率明显更高。
    """
    from PIL import Image, ImageDraw

    im = Image.open(screenshot_path).convert("RGB")
    if not ch.items:
        im.save(out_path)
        return out_path

    x0 = max(0, min(it.x for it in ch.items) - 24)
    y0 = max(0, min(it.y for it in ch.items) - 110)   # 留出题干与示例图
    x1 = min(im.width, max(it.x + it.w for it in ch.items) + 24)
    y1 = min(im.height, max(it.y + it.h for it in ch.items) + 24)

    crop = im.crop((x0, y0, x1, y1))
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    draw = ImageDraw.Draw(crop)
    font = _load_font(18)

    for it in ch.items:
        bx = (it.x - x0) * scale
        by = (it.y - y0) * scale
        draw.rectangle([bx + 2, by + 2, bx + 30, by + 26], fill=(0, 0, 0))
        draw.text((bx + 7, by + 4), str(it.i), fill=(255, 255, 0), font=font)

    crop.save(out_path)
    return out_path


def _grid_prompt(question: str, n_items: int, k: int) -> str:
    return (
        "这是一张极验九宫格验证码的截图。每个格子的左上角用黑底黄字标注了编号 "
        f"0 到 {n_items - 1}。\n"
        f"题目：{question}\n"
        "请找出符合题目要求的格子（通常是与示例图属于同一类物体，注意排除外形相似但类别不同的干扰项）。\n"
        f"只输出 JSON，格式：{{\"picks\": [编号, ...]}}，需要 {k} 个编号，不要任何解释。"
    )


def parse_picks(text: str, n_items: int, k: int) -> list[int]:
    """从模型输出里稳健地提取索引列表。"""
    candidates: list[list[int]] = []

    # 1) 优先找 JSON 对象
    for m in re.finditer(r"\{[^{}]*\"?picks\"?\s*:\s*\[([^\]]*)\]", text or "", re.I):
        candidates.append([int(x) for x in re.findall(r"\d+", m.group(1))])
    # 2) 再找裸数组
    if not candidates:
        for m in re.finditer(r"\[([\d,\s]+)\]", text or ""):
            candidates.append([int(x) for x in re.findall(r"\d+", m.group(1))])
    # 3) 最后退化为全文数字
    if not candidates:
        nums = [int(x) for x in re.findall(r"\d+", text or "")]
        candidates.append(nums)

    for cand in candidates:
        seen: list[int] = []
        for n in cand:
            if 0 <= n < n_items and n not in seen:
                seen.append(n)
        if len(seen) >= k:
            return seen[:k]
    raise ValueError(f"模型未给出 {k} 个合法索引：{(text or '')[:120]!r}")


# ═══════════════════════════════════════════════════════════
#  VLM providers
# ═══════════════════════════════════════════════════════════


class VLMProvider:
    name = "base"

    def solve_grid(self, image_path: str, question: str, n_items: int, k: int) -> list[int]:
        raise NotImplementedError

    def solve_text(self, image_path: str, question: str) -> list[tuple[float, float]]:
        raise NotImplementedError

    def __str__(self) -> str:
        return self.name


class OllamaProvider(VLMProvider):
    """本地 Ollama（零 API 费用，核显下较慢）。"""

    def __init__(self) -> None:
        self.base = (os.environ.get("IKUUU_VLM_BASE_URL")
                     or "http://127.0.0.1:11434").rstrip("/")
        self.model = os.environ.get("IKUUU_VLM_MODEL") or "qwen2.5vl:3b"
        self.timeout = int(os.environ.get("IKUUU_VLM_TIMEOUT", "300"))
        self.name = f"ollama:{self.model}"

    def _generate(self, prompt: str, image_path: str) -> str:
        import requests

        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        resp = requests.post(
            f"{self.base}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "images": [b64],
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return str(resp.json().get("response", ""))

    def solve_grid(self, image_path, question, n_items, k, context=None):
        return parse_picks(
            self._generate(_grid_prompt(question, n_items, k), image_path), n_items, k
        )

    def solve_text(self, image_path, question):
        prompt = (
            f"题目：{question}\n"
            "请按题目要求的顺序，给出每个目标字在图片中的中心位置，坐标用 0~1 的归一化值。\n"
            '只输出 JSON：{"points": [[x1,y1],[x2,y2],...]}'
        )
        text = self._generate(prompt, image_path)
        return parse_points(text)


class OpenAIProvider(VLMProvider):
    """任意 OpenAI 兼容端点（云端 API，或本机 LM Studio / vLLM）。"""

    def __init__(self) -> None:
        self.model = os.environ.get("IKUUU_VLM_MODEL") or "gpt-4o-mini"
        self.base = os.environ.get("IKUUU_VLM_BASE_URL") or None
        self.key = (os.environ.get("IKUUU_VLM_API_KEY")
                    or os.environ.get("OPENAI_API_KEY") or "")
        self.timeout = int(os.environ.get("IKUUU_VLM_TIMEOUT", "120"))
        self.name = f"openai:{self.model}"

    def _chat(self, prompt: str, image_path: str) -> str:
        from openai import OpenAI

        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        client = OpenAI(api_key=self.key, base_url=self.base, timeout=self.timeout)
        resp = client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }],
        )
        return resp.choices[0].message.content or ""

    def solve_grid(self, image_path, question, n_items, k, context=None):
        return parse_picks(
            self._chat(_grid_prompt(question, n_items, k), image_path), n_items, k
        )

    def solve_text(self, image_path, question):
        prompt = (
            f"题目：{question}\n"
            "请按题目要求的顺序，给出每个目标字在图片中的中心位置，坐标用 0~1 的归一化值。\n"
            '只输出 JSON：{"points": [[x1,y1],[x2,y2],...]}'
        )
        return parse_points(self._chat(prompt, image_path))


class ManualProvider(VLMProvider):
    """人工兜底：终端里看截图、输编号。定时任务场景不适合。"""

    name = "manual"

    def solve_grid(self, image_path, question, n_items, k, context=None):
        print(f"\n[人工] 题目：{question}")
        print(f"[人工] 请打开截图：{image_path}")
        while True:
            try:
                raw = input(f"[人工] 输入 {k} 个编号（逗号分隔，0-{n_items - 1}）：").strip()
            except EOFError:
                raise ValueError("无人值守环境下无法使用 manual provider") from None
            picks = [int(x) for x in re.findall(r"\d+", raw)]
            if len({p for p in picks if 0 <= p < n_items}) >= k:
                return list(dict.fromkeys(p for p in picks if 0 <= p < n_items))[:k]
            print("[人工] 输入不合法，重试")

    def solve_text(self, image_path, question):
        raise ValueError("文字点选题型不支持人工兜底（请直接手动刷新 Cookie）")


class FileProvider(VLMProvider):
    """集成测试用：从固定文件读答案，验证"检测→点击→通过"链路。"""

    name = "file"

    def solve_grid(self, image_path, question, n_items, k, context=None):
        path = Path(os.environ.get("IKUUU_TEST_ANSWER", ""))
        wait = int(os.environ.get("IKUUU_TEST_WAIT", "0"))
        deadline = time.time() + wait
        while not path.exists() and time.time() < deadline:
            time.sleep(0.5)
        if not path.exists():
            raise ValueError(f"IKUUU_TEST_ANSWER 不存在: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return parse_picks(json.dumps(data, ensure_ascii=False), n_items, k)

    def solve_text(self, image_path, question):
        raise ValueError("测试 provider 不支持文字点选")


class ClipProvider(VLMProvider):
    """本地轻量视觉相似度：MobileCLIP-S0 int8（约 12MB）+ onnxruntime。

    不依赖大模型或任何 API。两种策略：
      1) 定位到示例图 → 与 9 格逐一算余弦相似度，取 Top-K；
      2) 定位不到 → 在 9 格中找「彼此最相似的 K 个」（聚类兜底）。
    """

    name = "clip"

    def __init__(self) -> None:
        import onnxruntime as ort

        if CLIP_MODEL_PATH.exists():
            model, self.image_size, self.normalize = CLIP_MODEL_PATH, 224, True
            self.name = "clip:vit-b32"
        elif CLIP_FALLBACK_MODEL_PATH.exists():
            # 11M 小模型：能跑但判别力明显不足（实测把矿石/人像/船聚成一类），仅作兜底
            model, self.image_size, self.normalize = (
                CLIP_FALLBACK_MODEL_PATH, 256, False
            )
            self.name = "clip:mobileclip_s0(弱)"
        else:
            raise RuntimeError(
                f"缺少视觉模型。请下载（约 85MB）：\n"
                "  mkdir -p models\n"
                "  curl -L -o models/clip_vit_b32_vision_int8.onnx "
                "https://hf-mirror.com/Xenova/clip-vit-base-patch32/resolve/main/"
                "onnx/vision_model_int8.onnx"
            )
        self.session = ort.InferenceSession(
            str(model), providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name

    def _preprocess(self, im):
        import numpy as np
        from PIL import Image

        size = self.image_size
        w, h = im.size
        if w <= 0 or h <= 0:
            raise ValueError(f"图片尺寸无效: {im.size}")
        scale = size / min(w, h)
        resample = (Image.Resampling.BICUBIC if self.normalize
                    else Image.Resampling.BILINEAR)
        im = im.resize((max(size, round(w * scale)), max(size, round(h * scale))),
                       resample)
        left = (im.width - size) // 2
        top = (im.height - size) // 2
        im = im.crop((left, top, left + size, top + size))
        arr = np.asarray(im, dtype=np.float32) / 255.0
        if self.normalize:   # CLIP 标准归一化；MobileCLIP 不做
            mean = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
            std = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
            arr = (arr - mean) / std
        return arr.transpose(2, 0, 1)[None, ...]

    def _embed(self, im):
        out = self.session.run(None, {self.input_name: self._preprocess(im)})[0]
        v = out[0]
        norm = float((v * v).sum() ** 0.5) or 1.0
        return v / norm

    @staticmethod
    def _cos(a, b) -> float:
        import numpy as np

        return float(np.dot(a, b))

    def solve_grid(self, image_path, question, n_items, k, context=None):
        from io import BytesIO
        from itertools import combinations

        import requests
        from PIL import Image

        if not context or not context.get("screenshot"):
            raise ValueError("ClipProvider 需要 context（原截图与格子坐标）")

        items = context["items"]
        urls = context.get("item_urls") or []
        cells = [p for p in (context.get("cell_paths") or []) if p]
        if items and len(cells) == len(items):
            # 最优：逐格元素截图，真实反映每格渲染内容
            images = [Image.open(p).convert("RGB") for p in cells]
            logger.info("CLIP 使用逐格元素截图（%d 格）", len(images))
        elif items and len(urls) >= len(items) and all(urls[:len(items)]):
            # 次优：下载极验的格子图 URL
            images = []
            for u in urls[:len(items)]:
                resp = requests.get(u, timeout=15)
                resp.raise_for_status()
                images.append(Image.open(BytesIO(resp.content)).convert("RGB"))
            logger.info("CLIP 使用 DOM 原图（%d 张）", len(images))
        else:
            im = Image.open(context["screenshot"]).convert("RGB")
            if any(w <= 2 or h <= 2 for (x, y, w, h) in items):
                raise ValueError("格子布局未就绪，且拿不到图片 URL")
            images = [im.crop((x, y, x + w, y + h)) for (x, y, w, h) in items]
            logger.info("CLIP 使用截图裁剪（%d 格）", len(images))

        embs = [self._embed(img) for img in images]

        ref = context.get("reference_box")
        # 聚类实测已足够可靠；示例图定位不稳定，默认不用，除非显式开启
        if ref and os.environ.get("IKUUU_CLIP_USE_REF") == "1":
            rx, ry, rw, rh = ref
            ref_emb = self._embed(im.crop((rx, ry, rx + rw, ry + rh)))
            sims = [self._cos(ref_emb, e) for e in embs]
            order = sorted(range(len(sims)), key=lambda i: sims[i], reverse=True)
            logger.info("CLIP 示例图相似度: %s",
                        ", ".join(f"{i}:{sims[i]:.3f}" for i in order))
            return order[:k]

        # 兜底：9 格中找彼此最相似的 k 个
        best_score, best_combo = -9.9, list(range(k))
        for combo in combinations(range(len(embs)), k):
            pairs = list(combinations(combo, 2))
            score = sum(self._cos(embs[i], embs[j]) for i, j in pairs) / len(pairs)
            if score > best_score:
                best_score, best_combo = score, list(combo)
        logger.info("CLIP 聚类兜底: %s（平均相似度 %.3f）", best_combo, best_score)
        return best_combo


def parse_points(text: str) -> list[tuple[float, float]]:
    """解析归一化坐标。"""
    for m in re.finditer(r"\{\s*\"?points\"?\s*:\s*\[([^\]]*(?:\][^\]]*)*?)\]\s*\}", text or "", re.I):
        pts = re.findall(r"\[\s*([\d.]+)\s*,\s*([\d.]+)\s*\]", m.group(1))
        if pts:
            return [(float(a), float(b)) for a, b in pts]
    pts = re.findall(r"\[\s*(0?\.\d+|\d)\s*,\s*(0?\.\d+|\d)\s*\]", text or "")
    return [(float(a), float(b)) for a, b in pts]


def _ollama_alive(base: str = "http://127.0.0.1:11434") -> bool:
    try:
        import requests

        r = requests.get(f"{base.rstrip('/')}/api/tags", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def create_provider() -> VLMProvider:
    """按环境变量或自动探测选择 provider。

    auto 顺序：本地 CLIP（最轻）→ Ollama → OpenAI 兼容 → 人工。
    """
    forced = (os.environ.get("IKUUU_VLM_PROVIDER") or "auto").strip().lower()
    if forced == "manual":
        return ManualProvider()
    if forced == "file":
        return FileProvider()
    if forced == "clip":
        return ClipProvider()
    if forced == "ollama":
        return OllamaProvider()
    if forced in ("openai", "api"):
        return OpenAIProvider()
    if forced not in ("", "auto"):
        logger.warning("未知 IKUUU_VLM_PROVIDER=%s，回退 auto", forced)

    if CLIP_MODEL_PATH.exists():
        return ClipProvider()
    if _ollama_alive():
        return OllamaProvider()
    if os.environ.get("IKUUU_VLM_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        return OpenAIProvider()
    logger.warning("未发现本地 CLIP 模型 / Ollama / API key，回退人工模式（manual）")
    return ManualProvider()


# ═══════════════════════════════════════════════════════════
#  人类轨迹点击
# ═══════════════════════════════════════════════════════════


async def click_human(page, tx: float, ty: float) -> None:
    """贝塞尔曲线移动 + 抖动 + 变速 + 按下/抬起。"""
    sx, sy = random.uniform(200, 1000), random.uniform(200, 650)
    await page.mouse.move(sx, sy)
    await asyncio.sleep(random.uniform(0.1, 0.25))
    cx = (sx + tx) / 2 + random.uniform(-110, 110)
    cy = (sy + ty) / 2 + random.uniform(-80, 80)
    steps = random.randint(24, 38)
    for k in range(1, steps + 1):
        t = k / steps
        await page.mouse.move(
            (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t * t * tx + random.uniform(-1.2, 1.2),
            (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t * t * ty + random.uniform(-1.2, 1.2),
        )
        await asyncio.sleep(random.uniform(0.005, 0.018))
    await asyncio.sleep(random.uniform(0.1, 0.25))
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.05, 0.12))
    await page.mouse.up()


# ═══════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════


async def solve_challenge(page, provider: VLMProvider,
                          max_rounds: int = SOLVE_ROUNDS) -> tuple[bool, str]:
    """在已触发极验的页面上解题，直到 isReady 或失败。

    Returns:
        (是否通过, 说明)
    """
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    # 极验出题后有展开动画，元素 bbox 在 2~3 秒后才稳定（实测 2.5s 后完全正常）。
    # 过早读取会拿到 bbox=0，导致点击落到左上角。
    await asyncio.sleep(2.5)
    last_msg = "未检测到题目"

    for rnd in range(1, max_rounds + 1):
        ch = await detect(page)
        if ch.ready:
            return True, f"极验已放行（第 {rnd} 轮前）"
        if ch.kind == "unknown":
            await asyncio.sleep(1.5)
            continue

        # 出题动画期间 .geetest_item 可能还没完成布局（bbox 全 0），等它稳定
        if ch.kind == "nine":
            for _ in range(10):
                if ch.items and all(it.w > 2 and it.h > 2 for it in ch.items):
                    break
                await asyncio.sleep(0.5)
                ch = await detect(page)
            if not ch.items or any(it.w <= 2 or it.h <= 2 for it in ch.items):
                # 兜底：bbox 读不到时，用 .geetest_nine 容器按 3x3 等分推算点击坐标
                nb = await page.evaluate("""() => {
                    const e = document.querySelector('.geetest_nine');
                    if (!e) return null;
                    const r = e.getBoundingClientRect();
                    return {x: r.x, y: r.y, w: r.width, h: r.height};
                }""")
                if nb and nb["w"] > 30 and nb["h"] > 30:
                    cw, chh = nb["w"] / 3, nb["h"] / 3
                    ch.items = [
                        Item(i, round(nb["x"] + (i % 3) * cw),
                             round(nb["y"] + (i // 3) * chh),
                             round(cw), round(chh))
                        for i in range(9)
                    ]
                    logger.info("bbox 无效，改用 .geetest_nine 等分推算坐标")
                else:
                    last_msg = f"第 {rnd} 轮九宫格布局异常（格子尺寸无效）"
                    logger.warning(last_msg)
                    continue

        shot = str(SCREENSHOT_DIR / f"challenge_r{rnd}_{int(time.time())}.png")
        await page.screenshot(path=shot)

        if ch.kind == "nine":
            # 逐格元素截图：反映实际渲染结果，可绕开雪碧图/整页截图时序问题
            cell_paths: list[str] = []
            for it in ch.items:
                p = str(SCREENSHOT_DIR / f"cell_r{rnd}_{it.i}.png")
                try:
                    await page.locator(f".geetest_imgs{it.i}").screenshot(path=p)
                    cell_paths.append(p)
                except Exception:  # noqa: BLE001
                    cell_paths.append("")

            img = build_grid_image(shot, ch, str(Path(shot).with_name(
                Path(shot).stem + "_grid.png")))
            k = wanted_picks(ch.question)
            logger.info("第 %d 轮：九宫格，题干=%r，需选 %d 个，交 %s 识别",
                        rnd, ch.question, k, provider)
            try:
                # provider 是同步 HTTP 调用（Ollama 可能几十秒），放线程里，
                # 避免阻塞 Playwright 的事件循环把页面连接拖死
                picks = await asyncio.to_thread(
                    provider.solve_grid, img, ch.question, len(ch.items), k,
                    {"screenshot": shot,
                     "items": [(it.x, it.y, it.w, it.h) for it in ch.items],
                     "item_urls": ch.item_urls,
                     "cell_paths": cell_paths,
                     "reference_box": await _find_reference_box(page)},
                )
            except Exception as e:  # noqa: BLE001
                return False, f"第 {rnd} 轮识别失败：{e}"
            logger.info("第 %d 轮：识别结果 %s，开始点击", rnd, picks)
            for idx in picks:
                cx, cy = ch.items[idx].center
                await click_human(page, cx, cy)
                await asyncio.sleep(random.uniform(0.35, 0.8))
            await asyncio.sleep(2.5)

            after = await detect(page)
            if after.ready:
                return True, f"第 {rnd} 轮通过（九宫格，选 {picks}）"
            last_msg = f"第 {rnd} 轮点击后仍未通过（题干={after.question!r}）"
            logger.warning(last_msg)

        elif ch.kind == "text":
            # CLIP 只解九宫格；文字点选需要 OCR，当前不支持。
            # 返回可重试标记，上层换一个全新会话再试（题型每次随机，可能换成九宫格）。
            logger.info("第 %d 轮：遇到文字点选题型，换会话重试", rnd)
            return False, "TEXT_CHALLENGE"
            if not points or not ch.window_box:
                return False, "文字点选未拿到有效坐标"
            wb = ch.window_box
            for nx, ny in points:
                await click_human(page, wb["x"] + nx * wb["w"], wb["y"] + ny * wb["h"])
                await asyncio.sleep(random.uniform(0.35, 0.8))
            await asyncio.sleep(2.5)
            after = await detect(page)
            if after.ready:
                return True, f"第 {rnd} 轮通过（文字点选）"
            last_msg = f"第 {rnd} 轮文字点选未通过"

        elif ch.kind == "slide":
            return False, "遇到滑块题型，本模块未实现（请手动刷新 Cookie）"

    return False, last_msg
