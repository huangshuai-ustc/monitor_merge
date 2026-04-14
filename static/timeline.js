/**
 * 时间轴组件
 * 
 * 功能:
 * - 多轨道显示每个摄像头有视频的时间段（色块）
 * - 可拖拽游标跳转时间
 * - 鼠标悬停显示对应时间
 * - 缩放（放大/缩小时间范围）
 * - 平移（拖拽空白区域左右滚动）
 */

class Timeline {
    constructor(container, options = {}) {
        this.container = container;
        this.cameras = [];
        this.globalRange = null;

        // 当前可视范围（Unix timestamp）
        this.viewStart = 0;
        this.viewEnd = 0;

        // 游标位置
        this.cursorTime = 0;

        // 缩放级别: 可视窗口的秒数
        this.minViewSpan = 60;          // 最小1分钟
        this.maxViewSpan = 86400 * 7;   // 最大7天

        // 回调
        this.onTimeChange = options.onTimeChange || (() => {});
        this.onSeek = options.onSeek || (() => {});

        // 状态
        this.isDraggingCursor = false;
        this.isPanning = false;
        this.panStartX = 0;
        this.panStartViewStart = 0;

        // DOM 元素引用
        this.els = {};

        this._build();
        this._bindEvents();
    }

    /**
     * 设置数据
     */
    setData(cameras, globalRange) {
        this.cameras = cameras;
        this.globalRange = globalRange;

        if (globalRange) {
            this.viewStart = globalRange.start_ts;
            this.viewEnd = globalRange.end_ts;
            this.cursorTime = globalRange.start_ts;

            // 默认显示最后一天
            const oneDayAgo = globalRange.end_ts - 86400;
            if (oneDayAgo > globalRange.start_ts) {
                this.viewStart = oneDayAgo;
            }
            this.cursorTime = this.viewStart;
        }

        this._buildTracks();
        this.render();
    }

    /**
     * 设置游标到指定时间
     */
    setCursorTime(ts) {
        if (!this.globalRange) return;
        this.cursorTime = Math.max(this.globalRange.start_ts, Math.min(ts, this.globalRange.end_ts));
        this._renderCursor();
        this._updateTimeDisplay();
    }

    /**
     * 跳转到指定日期
     */
    goToDate(dateStr) {
        // dateStr: "2025-06-13"
        const d = new Date(dateStr + 'T00:00:00');
        const dayStart = d.getTime() / 1000;
        const dayEnd = dayStart + 86400;

        this.viewStart = dayStart;
        this.viewEnd = dayEnd;
        this.cursorTime = dayStart;

        this.render();
        this.onTimeChange(this.cursorTime);
    }

    /**
     * 缩放
     */
    zoom(factor) {
        const center = this.cursorTime || (this.viewStart + this.viewEnd) / 2;
        const currentSpan = this.viewEnd - this.viewStart;
        let newSpan = currentSpan * factor;

        newSpan = Math.max(this.minViewSpan, Math.min(newSpan, this.maxViewSpan));

        this.viewStart = center - newSpan / 2;
        this.viewEnd = center + newSpan / 2;

        this.render();
    }

    /**
     * 前进/后退
     */
    pan(fraction) {
        const span = this.viewEnd - this.viewStart;
        const delta = span * fraction;
        this.viewStart += delta;
        this.viewEnd += delta;
        this.render();
    }

    // ==================== 内部方法 ====================

    _build() {
        this.container.innerHTML = '';
        this.container.classList.add('timeline-area');

        // 缩放控制
        const zoomBar = document.createElement('div');
        zoomBar.className = 'zoom-controls';
        zoomBar.innerHTML = `
            <button data-action="zoom-in" title="放大">🔍+</button>
            <button data-action="zoom-out" title="缩小">🔍−</button>
            <span class="zoom-info"></span>
            <button data-action="pan-left" title="向左">◀</button>
            <button data-action="pan-right" title="向右">▶</button>
            <button data-action="fit" title="适配全部">全部</button>
            <button data-action="today" title="今天">今天</button>
        `;
        this.container.appendChild(zoomBar);
        this.els.zoomInfo = zoomBar.querySelector('.zoom-info');

        // 时间轴滚动容器
        const scrollWrapper = document.createElement('div');
        scrollWrapper.className = 'timeline-scroll-wrapper';
        this.container.appendChild(scrollWrapper);

        const inner = document.createElement('div');
        inner.className = 'timeline-inner';
        scrollWrapper.appendChild(inner);

        this.els.scrollWrapper = scrollWrapper;
        this.els.inner = inner;

        // 轨道容器
        this.els.tracks = document.createElement('div');
        this.els.tracks.className = 'timeline-tracks';
        inner.appendChild(this.els.tracks);

        // 时间刻度
        this.els.ruler = document.createElement('div');
        this.els.ruler.className = 'time-ruler';
        this.els.ruler.innerHTML = `
            <div class="ruler-label-spacer"></div>
            <div class="ruler-bar"></div>
        `;
        inner.appendChild(this.els.ruler);
        this.els.rulerBar = this.els.ruler.querySelector('.ruler-bar');
    }

