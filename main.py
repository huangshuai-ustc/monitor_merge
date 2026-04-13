#!/usr/bin/env python3
"""
监控视频多画幅拼接工具

用法:
    python main.py                          # 使用 config.yaml
    python main.py --folders cam1 cam2      # 直接指定文件夹
    python main.py --help                   # 查看帮助
"""

import argparse
import sys
import os
import yaml
from pathlib import Path
from typing import List, Dict
from datetime import datetime

from time_extract import scan_folder, VideoSegment
from timeline import build_timeline, print_timeline_summary
from merge import merge_videos


def load_config(config_path: str = "config.yaml") -> dict:
    """加载配置文件"""
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}


def check_dependencies():
    """检查必要的依赖"""
    # 检查 FFmpeg
    import subprocess
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            version_line = result.stdout.split('\n')[0]
            print(f"✅ FFmpeg: {version_line}")
        else:
            print("❌ FFmpeg 未正确安装")
            sys.exit(1)
    except FileNotFoundError:
        print("❌ FFmpeg 未安装，请先安装:")
        print("   Ubuntu/Debian: sudo apt install ffmpeg")
        print("   macOS: brew install ffmpeg")
        print("   Windows: https://ffmpeg.org/download.html")
        sys.exit(1)

    # 检查 ffprobe
    try:
        subprocess.run(['ffprobe', '-version'], capture_output=True, check=True)
        print("✅ ffprobe: OK")
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("❌ ffprobe 未安装（通常随 FFmpeg 一起安装）")
        sys.exit(1)

    # 检查 OpenCV
    try:
        import cv2
        print(f"✅ OpenCV: {cv2.__version__}")
    except ImportError:
        print("⚠ OpenCV 未安装 (pip install opencv-python)")
        print("  OCR 时间提取功能将不可用")

    # 检查 Tesseract
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        print(f"✅ Tesseract: OK")
    except Exception:
        print("⚠ Tesseract 未安装")
        print("  OCR 时间提取功能将不可用")
        print("  安装: sudo apt install tesseract-ocr")


