"""
从视频中提取时间信息
支持三种方式：OCR读取画面时间、从文件名解析、从metadata读取
"""

import cv2
import os
import re
import subprocess
import json
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Optional, Tuple
from pathlib import Path

try:
    import pytesseract
except ImportError:
    pytesseract = None


@dataclass
class VideoSegment:
    """一个视频片段的时间信息"""
    filepath: str
    start_time: datetime
    end_time: datetime
    duration: float  # 秒
    width: int
    height: int

    def __repr__(self):
        return (f"VideoSegment({Path(self.filepath).name}, "
                f"{self.start_time.strftime('%H:%M:%S')}~"
                f"{self.end_time.strftime('%H:%M:%S')})")


def get_video_info(filepath: str) -> Tuple[float, int, int]:
    """用 ffprobe 获取视频时长和分辨率"""
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_format', '-show_streams',
        filepath
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)

        duration = float(info['format']['duration'])
        
        # 找视频流
        width, height = 1920, 1080
        for stream in info.get('streams', []):
            if stream['codec_type'] == 'video':
                width = int(stream['width'])
                height = int(stream['height'])
                break

        return duration, width, height
    except Exception as e:
        print(f"  ⚠ ffprobe 失败 {filepath}: {e}")
        # fallback: 用 OpenCV
        cap = cv2.VideoCapture(filepath)
        if cap.isOpened():
            fps = cap.get(cv2.CAP_PROP_FPS) or 25
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cap.release()
            return frames / fps, width, height
        return 0, 1920, 1080


def extract_time_ocr(
    filepath: str,
    ocr_region: dict,
    time_format: str,
    sample_frame: int = 0
) -> Optional[datetime]:
    """
    从视频帧的左上角 OCR 读取时间
    """
    if pytesseract is None:
        raise ImportError("需要安装 pytesseract: pip install pytesseract")

    cap = cv2.VideoCapture(filepath)
    if not cap.isOpened():
        return None

    # 跳到指定帧（默认第0帧，可以跳几帧避免黑屏）
    if sample_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, sample_frame)

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        return None

    h, w = frame.shape[:2]

    # 裁剪 OCR 区域
    x1 = int(w * ocr_region.get('x', 0))
    y1 = int(h * ocr_region.get('y', 0))
    x2 = int(w * (ocr_region.get('x', 0) + ocr_region.get('w', 0.35)))
    y2 = int(h * (ocr_region.get('y', 0) + ocr_region.get('h', 0.06)))

    roi = frame[y1:y2, x1:x2]

    # 预处理：灰度 + 二值化 + 放大（提高 OCR 准确率）
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    # 尝试白色文字（监控常见）
    _, binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)

    # 放大 3 倍
    scale = 3
    binary = cv2.resize(binary, (binary.shape[1] * scale, binary.shape[0] * scale),
                        interpolation=cv2.INTER_CUBIC)

    # OCR
    custom_config = r'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789-/: '
    text = pytesseract.image_to_string(binary, config=custom_config).strip()

    if not text:
        # 尝试反色（黑色文字）
        _, binary_inv = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)
        binary_inv = cv2.resize(binary_inv,
                                (binary_inv.shape[1] * scale, binary_inv.shape[0] * scale),
                                interpolation=cv2.INTER_CUBIC)
        text = pytesseract.image_to_string(binary_inv, config=custom_config).strip()

    if not text:
        return None

    # 解析时间
    # 清理 OCR 结果中的常见错误
    text = text.replace('O', '0').replace('l', '1').replace('I', '1')
    text = re.sub(r'[^\d\-/: ]', '', text).strip()

    try:
        return datetime.strptime(text, time_format)
    except ValueError:
        # 尝试常见变体
        for fmt in [
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%d-%m-%Y %H:%M:%S",
            "%Y%m%d %H%M%S",
        ]:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        print(f"  ⚠ OCR 识别到 '{text}' 但无法解析为时间")
        return None


def extract_time_filename(filepath: str, pattern: str) -> Optional[datetime]:
    """从文件名中解析时间"""
    filename = Path(filepath).stem
    match = re.search(pattern, filename)
    if match:
        groups = match.groups()
        if len(groups) >= 6:
            try:
                return datetime(
                    int(groups[0]), int(groups[1]), int(groups[2]),
                    int(groups[3]), int(groups[4]), int(groups[5])
                )
            except ValueError:
                pass
    return None


def extract_time_metadata(filepath: str) -> Optional[datetime]:
    """从视频 metadata 中读取创建时间"""
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_format',
        filepath
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        tags = info.get('format', {}).get('tags', {})

        for key in ['creation_time', 'date', 'CREATION_TIME']:
            if key in tags:
                time_str = tags[key]
                for fmt in [
                    "%Y-%m-%dT%H:%M:%S.%fZ",
                    "%Y-%m-%dT%H:%M:%SZ",
                    "%Y-%m-%d %H:%M:%S",
                ]:
                    try:
                        return datetime.strptime(time_str, fmt)
                    except ValueError:
                        continue
    except Exception:
        pass
    return None


def scan_folder(
    folder: str,
    method: str = "ocr",
    ocr_region: dict = {},
    time_format: str = "%Y-%m-%d %H:%M:%S",
    filename_pattern: str = r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})"
) -> List[VideoSegment]:
    """
    扫描一个文件夹，返回所有视频片段的时间信息
    """
    video_extensions = {'.mp4', '.avi', '.mkv', '.mov', '.flv', '.ts', '.wmv', '.m4v'}

    files = []
    folder_path = Path(folder)
    for f in sorted(folder_path.iterdir()):
        if f.suffix.lower() in video_extensions and f.is_file():
            files.append(str(f))

    print(f"📁 扫描 {folder}: 找到 {len(files)} 个视频文件")

    segments = []
    for i, filepath in enumerate(files):
        if (i + 1) % 50 == 0 or i == 0:
            print(f"  处理 {i+1}/{len(files)}: {Path(filepath).name}")

        # 获取视频基本信息
        duration, width, height = get_video_info(filepath)
        if duration <= 0:
            print(f"  ⚠ 跳过无效视频: {filepath}")
            continue

        # 提取开始时间
        start_time = None

        if method == "ocr":
            # 尝试多个帧位置
            for frame_idx in [5, 0, 30]:
                start_time = extract_time_ocr(
                    filepath, ocr_region or {}, time_format, frame_idx
                )
                if start_time:
                    break
        elif method == "filename":
            start_time = extract_time_filename(filepath, filename_pattern)
        elif method == "metadata":
            start_time = extract_time_metadata(filepath)
        else:
            # 依次尝试
            start_time = extract_time_filename(filepath, filename_pattern)
            if not start_time:
                start_time = extract_time_metadata(filepath)
            if not start_time and pytesseract:
                start_time = extract_time_ocr(
                    filepath, ocr_region or {}, time_format, 5
                )

        if start_time is None:
            print(f"  ⚠ 无法提取时间，跳过: {Path(filepath).name}")
            continue

        end_time = start_time + timedelta(seconds=duration)

        segments.append(VideoSegment(
            filepath=filepath,
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            width=width,
            height=height,
        ))

    # 按开始时间排序
    segments.sort(key=lambda s: s.start_time)
    
    if segments:
        print(f"  ✅ 有效片段 {len(segments)} 个, "
              f"时间范围: {segments[0].start_time} ~ {segments[-1].end_time}")

    return segments