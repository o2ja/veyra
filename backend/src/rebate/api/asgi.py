"""ASGI entrypoint: `uvicorn rebate.api.asgi:app` / gunicorn with UvicornWorker."""

from rebate.api.app import create_app

app = create_app()
