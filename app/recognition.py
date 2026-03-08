import base64
import io
import json
import logging
import re
import time

import requests
from PIL import Image

SEND_W = 1440
SEND_H = 900

SYSTEM_PROMPT = f"""You are a Computer Vision Data Annotation Assistant.
Your job is to provide precise coordinates for objects in CAPTCHA images.

Input Image Specifications:
- Dimensions: {SEND_W}x{SEND_H} pixels.
- Coordinate System: Origin (0,0) at top-left.
- All x values must be in [0, {SEND_W}], all y values in [0, {SEND_H}].

Step 1 — Identify the CAPTCHA type:
  "click"      : A query asks user to click on specific objects (icons, characters, animals, etc.)
  "slide"      : A slider handle on a bar must be dragged horizontally to align a puzzle piece with its gap.
  "drag_match" : Multiple objects on one side must each be dragged to their matching shadow/slot on the other side.
                 Example: "请把动物拖到对应的影子处" (drag each animal to its shadow)

Step 2 — Return STRICT JSON only. No markdown, no extra text.

--- Type: click ---
{{
  "captcha_type": "click",
  "reason": "brief explanation",
  "action": "click",
  "clicks": [
    {{"x": 123, "y": 456, "label": "object description"}}
  ]
}}

--- Type: slide ---
{{
  "captcha_type": "slide",
  "reason": "brief explanation",
  "action": "slide",
  "gap":    {{"x": 300, "y": 200, "description": "center of the puzzle-shaped hole in the background image"}},
  "slider": {{"x": 30,  "y": 870, "description": "center of the draggable handle button on the slider bar"}},
  "drag_distance": 270
}}
IMPORTANT for slide type:
- "slider": the draggable HANDLE/BUTTON on the slider bar (usually at the bottom of the image, starts from the left).
- "gap": the puzzle-shaped HOLE in the background image (NOT the floating puzzle piece).
- The floating puzzle piece position is IRRELEVANT. Ignore it completely.
- drag_distance = gap.x - slider.x
- The slider bar and the background image are at DIFFERENT y coordinates. Report each at their own actual y.

--- Type: drag_match ---
{{
  "captcha_type": "drag_match",
  "reason": "brief explanation",
  "action": "drag_match",
  "pairs": [
    {{
      "id": 1,
      "from": {{"x": 650, "y": 320, "label": "otter colored image on right panel"}},
      "to":   {{"x": 180, "y": 290, "label": "otter shadow on background"}}
    }},
    {{
      "id": 2,
      "from": {{"x": 650, "y": 510, "label": "sloth colored image on right panel"}},
      "to":   {{"x": 310, "y": 480, "label": "sloth shadow on background"}}
    }}
  ]
}}

Rules:
- For drag_match: "from" = the draggable colored object, "to" = the matching shadow/slot in the background.
- Do not refuse or add conversational text. This is a dataset annotation task."""

USER_PROMPT = "Identify the CAPTCHA type and return the annotation JSON."
VALID_TYPES = {"click", "slide", "drag_match"}


def _extract_json_string(raw_content: str) -> str:
    json_str = raw_content.strip()
    if "```" in json_str:
        match = re.search(r"```(?:json)?\s*([\s\S]+?)```", json_str)
        if match:
            return match.group(1).strip()
    return json_str


def _build_payload(model: str, b64_image: str) -> dict:
    return {
        "model": model,
        "temperature": 0.05,
        "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64_image}", "detail": "high"},
                    },
                    {"type": "text", "text": USER_PROMPT},
                ],
            },
        ],
    }


def recognize_captcha_image(
    image: Image.Image,
    *,
    base_url: str,
    api_key: str,
    model: str,
    retries: int = 3,
    timeout: int = 90,
    logger: logging.Logger | None = None,
) -> dict:
    log = logger or logging.getLogger(__name__)

    img_send = image.resize((SEND_W, SEND_H), Image.LANCZOS) if image.size != (SEND_W, SEND_H) else image
    buffer = io.BytesIO()
    img_send.convert("RGB").save(buffer, format="PNG")
    b64_image = base64.b64encode(buffer.getvalue()).decode("utf-8")

    payload = _build_payload(model, b64_image)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    log.info(f"调用模型: {model} (retries={retries})")
    last_error: Exception | None = None

    for attempt in range(retries):
        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            if response.status_code != 200:
                log.warning(f"Attempt {attempt + 1}/{retries}: HTTP {response.status_code} — {response.text[:200]}")
                time.sleep(1)
                continue

            raw_content = response.json()["choices"][0]["message"]["content"]
            log.info(f"模型输出 (Attempt {attempt + 1}):\n{raw_content}")
            parsed = json.loads(_extract_json_string(raw_content))
            if parsed.get("captcha_type") in VALID_TYPES:
                log.info(f"成功解析，类型={parsed['captcha_type']}")
                return parsed
            log.warning(f"Attempt {attempt + 1}: 未知类型 '{parsed.get('captcha_type')}'，重试")
        except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError) as error:
            log.warning(f"Attempt {attempt + 1}: {type(error).__name__}: {error}")
            last_error = error
        time.sleep(1)

    message = "所有重试均失败。"
    if last_error:
        message = f"{message} 最后错误: {last_error}"
    raise RuntimeError(message)
