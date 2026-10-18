"""Print the OpenAPI document: `python -m analysis.export_openapi > docs/openapi.json`."""

import json

from .main import create_app

if __name__ == "__main__":
    print(json.dumps(create_app(run_worker=False).openapi(), indent=2, sort_keys=True))
