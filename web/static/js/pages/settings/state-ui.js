// 64b-2: 新 section ID（3 個）取代舊 6 tab ID（CD-64-B6）
const SETTINGS_TAB_IDS = ['sec-search', 'sec-gallery', 'sec-system'];

// 64b-2: 舊 6-tab 值遷移 map（舊 localStorage/hash → 新 section id；CD-64-B6）
const LEGACY_TAB_MAP = {
    display:   'sec-search',
    scraping:  'sec-search',
    sources:   'sec-search',
    translate: 'sec-search',
    advanced:  'sec-search',
    organize:  'sec-gallery',
};

export function stateUI() {
    return {
        // ===== UI State =====
        // 64b-2: activeTab → activeSection（初始值 sec-search；由 _initActiveTab 處理）
        activeSection: 'sec-search',
        newSuffixInput: '',
        showPathHelp: false,
        showSampleImagesHelp: false,

        // 64b-1: 進階摺疊開關（x-collapse 驅動）
        scraperAdvanced: false,
        galleryAdvanced: false,

        // Toast state
        _toast: { message: '', type: 'success', visible: false },
        _toastTimer: null,

        // Dirty Check Modal State
        dirtyCheckModalOpen: false,

        // Reset Config Modal State (T3.4)
        resetConfigModalOpen: false,
        _resetConfigLoading: false,

        // B1: Scanner directory link state
        favoriteScannerLink: null,   // null=隱藏, {linked, matched_directory}=已查
        showDirDropdown: false,
        scannerDirectories: [],

        // 64b-2: IntersectionObserver ref（供 cleanup disconnect）
        _sectionObserver: null,

        // ===== Methods =====
        showToast(message, type = 'success', duration = 2500) {
            this._toast.message = message;
            this._toast.type = type;
            this._toast.visible = true;
            if (this._toastTimer) clearTimeout(this._toastTimer);
            this._toastTimer = setTimeout(() => {
                this._toast.visible = false;
                this._toastTimer = null;
            }, duration);
        },

        async selectOutputFolder() {
            if (typeof window.pywebview === 'undefined' || !window.pywebview.api) {
                this.showToast(window.t('settings.toast.desktop_only'), 'info');
                return;
            }

            try {
                const result = await window.pywebview.api.select_folder();
                if (result && result.folder) {
                    this.form.avlistOutputDir = result.folder;
                }
            } catch (e) {
                console.error('選擇資料夾失敗:', e);
            }
        },

        // Dirty check modal — 儲存更改後離開
        async dirtyCheckSave() {
            await this.saveConfig();
            // saveConfig 成功會更新 savedState，isDirty 變 false
            if (!this.isDirty) {
                // 儲存成功，透過 lifecycle API 執行 cleanup 再跳轉
                this.dirtyCheckModalOpen = false;
                if (window.__leavePage) {
                    if (!window.__leavePage(this.pendingNavigationUrl)) return;
                }
                window.location.href = this.pendingNavigationUrl;
            }
            // 儲存失敗：modal 保持開啟，toast 已顯示錯誤
            // 用戶可選「不儲存」離開或「取消」留下
        },

        // Dirty check modal — 不儲存直接離開
        dirtyCheckDiscard() {
            this.savedState = null;  // 防止殘留
            // T3(40b): 透過 lifecycle API 執行 cleanup 再跳轉
            // __leavePage 回傳 false 表示 cleanup 阻止導航（例如仍有進行中請求）
            if (window.__leavePage) {
                if (!window.__leavePage(this.pendingNavigationUrl)) return;
            }
            window.location.href = this.pendingNavigationUrl;
        },

        // Dirty check modal — 取消（留在 settings）
        dirtyCheckCancel() {
            this.pendingNavigationUrl = '';
            this.dirtyCheckModalOpen = false;
        },

        // ─── 64b-2: activeSection / URL hash / localStorage ──────────────────
        // ⚠️ 具名 init helper（禁加 stateUI.init() — 會覆蓋 stateConfig.init）。
        // 由 state-config.js init() 末尾呼叫，比照 _initB1 慣例。
        _initActiveTab() {
            // 優先序：URL hash > localStorage('settings_active_tab') > 'sec-search'
            // 舊 6-tab 值先過 LEGACY_TAB_MAP 再驗新 SETTINGS_TAB_IDS

            let resolved = 'sec-search';

            // 1) URL hash（去掉前導 #）
            const hashId = (location.hash || '').replace(/^#/, '');
            if (hashId) {
                const mapped = LEGACY_TAB_MAP[hashId];
                if (mapped) {
                    resolved = mapped;
                } else if (SETTINGS_TAB_IDS.includes(hashId)) {
                    resolved = hashId;
                }
                // else: 未知 hash → fallback sec-search
            } else {
                // 2) localStorage（隱私模式 / storage 不可用會拋，包 try-catch）
                try {
                    const stored = localStorage.getItem('settings_active_tab');
                    if (stored) {
                        const mappedStored = LEGACY_TAB_MAP[stored];
                        if (mappedStored) {
                            resolved = mappedStored;
                        } else if (SETTINGS_TAB_IDS.includes(stored)) {
                            resolved = stored;
                        }
                        // else: 未知值 → fallback sec-search
                    }
                } catch (e) {
                    console.warn('[settings] read settings_active_tab failed:', e);
                }
            }

            this.activeSection = resolved;

            // 初始 deep-link 捲動：resolved 非首段（sec-search 在頂部，無需捲）時，
            // 載入後 instant 捲到該段，否則頁面停在頂部 → IO 會立刻把 activeSection
            // clobber 回 sec-search，deep-link 失效。用 requestAnimationFrame 確保
            // section 已 layout；instant（smooth=false）避免與 IO 捲動途中競態。
            if (resolved !== 'sec-search') {
                requestAnimationFrame(() => this.scrollToSection(resolved, false));
            }

            // activeSection 變更 → 記憶 + 同步 URL（replaceState，不堆瀏覽歷史）
            // IO callback 只設 activeSection，由此 $watch 統一寫 localStorage + replaceState。
            this.$watch('activeSection', (val) => {
                if (!SETTINGS_TAB_IDS.includes(val)) return;
                try {
                    localStorage.setItem('settings_active_tab', val);
                } catch (e) {
                    console.warn('[settings] write settings_active_tab failed:', e);
                }
                history.replaceState(null, '', '#' + val);
            });

            // 外部深連結（如 /settings#organize）：監聽 hashchange
            window.addEventListener('hashchange', () => {
                const raw = (location.hash || '').replace(/^#/, '');
                const id = LEGACY_TAB_MAP[raw] || (SETTINGS_TAB_IDS.includes(raw) ? raw : 'sec-search');
                if (id !== this.activeSection) this.activeSection = id;
            });

            // 64b-2: IntersectionObserver scrollspy（掛在此函式末尾，不另外 x-init）
            this._initScrollspy();
        },

        // ─── 64b-2: scrollToSection（64b-4: + US-B2 auto-expand）──────────────
        scrollToSection(id, smooth = true) {
            const el = document.getElementById(id);
            if (!el) return;
            // 64b-4 US-B2: 使用者點 nav（smooth=true）時自動展開該區進階摺疊；
            // 初始 deep-link（smooth=false）不展開，避免載入即動所有摺疊。
            if (smooth) {
                const SECTION_COLLAPSE = { 'sec-search': 'scraperAdvanced', 'sec-gallery': 'galleryAdvanced' };
                const prop = SECTION_COLLAPSE[id];
                if (prop) this[prop] = true;
            }
            el.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
        },

        // ─── 64b-2: IntersectionObserver scrollspy ────────────────────────────
        _initScrollspy() {
            const sections = SETTINGS_TAB_IDS.map(id => document.getElementById(id)).filter(Boolean);
            if (!sections.length) return;

            const observer = new IntersectionObserver((entries) => {
                // 取 intersecting 且 intersectionRatio 最大的那個
                let best = null;
                for (const entry of entries) {
                    if (entry.isIntersecting) {
                        if (!best || entry.intersectionRatio > best.intersectionRatio) best = entry;
                    }
                }
                if (best) {
                    const id = best.target.id;
                    if (SETTINGS_TAB_IDS.includes(id) && id !== this.activeSection) {
                        // 只設 activeSection；設值會觸發 $watch('activeSection')，
                        // 由 $watch 統一寫 localStorage + history.replaceState。
                        // 不要在此重複寫（會造成 double replaceState）。
                        this.activeSection = id;
                    }
                }
            }, {
                rootMargin: '-20% 0px -60% 0px',   // 進入視窗上方 20% 即觸發；下方 60% 留白
                threshold: [0, 0.1, 0.25, 0.5]
            });

            sections.forEach(s => observer.observe(s));
            // cleanup：存 observer ref 供 state-config.js cleanup() disconnect 用
            this._sectionObserver = observer;
        },

        // ─── B1: Scanner directory link ───────────────────────────────────────

        async _initB1() {
            try {
                const cfg = await fetch('/api/config').then(r => r.json());
                // response 結構：{success, data: {gallery: {directories}}}
                this.scannerDirectories =
                    (cfg.data && cfg.data.gallery && cfg.data.gallery.directories) || [];
            } catch (e) {
                console.error('[B1] _initB1: fetch /api/config failed', e);
                this.scannerDirectories = [];
            }
            // $watch 在 Alpine init() hook 內才能呼叫（由 state-config.js init() 呼叫 _initB1 後掛）
            this.$watch('form.searchFavoriteFolder', () => this.refreshScannerLink());
            // 初始刷新一次（反映 loadConfig 填好的值）
            await this.refreshScannerLink();
        },

        async refreshScannerLink() {
            const fav = (this.form && this.form.searchFavoriteFolder) || '';
            if (!fav.trim()) {
                this.favoriteScannerLink = null;
                return;
            }
            try {
                const resp = await fetch(
                    '/api/settings/favorite-scanner-link?favorite=' + encodeURIComponent(fav)
                );
                this.favoriteScannerLink = await resp.json();
            } catch (e) {
                console.error('[B1] refreshScannerLink failed', e);
                this.favoriteScannerLink = null;
            }
        },

        pickScannerDirectory(dir) {
            if (this.form) this.form.searchFavoriteFolder = dir;
            this.showDirDropdown = false;
            this.refreshScannerLink();
        },
    };
}
