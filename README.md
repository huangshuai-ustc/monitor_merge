# 监控回放系统

一个基于 Web 的监控视频回放系统，支持多摄像头视频管理、H.265 视频播放、帧截图浏览等功能。

## 功能特性

- **多摄像头支持**：可配置多个摄像头，每个摄像头包含多个视频片段
- **时间轴导航**：可视化时间轴，支持拖拽定位
- **两种播放模式**：
  - **帧截图模式**：每秒截取一帧 JPEG 图片，兼容性好
  - **转码播放模式**：后端 FFmpeg 转码 H.265 → H.264，支持拖拽
- **播放控制**：播放/暂停/停止、变速（0.25x ~ 60x）、逐帧跳转
- **日期跳转**：快速跳转到指定日期

## 环境要求

- Python 3.8+
- FFmpeg（已安装本地）
- 现代浏览器（Chrome/Firefox/Edge/Safari）

## 安装部署

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置摄像头

编辑 `config.yaml`：

```yaml
cameras:
  - name: "摄像头名称"
    folder: "./视频目录路径"

# 文件名格式 - 用正则捕获开始和结束时间
filename_pattern: "_(\\d{14})_(\\d{14})\\.mp4$"
time_format: "%Y%m%d%H%M%S"

server:
  host: "0.0.0.0"
  port: 8080
```

### 3. 视频文件命名规范

视频文件名需包含开始和结束时间戳，格式示例：
```
video_0400_0_10_20250613113832_20250613115449.mp4
```
即：`..._<开始YYYYMMDDHHmmss>_<结束YYYYMMDDHHmmss>.mp4`

### 4. 启动服务

```bash
python server.py
```

然后在浏览器打开 http://localhost:8080

## 视频编码说明

### 问题背景

原始监控视频通常使用 H.265 (HEVC) 编码，但大多数桌面浏览器（Chrome、Firefox、Edge）原生不支持 H.265 解码。

### 解决方案

本系统提供两种播放模式：

| 模式 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| 帧截图 | 后端 FFmpeg 截取单帧 | 兼容性好 | 不连续 |
| 转码播放 | 后端 FFmpeg 转码 H.265→H.264 | 支持拖拽、快进 | 首次需要等待转码 |

### 使用转码播放模式

1. 在播放模式选择器中选择 **"转码播放模式 (H.265→H.264)"**
2. 首次播放时会自动在后台转码视频
3. 转码后的视频会缓存，避免重复转码
4. 支持拖拽进度条

**注意**：
- 首次播放需要等待转码完成（视频时长而定）
- 转码后的文件缓存在 `transcoded/` 目录

## 项目结构

```
monitor_player/
├── server.py          # FastAPI 后端服务
├── scanner.py         # 视频文件扫描器
├── config.yaml        # 配置文件
├── requirements.txt  # Python 依赖
├── transcoded/       # 转码缓存目录（自动创建）
├── static/            # 前端静态文件
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   └── timeline.js
└── 视频目录/          # 监控视频存放目录
    ├── 摄像头1/
    ├── 摄像头2/
    └── ...
```

## API 接口

| 接口 | 说明 |
|------|------|
| `GET /api/cameras` | 获取所有摄像头信息 |
| `GET /api/frame/{cam_id}` | 获取指定时间的帧截图 |
| `GET /api/clip_info/{cam_id}` | 查询某时刻对应的视频文件 |
| `GET /api/transcode/{cam_id}/{filename}` | 转码并返回视频文件 |
| `GET /video_files/{cam_id}/{filename}` | 提供原始视频文件 |

## 常见问题

### 1. 视频播放失败

确保系统已安装 FFmpeg：
```bash
ffmpeg -version
```

### 2. 转码播放模式加载慢

首次播放需要等待转码。转码后的文件会缓存在 `transcoded/` 目录，后续播放会更快。

### 3. 视频文件不被识别

检查 `config.yaml` 中的 `filename_pattern` 是否与实际文件名匹配。

## 技术栈

- 后端：FastAPI + Python + FFmpeg
- 前端：原生 JavaScript
- 视频处理：FFmpeg
