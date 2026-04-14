"""
视频文件扫描器 - 从文件名解析起止时间

支持的文件名格式:
  video_0400_0_10_20250613113832_20250613115449.mp4
  即: ..._<开始YYYYMMDDHHmmss>_<结束YYYYMMDDHHmmss>.mp4
"""

import os
import re
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class VideoClip:
    """一个视频片段"""
    filepath: str
    filename: str
    start_time: datetime
    end_time: datetime

    @property
    def duration(self) -> float:
        return (self.end_time - self.start_time).total_seconds()

    @property
    def start_ts(self) -> float:
        return self.start_time.timestamp()

    @property
    def end_ts(self) -> float:
        return self.end_time.timestamp()

    def to_dict(self) -> dict:
        return {
            'filepath': self.filepath,
            'filename': self.filename,
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat(),
            'start_ts': self.start_ts,
            'end_ts': self.end_ts,
            'duration': self.duration,
        }


@dataclass
class Camera:
    """一个摄像头"""
    id: int
    name: str
    folder: str
    clips: List[VideoClip] = field(default_factory=list)

    @property
    def total_duration(self) -> float:
        return sum(c.duration for c in self.clips)

    @property
    def time_ranges(self) -> List[dict]:
        """
        合并连续/重叠片段, 返回有视频的时间段列表
        用于前端时间轴上画色块
        """
        if not self.clips:
            return []

        ranges = []
        cur_start = self.clips[0].start_time
        cur_end = self.clips[0].end_time

        for clip in self.clips[1:]:
            # 间隔 < 2秒 视为连续
            gap = (clip.start_time - cur_end).total_seconds()
            if gap <= 2:
                cur_end = max(cur_end, clip.end_time)
            else:
                ranges.append({
                    'start_ts': cur_start.timestamp(),
                    'end_ts': cur_end.timestamp(),
                })
                cur_start = clip.start_time
                cur_end = clip.end_time

        ranges.append({
            'start_ts': cur_start.timestamp(),
            'end_ts': cur_end.timestamp(),
        })
        return ranges

    def find_clip_at(self, ts: float) -> Optional[Tuple[VideoClip, float]]:
        """
        二分查找: 给定 unix timestamp, 找到包含它的 clip
        返回 (clip, offset_in_clip_seconds)
        """
        target = datetime.fromtimestamp(ts)

        lo, hi = 0, len(self.clips) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            c = self.clips[mid]
            if c.start_time <= target <= c.end_time:
                offset = (target - c.start_time).total_seconds()
                return c, offset
            elif target < c.start_time:
                hi = mid - 1
            else:
                lo = mid + 1
        return None

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'clip_count': len(self.clips),
            'total_duration': self.total_duration,
            'time_ranges': self.time_ranges,
        }


def scan_folder(
    camera_id: int,
    name: str,
    folder: str,
    filename_pattern: str,
    time_format: str,
) -> Camera:
    """
    扫描文件夹, 从文件名提取起止时间

    filename_pattern 应包含2个捕获组: (开始时间)(结束时间)
    例: r"_(\d{14})_(\d{14})\.mp4$"
    """
    cam = Camera(id=camera_id, name=name, folder=folder)
    folder_path = Path(folder)

    if not folder_path.exists():
        print(f"  ⚠ 文件夹不存在: {folder}")
        return cam

    pattern = re.compile(filename_pattern)
    video_exts = {'.mp4', '.avi', '.mkv', '.mov', '.ts', '.flv'}

    # 递归搜索所有视频
    files = sorted(
        f for f in folder_path.rglob("*")
        if f.is_file() and f.suffix.lower() in video_exts
    )

    parsed = 0
    skipped = 0

    for f in files:
        m = pattern.search(f.name)
        if not m:
            skipped += 1
            continue

        try:
            start_str, end_str = m.group(1), m.group(2)
            start_time = datetime.strptime(start_str, time_format)
            end_time = datetime.strptime(end_str, time_format)

            # 基本校验
            if end_time <= start_time:
                skipped += 1
                continue
            if (end_time - start_time).total_seconds() > 86400:
                skipped += 1
                continue

            cam.clips.append(VideoClip(
                filepath=str(f.resolve()),
                filename=f.name,
                start_time=start_time,
                end_time=end_time,
            ))
            parsed += 1

        except (ValueError, IndexError):
            skipped += 1
            continue

    # 按开始时间排序
    cam.clips.sort(key=lambda c: c.start_time)

    print(f"  📹 {name}: {parsed} 个片段解析成功"
          f"{f', {skipped} 个跳过' if skipped else ''}")

    if cam.clips:
        print(f"     时间范围: {cam.clips[0].start_time} ~ {cam.clips[-1].end_time}")
        print(f"     总时长: {cam.total_duration/3600:.2f} 小时")
        print(f"     连续段数: {len(cam.time_ranges)}")

    return cam


def scan_all(config: dict) -> List[Camera]:
    """扫描配置中所有摄像头"""
    pattern = config.get('filename_pattern', r'_(\d{14})_(\d{14})\.mp4$')
    time_fmt = config.get('time_format', '%Y%m%d%H%M%S')

    cameras = []
    for i, cam_conf in enumerate(config.get('cameras', [])):
        cam = scan_folder(
            camera_id=i,
            name=cam_conf['name'],
            folder=cam_conf['folder'],
            filename_pattern=pattern,
            time_format=time_fmt,
        )
        cameras.append(cam)

    return cameras