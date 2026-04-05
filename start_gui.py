from __future__ import annotations

import sys
from pathlib import Path

from screenmon.gui import launch_gui


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    config_path = base_dir / "config.yaml"
    launch_gui(config_path)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"启动 GUI 失败: {exc}", file=sys.stderr)
        raise
