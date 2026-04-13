"""
使用 FFmpeg 进行多画幅视频拼接
"""

import subprocess
import os
import math
import tempfile
from pathlib import Path
from typing import List, Dict, Tuple
from datetime import datetime
from timeline import TimeSlot


def get_layout(num_cams: int) -> Tuple[int, int]:
    """
    根据监控数量决定布局
    返回 (cols, rows)
    """
    layouts = {
        1: (1, 1),
        2: (2, 1),
        3: (2, 2),  # 3个也用 2x2，空一格黑屏
        4: (2, 2),
        5: (3, 2),
        6: (3, 2),
        7: (3, 3),
        8: (3, 3),
        9: (3, 3),
    }
    if num_cams in layouts:
        return layouts[num_cams]
    # 超过9个
    cols = math.ceil(math.sqrt(num_cams))
    rows = math.ceil(num_cams / cols)
    return cols, rows


def build_segment_ffmpeg_cmd(
    slot: TimeSlot,
    num_cams: int,
    output_width: int,
    output_height: int,
    fps: float,
    output_path: str,
    show_timestamp: bool = True
) -> List[str]:
    """
    为一个时间段构建 FFmpeg 命令
    使用 filter_complex 实现多画幅拼接
    """
    cols, rows = get_layout(num_cams)
    cell_w = output_width // cols
    cell_h = output_height // rows

    cmd = ['ffmpeg', '-y']

    # 输入源
    input_map = {}  # cam_idx -> ffmpeg input index
    input_idx = 0

    for cam_idx in range(num_cams):
        if cam_idx in slot.sources:
            filepath, offset = slot.sources[cam_idx]
            cmd.extend([
                '-ss', f'{offset:.3f}',
                '-t', f'{slot.duration:.3f}',
                '-i', filepath
            ])
            input_map[cam_idx] = input_idx
            input_idx += 1

    # 构建 filter_complex
    filter_parts = []
    overlay_inputs = []

    # 创建黑色底板
    filter_parts.append(
        f"color=c=black:s={output_width}x{output_height}:d={slot.duration}:r={fps}[base]"
    )

    current_base = "base"

    for cam_idx in range(num_cams):
        col = cam_idx % cols
        row = cam_idx // cols
        x = col * cell_w
        y = row * cell_h

        if cam_idx in input_map:
            idx = input_map[cam_idx]
            # 缩放视频到格子大小
            filter_parts.append(
                f"[{idx}:v]scale={cell_w}:{cell_h}:force_original_aspect_ratio=decrease,"
                f"pad={cell_w}:{cell_h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1[scaled{cam_idx}]"
            )
            # 叠加到底板
            next_base = f"tmp{cam_idx}"
            filter_parts.append(
                f"[{current_base}][scaled{cam_idx}]overlay={x}:{y}[{next_base}]"
            )
            current_base = next_base
        # 没有视频的格子自动保持黑色（底板就是黑色）

    # 可选：添加时间戳水印
    if show_timestamp:
        time_str = slot.start_time.strftime('%Y-%m-%d %H:%M:%S')
        final_label = "final"
        filter_parts.append(
            f"[{current_base}]drawtext="
            f"text='{time_str}':"
            f"fontsize=24:fontcolor=white:"
            f"x=10:y={output_height - 40}:"
            f"box=1:boxcolor=black@0.5:boxborderw=5"
            f"[{final_label}]"
        )
        current_base = final_label

    # 如果最后的标签不是 "final"，重命名
    if current_base != "final":
        # 修改最后一个 filter 的输出标签
        last = filter_parts[-1]
        old_label = f"[{current_base}]"
        filter_parts[-1] = last.rsplit(']', 1)[0] + ']'
        # 直接用 current_base 作为输出
        output_label = current_base
    else:
        output_label = "final"

    filter_complex = ";\n".join(filter_parts)

    cmd.extend([
        '-filter_complex', filter_complex,
        '-map', f'[{output_label}]',
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '23',
        '-r', str(fps),
        '-pix_fmt', 'yuv420p',
        '-an',  # 暂不处理音频
        output_path
    ])

    return cmd


