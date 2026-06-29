export function stateDuplicates() {
    return {
        duplicateLoading: false,
        duplicateError: '',
        duplicateReport: null,
        duplicateShowMultipart: false,
        duplicateDeleteModalOpen: false,
        duplicateDeleteLoading: false,
        duplicateDeleteApplying: false,
        duplicateDeletePreview: null,
        duplicateDeleteItem: null,
        duplicateDeleteError: '',
        emptyFolderLoading: false,
        emptyFolderApplying: false,
        emptyFolderError: '',
        emptyFolderPreview: null,
        emptyFolderModalOpen: false,
        titlePlaceholderLoading: false,
        titlePlaceholderApplying: false,
        titlePlaceholderError: '',
        titlePlaceholderPreview: null,
        titlePlaceholderModalOpen: false,

        async apiErrorMessage(resp, fallback) {
            try {
                const data = await resp.json();
                if (typeof data?.detail === 'string') return data.detail;
                if (typeof data?.detail?.message === 'string') return data.detail.message;
                if (typeof data?.message === 'string') return data.message;
                if (typeof data?.error === 'string') return data.error;
            } catch {
                // ignore JSON parse failures and fall through to the fixed fallback
            }
            return fallback || ('HTTP ' + resp.status);
        },

        get duplicateSummary() {
            return this.duplicateReport?.summary || {
                duplicate_group_count: 0,
                duplicate_file_count: 0,
                multipart_group_count: 0,
                hidden_multipart_count: 0,
                missing_path_count: 0,
                returned_group_count: 0,
            };
        },

        get duplicateGroups() {
            return this.duplicateReport?.groups || [];
        },

        duplicateClassificationLabel(group) {
            if (group?.classification === 'multipart') {
                return window.t('scanner.duplicates.classification_multipart');
            }
            return window.t('scanner.duplicates.classification_duplicate');
        },

        duplicateReasonLabel(reason) {
            if (!reason) return '';
            if (reason === 'multiple_unlabeled_files') {
                return window.t('scanner.duplicates.reason_multiple_unlabeled');
            }
            if (reason === 'complementary_multipart') {
                return window.t('scanner.duplicates.reason_complementary_multipart');
            }
            if (reason === 'mixed_unlabeled_and_multipart') {
                return window.t('scanner.duplicates.reason_mixed');
            }
            if (reason.startsWith('duplicate_part:')) {
                return window.t('scanner.duplicates.reason_duplicate_part', {
                    part: reason.slice('duplicate_part:'.length),
                });
            }
            return reason;
        },

        duplicateTagsText(tags) {
            if (!tags || tags.length === 0) return window.t('scanner.duplicates.none');
            return tags.join(', ');
        },

        formatDuplicateSize(bytes) {
            const value = Number(bytes || 0);
            if (!value) return window.t('scanner.duplicates.unknown');
            const gb = value / (1024 * 1024 * 1024);
            if (gb >= 1) return gb.toFixed(2) + ' GB';
            const mb = value / (1024 * 1024);
            return mb.toFixed(1) + ' MB';
        },

        formatDuplicateMtime(value) {
            const numeric = Number(value || 0);
            if (!numeric) return window.t('scanner.duplicates.unknown');
            const date = new Date(numeric * 1000);
            if (Number.isNaN(date.getTime())) return window.t('scanner.duplicates.unknown');
            return date.toLocaleString();
        },

        duplicateDeleteAllowed(group, item) {
            return group?.classification !== 'multipart'
                && item?.delete_allowed === true
                && item?.exists !== false;
        },

        async loadDuplicateNumbers() {
            this.duplicateLoading = true;
            this.duplicateError = '';
            try {
                const params = new URLSearchParams({
                    include_multipart: this.duplicateShowMultipart ? 'true' : 'false',
                    include_missing_paths: 'true',
                    limit: '500',
                });
                const resp = await fetch('/api/library-migration/duplicates?' + params.toString());
                if (!resp.ok) {
                    throw new Error(await this.apiErrorMessage(resp, 'HTTP ' + resp.status));
                }
                this.duplicateReport = await resp.json();
                const count = this.duplicateSummary.duplicate_group_count || 0;
                this.showToast(
                    count > 0
                        ? window.t('scanner.duplicates.toast_found', { count })
                        : window.t('scanner.duplicates.toast_none'),
                    count > 0 ? 'warning' : 'success',
                    3500,
                );
            } catch (e) {
                this.duplicateError = e.message || String(e);
                this.showToast(window.t('scanner.duplicates.toast_failed'), 'error', 4000);
            } finally {
                this.duplicateLoading = false;
            }
        },

        async openDuplicateFolder(path) {
            if (!path) return;
            if (!window.pywebview?.api?.open_folder) {
                this.showToast(window.t('scanner.toast.desktop_only'), 'info');
                return;
            }
            try {
                const ok = await window.pywebview.api.open_folder(path);
                this.showToast(
                    ok ? window.t('scanner.duplicates.opened_folder') : window.t('scanner.duplicates.open_failed'),
                    ok ? 'success' : 'error',
                );
            } catch {
                this.showToast(window.t('scanner.duplicates.open_failed'), 'error');
            }
        },

        async openDuplicateDeleteModal(group, item) {
            if (!this.duplicateDeleteAllowed(group, item)) {
                this.showToast(window.t('scanner.duplicates.delete_multipart_disabled'), 'info', 3500);
                return;
            }
            this.duplicateDeleteLoading = true;
            this.duplicateDeleteError = '';
            this.duplicateDeletePreview = null;
            this.duplicateDeleteItem = item;
            try {
                const resp = await fetch('/api/library-migration/duplicate-delete/preview', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: item.path }),
                });
                if (!resp.ok) {
                    throw new Error(await this.apiErrorMessage(resp, 'HTTP ' + resp.status));
                }
                this.duplicateDeletePreview = await resp.json();
                this.duplicateDeleteModalOpen = true;
            } catch (e) {
                this.duplicateDeleteError = e.message || String(e);
                this.showToast(window.t('scanner.duplicates.delete_preview_failed'), 'error', 4000);
            } finally {
                this.duplicateDeleteLoading = false;
            }
        },

        cancelDuplicateDelete() {
            if (this.duplicateDeleteApplying) return;
            this.closeDuplicateDeleteModal();
        },

        closeDuplicateDeleteModal() {
            this.duplicateDeleteModalOpen = false;
            this.duplicateDeletePreview = null;
            this.duplicateDeleteItem = null;
            this.duplicateDeleteError = '';
        },

        async confirmDuplicateDelete() {
            if (!this.duplicateDeletePreview || this.duplicateDeleteApplying) return;
            this.duplicateDeleteApplying = true;
            this.duplicateDeleteError = '';
            try {
                const resp = await fetch('/api/library-migration/duplicate-delete/apply', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        path: this.duplicateDeletePreview.path_uri || this.duplicateDeletePreview.path,
                        confirm: true,
                    }),
                });
                if (!resp.ok) {
                    throw new Error(await this.apiErrorMessage(resp, 'HTTP ' + resp.status));
                }
                const result = await resp.json();
                const removedFolders = Number(result.removed_empty_folder_count || 0);
                const warningCount = (result.warnings || []).length;
                let message = window.t('scanner.duplicates.delete_success');
                if (removedFolders > 0) {
                    message = window.t('scanner.duplicates.delete_success_with_empty_folders', {
                        count: removedFolders,
                    });
                } else if (warningCount > 0) {
                    message = window.t('scanner.duplicates.delete_success_empty_folder_warning');
                }
                this.showToast(message, warningCount > 0 ? 'warning' : 'success', 4500);
                this.closeDuplicateDeleteModal();
                await this.loadDuplicateNumbers();
            } catch (e) {
                this.duplicateDeleteError = e.message || String(e);
                this.showToast(window.t('scanner.duplicates.delete_failed'), 'error', 4500);
            } finally {
                this.duplicateDeleteApplying = false;
            }
        },

        get emptyFolderSummary() {
            return this.emptyFolderPreview || {
                folder_count: 0,
                folders: [],
                skipped_protected_count: 0,
                protected_names: [],
            };
        },

        async loadEmptyFolders() {
            this.emptyFolderLoading = true;
            this.emptyFolderError = '';
            try {
                const resp = await fetch('/api/library-migration/empty-folders/preview', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({}),
                });
                if (!resp.ok) {
                    throw new Error(await this.apiErrorMessage(resp, 'HTTP ' + resp.status));
                }
                this.emptyFolderPreview = await resp.json();
                const count = Number(this.emptyFolderPreview.folder_count || 0);
                this.showToast(
                    count > 0
                        ? window.t('scanner.empty_folders.toast_found', { count })
                        : window.t('scanner.empty_folders.toast_none'),
                    count > 0 ? 'info' : 'success',
                    3500,
                );
            } catch (e) {
                this.emptyFolderError = e.message || String(e);
                this.showToast(window.t('scanner.empty_folders.toast_failed'), 'error', 4000);
            } finally {
                this.emptyFolderLoading = false;
            }
        },

        openEmptyFolderCleanupModal() {
            if (!this.emptyFolderPreview || Number(this.emptyFolderPreview.folder_count || 0) <= 0) {
                this.showToast(window.t('scanner.empty_folders.toast_none'), 'info', 3000);
                return;
            }
            this.emptyFolderModalOpen = true;
        },

        cancelEmptyFolderCleanup() {
            if (this.emptyFolderApplying) return;
            this.emptyFolderModalOpen = false;
            this.emptyFolderError = '';
        },

        async confirmEmptyFolderCleanup() {
            if (!this.emptyFolderPreview || this.emptyFolderApplying) return;
            this.emptyFolderApplying = true;
            this.emptyFolderError = '';
            try {
                const paths = (this.emptyFolderPreview.folders || []).map(folder => folder.path);
                const resp = await fetch('/api/library-migration/empty-folders/apply', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ paths, confirm: true }),
                });
                if (!resp.ok) {
                    throw new Error(await this.apiErrorMessage(resp, 'HTTP ' + resp.status));
                }
                const result = await resp.json();
                const count = Number(result.removed_empty_folder_count || 0);
                this.showToast(
                    window.t('scanner.empty_folders.cleanup_success', { count }),
                    'success',
                    4000,
                );
                this.emptyFolderModalOpen = false;
                await this.loadEmptyFolders();
            } catch (e) {
                this.emptyFolderError = e.message || String(e);
                this.showToast(window.t('scanner.empty_folders.cleanup_failed'), 'error', 4500);
            } finally {
                this.emptyFolderApplying = false;
            }
        },

        get titlePlaceholderSummary() {
            return this.titlePlaceholderPreview?.summary || {
                candidate_count: 0,
                planned_count: 0,
                conflict_count: 0,
                sidecar_count: 0,
            };
        },

        get titlePlaceholderEntries() {
            return this.titlePlaceholderPreview?.entries || [];
        },

        titlePlaceholderReasonLabel(reason) {
            if (!reason) return '';
            return String(reason).split(',').map(part => {
                const key = 'scanner.title_placeholders.reason_' + part.trim();
                const translated = window.t(key);
                return translated === key ? part.trim() : translated;
            }).filter(Boolean).join(', ');
        },

        titlePlaceholderStatusLabel(status) {
            const key = 'scanner.title_placeholders.status_' + (status || 'planned');
            const translated = window.t(key);
            return translated === key ? (status || '') : translated;
        },

        async loadTitlePlaceholders() {
            this.titlePlaceholderLoading = true;
            this.titlePlaceholderError = '';
            try {
                const resp = await fetch('/api/library-migration/title-placeholder/preview', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({}),
                });
                if (!resp.ok) {
                    throw new Error(await this.apiErrorMessage(resp, 'HTTP ' + resp.status));
                }
                this.titlePlaceholderPreview = await resp.json();
                const count = Number(this.titlePlaceholderSummary.candidate_count || 0);
                this.showToast(
                    count > 0
                        ? window.t('scanner.title_placeholders.toast_found', { count })
                        : window.t('scanner.title_placeholders.toast_none'),
                    count > 0 ? 'warning' : 'success',
                    3500,
                );
            } catch (e) {
                this.titlePlaceholderError = e.message || String(e);
                this.showToast(window.t('scanner.title_placeholders.toast_failed'), 'error', 4000);
            } finally {
                this.titlePlaceholderLoading = false;
            }
        },

        openTitlePlaceholderModal() {
            if (!this.titlePlaceholderPreview || Number(this.titlePlaceholderSummary.planned_count || 0) <= 0) {
                this.showToast(window.t('scanner.title_placeholders.toast_none'), 'info', 3000);
                return;
            }
            this.titlePlaceholderModalOpen = true;
        },

        cancelTitlePlaceholderMove() {
            if (this.titlePlaceholderApplying) return;
            this.titlePlaceholderModalOpen = false;
            this.titlePlaceholderError = '';
        },

        async confirmTitlePlaceholderMove() {
            if (!this.titlePlaceholderPreview || this.titlePlaceholderApplying) return;
            this.titlePlaceholderApplying = true;
            this.titlePlaceholderError = '';
            try {
                const resp = await fetch('/api/library-migration/title-placeholder/apply', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        manifest: this.titlePlaceholderPreview.manifest,
                        confirm: true,
                        batch_size: 20,
                    }),
                });
                if (!resp.ok) {
                    throw new Error(await this.apiErrorMessage(resp, 'HTTP ' + resp.status));
                }
                const result = await resp.json();
                const moved = Number(result.moved_entries || 0);
                const remaining = Number(result.remaining || 0);
                const removedFolders = Number(result.removed_empty_folder_count || 0);
                this.showToast(
                    window.t('scanner.title_placeholders.move_success', { moved, remaining, folders: removedFolders }),
                    'success',
                    4500,
                );
                this.titlePlaceholderModalOpen = false;
                await this.loadTitlePlaceholders();
                if (typeof this.loadEmptyFolders === 'function') {
                    this.emptyFolderPreview = null;
                }
            } catch (e) {
                this.titlePlaceholderError = e.message || String(e);
                this.showToast(window.t('scanner.title_placeholders.move_failed'), 'error', 4500);
            } finally {
                this.titlePlaceholderApplying = false;
            }
        },
    };
}
