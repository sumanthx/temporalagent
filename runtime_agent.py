#!/usr/bin/env python3
from temporal_agent.app import build_demo
from temporal_agent.server import serve


if __name__ == "__main__":
    serve(build_demo(), port=8080)