    _buildTracks() {
        this.els.tracks.innerHTML = '';
        this.els.trackBars = [];

        this.cameras.forEach((cam, i) => {
            const track = document.createElement('div');
            track.className = 'timeline-track';

            const label = document.createElement('div');
            label.className = 'track-label';
            label.textContent = cam.name;
            label.title = cam.name;

            const bar = document.createElement('div');
            bar.className = 'track-bar';
            bar.dataset.cam = i;

            // 悬停时间提示
            const hoverTime = document.createElement('div');
            hoverTime.className = 'hover-time';
            bar.appendChild(hoverTime);

            // 游标线
            const cursor = document.createElement('div');
            cursor.className = 'cursor-line';
            cursor.style.display = 'none';
            bar.appendChild(cursor);

            track.appendChild(label);
            track.appendChild(bar);
            this.els.tracks.appendChild(track);
            this.els.trackBars.push({ bar, cursor, hoverTime });
        });
    }

    _bindEvents() {
        // 缩放按钮
        this.container.addEventListener('click', (e) => {
            const btn = e.target.closest('button[data-action]');
            if (!btn) return;

            const action = btn.dataset.action;
            switch (action) {
                case 'zoom-in': this.zoom(0.5); break;
                case 'zoom-out': this.zoom(2); break;
                case 'pan-left': this.pan(-0.3); break;
                case 'pan-right': this.pan(0.3); break;
                case 'fit': this._fitAll(); break;
                case 'today': this._goToday(); break;
            }
        });

        // 鼠标滚轮缩放
        this.container.addEventListener('wheel', (e) => {
            e.preventDefault();
            const factor = e.deltaY > 0 ? 1.3 : 0.7;

            // 以鼠标位置为中心缩放
            const bar = e.target.closest('.track-bar') || e.target.closest('.ruler-bar');
            if (bar) {
                const rect = bar.getBoundingClientRect();
                const ratio = (e.clientX - rect.left) / rect.width;
                const mouseTime = this.viewStart + (this.viewEnd - this.viewStart) * ratio;

                const currentSpan = this.viewEnd - this.viewStart;
                let newSpan = currentSpan * factor;
                newSpan = Math.max(this.minViewSpan, Math.min(newSpan, this.maxViewSpan));

                this.viewStart = mouseTime - newSpan * ratio;
                this.viewEnd = mouseTime + newSpan * (1 - ratio);
                this.render();
            } else {
                this.zoom(factor);
            }
        }, { passive: false });

        // 轨道点击 & 拖拽
        this.container.addEventListener('mousedown', (e) => {
            const bar = e.target.closest('.track-bar');
            if (bar) {
                e.preventDefault();
                this.isDraggingCursor = true;
                this._seekToMouse(e, bar);
            }

            // 在刻度尺上拖拽 → 平移
            const ruler = e.target.closest('.ruler-bar');
            if (ruler) {
                e.preventDefault();
                this.isPanning = true;
                this.panStartX = e.clientX;
                this.panStartViewStart = this.viewStart;
            }
        });

        document.addEventListener('mousemove', (e) => {
            // 拖拽游标
            if (this.isDraggingCursor) {
                const bar = this.els.trackBars[0]?.bar;
                if (bar) this._seekToMouse(e, bar);
            }

            // 平移
            if (this.isPanning) {
                const bar = this.els.rulerBar;
                const rect = bar.getBoundingClientRect();
                const dx = e.clientX - this.panStartX;
                const span = this.viewEnd - this.viewStart;
                const timeDelta = -(dx / rect.width) * span;
                this.viewStart = this.panStartViewStart + timeDelta;
                this.viewEnd = this.viewStart + span;
                this.render();
            }

            // 悬停提示
            if (!this.isDraggingCursor && !this.isPanning) {
                const bar = e.target.closest('.track-bar');
                if (bar) {
                    const rect = bar.getBoundingClientRect();
                    const ratio = (e.clientX - rect.left) / rect.width;
                    const t = this.viewStart + (this.viewEnd - this.viewStart) * ratio;
                    const hoverEl = bar.querySelector('.hover-time');
                    if (hoverEl) {
                        hoverEl.textContent = this._formatTime(t);
                        hoverEl.style.left = `${ratio * 100}%`;
                    }
                }
            }
        });

        document.addEventListener('mouseup', () => {
            if (this.isDraggingCursor) {
                this.isDraggingCursor = false;
                this.onSeek(this.cursorTime);
            }
            this.isPanning = false;
        });
    }

