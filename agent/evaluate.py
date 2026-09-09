"""生成第三阶段质量评测报告，不调用模型或修改业务数据。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from core.evaluation import build_quality_report


def main() -> None:
    """运行固定路由测试并汇总已有运行轨迹。"""
    parser = argparse.ArgumentParser(description="Digital customer service quality report")
    parser.add_argument(
        "--output",
        type=Path,
        default=AGENT_DIR / "runtime" / "quality_report.json",
        help="Report JSON output path",
    )
    args = parser.parse_args()
    report = build_quality_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
        file.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
