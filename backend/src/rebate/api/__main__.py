"""Print the OpenAPI document to stdout (used by `make gen-client`). Needs no database."""

import json
import os
import sys

os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("SECRET_KEY", "openapi-export-only-not-a-secret-000000")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://openapi-export@localhost/none")

from rebate.api.app import create_app  # env must be set before settings load

document = json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"
sys.stdout.buffer.write(document.encode())  # identical bytes on Windows and Linux
