/**
 * 主应用逻辑
 *
 * 功能:
 * - 加载摄像头数据
 * - 管理视频网格
 * - 播放控制（播放/暂停、变速）
 * - 帧模式 & 视频模式切换
 * - H.265 WASM 解码播放
 */

class MonitorApp {
    constructor() {
        this.cameras = [];
        this.globalRange = null;
        this.layout = { cols: 2, rows: 2 };

        // 播放状态
        this.isPlaying = false;
        this.playSpeed = 1;
        this.currentTime = 0;     // Unix timestamp
        this.playTimer = null;
        this.frameInterval = 1000; // 帧刷新间隔 ms

        // 各摄像头的 <img> 或 <video> 元素
        this.cells = [];

        // 模式: 'frame'=逐帧截图, 'video'=WASM解码
        this.mode = 'frame';

        // 视频播放器状态
        this.videoPlayers = [];

        // 时间轴
        this.timeline = null;

        // 帧加载节流
        this.frameLoadPending = false;
        this.lastFrameLoadTime = 0;

        // 视频转码缓存
        this.wasmVideoCache = new Map();  // camId -> { filename }
    }

    async init() {
        this.showLoading(true);

        try {
            const resp = await fetch('/api/cameras');
            const data = await resp.json();

            this.cameras = data.cameras;
            this.globalRange = data.global_range;
            this.layout = data.layout;

            if (this.globalRange) {
                this.currentTime = this.globalRange.end_ts - 3600; // 默认从最后1小时开始
                if (this.currentTime < this.globalRange.start_ts) {
                    this.currentTime = this.globalRange.start_ts;
                }
            }

            this._buildGrid();
            this._initTimeline();
            this._initControls();

            this._loadFrames();

        } catch (err) {
            console.error('初始化失败:', err);
            alert('加载失败: ' + err.message);
        }

        this.showLoading(false);
    }

    // ==================== 视频网格 ====================

    _buildGrid() {
        const grid = document.getElementById('video-grid');
        grid.style.gridTemplateColumns = `repeat(${this.layout.cols}, 1fr)`;
        grid.style.gridTemplateRows = `repeat(${this.layout.rows}, 1fr)`;
        grid.innerHTML = '';

        this.cells = [];
        this.videoPlayers = [];

        this.cameras.forEach((cam, i) => {
            const cell = document.createElement('div');
            cell.className = 'video-cell';

            // 摄像头名称标签
            const label = document.createElement('div');
            label.className = 'cam-label';
            label.textContent = cam.name;
            cell.appendChild(label);

            // 帧模式的 img
            const img = document.createElement('img');
            img.style.display = 'block';
            img.alt = cam.name;
            img.draggable = false;
            cell.appendChild(img);

            // 视频模式的 video
            const video = document.createElement('video');
            video.style.display = 'none';
            video.muted = true;
            video.playsInline = true;
            cell.appendChild(video);

            // 无信号提示
            const noSignal = document.createElement('div');
            noSignal.className = 'no-signal';
            noSignal.textContent = '无录像';
            noSignal.style.display = 'none';
            cell.appendChild(noSignal);

            // 双击全屏
            cell.addEventListener('dblclick', () => this._toggleFullscreen(cell));

            grid.appendChild(cell);
            this.cells.push({ cell, img, video, noSignal, camId: i });
            this.videoPlayers.push({ video, currentClipUrl: null });
        });
    }

    _toggleFullscreen(cell) {
        if (cell.classList.contains('fullscreen-cell')) {
            cell.classList.remove('fullscreen-cell');
        } else {
            document.querySelectorAll('.fullscreen-cell').forEach(c => c.classList.remove('fullscreen-cell'));
            cell.classList.add('fullscreen-cell');
        }
    }

    // ==================== 时间轴 ====================

    _initTimeline() {
        const container = document.getElementById('timeline-container');

        this.timeline = new Timeline(container, {
            onTimeChange: (t) => {
                this.currentTime = t;
                this._loadFrames();
            },
            onSeek: (t) => {
                this.currentTime = t;
                if (this.mode === 'video') {
                    this._seekVideos();
                } else {
                    this._loadFrames();
                }
            },
        });

        this.timeline.setData(this.cameras, this.globalRange);
        this.timeline.setCursorTime(this.currentTime);
    }

    // ==================== 控制栏 ====================

