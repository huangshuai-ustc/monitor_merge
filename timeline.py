"""
构建全局时间轴，确定每个时间点每个监控的视频源
"""

from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from time_extract import VideoSegment


@dataclass
class TimeSlot:
    """一个时间段内各监控的视频安排"""
    start_time: datetime
    end_time: datetime
    duration: float
    # cam_index -> (video_filepath, offset_in_video)
    sources: Dict[int, Tuple[str, float]] = field(default_factory=dict)


def build_timeline(
    all_segments: Dict[int, List[VideoSegment]],
    fps: float = 25
) -> List[TimeSlot]:
    """
    根据所有监控的视频片段，构建全局时间轴
    
    all_segments: {cam_index: [VideoSegment, ...]}
    返回: 按时间排序的 TimeSlot 列表
    """
    # 1. 收集所有时间边界点
    boundaries = set()
    for cam_idx, segments in all_segments.items():
        for seg in segments:
            boundaries.add(seg.start_time)
            boundaries.add(seg.end_time)

    if not boundaries:
        return []

    boundaries = sorted(boundaries)

    # 2. 在相邻边界点之间创建时间段
    time_slots = []
    for i in range(len(boundaries) - 1):
        t_start = boundaries[i]
        t_end = boundaries[i + 1]
        duration = (t_end - t_start).total_seconds()

        if duration < 0.04:  # 小于一帧，跳过
            continue

        slot = TimeSlot(
            start_time=t_start,
            end_time=t_end,
            duration=duration,
            sources={}
        )

        # 3. 对每个监控，查找覆盖此时间段的视频
        for cam_idx, segments in all_segments.items():
            for seg in segments:
                if seg.start_time <= t_start and seg.end_time >= t_end:
                    # 此视频覆盖了这个时间段
                    offset = (t_start - seg.start_time).total_seconds()
                    slot.sources[cam_idx] = (seg.filepath, offset)
                    break

        time_slots.append(slot)

    # 4. 合并连续的、源相同的时间段（减少 FFmpeg 调用次数）
    merged = merge_consecutive_slots(time_slots, all_segments)

    return merged


def merge_consecutive_slots(
    slots: List[TimeSlot],
    all_segments: Dict[int, List[VideoSegment]]
) -> List[TimeSlot]:
    """
    合并连续且视频源完全相同的时间段
    （同一组视频文件，只是时间在延续）
    """
    if not slots:
        return []

    cam_indices = sorted(all_segments.keys())
    merged = [slots[0]]

    for slot in slots[1:]:
        prev = merged[-1]

        # 检查是否可以合并：时间连续 + 每个监控的视频源文件相同
        can_merge = (slot.start_time == prev.end_time)

        if can_merge:
            for cam_idx in cam_indices:
                prev_src = prev.sources.get(cam_idx)
                curr_src = slot.sources.get(cam_idx)

                if prev_src is None and curr_src is None:
                    continue
                elif prev_src is not None and curr_src is not None:
                    if prev_src[0] != curr_src[0]:
                        can_merge = False
                        break
                else:
                    can_merge = False
                    break

        if can_merge:
            # 合并：延长前一个 slot
            merged[-1] = TimeSlot(
                start_time=prev.start_time,
                end_time=slot.end_time,
                duration=(slot.end_time - prev.start_time).total_seconds(),
                sources=prev.sources  # 保持原始 offset
            )
        else:
            merged.append(slot)

    return merged


def print_timeline_summary(
    time_slots: List[TimeSlot],
    num_cams: int
):
    """打印时间轴摘要"""
    if not time_slots:
        print("⚠ 时间轴为空")
        return

    total_duration = sum(s.duration for s in time_slots)
    overall_start = time_slots[0].start_time
    overall_end = time_slots[-1].end_time

    print(f"\n📊 时间轴摘要:")
    print(f"  总时间范围: {overall_start} ~ {overall_end}")
    print(f"  总时长: {total_duration:.1f}秒 ({total_duration/3600:.2f}小时)")
    print(f"  时间段数量: {len(time_slots)}")
    print(f"  监控数量: {num_cams}")

    # 统计每个监控的覆盖率
    for cam_idx in range(num_cams):
        covered = sum(
            s.duration for s in time_slots
            if cam_idx in s.sources
        )
        pct = (covered / total_duration * 100) if total_duration > 0 else 0
        print(f"  监控{cam_idx+1} 覆盖: {covered:.1f}秒 ({pct:.1f}%)")

    # 显示前几个时间段
    print(f"\n  前5个时间段:")
    for slot in time_slots[:5]:
        active = [f"cam{k+1}" for k in sorted(slot.sources.keys())]
        inactive = [f"cam{i+1}" for i in range(num_cams) if i not in slot.sources]
        print(f"    {slot.start_time.strftime('%H:%M:%S')} ~ "
              f"{slot.end_time.strftime('%H:%M:%S')} "
              f"({slot.duration:.1f}s) "
              f"有画面: {','.join(active) or '无'} "
              f"黑屏: {','.join(inactive) or '无'}")