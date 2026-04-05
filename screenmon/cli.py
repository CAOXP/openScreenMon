from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from .app import ScreenMonApp
from .config import load_config


async def _async_main(args):
    cfg = load_config(args.config)
    app = ScreenMonApp(cfg)
    try:
        await app.run(run_once=args.run_once)
    except asyncio.CancelledError:  # pragma: no cover
        pass
    finally:
        await app.shutdown()


def main():
    parser = argparse.ArgumentParser(description="ScreenMon 后台监控服务")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="配置文件路径")
    parser.add_argument("--run-once", action="store_true", help="仅执行一次任务")
    parser.add_argument("--gui", action="store_true", help="启动图形界面编辑配置")
    args = parser.parse_args()

    if args.gui:
        from .gui import launch_gui

        launch_gui(args.config)
        return

    try:
        asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        logging.info("收到中断信号，安全退出")


if __name__ == "__main__":
    main()