def merge_videos(
    time_slots: List[TimeSlot],
    num_cams: int,
    output_file: str,
    output_width: int = 1920,
    output_height: int = 1080,
    fps: float = 25,
    temp_dir: str = "",
    max_segment_duration: float = 300,  # 每个临时片段最长5分钟
):
    """
    主合并函数
    
    策略：
    1. 将时间轴分成可管理的小段
    2. 每段用 FFmpeg 生成临时视频
    3. 最后用 FFmpeg concat 合并所有临时视频
    """
    if not time_slots:
        print("⚠ 没有可合并的时间段")
        return

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 创建临时目录
    if temp_dir:
        tmp = Path(temp_dir)
        tmp.mkdir(parents=True, exist_ok=True)
    else:
        tmp = Path(tempfile.mkdtemp(prefix="monitor_merge_"))

    print(f"\n🔨 开始合并, 临时目录: {tmp}")
    print(f"   布局: {get_layout(num_cams)} ({num_cams}路监控)")
    print(f"   输出: {output_width}x{output_height} @ {fps}fps")

    # 进一步拆分过长的时间段
    segments_to_process = []
    for slot in time_slots:
        if slot.duration <= max_segment_duration:
            segments_to_process.append(slot)
        else:
            # 拆分
            remaining = slot.duration
            current_start = slot.start_time
            while remaining > 0:
                chunk_duration = min(remaining, max_segment_duration)
                from datetime import timedelta
                chunk_end = current_start + timedelta(seconds=chunk_duration)

                # 计算各源的 offset
                chunk_sources = {}
                for cam_idx, (filepath, orig_offset) in slot.sources.items():
                    time_diff = (current_start - slot.start_time).total_seconds()
                    chunk_sources[cam_idx] = (filepath, orig_offset + time_diff)

                segments_to_process.append(TimeSlot(
                    start_time=current_start,
                    end_time=chunk_end,
                    duration=chunk_duration,
                    sources=chunk_sources
                ))

                current_start = chunk_end
                remaining -= chunk_duration

    print(f"   总共 {len(segments_to_process)} 个处理段")

    # 生成每个片段
    segment_files = []
    total = len(segments_to_process)

    for i, slot in enumerate(segments_to_process):
        seg_file = str(tmp / f"seg_{i:06d}.mp4")
        segment_files.append(seg_file)

        progress = (i + 1) / total * 100
        print(f"\r   ⏳ 处理片段 {i+1}/{total} ({progress:.1f}%) "
              f"{slot.start_time.strftime('%H:%M:%S')} "
              f"[{slot.duration:.1f}s] "
              f"活跃: {len(slot.sources)}/{num_cams}路",
              end='', flush=True)

        cmd = build_segment_ffmpeg_cmd(
            slot=slot,
            num_cams=num_cams,
            output_width=output_width,
            output_height=output_height,
            fps=fps,
            output_path=seg_file,
            show_timestamp=True
        )

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5分钟超时
            )
            if result.returncode != 0:
                print(f"\n   ⚠ FFmpeg 错误 (片段 {i}):")
                # 只打印最后几行错误信息
                err_lines = result.stderr.strip().split('\n')
                for line in err_lines[-5:]:
                    print(f"      {line}")
                # 生成纯黑替代片段
                _generate_black_segment(
                    seg_file, slot.duration,
                    output_width, output_height, fps
                )
        except subprocess.TimeoutExpired:
            print(f"\n   ⚠ 片段 {i} 处理超时，用黑屏替代")
            _generate_black_segment(
                seg_file, slot.duration,
                output_width, output_height, fps
            )
        except Exception as e:
            print(f"\n   ⚠ 片段 {i} 异常: {e}")
            _generate_black_segment(
                seg_file, slot.duration,
                output_width, output_height, fps
            )

    print(f"\n\n   ✅ 所有片段处理完成，开始合并...")

    # 用 concat demuxer 合并所有片段
    concat_file = str(tmp / "concat_list.txt")
    with open(concat_file, 'w', encoding='utf-8') as f:
        for seg_file in segment_files:
            if os.path.exists(seg_file) and os.path.getsize(seg_file) > 0:
                # concat demuxer 需要转义单引号
                safe_path = seg_file.replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

    concat_cmd = [
        'ffmpeg', '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', concat_file,
        '-c', 'copy',  # 直接复制，不重新编码
        '-movflags', '+faststart',
        str(output_file)
    ]

    print(f"   🔗 正在拼接最终视频...")
    try:
        result = subprocess.run(
            concat_cmd,
            capture_output=True,
            text=True,
            timeout=600
        )
        if result.returncode != 0:
            print(f"   ⚠ concat 失败，尝试重新编码合并...")
            # fallback: 重新编码合并
            concat_cmd_reencode = [
                'ffmpeg', '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', concat_file,
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '23',
                '-pix_fmt', 'yuv420p',
                '-movflags', '+faststart',
                str(output_file)
            ]
            subprocess.run(concat_cmd_reencode, check=True, timeout=1800)
    except Exception as e:
        print(f"   ❌ 最终合并失败: {e}")
        return

    # 输出文件信息
    if os.path.exists(output_file):
        size_mb = os.path.getsize(output_file) / (1024 * 1024)
        print(f"\n   🎉 完成! 输出: {output_file}")
        print(f"   📦 文件大小: {size_mb:.1f} MB")
    else:
        print(f"\n   ❌ 输出文件未生成")

    # 清理临时文件
    cleanup = input("\n   是否清理临时文件? (y/n): ").strip().lower()
    if cleanup == 'y':
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        print("   🧹 临时文件已清理")
    else:
        print(f"   📂 临时文件保留在: {tmp}")


