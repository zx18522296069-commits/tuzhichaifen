from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from config import Settings
from excel_writer import validate_result, write_result
from image_parser import parse_image
from matcher import match_image
from pipeline import run_drive
from source_reader import read_summary_workbook


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FastCAM 拆图结果云端自动生成")
    parser.add_argument("--dry-run", action="store_true", help="完整读取与生成，但不写回 Drive、不改图片名")
    parser.add_argument("--only", help="只处理文件名中包含该文本的图片")
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--local-image", type=Path, help="本地单图回归测试")
    parser.add_argument("--local-summary", type=Path, help="本地汇总表回归测试")
    parser.add_argument("--original-filename", help="本地测试时使用的原始图片名")
    return parser.parse_args()


def _run_local(args: argparse.Namespace) -> int:
    if not args.local_image or not args.local_summary:
        raise SystemExit("本地测试必须同时提供 --local-image 和 --local-summary")
    image = parse_image(args.local_image, args.original_filename)
    sources = read_summary_workbook(args.local_summary, "王振海/正在加工/本地回归")
    matches = match_image(image, sources)
    result_path = args.output_dir / f"{image.main_name}_完成.xlsx"
    write_result(result_path, image, matches)
    validate_result(result_path, len(matches))
    print(
        json.dumps(
            {
                "output": str(result_path),
                "main_name": image.main_name,
                "program_no": image.program_no,
                "marked_weight_kg": image.marked_weight_kg,
                "parts": [
                    {
                        "order_no": item.image.order_no,
                        "drawing_no": item.image.drawing_no,
                        "thickness": item.image.thickness,
                        "base_quantity": item.image.base_quantity,
                        "bevel": item.image.bevel,
                        "split_quantity": item.image.split_quantity,
                        "split_weight_kg": item.split_weight_kg,
                    }
                    for item in matches
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _arguments()
    if args.local_image or args.local_summary:
        return _run_local(args)
    items = run_drive(Settings.from_env(), args.output_dir, dry_run=args.dry_run, only=args.only)
    failures = [item for item in items if item.status == "failed"]
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

