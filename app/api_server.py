import io
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from PIL import Image, ImageDraw, UnidentifiedImageError

from app.recognition import SEND_H, SEND_W, recognize_captcha_image

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
PLACEHOLDER_KEYS = {"", "YOUR_API_KEY", "<YOUR_API_KEY>", "changeme", "CHANGEME"}


@dataclass
class ServiceConfig:
    api_host: str
    api_port: int
    base_url: str
    api_key: str
    model: str
    retries: int
    timeout: int
    max_image_bytes: int


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_yaml_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise RuntimeError(f"未找到配置文件: {path}")
    parsed: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise RuntimeError(f"config.yaml 第 {line_no} 行格式错误: {raw_line}")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise RuntimeError(f"config.yaml 第 {line_no} 行缺少 key: {raw_line}")
        if " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        parsed[key] = _strip_quotes(value)
    return parsed


def _to_int(raw_value: str | int, field: str) -> int:
    try:
        return int(raw_value)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"配置项 {field} 必须为整数，当前值: {raw_value}") from error


def _validate_config(config: ServiceConfig) -> None:
    errors: list[str] = []
    if not config.api_host.strip():
        errors.append("api_host 不能为空")
    if config.api_port < 1 or config.api_port > 65535:
        errors.append("api_port 必须在 1~65535")
    if not config.base_url.strip():
        errors.append("base_url 不能为空")
    if not config.model.strip():
        errors.append("model 不能为空")
    if config.api_key.strip() in PLACEHOLDER_KEYS:
        errors.append("api_key 未配置，请在 config.yaml 中填写真实密钥")
    if config.retries < 1:
        errors.append("retries 必须大于等于 1")
    if config.timeout < 5:
        errors.append("timeout 必须大于等于 5")
    if config.max_image_bytes < 1024:
        errors.append("max_image_bytes 必须大于等于 1024")
    if errors:
        detail = "; ".join(errors)
        raise RuntimeError(f"服务配置校验失败: {detail}，配置文件: {CONFIG_PATH}")


def _load_defaults() -> ServiceConfig:
    raw = {
        "api_host": "0.0.0.0",
        "api_port": 8000,
        "base_url": "https://api.amethyst.ltd/v1",
        "api_key": "",
        "model": "gpt-5.4",
        "retries": 3,
        "timeout": 90,
        "max_image_bytes": 5 * 1024 * 1024,
    }
    yaml_values = _parse_yaml_file(CONFIG_PATH)
    for key in raw:
        if key in yaml_values:
            raw[key] = yaml_values[key]
    env_map = {
        "CAPTCHA_API_HOST": "api_host",
        "CAPTCHA_API_PORT": "api_port",
        "CAPTCHA_BASE_URL": "base_url",
        "CAPTCHA_API_KEY": "api_key",
        "CAPTCHA_MODEL": "model",
        "CAPTCHA_RETRIES": "retries",
        "CAPTCHA_TIMEOUT": "timeout",
        "CAPTCHA_MAX_IMAGE_BYTES": "max_image_bytes",
    }
    for env_name, field_name in env_map.items():
        env_value = os.getenv(env_name)
        if env_value is not None:
            raw[field_name] = env_value
    config = ServiceConfig(
        api_host=str(raw["api_host"]).strip(),
        api_port=_to_int(raw["api_port"], "api_port"),
        base_url=str(raw["base_url"]).strip().rstrip("/"),
        api_key=str(raw["api_key"]).strip(),
        model=str(raw["model"]).strip(),
        retries=_to_int(raw["retries"], "retries"),
        timeout=_to_int(raw["timeout"], "timeout"),
        max_image_bytes=_to_int(raw["max_image_bytes"], "max_image_bytes"),
    )
    _validate_config(config)
    return config


DEFAULTS = _load_defaults()


class SensitiveDataFilter(logging.Filter):
    def __init__(self, secret_keys: list[str] | None = None) -> None:
        super().__init__()
        self.secret_keys = [key for key in (secret_keys or []) if key]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        message = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]+", r"\1***", message)
        for secret in self.secret_keys:
            message = message.replace(secret, "***")
        if message != record.getMessage():
            record.msg = message
            record.args = ()
        return True


def _mask_sensitive_text(text: str | None, secrets: list[str] | None = None) -> str | None:
    if text is None:
        return None
    masked = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]+", r"\1***", text)
    for secret in secrets or []:
        if secret:
            masked = masked.replace(secret, "***")
    return masked


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("captcha_api")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    handler.addFilter(SensitiveDataFilter([DEFAULTS.api_key]))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = _build_logger()
app = FastAPI(title="CaptchaVision API", version="0.1.0")


def _error_payload(code: str, message: str, details: str | None = None) -> dict[str, Any]:
    return {
        "success": False,
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "details": details,
        },
    }


