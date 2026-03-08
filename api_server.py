from app.api_server import app


if __name__ == "__main__":
    import uvicorn

    from app.api_server import DEFAULTS

    uvicorn.run(
        "app.api_server:app",
        host=DEFAULTS.api_host,
        port=DEFAULTS.api_port,
        reload=False,
    )
