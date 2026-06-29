export function stateProviders() {
    return {
        // ===== Provider Status State =====
        ollamaStatus: '',
        modelStatus: '',
        geminiStatus: '',
        geminiModelStatus: '',
        ollamaModels: [],
        geminiModels: [],
        openaiStatus: '',
        openaiModelStatus: '',
        openaiModels: [],
        openaiUseCustomModel: false,
        fetchingOpenaiModels: false,
        testingOpenaiTranslate: false,

        // Test buttons loading state
        testOllamaLoading: false,
        testModelLoading: false,
        testGeminiLoading: false,
        testGeminiTranslateLoading: false,
        proxyStatus: '',
        proxyStatusOk: false,
        testProxyLoading: false,
        stashStatus: '',
        stashStatusOk: false,
        testStashLoading: false,

        // ===== Methods =====
        _nasPayload() {
            return {
                id: this.editingNasId || '',
                name: this.form.nasName.trim(),
                host: this.form.nasHost.trim(),
                share: this.form.nasShare.trim(),
                subpath: this.form.nasSubpath.trim(),
                username: this.form.nasUsername.trim(),
                password: this.form.nasPassword,
                enabled: this.form.nasEnabled !== false,
                add_to_gallery: this.form.nasAddToGallery === true,
            };
        },

        _resetNasForm() {
            this.form.nasName = '';
            this.form.nasHost = '';
            this.form.nasShare = '';
            this.form.nasSubpath = '';
            this.form.nasUsername = '';
            this.form.nasPassword = '';
            this.form.nasEnabled = true;
            this.form.nasAddToGallery = true;
            this.editingNasId = '';
        },

        editNasShare(share) {
            if (!share) return;
            this.editingNasId = share.id || '';
            this.form.nasName = share.name || '';
            this.form.nasHost = share.host || '';
            this.form.nasShare = share.share || '';
            this.form.nasSubpath = share.subpath || '';
            this.form.nasUsername = share.username || '';
            this.form.nasPassword = '';
            this.form.nasEnabled = share.enabled !== false;
            this.form.nasAddToGallery = share.add_to_gallery !== false;
            this.nasStatusOk = true;
            this.nasStatus = '正在編輯已保存 NAS；密碼不會回填，如需更新請重新輸入。';
        },

        cancelNasEdit() {
            this._resetNasForm();
            this.nasStatus = '';
            this.nasStatusOk = false;
        },

        async testNasConnection() {
            const payload = this._nasPayload();
            if (!payload.host || !payload.share) {
                this.nasStatusOk = false;
                this.nasStatus = '請先填寫 NAS 主機與共享名稱';
                return;
            }
            this.nasLoading = true;
            this.nasStatusOk = false;
            this.nasStatus = window.t('settings.status.testing');
            try {
                const resp = await fetch('/api/settings/nas/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const result = await resp.json();
                this.nasStatusOk = result.success === true;
                const code = result.code ? `[${result.code}] ` : '';
                this.nasStatus = (result.success ? '✓ ' : '✗ ') + code + (result.message || result.error || 'NAS 連線測試完成');
            } catch (e) {
                this.nasStatusOk = false;
                this.nasStatus = window.t('settings.status.network_error');
            } finally {
                this.nasLoading = false;
            }
        },

        async saveNasShare() {
            const payload = this._nasPayload();
            if (!payload.host || !payload.share) {
                this.nasStatusOk = false;
                this.nasStatus = '請先填寫 NAS 主機與共享名稱';
                return;
            }
            this.nasLoading = true;
            this.nasStatusOk = false;
            const editing = !!this.editingNasId;
            this.nasStatus = editing ? '更新 NAS 設定中...' : '保存 NAS 設定中...';
            try {
                const resp = await fetch('/api/settings/nas/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const result = await resp.json();
                if (result.success === true) {
                    this.nasStatusOk = true;
                    this.nasStatus = `✓ 已${editing ? '更新' : '保存'}：${result.share?.unc_path || ''}`;
                    this._resetNasForm();
                    await this.loadConfig();
                } else {
                    const code = result.code ? `[${result.code}] ` : '';
                    this.nasStatus = `✗ ${code}${result.message || result.error || 'NAS 保存失敗'}`;
                }
            } catch (e) {
                this.nasStatus = window.t('settings.status.network_error');
            } finally {
                this.nasLoading = false;
            }
        },

        async refreshNasStatus() {
            try {
                const resp = await fetch('/api/settings/nas/status');
                const result = await resp.json();
                if (result.success && Array.isArray(result.data)) {
                    const byId = Object.fromEntries(result.data.map((s) => [s.id, s]));
                    this.nasShares = (this.nasShares || []).map((s) => ({ ...s, ...(byId[s.id] || {}) }));
                }
            } catch (_e) {
                // status is best-effort; settings can still be saved.
            }
        },

        async removeNasShare(share) {
            if (!share?.id || this.nasLoading) return;
            const ok = window.confirm('只會移除 OpenAver 保存的 NAS 設定，不會刪除媒體檔案，也不會清除 Windows Credential Manager 憑據。');
            if (!ok) return;

            this.nasLoading = true;
            this.nasStatusOk = false;
            this.nasStatus = '移除 NAS 設定中...';
            try {
                const resp = await fetch(`/api/settings/nas/${encodeURIComponent(share.id)}`, {
                    method: 'DELETE',
                });
                const result = await resp.json();
                if (result.success === true) {
                    if (this.editingNasId === share.id) this._resetNasForm();
                    this.nasStatusOk = true;
                    this.nasStatus = result.removed
                        ? '✓ 已移除 NAS 設定；掃描資料夾與憑據保持不變。'
                        : '✓ NAS 設定已不存在。';
                    await this.loadConfig();
                } else {
                    const code = result.code ? `[${result.code}] ` : '';
                    this.nasStatus = `✗ ${code}${result.message || result.error || 'NAS 移除失敗'}`;
                }
            } catch (_e) {
                this.nasStatus = window.t('settings.status.network_error');
            } finally {
                this.nasLoading = false;
            }
        },

        async loadOllamaModels(url, savedModel = '') {
            if (!url) return;

            this.ollamaStatus = `<span class="text-base-content/50">${window.t('settings.status.loading_models')}</span>`;

            try {
                const resp = await fetch(`/api/ollama/models?url=${encodeURIComponent(url)}`);
                const result = await resp.json();

                if (result.success && result.models && result.models.length > 0) {
                    this.ollamaModels = result.models;

                    // 設定儲存的模型
                    if (savedModel && result.models.includes(savedModel)) {
                        this.form.ollamaModel = savedModel;
                    } else if (result.models.length > 0) {
                        this.form.ollamaModel = result.models[0];
                    }

                    this.ollamaStatus = `<span class="text-success"><i class="bi bi-check-circle"></i> ${window.t('settings.status.n_models', {count: result.models.length})}</span>`;
                } else {
                    this.ollamaStatus = `<span class="text-warning"><i class="bi bi-exclamation-circle"></i> ${result.error || window.t('settings.status.connection_failed')}</span>`;
                }
            } catch (e) {
                this.ollamaStatus = `<span class="text-warning"><i class="bi bi-exclamation-circle"></i> ${window.t('settings.status.connection_failed')}</span>`;
            }
        },

        async testProxy() {
            if (!this.form.proxyUrl.trim()) return;

            this.testProxyLoading = true;
            this.proxyStatusOk = false;
            this.proxyStatus = window.t('settings.status.testing');

            try {
                const resp = await fetch('/api/proxy/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ proxy_url: this.form.proxyUrl.trim() })
                });
                const result = await resp.json();

                if (result.success === true) {
                    this.proxyStatusOk = true;
                    this.proxyStatus = `✓ ${result.message}`;
                } else {
                    this.proxyStatus = `✗ ${result.message}`;
                }
            } catch (e) {
                this.proxyStatus = window.t('settings.status.network_error');
            } finally {
                this.testProxyLoading = false;
            }
        },

        async testStashConnection() {
            if (!this.form.stashUrl.trim()) {
                this.stashStatusOk = false;
                this.stashStatus = '請先填寫 Stash URL';
                return;
            }

            this.testStashLoading = true;
            this.stashStatusOk = false;
            this.stashStatus = window.t('settings.status.testing');

            try {
                const resp = await fetch('/api/settings/stash/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: this.form.stashUrl.trim(),
                        api_key: this.form.stashApiKey.trim(),
                        proxy_url: this.form.stashProxyUrl.trim(),
                    })
                });
                const result = await resp.json();

                if (result.success === true) {
                    this.stashStatusOk = true;
                    const version = result.version ? ` (${result.version})` : '';
                    this.stashStatus = `✓ ${result.message || 'Stash 連線成功'}${version}`;
                } else {
                    const code = result.code ? `[${result.code}] ` : '';
                    this.stashStatus = `✗ ${code}${result.message || result.error || 'Stash 連線失敗'}`;
                }
            } catch (e) {
                this.stashStatus = window.t('settings.status.network_error');
            } finally {
                this.testStashLoading = false;
            }
        },

        async testOllamaConnection() {
            const url = this.form.ollamaUrl.trim();

            if (!url) {
                this.ollamaStatus = `<span class="text-error">${window.t('settings.status.enter_url')}</span>`;
                return;
            }

            this.testOllamaLoading = true;
            this.ollamaStatus = `<span class="text-base-content/50">${window.t('settings.status.connecting')}</span>`;

            try {
                const resp = await fetch(`/api/ollama/models?url=${encodeURIComponent(url)}`);
                const result = await resp.json();

                if (result.success && result.models && result.models.length > 0) {
                    this.ollamaModels = result.models;
                    this.form.ollamaModel = this.ollamaModels.includes(this.form.ollamaModel)
                        ? this.form.ollamaModel
                        : result.models[0];

                    this.ollamaStatus = `<span class="text-success"><i class="bi bi-check-circle"></i> ${window.t('settings.status.connected_n_models', {count: result.models.length})}</span>`;
                } else {
                    this.ollamaStatus = `<span class="text-error"><i class="bi bi-x-circle"></i> ${result.error || window.t('settings.status.no_models')}</span>`;
                    this.ollamaModels = [];
                }
            } catch (e) {
                this.ollamaStatus = `<span class="text-error"><i class="bi bi-x-circle"></i> ${e.message}</span>`;
                this.ollamaModels = [];
            } finally {
                this.testOllamaLoading = false;
            }
        },

        async testModel() {
            const url = this.form.ollamaUrl.trim();
            const model = this.form.ollamaModel;

            if (!url || !model) {
                this.modelStatus = `<span class="text-error">${window.t('settings.status.select_model')}</span>`;
                return;
            }

            this.testModelLoading = true;
            this.modelStatus = `<span class="text-base-content/50">${window.t('settings.status.testing')}</span>`;

            try {
                const resp = await fetch('/api/ollama/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url, model })
                });
                const result = await resp.json();

                if (result.success) {
                    this.modelStatus = `<span class="text-success"><i class="bi bi-check-circle"></i> ${result.result}</span>`;
                } else {
                    this.modelStatus = `<span class="text-error"><i class="bi bi-x-circle"></i> ${result.error}</span>`;
                }
            } catch (e) {
                this.modelStatus = `<span class="text-error"><i class="bi bi-x-circle"></i> ${e.message}</span>`;
            } finally {
                this.testModelLoading = false;
            }
        },

        async testGeminiConnection() {
            const apiKey = this.form.geminiApiKey;

            if (!apiKey) {
                this.geminiStatus = `<span class="text-error"><i class="bi bi-x-circle"></i> ${window.t('settings.status.enter_api_key')}</span>`;
                return;
            }

            this.testGeminiLoading = true;
            this.geminiStatus = `<span class="text-base-content/50">${window.t('settings.status.connecting')}</span>`;

            try {
                const response = await fetch('/api/gemini/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ api_key: apiKey })
                });

                const data = await response.json();

                if (data.success) {
                    this.geminiStatus = `<span class="text-success"><i class="bi bi-check-circle"></i> ${window.t('settings.status.connected_n_models', {count: data.count})}</span>`;
                    this.geminiModels = data.models;

                    // 如果當前 model 不在 allowlist，自動選第一個
                    const modelNames = data.models.map(m => m.name);
                    if (data.models.length > 0 && !modelNames.includes(this.form.geminiModel)) {
                        this.form.geminiModel = data.models[0].name;
                        // auto-fallback 不算用戶修改，同步快照避免 isDirty 誤判
                        if (this.savedState) this.savedState.geminiModel = this.form.geminiModel;
                    }
                } else {
                    this.geminiStatus = `<span class="text-error"><i class="bi bi-x-circle"></i> ${data.error || window.t('settings.status.connect_failed')}</span>`;
                    this.geminiModels = [];
                    this.geminiModelStatus = '';
                }
            } catch (error) {
                this.geminiStatus = `<span class="text-error"><i class="bi bi-x-circle"></i> ${error.message}</span>`;
            } finally {
                this.testGeminiLoading = false;
            }
        },

        async testGeminiTranslation() {
            const apiKey = this.form.geminiApiKey;
            const model = this.form.geminiModel;

            if (!apiKey) {
                this.geminiModelStatus = `<span class="text-error"><i class="bi bi-x-circle"></i> ${window.t('settings.status.enter_api_key_first')}</span>`;
                return;
            }

            if (!model) {
                this.geminiModelStatus = `<span class="text-error"><i class="bi bi-x-circle"></i> ${window.t('settings.status.select_model')}</span>`;
                return;
            }

            this.testGeminiTranslateLoading = true;
            this.geminiModelStatus = `<span class="text-base-content/50">${window.t('settings.status.testing_translation')}</span>`;

            try {
                const response = await fetch('/api/gemini/test-translate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        api_key: apiKey,
                        model: model
                    })
                });

                const data = await response.json();

                if (data.success) {
                    this.geminiModelStatus = `<span class="text-success"><i class="bi bi-check-circle-fill"></i> ${window.t('settings.status.translation_success', {translation: data.translation})}</span>`;
                } else {
                    this.geminiModelStatus = `<span class="text-error"><i class="bi bi-exclamation-triangle-fill"></i> ${data.error}</span>`;
                }
            } catch (error) {
                this.geminiModelStatus = `<span class="text-error"><i class="bi bi-x-circle"></i> ${window.t('settings.status.test_failed', {msg: error.message})}</span>`;
            } finally {
                this.testGeminiTranslateLoading = false;
            }
        },

        async fetchOpenAIModels({ source = 'manual' } = {}) {
            const baseUrl = this.form.openaiBaseUrl.trim();

            if (!baseUrl) {
                this.openaiStatus = `<span class="text-error"><i class="bi bi-x-circle"></i> ${window.t('settings.status.enter_url')}</span>`;
                return;
            }

            this.fetchingOpenaiModels = true;
            this.openaiStatus = `<span class="text-base-content/50">${window.t('settings.status.connecting')}</span>`;

            try {
                const response = await fetch('/api/openai/models', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        base_url: baseUrl,
                        api_key: this.form.openaiApiKey
                    })
                });

                const data = await response.json();

                if (data.success && data.models && data.models.length > 0) {
                    this.openaiModels = data.models;
                    this.openaiStatus = `<span class="text-success"><i class="bi bi-check-circle"></i> ${window.t('settings.status.connected_n_models', {count: data.models.length})}</span>`;

                    // model 不在清單中 → 切換到自訂模式（保留用戶的 custom model）
                    if (!this.openaiModels.includes(this.form.openaiModel)) {
                        if (this.form.openaiModel) {
                            if (source === 'manual') {
                                // 手動 Fetch：model 不在清單 → 切換 custom 模式（維持現有行為）
                                this.openaiUseCustomModel = true;
                            }
                            // source === 'auto'：不動 openaiUseCustomModel，保持 loadConfig 從 config 設的值
                        } else {
                            this.form.openaiModel = this.openaiModels[0];
                            // auto-assign 不算用戶修改，同步快照避免 isDirty 誤判
                            if (this.savedState) this.savedState.openaiModel = this.form.openaiModel;
                        }
                    }
                } else {
                    // 不清空 openaiModels — 保留舊清單
                    const errorKey = `settings.status.openai_${data.error || 'connection_failed'}`;
                    this.openaiStatus = `<span class="text-warning"><i class="bi bi-exclamation-circle"></i> ${window.t(errorKey)}</span>`;
                }
            } catch (error) {
                // 不清空 openaiModels — 保留舊清單
                this.openaiStatus = `<span class="text-error"><i class="bi bi-x-circle"></i> ${window.t('settings.status.openai_connection_failed')}</span>`;
            } finally {
                this.fetchingOpenaiModels = false;
            }
        },

        async testOpenAITranslation() {
            const baseUrl = this.form.openaiBaseUrl.trim();
            const model = this.form.openaiModel.trim();

            if (!baseUrl) {
                this.openaiModelStatus = `<span class="text-error"><i class="bi bi-x-circle"></i> ${window.t('settings.status.enter_url')}</span>`;
                return;
            }

            if (!model) {
                this.openaiModelStatus = `<span class="text-error"><i class="bi bi-x-circle"></i> ${window.t('settings.status.select_model')}</span>`;
                return;
            }

            this.testingOpenaiTranslate = true;
            this.openaiModelStatus = `<span class="text-base-content/50">${window.t('settings.status.testing_translation')}</span>`;

            try {
                const response = await fetch('/api/openai/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        base_url: baseUrl,
                        api_key: this.form.openaiApiKey,
                        model: model
                    })
                });

                const data = await response.json();

                if (data.success) {
                    const escapeHtml = (text) => {
                        const div = document.createElement('div');
                        div.textContent = text;
                        return div.innerHTML;
                    };
                    if (data.translation === 'ja_skip') {
                        this.openaiModelStatus = `<span class="text-info"><i class="bi bi-info-circle"></i> ${window.t('settings.status.ja_skip')}</span>`;
                    } else {
                        this.openaiModelStatus = `<span class="text-success"><i class="bi bi-check-circle-fill"></i> ${escapeHtml(data.translation)}</span>`;
                    }
                } else {
                    const errorKey = `settings.status.openai_${data.error || 'translate_failed'}`;
                    this.openaiModelStatus = `<span class="text-error"><i class="bi bi-exclamation-triangle-fill"></i> ${window.t(errorKey)}</span>`;
                }
            } catch (error) {
                this.openaiModelStatus = `<span class="text-error"><i class="bi bi-x-circle"></i> ${window.t('settings.status.openai_translate_failed')}</span>`;
            } finally {
                this.testingOpenaiTranslate = false;
            }
        },
    };
}