def _normalize_result_data(result: dict[str, Any]) -> dict[str, Any]:
    def strip_fields(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: strip_fields(v) for k, v in value.items() if k not in {"label", "description"}}
        if isinstance(value, list):
            return [strip_fields(item) for item in value]
        return value

    captcha_type = result.get("captcha_type")
    normalized = {
        "captcha_type": captcha_type,
        "action": result.get("action"),
        "clicks": strip_fields(result.get("clicks") if captcha_type == "click" else []),
        "gap": strip_fields(result.get("gap") if captcha_type == "slide" else None),
        "slider": strip_fields(result.get("slider") if captcha_type == "slide" else None),
        "drag_distance": result.get("drag_distance") if captcha_type == "slide" else None,
        "pairs": strip_fields(result.get("pairs") if captcha_type == "drag_match" else []),
    }
    if captcha_type == "slide" and isinstance(normalized.get("gap"), dict) and isinstance(normalized.get("slider"), dict):
        slider_y = normalized["slider"].get("y")
        if isinstance(slider_y, (int, float)):
            normalized["gap"]["y"] = slider_y
    return normalized


def _success_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": True,
        "data": _normalize_result_data(result),
        "error": None,
    }


def _extract_point(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    x = value.get("x")
    y = value.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    return float(x), float(y)


def _draw_point(draw: ImageDraw.ImageDraw, point: tuple[float, float], color: str) -> None:
    x, y = point
    r = 12
    draw.ellipse((x - r, y - r, x + r, y + r), outline=color, width=4)
    draw.line((x - r - 6, y, x + r + 6, y), fill=color, width=3)
    draw.line((x, y - r - 6, x, y + r + 6), fill=color, width=3)


def _scale_point_to_preview(point: tuple[float, float], preview_size: tuple[int, int]) -> tuple[float, float]:
    width, height = preview_size
    scale_x = width / float(SEND_W)
    scale_y = height / float(SEND_H)
    scaled_x = max(0.0, min(width - 1.0, point[0] * scale_x))
    scaled_y = max(0.0, min(height - 1.0, point[1] * scale_y))
    return scaled_x, scaled_y


def _render_preview_image(image: Image.Image, result: dict[str, Any]) -> bytes:
    preview = image.convert("RGBA")
    draw = ImageDraw.Draw(preview)
    captcha_type = result.get("captcha_type")
    if captcha_type == "click":
        for item in result.get("clicks") or []:
            point = _extract_point(item)
            if point is not None:
                _draw_point(draw, _scale_point_to_preview(point, preview.size), "#ff3b30")
    elif captcha_type == "slide":
        gap = _extract_point(result.get("gap"))
        slider = _extract_point(result.get("slider"))
        gap_scaled = _scale_point_to_preview(gap, preview.size) if gap is not None else None
        slider_scaled = _scale_point_to_preview(slider, preview.size) if slider is not None else None
        if gap_scaled is not None:
            _draw_point(draw, gap_scaled, "#34c759")
        if slider_scaled is not None:
            _draw_point(draw, slider_scaled, "#007aff")
        if gap_scaled is not None and slider_scaled is not None:
            draw.line((slider_scaled[0], slider_scaled[1], gap_scaled[0], gap_scaled[1]), fill="#ffcc00", width=4)
    elif captcha_type == "drag_match":
        for pair in result.get("pairs") or []:
            src = _extract_point(pair.get("from") if isinstance(pair, dict) else None)
            dst = _extract_point(pair.get("to") if isinstance(pair, dict) else None)
            src_scaled = _scale_point_to_preview(src, preview.size) if src is not None else None
            dst_scaled = _scale_point_to_preview(dst, preview.size) if dst is not None else None
            if src_scaled is not None:
                _draw_point(draw, src_scaled, "#ff9500")
            if dst_scaled is not None:
                _draw_point(draw, dst_scaled, "#af52de")
            if src_scaled is not None and dst_scaled is not None:
                draw.line((src_scaled[0], src_scaled[1], dst_scaled[0], dst_scaled[1]), fill="#ffcc00", width=4)
    buffer = io.BytesIO()
    preview.save(buffer, format="PNG")
    return buffer.getvalue()


def _extract_detail(errors: list[dict[str, Any]]) -> str:
    messages: list[str] = []
    for item in errors:
        loc = ".".join([str(p) for p in item.get("loc", []) if p != "body"])
        msg = item.get("msg", "请求参数无效")
        messages.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(messages)


async def _find_extra_params(request: Request) -> list[str]:
    allowed_fields = {"file"}
    extras: set[str] = {key for key in request.query_params.keys() if key not in allowed_fields}
    form = await request.form()
    extras.update({key for key in form.keys() if key not in allowed_fields})
    return sorted(extras)


@app.exception_handler(HTTPException)
async def handle_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
    details = str(exc.detail) if exc.detail is not None else None
    if exc.status_code == 400:
        code = "BAD_REQUEST"
    elif exc.status_code == 413:
        code = "IMAGE_TOO_LARGE"
    elif exc.status_code == 415:
        code = "UNSUPPORTED_MEDIA_TYPE"
    elif exc.status_code == 422:
        code = "INVALID_IMAGE"
    else:
        code = "HTTP_ERROR"
    return JSONResponse(status_code=exc.status_code, content=_error_payload(code, "请求处理失败", details))


@app.exception_handler(RequestValidationError)
async def handle_validation_exception(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = _extract_detail(exc.errors())
    if "file" in details:
        message = "缺少图片文件参数 file"
    else:
        message = "请求参数校验失败"
    return JSONResponse(status_code=422, content=_error_payload("VALIDATION_ERROR", message, details))


@app.exception_handler(Exception)
async def handle_unexpected_exception(_: Request, exc: Exception) -> JSONResponse:
    log.exception("服务内部错误: %s", str(exc))
    return JSONResponse(
        status_code=500,
        content=_error_payload("INTERNAL_SERVER_ERROR", "服务内部错误", "请检查服务日志"),
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "success": True,
        "data": {"status": "ok"},
        "error": None,
    }


@app.post("/api/v1/recognize")
async def recognize_api(
    request: Request,
    file: UploadFile = File(...),
) -> JSONResponse:
    config = DEFAULTS
    if not config.api_key:
        raise HTTPException(status_code=500, detail="服务端未配置 API key，请检查 config.yaml")
    extra_params = await _find_extra_params(request)
    if extra_params:
        return JSONResponse(
            status_code=400,
            content=_error_payload(
                "EXTRA_PARAMETERS_NOT_ALLOWED",
                "识别接口仅允许上传 file 图片字段",
                f"检测到不允许的入参: {', '.join(extra_params)}",
            ),
        )

    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail=f"仅支持图片文件，当前 content_type={file.content_type}")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(raw_bytes) > config.max_image_bytes:
        raise HTTPException(status_code=413, detail=f"图片超过大小限制 {config.max_image_bytes} bytes")

    try:
        image = Image.open(io.BytesIO(raw_bytes))
        image.load()
        image = image.convert("RGBA")
    except (UnidentifiedImageError, OSError) as error:
        raise HTTPException(status_code=422, detail=f"无效图片内容: {error}") from error

    log.info(
        "接收识别请求 file=%s size=%s model=%s retries=%s timeout=%s base_url=%s",
        file.filename or "<unknown>",
        len(raw_bytes),
        config.model,
        config.retries,
        config.timeout,
        config.base_url,
    )

    try:
        result = recognize_captcha_image(
            image,
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            retries=config.retries,
            timeout=config.timeout,
            logger=log,
        )
    except RuntimeError as error:
        log.error("模型调用失败: %s", str(error))
        safe_detail = _mask_sensitive_text(str(error), [config.api_key, DEFAULTS.api_key])
        return JSONResponse(
            status_code=502,
            content=_error_payload("MODEL_CALL_FAILED", "模型调用失败", safe_detail),
        )

    return JSONResponse(status_code=200, content=_success_payload(result))


@app.post("/api/v1/recognize/preview")
async def recognize_preview_api(
    request: Request,
    file: UploadFile = File(...),
) -> Response:
    config = DEFAULTS
    if not config.api_key:
        raise HTTPException(status_code=500, detail="服务端未配置 API key，请检查 config.yaml")
    extra_params = await _find_extra_params(request)
    if extra_params:
        return JSONResponse(
            status_code=400,
            content=_error_payload(
                "EXTRA_PARAMETERS_NOT_ALLOWED",
                "识别接口仅允许上传 file 图片字段",
                f"检测到不允许的入参: {', '.join(extra_params)}",
            ),
        )
    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail=f"仅支持图片文件，当前 content_type={file.content_type}")
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(raw_bytes) > config.max_image_bytes:
        raise HTTPException(status_code=413, detail=f"图片超过大小限制 {config.max_image_bytes} bytes")
    try:
        image = Image.open(io.BytesIO(raw_bytes))
        image.load()
        image = image.convert("RGBA")
    except (UnidentifiedImageError, OSError) as error:
        raise HTTPException(status_code=422, detail=f"无效图片内容: {error}") from error
    try:
        result = recognize_captcha_image(
            image,
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            retries=config.retries,
            timeout=config.timeout,
            logger=log,
        )
    except RuntimeError as error:
        log.error("模型调用失败: %s", str(error))
        safe_detail = _mask_sensitive_text(str(error), [config.api_key, DEFAULTS.api_key])
        return JSONResponse(
            status_code=502,
            content=_error_payload("MODEL_CALL_FAILED", "模型调用失败", safe_detail),
        )
    preview_bytes = _render_preview_image(image, _normalize_result_data(result))
    return Response(content=preview_bytes, media_type="image/png")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.api_server:app",
        host=DEFAULTS.api_host,
        port=DEFAULTS.api_port,
        reload=False,
    )