def _generate_black_segment(
    output_path: str,
    duration: float,
    width: int,
    height: int,
    fps: float
):
    """生成纯黑色替代片段（当某个片段处理失败时使用）"""
    cmd = [
        'ffmpeg', '-y',
        '-f', 'lavfi',
        '-i', f'color=c=black:s={width}x{height}:d={duration}:r={fps}',
        '-c:v', 'libx264',
        '-preset', 'ultrafast',
        '-crf', '28',
        '-pix_fmt', 'yuv420p',
        output_path
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60, check=True)
    except Exception:
        pass


def merge_videos_streaming(
    time_slots: List[TimeSlot],
    num_cams: int,
    output_file: str,
    output_width: int = 1920,
    output_height: int = 1080,
    fps: float = 25,
):
    """
    流式合并方案（适合超长视频）
    使用单个 FFmpeg 进程 + 复杂 filter graph
    
    注意：此方法对于几百个视频片段可能导致 FFmpeg 命令过长，
    仅适用于片段数量较少的情况。
    大量片段请使用 merge_videos() 分段合并方案。
    """
    if not time_slots:
        return

    cols, rows = get_layout(num_cams)
    cell_w = output_width // cols
    cell_h = output_height // rows

    # 收集所有唯一的视频文件
    all_files = {}  # filepath -> input_index
    input_idx = 0
    for slot in time_slots:
        for cam_idx, (filepath, offset) in slot.sources.items():
            if filepath not in all_files:
                all_files[filepath] = input_idx
                input_idx += 1

    if input_idx > 100:
        print("⚠ 输入文件过多，建议使用分段合并方案 (merge_videos)")
        return

    cmd = ['ffmpeg', '-y']

    # 添加所有输入文件
    for filepath in sorted(all_files.keys(), key=lambda x: all_files[x]):
        cmd.extend(['-i', filepath])

    # 这里构建完整的 filter_complex 会非常复杂
    # 对于大量片段，分段方案更实际
    print("⚠ 流式方案暂不支持大量片段，请使用 merge_videos()")