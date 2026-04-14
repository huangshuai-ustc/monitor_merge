"""
FastAPI 后端
提供: 摄像头信息、帧截取、视频流、原始文件访问
"""

import os
import subprocess
import asyncio
import yaml
from pathlib import Path
from datetime import datetime
from typing import List

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import (
    JSONResponse, Response, StreamingResponse, FileResponse
)
from fastapi.middleware.cors import CORSMiddleware

from scanner import scan_all, Camera

# ==================== 全局 ====================

app = FastAPI(title="监控回放系统")

# 添加 CORS 支持
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

cameras: List[Camera] = []
config: dict = {}


def load_config() -> dict:
    for path in ['config.yaml', 'config.yml']:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
    return {}


@app.on_event("startup")
async def startup():
    global cameras, config
    config = load_config()

    print("=" * 60)
    print("🎬 监控回放系统")
    print("=" * 60)

    cameras = scan_all(config)

    total_clips = sum(len(c.clips) for c in cameras)
    print(f"\n✅ 扫描完成: {len(cameras)} 个摄像头, {total_clips} 个片段")

    port = config.get('server', {}).get('port', 8080)
    print(f"🌐 浏览器打开: http://localhost:{port}")
    print("=" * 60)


# ==================== API ====================

@app.get("/api/cameras")
async def api_cameras():
    """返回所有摄像头信息 + 全局时间范围"""
    all_starts = []
    all_ends = []
    for cam in cameras:
        if cam.clips:
            all_starts.append(cam.clips[0].start_ts)
            all_ends.append(cam.clips[-1].end_ts)

    global_range = None
    if all_starts:
        global_range = {
            'start_ts': min(all_starts),
            'end_ts': max(all_ends),
        }

    n = len(cameras)
    if n <= 1:
        cols, rows = 1, 1
    elif n == 2:
        cols, rows = 2, 1
    elif n <= 4:
        cols, rows = 2, 2
    elif n <= 6:
        cols, rows = 3, 2
    else:
        cols, rows = 3, 3

    return {
        'cameras': [c.to_dict() for c in cameras],
        'global_range': global_range,
        'layout': {'cols': cols, 'rows': rows},
    }


@app.get("/api/frame/{cam_id}")
async def api_frame(
    cam_id: int,
    t: float = Query(..., description="Unix timestamp"),
    w: int = Query(640),
    h: int = Query(360),
):
    """截取一帧 JPEG"""
    if cam_id < 0 or cam_id >= len(cameras):
        raise HTTPException(404, "摄像头不存在")

    result = cameras[cam_id].find_clip_at(t)
    if result is None:
        return Response(content=_black_jpeg(w, h), media_type="image/jpeg")

    clip, offset = result

    cmd = [
        'ffmpeg', '-v', 'error',
        '-ss', f'{offset:.3f}',
        '-i', clip.filepath,
        '-vframes', '1',
        '-vf', (
            f'scale={w}:{h}:'
            f'force_original_aspect_ratio=decrease,'
            f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black'
        ),
        '-f', 'image2', '-c:v', 'mjpeg', '-q:v', '5',
        'pipe:1',
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0 and stdout:
            return Response(content=stdout, media_type="image/jpeg")
    except Exception:
        pass

    return Response(content=_black_jpeg(w, h), media_type="image/jpeg")


@app.get("/api/stream/{cam_id}")
async def api_stream(
    cam_id: int,
    t: float = Query(...),
    duration: float = Query(30),
):
    """
    返回从指定时间开始的 mp4 片段流
    前端用 <video> 的 MediaSource 或直接 src 播放
    """
    if cam_id < 0 or cam_id >= len(cameras):
        raise HTTPException(404)

    result = cameras[cam_id].find_clip_at(t)
    if result is None:
        raise HTTPException(404, "该时间无视频")

    clip, offset = result
    actual_dur = min(duration, clip.duration - offset)

    cmd = [
        'ffmpeg', '-v', 'error',
        '-ss', f'{offset:.3f}',
        '-t', f'{actual_dur:.3f}',
        '-i', clip.filepath,
        '-c:v', 'libx264', '-preset', 'ultrafast',
        '-crf', '28',
        '-c:a', 'aac', '-b:a', '64k',
        '-movflags', 'frag_keyframe+empty_moov+faststart',
        '-f', 'mp4',
        'pipe:1',
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def generate():
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            yield chunk
        await proc.wait()

    return StreamingResponse(generate(), media_type="video/mp4")


@app.get("/api/clip_info/{cam_id}")
async def api_clip_info(cam_id: int, t: float = Query(...)):
    """查询某时刻对应的原始文件和偏移"""
    if cam_id < 0 or cam_id >= len(cameras):
        raise HTTPException(404)

    result = cameras[cam_id].find_clip_at(t)
    if result is None:
        return JSONResponse({'found': False})

    clip, offset = result
    return {
        'found': True,
        'filename': clip.filename,
        'offset': offset,
        'duration': clip.duration,
        'start_ts': clip.start_ts,
        'end_ts': clip.end_ts,
        'file_url': f'/video_files/{cam_id}/{clip.filename}',
    }


@app.get("/video_files/{cam_id}/{filename:path}")
async def serve_video_file(cam_id: int, filename: str):
    """直接提供原始视频文件 (支持 Range 请求, 可拖拽)"""
    if cam_id < 0 or cam_id >= len(cameras):
        raise HTTPException(404)

    for clip in cameras[cam_id].clips:
        if clip.filename == filename:
            if os.path.exists(clip.filepath):
                return FileResponse(clip.filepath, media_type="video/mp4")

    raise HTTPException(404)


@app.get("/api/video_data/{cam_id}/{filename:path}")
async def get_video_data(cam_id: int, filename: str, request: Request):
    """
    获取视频文件的原始字节数据（支持 Range 请求）
    供 ffmpeg.wasm 解码 H.265 视频使用
    """
    if cam_id < 0 or cam_id >= len(cameras):
        raise HTTPException(404, "摄像头不存在")

    clip = None
    for c in cameras[cam_id].clips:
        if c.filename == filename:
            clip = c
            break

    if clip is None or not os.path.exists(clip.filepath):
        raise HTTPException(404, "文件不存在")

    filepath = clip.filepath
    file_size = os.path.getsize(filepath)

    # 检查 Range 请求
    range_header = request.headers.get("Range")
    if range_header:
        # 解析 Range: bytes=start-end
        try:
            range_match = range_header.replace("bytes=", "").split("-")
            start = int(range_match[0]) if range_match[0] else 0
            end = int(range_match[1]) if range_match[1] else file_size - 1
        except (ValueError, IndexError):
            start, end = 0, file_size - 1

        # 限制范围
        start = max(0, start)
        end = min(end, file_size - 1)
        content_length = end - start + 1

        with open(filepath, "rb") as f:
            f.seek(start)
            data = f.read(content_length)

        return Response(
            content=data,
            status_code=206,
            media_type="video/mp4",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
            },
        )
    else:
        # 返回整个文件
        with open(filepath, "rb") as f:
            data = f.read()

        return Response(
            content=data,
            media_type="video/mp4",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            },
        )


