import argparse

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(prog="jevllm-web")
    ap.add_argument("--host", default="127.0.0.1", help="bind address; 127.0.0.1 keeps the lab on this machine")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    uvicorn.run("jevllm.web.app:app", host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
