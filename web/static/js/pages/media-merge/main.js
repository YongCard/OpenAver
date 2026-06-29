function apiErrorMessage(error, fallback) {
    const detail = error?.detail;
    if (detail && typeof detail === 'object' && detail.message) return detail.message;
    if (typeof detail === 'string') return detail;
    return fallback || window.t('media_merge.toast_failed');
}

async function postJson(url, body) {
    const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw data;
    return data;
}

document.addEventListener('alpine:init', () => {
    Alpine.data('mediaMergePage', () => ({
        busy: false,
        merging: false,
        ffmpeg: { available: false, source: 'missing', path: '', version: '' },
        cleanupSupported: false,
        cleanupSources: false,
        rememberCleanup: false,
        inputPaths: [],
        pathsText: '',
        previewData: { items: [], output_path: '', extension_warning: false },
        outputPath: '',
        overwrite: false,
        lastOutputPath: '',
        mergeStage: '',
        mergePercent: 0,
        mergeStartedAt: 0,
        elapsedSeconds: 0,
        mergeResult: null,
        mergeError: null,
        mergeTimer: null,
        toast: { visible: false, message: '', type: 'info', timer: null },

        get ffmpegLabel() {
            if (!this.ffmpeg.available) return window.t('media_merge.ffmpeg_missing');
            const sourceKey = `media_merge.ffmpeg_source_${this.ffmpeg.source || 'path'}`;
            const source = window.t(sourceKey);
            return window.t('media_merge.ffmpeg_ready', {
                source: source === `[${sourceKey}]` ? this.ffmpeg.source : source,
            });
        },

        get mergeStageLabel() {
            if (!this.mergeStage) return '';
            const key = `media_merge.stage_${this.mergeStage}`;
            const label = window.t(key);
            return label === `[${key}]` ? this.mergeStage : label;
        },

        get mergeProgressLabel() {
            return `${Math.round(this.mergePercent || 0)}%`;
        },

        async init() {
            await this.loadFfmpegStatus();
            window.handlePyWebViewDrop = (paths) => {
                this.addDroppedPaths(paths);
            };
            window.handleFolderDrop = (folders) => {
                this.addDroppedFolders(folders);
            };
        },

        showToast(message, type = 'info') {
            this.toast.message = message;
            this.toast.type = type;
            this.toast.visible = true;
            if (this.toast.timer) clearTimeout(this.toast.timer);
            this.toast.timer = setTimeout(() => { this.toast.visible = false; }, 3200);
        },

        async loadFfmpegStatus() {
            try {
                const response = await fetch('/api/media-merge/ffmpeg');
                const data = await response.json();
                if (!response.ok) throw data;
                this.ffmpeg = data || this.ffmpeg;
                this.cleanupSupported = !!data.cleanup_supported;
                this.cleanupSources = this.cleanupSupported && !!data.cleanup_sources_default;
                if (!this.cleanupSupported) this.rememberCleanup = false;
            } catch (error) {
                this.showToast(apiErrorMessage(error, window.t('media_merge.toast_ffmpeg_failed')), 'warning');
            }
        },

        async selectFiles() {
            if (!window.pywebview?.api?.select_files) {
                this.showToast(window.t('media_merge.desktop_select_only'), 'info');
                return;
            }
            try {
                const paths = await window.pywebview.api.select_files();
                if (Array.isArray(paths) && paths.length) {
                    this.inputPaths = Array.from(new Set([...this.inputPaths, ...paths]));
                    this.pathsText = this.inputPaths.join('\n');
                    await this.preview();
                }
            } catch (_error) {
                this.showToast(window.t('media_merge.toast_select_failed'), 'error');
            }
        },

        async addDroppedPaths(paths) {
            if (!Array.isArray(paths) || !paths.length || this.merging) return;
            this.inputPaths = Array.from(new Set([
                ...this.inputPaths,
                ...paths.map((path) => String(path || '').trim()).filter(Boolean),
            ]));
            this.pathsText = this.inputPaths.join('\n');
            await this.preview();
        },

        async addDroppedFolders(folders) {
            if (!Array.isArray(folders) || !folders.length || this.merging) return;
            // pywebview on_drop expands folders into handlePyWebViewDrop(paths). Keep this
            // fallback so plain folder drops still give users visible feedback.
            this.showToast(window.t('media_merge.toast_drop_folder_expand'), 'info');
        },

        syncPathsFromText(resetPreview = true) {
            this.inputPaths = this.pathsText
                .split(/\r?\n/)
                .map((line) => line.trim().replace(/^"|"$/g, ''))
                .filter(Boolean);
            if (resetPreview) {
                this.previewData = { items: [], output_path: '', extension_warning: false };
                this.mergeResult = null;
            }
        },

        async preview() {
            this.syncPathsFromText();
            this.busy = true;
            try {
                const data = await postJson('/api/media-merge/preview', { paths: this.inputPaths });
                this.previewData = data.data || { items: [], output_path: '', extension_warning: false };
                this.outputPath = this.previewData.output_path || this.outputPath;
                if (Array.isArray(this.previewData.items)) {
                    this.inputPaths = this.previewData.items.map((item) => item.path);
                    this.pathsText = this.inputPaths.join('\n');
                }
                this.showToast(window.t('media_merge.toast_preview_done'), 'success');
            } catch (error) {
                this.showToast(apiErrorMessage(error, window.t('media_merge.toast_preview_failed')), 'error');
            } finally {
                this.busy = false;
            }
        },

        async runMerge() {
            this.syncPathsFromText(false);
            this.merging = true;
            this.mergeResult = null;
            this.mergeError = null;
            this.startMergeProgress();
            try {
                await this.postMergeStream({
                    paths: this.inputPaths,
                    output_path: this.outputPath,
                    overwrite: this.overwrite,
                    cleanup_sources: this.cleanupSources,
                    cleanup_sidecars: this.cleanupSources,
                    remember_cleanup: this.rememberCleanup,
                });
            } catch (error) {
                this.mergeError = this.normalizeMergeError(error);
                this.showToast(apiErrorMessage(error, window.t('media_merge.toast_merge_failed')), 'error');
                this.mergeStage = '';
            } finally {
                this.stopMergeTimer();
                this.merging = false;
            }
        },

        async postMergeStream(payload) {
            const response = await fetch('/api/media-merge/run-stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw data;
            }
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const chunks = buffer.split('\n\n');
                buffer = chunks.pop() || '';
                chunks.forEach((chunk) => this.handleSseChunk(chunk));
            }
            if (buffer.trim()) this.handleSseChunk(buffer);
        },

        handleSseChunk(chunk) {
            const line = chunk.split('\n').find((item) => item.startsWith('data:'));
            if (!line) return;
            const event = JSON.parse(line.slice(5).trim());
            this.handleMergeEvent(event);
        },

        handleMergeEvent(event) {
            if (event.type === 'stage') {
                this.mergeStage = event.stage || this.mergeStage;
                if (Number.isFinite(Number(event.percent))) this.mergePercent = Number(event.percent);
                return;
            }
            if (event.type === 'progress') {
                this.mergeStage = event.stage || this.mergeStage || 'merging';
                if (Number.isFinite(Number(event.percent))) this.mergePercent = Number(event.percent);
                return;
            }
            if (event.type === 'warning') {
                this.showToast(this.cleanupWarningLabel(event.code), 'warning');
                return;
            }
            if (event.type === 'error') {
                throw {
                    detail: {
                        message: event.message,
                        code: event.code,
                        log_tail: event.log_tail || '',
                    },
                };
            }
            if (event.type === 'done') {
                const result = event.data || {};
                this.lastOutputPath = result.output_path || this.outputPath;
                this.mergeResult = result;
                this.mergeStage = 'done';
                this.mergePercent = 100;
                if (result.cleanup?.warning) {
                    this.showToast(this.cleanupWarningLabel(result.cleanup.warning), 'warning');
                } else {
                    this.showToast(window.t('media_merge.toast_merge_done'), 'success');
                }
            }
        },

        normalizeMergeError(error) {
            const detail = error?.detail && typeof error.detail === 'object' ? error.detail : {};
            return {
                code: detail.code || error?.code || 'media_merge_failed',
                message: detail.message || (typeof error?.detail === 'string' ? error.detail : window.t('media_merge.toast_merge_failed')),
                log_tail: detail.log_tail || error?.log_tail || '',
            };
        },

        startMergeProgress() {
            this.stopMergeTimer();
            this.mergeStage = 'preparing';
            this.mergePercent = 0;
            this.mergeStartedAt = Date.now();
            this.elapsedSeconds = 0;
            this.mergeTimer = setInterval(() => {
                this.elapsedSeconds = Math.floor((Date.now() - this.mergeStartedAt) / 1000);
            }, 1000);
        },

        stopMergeTimer() {
            if (this.mergeTimer) clearInterval(this.mergeTimer);
            this.mergeTimer = null;
        },

        formatBytes(bytes) {
            const value = Number(bytes || 0);
            if (!Number.isFinite(value) || value <= 0) return '0 B';
            const units = ['B', 'KB', 'MB', 'GB', 'TB'];
            let size = value;
            let index = 0;
            while (size >= 1024 && index < units.length - 1) {
                size /= 1024;
                index += 1;
            }
            return `${size.toFixed(index === 0 ? 0 : 2)} ${units[index]}`;
        },

        formatDuration(seconds) {
            const total = Math.max(0, Math.round(Number(seconds || 0)));
            const hours = Math.floor(total / 3600);
            const minutes = Math.floor((total % 3600) / 60);
            const secs = total % 60;
            if (hours > 0) {
                return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
            }
            return `${minutes}:${String(secs).padStart(2, '0')}`;
        },

        cleanupWarningLabel(code) {
            const key = `media_merge.cleanup_warning_${code || 'unknown'}`;
            const label = window.t(key);
            return label === `[${key}]` ? window.t('media_merge.cleanup_warning_unknown') : label;
        },

        async openOutputFolder() {
            if (!window.pywebview?.api?.open_folder) {
                this.showToast(window.t('media_merge.desktop_open_only'), 'info');
                return;
            }
            try {
                const ok = await window.pywebview.api.open_folder(this.lastOutputPath);
                if (!ok) this.showToast(window.t('media_merge.toast_open_failed'), 'warning');
            } catch (_error) {
                this.showToast(window.t('media_merge.toast_open_failed'), 'error');
            }
        },

        clearAll() {
            this.inputPaths = [];
            this.pathsText = '';
            this.previewData = { items: [], output_path: '', extension_warning: false };
            this.outputPath = '';
            this.lastOutputPath = '';
            this.mergeStage = '';
            this.mergePercent = 0;
            this.elapsedSeconds = 0;
            this.mergeResult = null;
            this.mergeError = null;
            this.stopMergeTimer();
        },
    }));
});
