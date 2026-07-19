from pathlib import Path
import argparse
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the organizations API.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Use 0.0.0.0 for LAN/container access.")
    parser.add_argument("--port", default=8000, type=int, help="Bind port.")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload for local development.")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "yandex_scraper.api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()