    _initControls() {
        // 播放/暂停
        document.getElementById('btn-play').addEventListener('click', () => this.togglePlay());

        // 停止
        document.getElementById('btn-stop').addEventListener('click', () => this.stop());

        // 上一帧 / 下一帧
        document.getElementById('btn-prev-frame').addEventListener('click', () => this.stepFrame(-1));
        document.getElementById('btn-next-frame').addEventListener('click', () => this.stepFrame(1));

        // 变速
        document.getElementById('speed-select').addEventListener('change', (e) => {
            this.playSpeed = parseFloat(e.target.value);
        });

        // 模式切换
        document.getElementById('mode-select').addEventListener('change', (e) => {
            this.mode = e.target.value;
            this._switchMode();
        });

        // 日期选择
        const dateInput = document.getElementById('date-input');
        if (this.globalRange) {
            const d = new Date(this.currentTime * 1000);
            dateInput.value = d.toISOString().split('T')[0];
        }
        dateInput.addEventListener('change', (e) => {
            this.timeline.goToDate(e.target.value);
            const d = new Date(e.target.value + 'T00:00:00');
            this.currentTime = d.getTime() / 1000;
            this._loadFrames();
        });

        // 键盘快捷键
        document.addEventListener('keydown', (e) => {
            if (e.target.tagName === 'INPUT') return;
            switch (e.key) {
                case ' ':
                    e.preventDefault();
                    this.togglePlay();
                    break;
                case 'ArrowLeft':
                    e.preventDefault();
                    this.stepFrame(e.shiftKey ? -10 : -1);
                    break;
                case 'ArrowRight':
                    e.preventDefault();
                    this.stepFrame(e.shiftKey ? 10 : 1);
                    break;
                case '+':
                case '=':
                    this.timeline.zoom(0.5);
                    break;
                case '-':
                    this.timeline.zoom(2);
                    break;
            }
        });
    }

    // ==================== 播放控制 ====================

    togglePlay() {
        if (this.isPlaying) {
            this.pause();
        } else {
            this.play();
        }
    }

    play() {
        this.isPlaying = true;
        document.getElementById('btn-play').textContent = '⏸';
        document.getElementById('btn-play').classList.add('active');

        if (this.mode === 'video') {
            this._playVideos();
        } else {
            this._startFramePlayback();
        }
    }

    pause() {
        this.isPlaying = false;
        document.getElementById('btn-play').textContent = '▶';
        document.getElementById('btn-play').classList.remove('active');

        if (this.playTimer) {
            clearInterval(this.playTimer);
            this.playTimer = null;
        }

        if (this.mode === 'video') {
            this.videoPlayers.forEach(vp => {
                try { vp.video.pause(); } catch(e) {}
            });
        }
    }

    stop() {
        this.pause();
        if (this.globalRange) {
            this.currentTime = this.globalRange.start_ts;
            this.timeline.setCursorTime(this.currentTime);
            this._loadFrames();
        }
    }

    stepFrame(steps) {
        // 每步 = 1秒 * playSpeed
        this.currentTime += steps * this.playSpeed;
        this.timeline.setCursorTime(this.currentTime);
        this._loadFrames();
    }

    // ==================== 帧模式 ====================

    _startFramePlayback() {
        if (this.playTimer) clearInterval(this.playTimer);

        const interval = Math.max(200, this.frameInterval / this.playSpeed);

        this.playTimer = setInterval(() => {
            this.currentTime += this.playSpeed;
            this.timeline.setCursorTime(this.currentTime);
            this._loadFrames();

            // 到达末尾自动停止
            if (this.globalRange && this.currentTime >= this.globalRange.end_ts) {
                this.pause();
            }
        }, interval);
    }

    async _loadFrames() {
        // 节流: 至少间隔 100ms
        const now = Date.now();
        if (now - this.lastFrameLoadTime < 100) {
            if (!this.frameLoadPending) {
                this.frameLoadPending = true;
                setTimeout(() => {
                    this.frameLoadPending = false;
                    this._loadFrames();
                }, 100);
            }
            return;
        }
        this.lastFrameLoadTime = now;

        const t = this.currentTime;

        // 并行加载所有摄像头的帧
        const promises = this.cells.map(async ({ img, noSignal, camId }) => {
            if (this.mode !== 'frame') return;

            const cam = this.cameras[camId];
            const hasVideo = this._hasVideoAt(cam, t);

            if (hasVideo) {
                const url = `/api/frame/${camId}?t=${t}&w=640&h=360&_=${now}`;
                try {
                    // 预加载避免闪烁
                    const response = await fetch(url);
                    if (response.ok) {
                        const blob = await response.blob();
                        const objectUrl = URL.createObjectURL(blob);
                        const oldSrc = img.src;
                        img.src = objectUrl;
                        img.style.display = 'block';
                        noSignal.style.display = 'none';
                                                // 释放旧的 blob URL
                        if (oldSrc && oldSrc.startsWith('blob:')) {
                            URL.revokeObjectURL(oldSrc);
                        }
                    }
                } catch (err) {
                    // 加载失败，静默处理
                }
            } else {
                img.style.display = 'none';
                noSignal.style.display = 'block';
            }
        });

        await Promise.allSettled(promises);
    }

    /**
     * 快速判断某摄像头在指定时间是否有视频（用 time_ranges 判断，不请求后端）
     */
    _hasVideoAt(cam, t) {
        if (!cam.time_ranges || cam.time_ranges.length === 0) return false;
        for (const range of cam.time_ranges) {
            if (t >= range.start_ts && t <= range.end_ts) return true;
            if (range.start_ts > t) break; // ranges 是有序的，后面不用看了
        }
        return false;
    }