def main():
    parser = argparse.ArgumentParser(
        description='监控视频多画幅拼接工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --folders ./cam1 ./cam2 ./cam3 ./cam4
  %(prog)s --folders ./cam1 ./cam2 --output merged.mp4 --method filename
  %(prog)s --config my_config.yaml
  %(prog)s --folders ./cam1 ./cam2 --resolution 3840x2160
        """
    )

    parser.add_argument(
        '--folders', '-f',
        nargs='+',
        help='监控视频文件夹路径（数量决定布局）'
    )
    parser.add_argument(
        '--output', '-o',
        default=None,
        help='输出文件路径 (默认: ./output/merged.mp4)'
    )
    parser.add_argument(
        '--config', '-c',
        default='config.yaml',
        help='配置文件路径 (默认: config.yaml)'
    )
    parser.add_argument(
        '--method', '-m',
        choices=['ocr', 'filename', 'metadata', 'auto'],
        default=None,
        help='时间提取方式 (默认: ocr)'
    )
    parser.add_argument(
        '--resolution', '-r',
        default=None,
        help='输出分辨率，如 1920x1080'
    )
    parser.add_argument(
        '--fps',
        type=float,
        default=None,
        help='输出帧率 (默认: 25)'
    )
    parser.add_argument(
        '--time-format',
        default=None,
        help='OCR 时间格式 (默认: "%%Y-%%m-%%d %%H:%%M:%%S")'
    )
    parser.add_argument(
        '--filename-pattern',
        default=None,
        help='文件名时间正则表达式'
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='仅检查依赖和扫描文件，不执行合并'
    )
    parser.add_argument(
        '--temp-dir',
        default=None,
        help='临时文件目录'
    )

    args = parser.parse_args()

    print("=" * 60)
    print("🎬 监控视频多画幅拼接工具")
    print("=" * 60)

    # 检查依赖
    check_dependencies()
    print()

    # 加载配置
    config = load_config(args.config)

    # 命令行参数覆盖配置文件
    folders = args.folders or config.get('input_folders', [])
    output_file = args.output or config.get('output_file', './output/merged.mp4')
    method = args.method or config.get('time_extract_method', 'ocr')
    fps = args.fps or config.get('fps', 25)
    time_format = args.time_format or config.get('time_format', '%Y-%m-%d %H:%M:%S')
    filename_pattern = (args.filename_pattern or
                        config.get('filename_pattern',
                                   r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})'))
    ocr_region = config.get('ocr_region', {'x': 0, 'y': 0, 'w': 0.35, 'h': 0.06})

    if args.resolution:
        parts = args.resolution.lower().split('x')
        output_width, output_height = int(parts[0]), int(parts[1])
    else:
        output_width = config.get('output_width', 1920)
        output_height = config.get('output_height', 1080)

    # 验证输入
    if not folders:
        print("❌ 未指定输入文件夹")
        print("   使用 --folders 参数或在 config.yaml 中配置 input_folders")
        parser.print_help()
        sys.exit(1)

    # 验证文件夹存在
    valid_folders = []
    for folder in folders:
        if os.path.isdir(folder):
            valid_folders.append(folder)
        else:
            print(f"⚠ 文件夹不存在，跳过: {folder}")

    if not valid_folders:
        print("❌ 没有有效的输入文件夹")
        sys.exit(1)

    num_cams = len(valid_folders)
    print(f"\n📹 监控数量: {num_cams}")
    for i, folder in enumerate(valid_folders):
        file_count = len([f for f in Path(folder).iterdir()
                         if f.is_file() and f.suffix.lower() in
                         {'.mp4', '.avi', '.mkv', '.mov', '.flv', '.ts'}])
        print(f"   监控{i+1}: {folder} ({file_count} 个视频)")

    print(f"\n⚙ 设置:")
    print(f"   时间提取: {method}")
    print(f"   输出分辨率: {output_width}x{output_height}")
    print(f"   帧率: {fps}")
    print(f"   输出文件: {output_file}")

    # ========== 第一步：扫描所有文件夹 ==========
    print(f"\n{'='*60}")
    print(f"📖 第一步：扫描视频文件并提取时间信息")
    print(f"{'='*60}")

    all_segments: Dict[int, List[VideoSegment]] = {}

    for i, folder in enumerate(valid_folders):
        segments = scan_folder(
            folder=folder,
            method=method,
            ocr_region=ocr_region,
            time_format=time_format,
            filename_pattern=filename_pattern,
        )
        all_segments[i] = segments

    # 检查是否有数据
    total_segments = sum(len(segs) for segs in all_segments.values())
    if total_segments == 0:
        print("\n❌ 未找到任何有效的视频片段")
        print("   请检查:")
        print("   1. 文件夹中是否有视频文件")
        print("   2. 时间提取方式是否正确 (--method)")
        print("   3. OCR 区域设置是否正确")
        sys.exit(1)

    # ========== 第二步：构建时间轴 ==========
    print(f"\n{'='*60}")
    print(f"📐 第二步：构建全局时间轴")
    print(f"{'='*60}")

    time_slots = build_timeline(all_segments, fps=fps)
    print_timeline_summary(time_slots, num_cams)

    if args.check:
        print("\n✅ 检查完成（--check 模式，不执行合并）")
        return

    # ========== 第三步：确认并合并 ==========
    total_duration = sum(s.duration for s in time_slots)
    estimated_size_mb = total_duration * 0.5  # 粗略估计：0.5MB/秒

    print(f"\n{'='*60}")
    print(f"🔨 第三步：合并视频")
    print(f"{'='*60}")
    print(f"   预计输出时长: {total_duration:.0f}秒 ({total_duration/3600:.2f}小时)")
    print(f"   预计文件大小: ~{estimated_size_mb:.0f} MB")
    print(f"   处理片段数: {len(time_slots)}")

    confirm = input("\n   确认开始合并? (y/n): ").strip().lower()
    if confirm != 'y':
        print("   已取消")
        return

    merge_videos(
        time_slots=time_slots,
        num_cams=num_cams,
        output_file=output_file,
        output_width=output_width,
        output_height=output_height,
        fps=fps,
        temp_dir=args.temp_dir,
    )


if __name__ == '__main__':
    main()