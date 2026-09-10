import argparse
import json

from .app import build_demo
from .models import Principal
from .server import serve


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("serve")
    run.add_argument("--port", type=int, default=8080)
    ask = sub.add_parser("ask")
    ask.add_argument("prompt")
    ask.add_argument("--principal", default="alice")
    args = parser.parse_args()
    app = build_demo()
    if args.command == "serve":
        serve(app, port=args.port)
    else:
        print(json.dumps(app.orchestrator.invoke(
            {"prompt": args.prompt}, Principal(args.principal)), indent=2))


if __name__ == "__main__":
    main()