    // ==================== 视频模式 (WASM 解码) ====================

    _switchMode() {
        if (this.mode === 'frame') {
            // 切到帧模式：隐藏 video，显示 img
            this.cells.forEach(({ img, video }) => {
                video.style.display = 'none';
                video.pause();
                video.src = '';
                img.style.display = 'block';
            });
            this._loadFrames();
        } else {
            // 切到视频模式：隐藏 img，显示 video
            this.cells.forEach(({ img, video }) => {
                img.style.display = 'none';
                video.style.display = 'block';
            });
            this._seekVideosWASM();
        }
    }

    async _seekVideosWASM() {
        const t = this.currentTime;

        const promises = this.cells.map(async ({ video, noSignal, camId }, idx) => {
            if (this.mode !== 'video') return;

            const cam = this.cameras[camId];
            const hasVideo = this._hasVideoAt(cam, t);

            if (!hasVideo) {
                video.style.display = 'none';
                noSignal.style.display = 'block';
                video.pause();
                video.src = '';
                this.videoPlayers[idx].currentClipUrl = null;
                return;
            }

            try {
                // 查询该时刻对应的原始文件
                const resp = await fetch(`/api/clip_info/${camId}?t=${t}`);
                const info = await resp.json();

                if (!info.found) {
                    video.style.display = 'none';
                    noSignal.style.display = 'block';
                    return;
                }

                noSignal.style.display = 'none';
                video.style.display = 'block';

                const filename = info.filename;
                const offset = info.offset;
                const fileUrl = info.file_url;

                // 检查缓存：如果已经在播放同一个文件，只需 seek
                const cached = this.wasmVideoCache.get(camId);
                if (cached && cached.filename === filename) {
                    // 尝试 seek 到指定位置
                    if (video.duration > 0) {
                        video.currentTime = offset;
                    }
                    video.playbackRate = this.playSpeed;
                    if (this.isPlaying) {
                        video.play().catch(() => {});
                    }
                    return;
                }

                // 显示加载状态
                noSignal.textContent = '加载中...';
                noSignal.style.display = 'block';

                // 记录
                this.wasmVideoCache.set(camId, { filename });

                // 直接使用原始视频文件
                video.src = fileUrl;
                video.load();
                this.videoPlayers[idx].currentClipUrl = filename;

                // 等待 loadedmetadata 后再 seek
                await new Promise((resolve) => {
                    const handler = () => {
                        video.removeEventListener('loadedmetadata', handler);
                        resolve();
                    };
                    video.addEventListener('loadedmetadata', handler);
                    setTimeout(resolve, 5000);
                });

                video.currentTime = offset;
                video.playbackRate = this.playSpeed;

                // 隐藏加载状态
                noSignal.style.display = 'none';

                if (this.isPlaying) {
                    try { await video.play(); } catch (e) { /* autoplay blocked */ }
                }

                // 监听视频播放进度，同步时间轴游标
                this._setupVideoTimeSync(video, camId, info);

            } catch (err) {
                console.error(`摄像头${camId}视频加载失败:`, err);
                noSignal.textContent = '加载失败';
                noSignal.style.display = 'block';
            }
        });

        await Promise.allSettled(promises);
    }

    async _seekVideos() {
        await this._seekVideosWASM();
    }

    _setupVideoTimeSync(video, camId, clipInfo) {
        // 移除旧的监听
        if (video._timeSyncHandler) {
            video.removeEventListener('timeupdate', video._timeSyncHandler);
        }

        video._timeSyncHandler = () => {
            // 用第一个摄像头的播放进度驱动时间轴
            if (camId === 0 || (camId > 0 && !this._hasVideoAt(this.cameras[0], this.currentTime))) {
                const realTime = clipInfo.start_ts + video.currentTime;
                this.currentTime = realTime;
                this.timeline.setCursorTime(realTime);
            }
        };

        video.addEventListener('timeupdate', video._timeSyncHandler);

        // 视频播放结束，尝试加载下一个片段
        if (video._endedHandler) {
            video.removeEventListener('ended', video._endedHandler);
        }

        video._endedHandler = () => {
            // 跳到下一秒，触发重新查找
            this.currentTime = clipInfo.end_ts + 0.5;
            this.timeline.setCursorTime(this.currentTime);
            if (this.isPlaying) {
                this._seekVideos();
            }
        };

        video.addEventListener('ended', video._endedHandler);
    }

    _playVideos() {
        this.cells.forEach(({ video }, idx) => {
            if (this.mode !== 'video') return;
            if (video.src && video.style.display !== 'none') {
                video.playbackRate = this.playSpeed;
                video.play().catch(() => {});
            }
        });
    }

    // ==================== UI ====================

    showLoading(show) {
        const el = document.getElementById('loading-overlay');
        if (el) {
            el.classList.toggle('hidden', !show);
        }
    }
}

// ==================== 启动 ====================
document.addEventListener('DOMContentLoaded', () => {
    const app = new MonitorApp();
    app.init();

    // 暴露到全局方便调试
    window.monitorApp = app;
});