    _seekToMouse(e, bar) {
        const rect = bar.getBoundingClientRect();
        let ratio = (e.clientX - rect.left) / rect.width;
        ratio = Math.max(0, Math.min(1, ratio));

        const t = this.viewStart + (this.viewEnd - this.viewStart) * ratio;
        this.cursorTime = t;
        this._renderCursor();
        this._updateTimeDisplay();
        this.onTimeChange(t);
    }

    _fitAll() {
        if (this.globalRange) {
            this.viewStart = this.globalRange.start_ts;
            this.viewEnd = this.globalRange.end_ts;
            this.render();
        }
    }

    _goToday() {
        const now = new Date();
        now.setHours(0, 0, 0, 0);
        const dayStart = now.getTime() / 1000;
        this.viewStart = dayStart;
        this.viewEnd = dayStart + 86400;
        this.render();
    }

    // ==================== 渲染 ====================

    render() {
        this._renderSegments();
        this._renderCursor();
        this._renderRuler();
        this._renderZoomInfo();
    }

    _renderSegments() {
        const span = this.viewEnd - this.viewStart;
        if (span <= 0) return;

        this.cameras.forEach((cam, i) => {
            if (!this.els.trackBars[i]) return;
            const bar = this.els.trackBars[i].bar;

            // 清除旧的色块
            bar.querySelectorAll('.segment').forEach(el => el.remove());

            (cam.time_ranges || []).forEach(range => {
                // 计算色块在当前视图中的位置
                const segStart = Math.max(range.start_ts, this.viewStart);
                const segEnd = Math.min(range.end_ts, this.viewEnd);

                if (segStart >= segEnd) return; // 不在视图内

                const leftPct = ((segStart - this.viewStart) / span) * 100;
                const widthPct = ((segEnd - segStart) / span) * 100;

                const seg = document.createElement('div');
                seg.className = 'segment';
                seg.style.left = `${leftPct}%`;
                seg.style.width = `${Math.max(widthPct, 0.1)}%`;
                bar.appendChild(seg);
            });
        });
    }

    _renderCursor() {
        const span = this.viewEnd - this.viewStart;
        if (span <= 0) return;

        const ratio = (this.cursorTime - this.viewStart) / span;
        const visible = ratio >= 0 && ratio <= 1;

        this.els.trackBars.forEach(({ cursor }) => {
            if (visible) {
                cursor.style.display = 'block';
                cursor.style.left = `${ratio * 100}%`;
            } else {
                cursor.style.display = 'none';
            }
        });
    }

    _renderRuler() {
        const rulerBar = this.els.rulerBar;
        rulerBar.innerHTML = '';

        const span = this.viewEnd - this.viewStart;
        if (span <= 0) return;

        // 根据时间跨度选择刻度间隔
        const intervals = [
            60, 300, 600, 900, 1800, 3600,
            7200, 14400, 21600, 43200, 86400,
        ];

        // 目标: 大约 8~15 个刻度
        let interval = intervals[intervals.length - 1];
        for (const iv of intervals) {
            if (span / iv <= 20 && span / iv >= 4) {
                interval = iv;
                break;
            }
        }

        // 找到第一个刻度
        const firstTick = Math.ceil(this.viewStart / interval) * interval;

        for (let t = firstTick; t <= this.viewEnd; t += interval) {
            const ratio = (t - this.viewStart) / span;
            const tick = document.createElement('div');
            tick.className = 'tick';
            tick.style.left = `${ratio * 100}%`;

            // 格式化
            const d = new Date(t * 1000);
            if (interval >= 86400) {
                tick.textContent = `${d.getMonth()+1}/${d.getDate()}`;
            } else if (interval >= 3600) {
                tick.textContent = `${String(d.getHours()).padStart(2,'0')}:00`;
            } else {
                tick.textContent = `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
            }

            rulerBar.appendChild(tick);
        }
    }

    _renderZoomInfo() {
        const span = this.viewEnd - this.viewStart;
        let text;
        if (span < 300) text = `${Math.round(span)}秒`;
        else if (span < 7200) text = `${(span/60).toFixed(0)}分钟`;
        else if (span < 172800) text = `${(span/3600).toFixed(1)}小时`;
        else text = `${(span/86400).toFixed(1)}天`;

        if (this.els.zoomInfo) {
            this.els.zoomInfo.textContent = `视图: ${text}`;
        }
    }

    _updateTimeDisplay() {
        const el = document.getElementById('current-time-display');
        if (el) {
            el.textContent = this._formatTimeFull(this.cursorTime);
        }
    }

    _formatTime(ts) {
        const d = new Date(ts * 1000);
        return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;
    }

    _formatTimeFull(ts) {
        const d = new Date(ts * 1000);
        const date = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
        const time = `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;
        return `${date} ${time}`;
    }
}