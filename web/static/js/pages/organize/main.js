function apiErrorMessage(error, fallback) {
    const detail = error?.detail;
    if (detail && typeof detail === 'object' && detail.message) return detail.message;
    if (typeof detail === 'string') return detail;
    return fallback || window.t('organize.toast_failed');
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
    Alpine.data('organizePage', () => ({
        busy: false,
        roots: [],
        selectedRoot: '',
        sources: [{ id: 'auto', name: window.t('organize.source_auto') }],
        source: 'auto',
        entries: [],
        summary: {},
        manifest: '',
        applyModalOpen: false,
        searchJob: null,
        searchLogs: [],
        searchPollTimer: null,
        lastSelectedIndex: null,
        pathFilter: '',
        reviewFilter: false,
        metadataFilter: false,
        selectedFilter: false,
        pendingApplyAfterSearch: false,
        pendingApplyIds: [],
        applyBatchSize: 20,
        applyingBatches: false,
        applyPaused: false,
        applyStats: { moved: 0, remaining: 0, batches: 0 },
        westernEntries: [],
        westernManifest: '',
        westernSummary: {},
        toast: { visible: false, message: '', type: 'info', timer: null },

        get foundCount() {
            return this.entries.filter((entry) => entry.status === 'found' || entry.metadata).length;
        },

        get plannedCount() {
            return this.entries.filter((entry) => entry.status === 'planned').length;
        },

        get westernPlannedCount() {
            return this.westernEntries.filter((entry) => entry.status === 'planned').length;
        },

        get selectedCount() {
            return this.entries.filter((entry) => entry.selected).length;
        },

        get allSelected() {
            return this.visibleEntries.length > 0 && this.visibleEntries.every((item) => item.entry.selected);
        },

        get visibleSelectedCount() {
            return this.visibleEntries.filter((item) => item.entry.selected).length;
        },

        get pathFilterOptions() {
            const options = [];
            const byKey = new Map();
            this.entries.forEach((entry) => {
                const key = this.entryPathKey(entry);
                if (!byKey.has(key)) {
                    const option = {
                        key,
                        label: this.entryPathLabel(entry),
                        root: entry.root || '',
                        count: 0,
                    };
                    byKey.set(key, option);
                    options.push(option);
                }
                byKey.get(key).count += 1;
            });
            return options;
        },

        get visibleEntries() {
            return this.entries
                .map((entry, index) => ({ entry, index }))
                .filter((item) => {
                    if (this.pathFilter && this.entryPathKey(item.entry) !== this.pathFilter) return false;
                    if (
                        this.reviewFilter
                        && item.entry.status !== 'needs_number'
                        && item.entry.reason !== 'number_not_found'
                        && !item.entry.needs_number
                    ) return false;
                    if (this.metadataFilter && item.entry.status !== 'found' && !item.entry.metadata) return false;
                    if (this.selectedFilter && !item.entry.selected) return false;
                    return true;
                })
                .map((item, visibleIndex) => ({
                    ...item,
                    visibleIndex,
                }));
        },

        get visibleCount() {
            return this.visibleEntries.length;
        },

        get searchJobRunning() {
            return this.searchJob?.phase === 'running' || this.searchJob?.phase === 'canceling';
        },

        get searchJobVisible() {
            return !!this.searchJob;
        },

        get bulkOperationRunning() {
            return this.searchJobRunning || this.applyingBatches;
        },

        get searchJobSummary() {
            return this.searchJob?.summary || {};
        },

        get progressPercent() {
            const total = this.searchJob?.total || 0;
            if (!total) return 0;
            return Math.min(100, Math.round(((this.searchJob?.current || 0) / total) * 100));
        },

        get progressText() {
            if (!this.searchJob) return '';
            if (this.searchJob.phase === 'running') {
                return window.t('organize.progress_running', {
                    current: this.searchJob.current || 0,
                    total: this.searchJob.total || 0,
                    label: this.searchJob.current_label || '-',
                });
            }
            if (this.searchJob.phase === 'canceling') return window.t('organize.progress_canceling');
            if (this.searchJob.phase === 'done') return window.t('organize.progress_done');
            if (this.searchJob.phase === 'canceled') return window.t('organize.progress_canceled');
            if (this.searchJob.phase === 'failed') return window.t('organize.progress_failed_state');
            return window.t('organize.progress_idle');
        },

        async init() {
            await Promise.all([this.loadRoots(), this.loadSources(), this.restoreSearchJob()]);
        },

        showToast(message, type = 'info') {
            this.toast.message = message;
            this.toast.type = type;
            this.toast.visible = true;
            if (this.toast.timer) clearTimeout(this.toast.timer);
            this.toast.timer = setTimeout(() => { this.toast.visible = false; }, 3200);
        },

        selectRoot(root) {
            this.selectedRoot = this.selectedRoot === root ? '' : root;
        },

        async loadRoots() {
            this.busy = true;
            try {
                const response = await fetch('/api/inbox-organizer/roots');
                const data = await response.json();
                if (!response.ok) throw data;
                this.roots = data.roots || [];
                if (!this.selectedRoot && this.roots.length === 1) {
                    this.selectedRoot = this.roots[0].root;
                }
            } catch (error) {
                this.showToast(apiErrorMessage(error, window.t('organize.toast_roots_failed')), 'error');
            } finally {
                this.busy = false;
            }
        },

        async loadSources() {
            try {
                const response = await fetch('/api/search/sources');
                const data = await response.json();
                if (response.ok && Array.isArray(data.sources)) {
                    this.sources = data.sources;
                }
            } catch (_error) {
                this.sources = [{ id: 'auto', name: window.t('organize.source_auto') }];
            }
        },

        async scanInbox() {
            this.busy = true;
            this.manifest = '';
            this.searchJob = null;
            this.searchLogs = [];
            try {
                const data = await postJson('/api/inbox-organizer/inventory', {
                    root: this.selectedRoot || null,
                });
                this.summary = data.summary || {};
                this.entries = (data.entries || []).map((entry) => ({
                    ...entry,
                    manual_number: entry.number || '',
                    selected: false,
                }));
                this.lastSelectedIndex = null;
                this.pathFilter = '';
                this.reviewFilter = false;
                this.metadataFilter = false;
                this.selectedFilter = false;
                this.pendingApplyAfterSearch = false;
                this.pendingApplyIds = [];
                this.showToast(
                    this.entries.length
                        ? window.t('organize.toast_scanned', { count: this.entries.length })
                        : window.t('organize.toast_scan_empty'),
                    this.entries.length ? 'success' : 'info',
                );
            } catch (error) {
                this.showToast(apiErrorMessage(error, window.t('organize.toast_scan_failed')), 'error');
            } finally {
                this.busy = false;
            }
        },

        async restoreSearchJob() {
            try {
                const response = await fetch('/api/inbox-organizer/search-jobs/current');
                const data = await response.json();
                if (!response.ok) throw data;
                if (data.job) {
                    this.applyJob(data.job);
                    if (this.searchJobRunning) this.startPolling();
                    else this.showToast(window.t('organize.toast_search_restored'), 'info');
                }
            } catch (_error) {
                // No persisted jobs across app restarts; silently keep current page empty.
            }
        },

        applyJob(job) {
            this.searchJob = job;
            this.searchLogs = job.logs || [];
            this.summary = job.summary || this.summary || {};
            if (Array.isArray(job.entries)) {
                const previous = new Map(this.entries.map((entry) => [String(entry.id), entry]));
                this.entries = job.entries.map((entry) => {
                    const old = previous.get(String(entry.id));
                    return {
                        ...(old || {}),
                        ...entry,
                        manual_number: entry.manual_number || old?.manual_number || entry.number || '',
                        selected: Boolean(old?.selected || entry.selected),
                    };
                });
            }
            this.$nextTick(() => this.scrollLogsToBottom());
        },

        startPolling() {
            if (this.searchPollTimer) clearInterval(this.searchPollTimer);
            this.searchPollTimer = setInterval(() => {
                this.refreshSearchJob();
            }, 1000);
        },

        stopPolling() {
            if (this.searchPollTimer) clearInterval(this.searchPollTimer);
            this.searchPollTimer = null;
        },

        async refreshSearchJob() {
            if (!this.searchJob?.job_id) return;
            try {
                const response = await fetch(`/api/inbox-organizer/search-jobs/${encodeURIComponent(this.searchJob.job_id)}`);
                const data = await response.json();
                if (!response.ok) throw data;
                this.applyJob(data.job);
                if (!this.searchJobRunning) {
                    this.stopPolling();
                    if (this.pendingApplyAfterSearch && this.searchJob.phase === 'done') {
                        await this.previewPendingApply();
                    } else {
                        this.pendingApplyAfterSearch = false;
                        this.pendingApplyIds = [];
                        this.showToast(
                            this.searchJob.phase === 'done'
                                ? window.t('organize.toast_search_done', {
                                    found: this.summary.found_count || 0,
                                    review: this.summary.needs_review_count || 0,
                                })
                                : window.t('organize.toast_search_stopped'),
                            this.searchJob.phase === 'done' ? 'success' : 'warning',
                        );
                    }
                }
            } catch (error) {
                this.stopPolling();
                this.pendingApplyAfterSearch = false;
                this.pendingApplyIds = [];
                this.showToast(apiErrorMessage(error, window.t('organize.toast_job_lost')), 'warning');
            }
        },

        async startSearchJob(selectedIds = null, options = {}) {
            this.busy = true;
            this.manifest = '';
            this.pendingApplyAfterSearch = Boolean(options.applyAfterSearch);
            this.pendingApplyIds = this.pendingApplyAfterSearch ? [...(selectedIds || [])] : [];
            try {
                const data = await postJson('/api/inbox-organizer/search-jobs', {
                    entries: this.entries,
                    source: this.source,
                    selected_ids: selectedIds,
                });
                this.applyJob(data.job);
                this.startPolling();
            } catch (error) {
                this.pendingApplyAfterSearch = false;
                this.pendingApplyIds = [];
                this.showToast(apiErrorMessage(error, window.t('organize.toast_search_failed')), 'error');
            } finally {
                this.busy = false;
            }
        },

        async searchEntries() {
            await this.startSearchJob(null);
        },

        selectedIds() {
            const selectedIds = this.entries
                .filter((entry) => entry.selected)
                .map((entry) => entry.id);
            return selectedIds;
        },

        entryPathKey(entry) {
            const root = String(entry.root || '');
            const source = String(entry.source || '');
            const normalizedRoot = root.replace(/\\/g, '/').replace(/\/+$/, '');
            const normalizedSource = source.replace(/\\/g, '/');
            const inboxPrefix = `${normalizedRoot}/#待整理/`;
            if (normalizedRoot && normalizedSource.startsWith(inboxPrefix)) {
                const rest = normalizedSource.slice(inboxPrefix.length);
                const first = rest.split('/').filter(Boolean)[0] || window.t('organize.group_root');
                return `${root}::${first}`;
            }
            return `${root}::${window.t('organize.group_root')}`;
        },

        entryPathLabel(entry) {
            const root = String(entry.root || '');
            const source = String(entry.source || '');
            const normalizedRoot = root.replace(/\\/g, '/').replace(/\/+$/, '');
            const normalizedSource = source.replace(/\\/g, '/');
            const inboxPrefix = `${normalizedRoot}/#待整理/`;
            if (normalizedRoot && normalizedSource.startsWith(inboxPrefix)) {
                const rest = normalizedSource.slice(inboxPrefix.length);
                return rest.split('/').filter(Boolean)[0] || window.t('organize.group_root');
            }
            return window.t('organize.group_root');
        },

        async searchSelectedAndApply() {
            const selectedIds = this.selectedIds();
            if (!selectedIds.length) {
                this.showToast(window.t('organize.toast_select_first'), 'info');
                return;
            }
            await this.startSearchJob(selectedIds, { applyAfterSearch: true });
        },

        async offlinePlanSelectedAndApply() {
            const selectedIds = this.selectedIds();
            if (!selectedIds.length) {
                this.showToast(window.t('organize.toast_select_first'), 'info');
                return;
            }
            const selected = new Set(selectedIds.map(String));
            const entries = this.entries.filter((entry) => selected.has(String(entry.id)));
            await this.planEntries(entries, {
                merge: true,
                endpoint: '/api/inbox-organizer/offline-plan',
                successKey: 'organize.toast_offline_plan_done',
            });
            const selectedPlannedCount = this.entries.filter((entry) => (
                selected.has(String(entry.id)) && entry.status === 'planned'
            )).length;
            if (this.manifest && selectedPlannedCount > 0) {
                await this.applyBatch();
            } else {
                this.showToast(window.t('organize.toast_offline_no_ready'), 'info');
            }
        },

        async cancelSearchJob() {
            if (!this.searchJob?.job_id) return;
            try {
                const data = await postJson(
                    `/api/inbox-organizer/search-jobs/${encodeURIComponent(this.searchJob.job_id)}/cancel`,
                    {},
                );
                this.applyJob(data.job);
            } catch (error) {
                this.showToast(apiErrorMessage(error, window.t('organize.toast_cancel_failed')), 'error');
            }
        },

        mergePlannedEntries(plannedEntries) {
            const byId = new Map((plannedEntries || []).map((entry) => [String(entry.id), entry]));
            const bySource = new Map((plannedEntries || []).map((entry) => [String(entry.source), entry]));
            this.entries = this.entries.map((entry) => {
                const replacement = byId.get(String(entry.id)) || bySource.get(String(entry.source));
                return replacement ? { ...entry, ...replacement, selected: entry.selected } : entry;
            });
        },

        entriesReadyForPlan(ids) {
            const selected = new Set((ids || []).map(String));
            return this.entries.filter((entry) => {
                if (!selected.has(String(entry.id))) return false;
                return entry.status === 'found' || entry.metadata;
            });
        },

        async planEntries(entries = this.entries, options = {}) {
            this.busy = true;
            try {
                const data = await postJson(options.endpoint || '/api/inbox-organizer/plan', { entries });
                this.summary = data.summary || {};
                if (options.merge) {
                    this.mergePlannedEntries(data.entries || []);
                } else {
                    this.entries = data.entries || [];
                }
                this.manifest = data.manifest || '';
                this.showToast(window.t(options.successKey || 'organize.toast_plan_done', {
                    planned: this.summary.planned_count || 0,
                    conflict: this.summary.conflict_count || 0,
                    rescrape: this.summary.needs_rescrape_count || 0,
                }), 'success');
            } catch (error) {
                this.showToast(apiErrorMessage(error, window.t('organize.toast_plan_failed')), 'error');
            } finally {
                this.busy = false;
            }
        },

        async previewAndConfirmApply() {
            await this.planEntries();
            if (this.manifest && this.plannedCount > 0) {
                this.applyModalOpen = true;
            }
        },

        async previewWestern() {
            this.busy = true;
            this.westernManifest = '';
            try {
                const data = await postJson('/api/western-organizer/preview', {});
                this.westernEntries = data.entries || [];
                this.westernManifest = data.manifest || '';
                this.westernSummary = data.summary || {};
                this.summary = data.summary || {};
                this.showToast(`歐美整理預覽：可整理 ${this.westernSummary.planned_count || 0}，衝突 ${this.westernSummary.conflict_count || 0}`, 'success');
            } catch (error) {
                this.showToast(apiErrorMessage(error, '歐美整理預覽失敗'), 'error');
            } finally {
                this.busy = false;
            }
        },

        async applyWesternBatch() {
            if (!this.westernManifest) return;
            this.busy = true;
            try {
                const data = await postJson('/api/western-organizer/apply', {
                    manifest: this.westernManifest,
                    confirm: true,
                    batch_size: this.applyBatchSize,
                });
                this.westernEntries = data.entries || [];
                this.westernSummary = {
                    total: this.westernEntries.length,
                    planned_count: data.remaining || 0,
                    moved_count: data.moved_entries || 0,
                };
                this.summary = this.westernSummary;
                if (!data.remaining) this.westernManifest = '';
                this.showToast(`歐美整理已套用：移動 ${data.moved_entries || 0}，剩餘 ${data.remaining || 0}`, 'success');
            } catch (error) {
                this.showToast(apiErrorMessage(error, '歐美整理套用失敗'), 'error');
            } finally {
                this.busy = false;
            }
        },

        async previewPendingApply() {
            const ids = [...this.pendingApplyIds];
            this.pendingApplyAfterSearch = false;
            this.pendingApplyIds = [];
            const entries = this.entriesReadyForPlan(ids);
            if (!entries.length) {
                this.manifest = '';
                this.showToast(window.t('organize.toast_selected_no_found'), 'info');
                return;
            }
            await this.planEntries(entries, { merge: true });
            const selected = new Set(ids.map(String));
            const selectedPlannedCount = this.entries.filter((entry) => (
                selected.has(String(entry.id)) && entry.status === 'planned'
            )).length;
            if (this.manifest && selectedPlannedCount > 0) {
                await this.applyBatch();
            }
        },

        async applyBatch() {
            this.busy = true;
            this.applyingBatches = true;
            this.applyPaused = false;
            this.applyStats = { moved: 0, remaining: 0, batches: 0 };
            this.applyModalOpen = false;
            try {
                let data = null;
                do {
                    data = await postJson('/api/inbox-organizer/apply', {
                        manifest: this.manifest,
                        confirm: true,
                        batch_size: this.applyBatchSize,
                    });
                    this.applyStats = {
                        moved: this.applyStats.moved + (data.moved_entries || 0),
                        remaining: data.remaining || 0,
                        batches: this.applyStats.batches + 1,
                    };
                    this.mergeApplyResult(data);
                    this.summary = {
                        ...this.summary,
                        total: this.entries.length,
                        planned_count: data.remaining || 0,
                        moved_count: this.applyStats.moved,
                    };
                    if (data.remaining > 0) await new Promise((resolve) => setTimeout(resolve, 120));
                } while (data && data.remaining > 0 && !this.applyPaused);
                if (!this.applyPaused) this.manifest = '';
                this.showToast(window.t('organize.toast_apply_done', {
                    moved: this.applyStats.moved,
                    remaining: this.applyStats.remaining,
                }), this.applyPaused ? 'info' : 'success');
                await this.loadRoots();
            } catch (error) {
                this.showToast(apiErrorMessage(error, window.t('organize.toast_apply_failed')), 'error');
            } finally {
                this.applyingBatches = false;
                this.busy = false;
            }
        },

        mergeApplyResult(data) {
            if (!Array.isArray(data.entries)) return;
            const moved = data.entries.filter((entry) => entry.status === 'moved');
            const movedIds = new Set(moved.map((entry) => String(entry.id)));
            const movedSources = new Set(moved.map((entry) => String(entry.source)));
            const returnedById = new Map(data.entries.map((entry) => [String(entry.id), entry]));
            this.entries = this.entries
                .filter((entry) => !movedIds.has(String(entry.id)) && !movedSources.has(String(entry.source)))
                .map((entry) => {
                    const returned = returnedById.get(String(entry.id));
                    const merged = returned ? { ...entry, ...returned } : entry;
                    return {
                        ...merged,
                        manual_number: merged.manual_number || merged.number || '',
                        selected: false,
                    };
                });
        },

        pauseApplyBatches() {
            this.applyPaused = true;
        },

        async openInbox(path) {
            if (!window.pywebview?.api?.open_folder) {
                this.showToast(window.t('organize.desktop_only'), 'info');
                return;
            }
            try {
                await window.pywebview.api.open_folder(path);
            } catch (_error) {
                this.showToast(window.t('organize.open_failed'), 'error');
            }
        },

        statusLabel(status) {
            return window.t(`organize.status_${status || 'unknown'}`);
        },

        reasonLabel(reason) {
            if (!reason) return '';
            const key = `organize.reason_${reason}`;
            const value = window.t(key);
            return value === `[${key}]` ? reason : value;
        },

        logMessage(item) {
            const key = `organize.log_${item.message}`;
            const value = window.t(key, {
                current: item.current || '',
                total: item.total || '',
                label: item.label || '',
                source: item.source || '',
            });
            return value === `[${key}]` ? (item.message || '') : value;
        },

        scrollLogsToBottom() {
            const el = this.$refs?.organizeLog;
            if (el) el.scrollTop = el.scrollHeight;
        },

        toggleAllSelected(event) {
            if (this.bulkOperationRunning) return;
            const checked = Boolean(event?.target?.checked);
            this.visibleEntries.forEach((item) => {
                item.entry.selected = checked;
            });
            this.lastSelectedIndex = checked && this.visibleEntries.length ? 0 : null;
        },

        selectVisibleEntries(selected) {
            if (this.bulkOperationRunning) return;
            this.visibleEntries.forEach((item) => {
                item.entry.selected = Boolean(selected);
            });
            this.lastSelectedIndex = selected && this.visibleEntries.length ? 0 : null;
        },

        clearEntryFilters() {
            this.pathFilter = '';
            this.reviewFilter = false;
            this.metadataFilter = false;
            this.selectedFilter = false;
            this.lastSelectedIndex = null;
        },

        toggleEntrySelection(visibleIndex, event) {
            if (this.bulkOperationRunning) return;
            const checked = Boolean(event?.target?.checked);
            if (event?.shiftKey && this.lastSelectedIndex !== null) {
                const start = Math.min(this.lastSelectedIndex, visibleIndex);
                const end = Math.max(this.lastSelectedIndex, visibleIndex);
                for (let i = start; i <= end; i += 1) {
                    const item = this.visibleEntries[i];
                    if (item?.entry) item.entry.selected = checked;
                }
            } else {
                const item = this.visibleEntries[visibleIndex];
                if (item?.entry) item.entry.selected = checked;
            }
            this.lastSelectedIndex = visibleIndex;
        },
    }));
});
