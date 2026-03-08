"""
captcha_cu_universal.py
-----------------------
通杀点选 + 滑动 + 拖拽匹配验证码，自动识别类型并标注。
- 读取 captcha_img.png
- 发送给模型，模型自行判断验证码类型并返回对应坐标
- 自动分支：
    click      → 标注点击圆圈
    slide      → 独立标注手柄+空隙，水平虚线表示拖动距离
    drag_match → 标注每对 from→to 带编号箭头
- 输出 captcha_result.png，日志写入 captcha_test.log

用法:
    python captcha_cu_universal.py [--image captcha_img.png] [--base-url https://api.amethyst.ltd/v1]
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from app.recognition import SEND_H, SEND_W, recognize_captcha_image

# ─── CLI ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="CU Universal CAPTCHA test")
parser.add_argument("--image",    default="captcha_img.png")
parser.add_argument("--base-url", default="https://api.amethyst.ltd/v1")
parser.add_argument("--api-key",  default="YOUR_API_KEY")
parser.add_argument("--model",    default="gpt-5.4")
parser.add_argument("--retries",  type=int, default=3)
parser.add_argument("--output",   default="captcha_result.png")
parser.add_argument("--log",      default="captcha_test.log")
args = parser.parse_args()

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(args.log, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ─── Load & resize image ──────────────────────────────────────────────────────
img_path = Path(args.image)
if not img_path.exists():
    log.error(f"找不到输入图片: {img_path.resolve()}")
    sys.exit(1)

img = Image.open(img_path).convert("RGBA")
img_w, img_h = img.size
log.info(f"原始图片尺寸: {img_w}×{img_h}")

scale_x = img_w / SEND_W
scale_y = img_h / SEND_H

log.info(f"发送尺寸: {SEND_W}×{SEND_H}  scale=({scale_x:.3f}, {scale_y:.3f})")

try:
    result = recognize_captcha_image(
        img,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        retries=args.retries,
        logger=log,
    )
except RuntimeError as e:
    log.error(str(e))
    sys.exit(1)

captcha_type = result.get("captcha_type")
action       = result.get("action", "unknown")
reason       = result.get("reason", "")

log.info(f"验证码类型: {captcha_type}  action={action}  reason={reason}")

# ─── Coord remap ──────────────────────────────────────────────────────────────
def remap(point):
    if not point:
        return None
    sx, sy = int(point.get("x", 0)), int(point.get("y", 0))
    return {**point, "x": round(sx * scale_x), "y": round(sy * scale_y), "_sx": sx, "_sy": sy}

# ─── Font ─────────────────────────────────────────────────────────────────────
try:
    font_label = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
except OSError:
    try:
        font_label = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 14)
        font_title = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 16)
    except OSError:
        font_label = ImageFont.load_default()
        font_title = font_label

RADIUS    = max(14, round(img_w / 60))
annotated = img.copy()

# ─── Draw helpers ─────────────────────────────────────────────────────────────
def draw_marker(base_img, cx, cy, color_hex, label, number):
    overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    rgb = tuple(bytes.fromhex(color_hex.lstrip("#")))
    ov_draw.ellipse(
        [cx - RADIUS, cy - RADIUS, cx + RADIUS, cy + RADIUS],
        fill=(*rgb, 200), outline=(*rgb, 255), width=2,
    )
    out = Image.alpha_composite(base_img, overlay)
    d = ImageDraw.Draw(out)
    num_str = str(number)
    bbox = d.textbbox((0, 0), num_str, font=font_label)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((cx - tw / 2, cy - th / 2), num_str, fill="#FFFFFF", font=font_label)
    lx, ly = cx + RADIUS + 4, cy - 10
    lw = d.textlength(label, font=font_label)
    d.rectangle([lx - 2, ly - 2, lx + lw + 4, ly + 18], fill=(0, 0, 0, 160))
    d.text((lx, ly), label, fill="#FFFFFF", font=font_label)
    return out

def draw_arrow(draw, sx, sy, gx, gy, color, label=""):
    """斜向箭头，用于 drag_match。"""
    draw.line([(sx, sy), (gx, gy)], fill=color, width=3)
    dx, dy = gx - sx, gy - sy
    length = max((dx**2 + dy**2) ** 0.5, 1)
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    tip   = (gx, gy)
    left  = (gx - 12 * ux + 6 * px, gy - 12 * uy + 6 * py)
    right = (gx - 12 * ux - 6 * px, gy - 12 * uy - 6 * py)
    draw.polygon([tip, left, right], fill=color)
    if label:
        mid_x, mid_y = (sx + gx) // 2, (sy + gy) // 2 - 14
        lw = draw.textlength(label, font=font_label)
        draw.rectangle([mid_x - 2, mid_y - 2, mid_x + lw + 4, mid_y + 18], fill=(0, 0, 0, 180))
        draw.text((mid_x, mid_y), label, fill=color, font=font_label)

def draw_horizontal_dashed_arrow(draw, sx, sy, gx, color, label=""):
    """水平虚线箭头，用于 slide，在手柄的 y 高度画。"""
    x = sx
    while x < gx:
        x_end = min(x + 4, gx)
        draw.line([(x, sy), (x_end, sy)], fill=color, width=2)
        x += 8
    # 末端箭头
    draw.polygon([(gx, sy), (gx - 10, sy - 5), (gx - 10, sy + 5)], fill=color)
    if label:
        mid_x = (sx + gx) // 2
        lw = draw.textlength(label, font=font_label)
        draw.rectangle([mid_x - 2, sy - 22, mid_x + lw + 4, sy - 4], fill=(0, 0, 0, 180))
        draw.text((mid_x, sy - 20), label, fill=color, font=font_label)

PAIR_COLORS = ["#FFD700", "#00E5FF", "#FF6BFF", "#00FF99", "#FF8C42"]

# ─── Branch: click ────────────────────────────────────────────────────────────
status_text = ""

if captcha_type == "click":
    clicks_raw = result.get("clicks", [])
    remapped   = [remap(c) for c in clicks_raw]
    for i, c in enumerate(remapped):
        if c:
            log.info(f"  [{i+1}] 原始({c['x']},{c['y']}) ← 发送({c['_sx']},{c['_sy']})  {c.get('label','')}")
            annotated = draw_marker(annotated, c["x"], c["y"], "#FF3B3B", c.get("label", f"#{i+1}")[:25], i + 1)
    status_text = f"[点选] ✓ {len(remapped)} 个目标 | {reason[:55]}"

# ─── Branch: slide ────────────────────────────────────────────────────────────
elif captcha_type == "slide":
    gap_r     = remap(result.get("gap"))
    slider_r  = remap(result.get("slider"))
    drag_s    = result.get("drag_distance", 0)
    drag_orig = round(drag_s * scale_x)

    # 独立标注：手柄（蓝）和空隙（红）各在自己实际的 y 坐标
    if slider_r:
        annotated = draw_marker(annotated, slider_r["x"], slider_r["y"], "#007AFF",
                                "slider handle", "S")
    if gap_r:
        annotated = draw_marker(annotated, gap_r["x"], gap_r["y"], "#FF3B3B",
                                "gap", "G")

    # 水平虚线箭头：在手柄的 y 高度，从手柄 x 到空隙 x
    if slider_r and gap_r:
        d = ImageDraw.Draw(annotated)
        draw_horizontal_dashed_arrow(
            d,
            slider_r["x"], slider_r["y"],
            gap_r["x"],
            "#FFD700",
            f"drag: {drag_orig}px"
        )

    log.info(f"缺口(原始): {gap_r}")
    log.info(f"滑块(原始): {slider_r}")
    log.info(f"拖动距离: {drag_orig}px (orig) / {drag_s}px (send)")
    gx_str = str(gap_r["x"]) if gap_r else "?"
    gy_str = str(gap_r["y"]) if gap_r else "?"
    status_text = f"[滑动] ✓ 缺口({gx_str},{gy_str})  拖动:{drag_orig}px | {reason[:40]}"

# ─── Branch: drag_match ───────────────────────────────────────────────────────
elif captcha_type == "drag_match":
    pairs = result.get("pairs", [])
    log.info(f"拖拽对数: {len(pairs)}")

    for pair in pairs:
        pid   = pair.get("id", "?")
        from_ = remap(pair.get("from"))
        to_   = remap(pair.get("to"))
        color = PAIR_COLORS[(int(str(pid)) - 1) % len(PAIR_COLORS)] if str(pid).isdigit() else PAIR_COLORS[0]

        if from_:
            log.info(f"  pair {pid} FROM: 原始({from_['x']},{from_['y']}) ← 发送({from_['_sx']},{from_['_sy']})  {from_.get('label','')}")
            annotated = draw_marker(annotated, from_["x"], from_["y"], color,
                                    f"#{pid} {from_.get('label','')[:18]}", pid)
        if to_:
            log.info(f"  pair {pid} TO:   原始({to_['x']},{to_['y']}) ← 发送({to_['_sx']},{to_['_sy']})  {to_.get('label','')}")
            annotated = draw_marker(annotated, to_["x"], to_["y"], color,
                                    f"#{pid}→ {to_.get('label','')[:16]}", f"{pid}▶")
        if from_ and to_:
            d = ImageDraw.Draw(annotated)
            draw_arrow(d, from_["x"], from_["y"], to_["x"], to_["y"], color, f"#{pid}")

    status_text = f"[拖拽匹配] ✓ {len(pairs)} 对 | {reason[:50]}"

# ─── Fallback ─────────────────────────────────────────────────────────────────
else:
    status_text = f"[未知] action={action} | {reason[:60]}"
    log.warning(f"未识别的验证码类型: {captcha_type}")

# ─── Info bar ─────────────────────────────────────────────────────────────────
bar_h  = 40
bar    = Image.new("RGBA", (img_w, bar_h), (30, 30, 30, 230))
canvas = Image.new("RGBA", (img_w, img_h + bar_h), (0, 0, 0, 255))
canvas.paste(bar, (0, 0))
canvas.paste(annotated, (0, bar_h))
annotated = canvas

d = ImageDraw.Draw(annotated)
d.text((8, 12), status_text, fill="#FFFFFF", font=font_title)
ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
d.text((img_w - 200, bar_h + img_h - 20), f"tested {ts}", fill=(180, 180, 180, 180), font=font_label)

# ─── Save ─────────────────────────────────────────────────────────────────────
out_path = Path(args.output)
annotated.convert("RGB").save(out_path)
log.info(f"输出: {out_path.resolve()}")

# ─── Summary ──────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  通杀验证码测试摘要")
print("=" * 60)
print(f"  验证码类型 : {captcha_type}")
print(f"  action     : {action}")
print(f"  reason     : {reason}")

if captcha_type == "click":
    for i, c in enumerate(remapped):
        if c:
            print(f"    [{i+1}] 原始({c['x']},{c['y']}) ← 发送({c['_sx']},{c['_sy']})  {c.get('label','')}")

elif captcha_type == "slide":
    if gap_r:
        print(f"  缺口  : 原始({gap_r['x']},{gap_r['y']}) ← 发送({gap_r['_sx']},{gap_r['_sy']})")
    if slider_r:
        print(f"  手柄  : 原始({slider_r['x']},{slider_r['y']}) ← 发送({slider_r['_sx']},{slider_r['_sy']})")
    print(f"  拖动  : {drag_orig}px (orig) / {drag_s}px (send)")

elif captcha_type == "drag_match":
    for pair in pairs:
        pid   = pair.get("id", "?")
        from_ = remap(pair.get("from"))
        to_   = remap(pair.get("to"))
        fl    = pair.get("from", {}).get("label", "")
        tl    = pair.get("to",   {}).get("label", "")
        if from_ and to_:
            print(f"  pair {pid}: ({from_['x']},{from_['y']}) → ({to_['x']},{to_['y']})  [{fl} → {tl}]")

print(f"  输出图 : {out_path}")
print(f"  日志   : {Path(args.log).resolve()}")
print("=" * 60)
