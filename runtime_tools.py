#!/usr/bin/env python3
from temporal_agent.app import build_tools_runtime
from temporal_agent.server import serve


if __name__ == "__main__":
    serve(build_tools_runtime(), port=8000)