@app.get("/stream/{camera_index}/{filename}")
async def stream_video(camera_index: int, filename: str):
    """FFmpeg 实时转码 H.265 -> H.264 流式播放"""
    config = load_config()
    cameras = config.get("cameras", [])
    
    if camera_index < 0 or camera_index >= len(cameras):
        raise HTTPException(status_code=404, detail="Camera not found")
    
    camera = cameras[camera_index]
    filepath = os.path.join(camera["folder"], filename)
    
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")

    def generate():
        cmd = [
            "ffmpeg",
            "-i", filepath,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",            # 稍微压一下，传输更快
            "-tune", "zerolatency",  # 降低延迟
            "-c:a", "aac",
            "-ac", "2",
            "-movflags", "frag_keyframe+empty_moov+faststart",
            "-f", "mp4",
            "-loglevel", "quiet",
            "-"
        ]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=65536
        )
        try:
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            process.kill()
            process.wait()

    return StreamingResponse(
        generate(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f"inline; filename={filename}",
            "Cache-Control": "no-cache",
        }
    )


def _black_jpeg(w: int, h: int) -> bytes:
    """生成纯黑 JPEG"""
    try:
        result = subprocess.run(
            [
                'ffmpeg', '-v', 'error',
                '-f', 'lavfi', '-i', f'color=black:s={w}x{h}:d=0.04',
                '-vframes', '1',
                '-f', 'image2', '-c:v', 'mjpeg', '-q:v', '10',
                'pipe:1',
            ],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except Exception:
        pass
    # 1x1 黑色 JPEG fallback
    return bytes([
        0xFF,0xD8,0xFF,0xE0,0x00,0x10,0x4A,0x46,0x49,0x46,0x00,0x01,
        0x01,0x00,0x00,0x01,0x00,0x01,0x00,0x00,0xFF,0xDB,0x00,0x43,
        0x00,0x08,0x06,0x06,0x07,0x06,0x05,0x08,0x07,0x07,0x07,0x09,
        0x09,0x08,0x0A,0x0C,0x14,0x0D,0x0C,0x0B,0x0B,0x0C,0x19,0x12,
        0x13,0x0F,0x14,0x1D,0x1A,0x1F,0x1E,0x1D,0x1A,0x1C,0x1C,0x20,
        0x24,0x2E,0x27,0x20,0x22,0x2C,0x23,0x1C,0x1C,0x28,0x37,0x29,
        0x2C,0x30,0x31,0x34,0x34,0x34,0x1F,0x27,0x39,0x3D,0x38,0x32,
        0x3C,0x2E,0x33,0x34,0x32,0xFF,0xC0,0x00,0x0B,0x08,0x00,0x01,
        0x00,0x01,0x01,0x01,0x11,0x00,0xFF,0xC4,0x00,0x1F,0x00,0x00,
        0x01,0x05,0x01,0x01,0x01,0x01,0x01,0x01,0x00,0x00,0x00,0x00,
        0x00,0x00,0x00,0x00,0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,
        0x09,0x0A,0x0B,0xFF,0xC4,0x00,0xB5,0x10,0x00,0x02,0x01,0x03,
        0x03,0x02,0x04,0x03,0x05,0x05,0x04,0x04,0x00,0x00,0x01,0x7D,
        0xFF,0xDA,0x00,0x08,0x01,0x01,0x00,0x00,0x3F,0x00,0x7B,0x94,
        0x11,0x00,0x00,0x00,0x00,0x00,0x00,0xFF,0xD9,
    ])


# ==================== 静态文件 & 首页 ====================

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(static_dir / "index.html"))


# ==================== 启动入口 ====================

if __name__ == "__main__":
    import uvicorn
    conf = load_config()
    s = conf.get('server', {})
    uvicorn.run(
        "server:app",
        host=s.get('host', '0.0.0.0'),
        port=s.get('port', 8080),
        reload=False,
    )