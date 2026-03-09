async ({
    token,
    source,
    action,
    timeoutMs = 45000,
    pollIntervalMs = 500,
}) => {
    const getSiteKey = () => {
        const iframe = document.querySelector(
            'iframe[src*="recaptcha/enterprise"], '
            + 'iframe[src*="recaptcha"]'
        );
        if (iframe) {
            const match = iframe.src.match(/[?&]k=([^&]+)/);
            return match ? match[1] : null;
        }
        const widget = document.querySelector('.g-recaptcha');
        return widget ? widget.getAttribute('data-sitekey') : null;
    };
    const setToken = (value) => {
        const forms = Array.from(document.querySelectorAll('form'));
        forms.forEach((form) => {
            let el = form.querySelector('[name="g-recaptcha-response"]');
            if (!el) {
                el = document.createElement('textarea');
                el.name = 'g-recaptcha-response';
                el.style.display = 'none';
                form.appendChild(el);
            }
            el.value = value || '';
        });
        document.querySelectorAll('[name="g-recaptcha-response"]')
            .forEach((el) => { el.value = value || ''; });
    };
    const getBuscarButton = () => (
        document.getElementById('frmPrincipal:btnBuscar')
        || document.querySelector('[id="frmPrincipal:btnBuscar"]')
        || Array.from(document.querySelectorAll('button,input,a,span'))
            .find((el) => (el.textContent || el.value || '').trim() === 'Consultar')
    );
    const clickBuscarButton = () => {
        const button = getBuscarButton();
        if (!button) {
            return false;
        }
        if (button.__codexClicked) {
            return true;
        }
        button.__codexClicked = true;
        if (typeof button.focus === 'function') {
            button.focus();
        }
        if (typeof button.click === 'function') {
            button.click();
        } else {
            button.dispatchEvent(new MouseEvent('click', {
                bubbles: true,
                cancelable: true,
                view: window,
            }));
        }
        return true;
    };
    const collect = () => {
        const msgs = document.getElementById('formMessages:messages');
        const panel = document.getElementById(
            'frmPrincipal:panelListaComprobantes'
        );
        const tas = document.querySelectorAll(
            '[name="g-recaptcha-response"]'
        );
        return {
            source,
            messages: msgs ? msgs.innerText.trim() : '',
            panelLen: panel ? panel.innerHTML.length : 0,
            panelHtml: panel ? panel.innerHTML : '',
            hasBuscarButton: !!getBuscarButton(),
            viewStateLen: (
                document.querySelector('[name="javax.faces.ViewState"]') || {}
            ).value?.length || 0,
            textareas: Array.from(tas).map((t, i) => ({
                index: i,
                len: t.value.length,
                form: (t.closest('form') || {}).id || 'none',
            })),
        };
    };
    const parsePartialResponse = (text) => {
        if (typeof text !== 'string' || !text.includes('partial-response')) {
            return null;
        }
        try {
            const parser = new DOMParser();
            const xml = parser.parseFromString(text, 'application/xml');
            const updates = Array.from(xml.getElementsByTagName('update'));
            const payload = {
                partialResponse: true,
                updateIds: updates.map((node) => node.getAttribute('id') || ''),
                panelHtml: '',
                messages: '',
                viewState: '',
            };
            updates.forEach((node) => {
                const nodeId = node.getAttribute('id') || '';
                const value = node.textContent || '';
                if (
                    !payload.panelHtml
                    && (
                        nodeId.includes('panelListaComprobantes')
                        || nodeId === 'javax.faces.ViewRoot'
                    )
                ) {
                    payload.panelHtml = value;
                }
                if (!payload.messages && nodeId.includes('formMessages')) {
                    payload.messages = value.replace(/<[^>]+>/g, ' ').trim();
                }
                if (nodeId === 'javax.faces.ViewState') {
                    payload.viewState = value;
                }
            });
            return payload;
        } catch (_error) {
            return {
                partialResponse: true,
                updateIds: [],
                panelHtml: '',
                messages: '',
                viewState: '',
            };
        }
    };

    return await new Promise(async (resolve) => {
        const origRcBuscar = window.rcBuscar;
        const origExecuteRecaptcha = window.executeRecaptcha;
        const origFetch = window.fetch;
        const xhrOpen = XMLHttpRequest.prototype.open;
        const xhrSend = XMLHttpRequest.prototype.send;
        const origEnterpriseExecute = (
            typeof grecaptcha !== 'undefined'
            && grecaptcha.enterprise
            && typeof grecaptcha.enterprise.execute === 'function'
        ) ? grecaptcha.enterprise.execute : null;
        const origEnterpriseGetResponse = (
            typeof grecaptcha !== 'undefined'
            && grecaptcha.enterprise
            && typeof grecaptcha.enterprise.getResponse === 'function'
        ) ? grecaptcha.enterprise.getResponse : null;
        const origEnterpriseReset = (
            typeof grecaptcha !== 'undefined'
            && grecaptcha.enterprise
            && typeof grecaptcha.enterprise.reset === 'function'
        ) ? grecaptcha.enterprise.reset : null;
        let settled = false;
        let timeoutId = null;
        let pollId = null;
        let observer = null;
        let submitted = false;
        let submitFlow = '';
        let submitCount = 0;
        const network = {
            inFlight: 0,
            requests: 0,
            lastStatus: null,
            lastUrl: '',
            lastResponseAt: 0,
            lastResponseSnippet: '',
            lastError: '',
            partialResponse: false,
            partialUpdateIds: [],
            partialPanelHtml: '',
            partialMessages: '',
        };

        const queueIdle = () => {
            try {
                return !(
                    window.PrimeFaces
                    && PrimeFaces.ajax
                    && PrimeFaces.ajax.Queue
                    && typeof PrimeFaces.ajax.Queue.isEmpty === 'function'
                ) || PrimeFaces.ajax.Queue.isEmpty();
            } catch (_error) {
                return true;
            }
        };

        const mergeCollectedState = (snapshot) => {
            const merged = { ...snapshot };
            if (
                network.partialPanelHtml
                && network.partialPanelHtml.length > merged.panelLen
            ) {
                merged.panelHtml = network.partialPanelHtml;
                merged.panelLen = network.partialPanelHtml.length;
            }
            if (network.partialMessages) {
                merged.messages = network.partialMessages;
            }
            merged.network = {
                inFlight: network.inFlight,
                requests: network.requests,
                lastStatus: network.lastStatus,
                lastUrl: network.lastUrl,
                lastResponseAt: network.lastResponseAt,
                lastResponseSnippet: network.lastResponseSnippet,
                lastError: network.lastError,
                partialResponse: network.partialResponse,
                partialUpdateIds: network.partialUpdateIds,
                queueIdle: queueIdle(),
            };
            const emptyPartialIds = new Set([
                'formMessages:messages',
                'javax.faces.ViewState',
            ]);
            merged.emptyPartialResponse = Boolean(
                merged.panelLen === 0
                && !(merged.messages || '').trim()
                && merged.network.partialResponse
                && merged.network.lastStatus === 200
                && merged.network.partialUpdateIds.length > 0
                && merged.network.partialUpdateIds.every(
                    (id) => emptyPartialIds.has(id)
                )
            );
            return merged;
        };

        const shouldFinish = (snapshot) => {
            const messages = (snapshot.messages || '').toLowerCase();
            return (
                snapshot.panelLen > 50
                || messages.includes('captcha')
                || messages.includes('no se encontraron')
                || messages.includes('ha ocurrido un error')
                || messages.includes('sesión expirada')
                || snapshot.emptyPartialResponse
            );
        };

        const captureResponse = (url, status, text) => {
            network.lastUrl = url || network.lastUrl;
            network.lastStatus = status;
            network.lastResponseAt = Date.now();
            network.lastResponseSnippet = (text || '').slice(0, 500);
            const partial = parsePartialResponse(text || '');
            if (partial) {
                network.partialResponse = true;
                network.partialUpdateIds = partial.updateIds;
                if (partial.panelHtml) {
                    network.partialPanelHtml = partial.panelHtml;
                }
                if (partial.messages) {
                    network.partialMessages = partial.messages;
                }
            }
        };

        const cleanup = () => {
            if (pollId) clearInterval(pollId);
            if (observer) observer.disconnect();
            window.fetch = origFetch;
            XMLHttpRequest.prototype.open = xhrOpen;
            XMLHttpRequest.prototype.send = xhrSend;
            if (typeof origRcBuscar === 'function') {
                window.rcBuscar = origRcBuscar;
            }
            if (typeof origExecuteRecaptcha === 'function') {
                window.executeRecaptcha = origExecuteRecaptcha;
            }
            if (
                origEnterpriseExecute
                && typeof grecaptcha !== 'undefined'
                && grecaptcha.enterprise
            ) {
                grecaptcha.enterprise.execute = origEnterpriseExecute;
            }
            if (
                origEnterpriseGetResponse
                && typeof grecaptcha !== 'undefined'
                && grecaptcha.enterprise
            ) {
                grecaptcha.enterprise.getResponse = origEnterpriseGetResponse;
            }
            if (
                origEnterpriseReset
                && typeof grecaptcha !== 'undefined'
                && grecaptcha.enterprise
            ) {
                grecaptcha.enterprise.reset = origEnterpriseReset;
            }
        };

        const finish = (extra = {}) => {
            if (settled) return;
            settled = true;
            if (timeoutId) clearTimeout(timeoutId);
            cleanup();
            resolve({
                ...mergeCollectedState(collect()),
                submitFlow,
                ...extra,
            });
        };

        const submit = () => {
            if (submitted) {
                return submitFlow || 'already_submitted';
            }
            submitCount += 1;
            if (submitCount > 1) {
                return submitFlow || 'already_submitted';
            }
            if (clickBuscarButton()) {
                submitted = true;
                submitFlow = 'button_click';
                return 'button_click';
            }
            if (typeof window.executeRecaptcha === 'function') {
                submitted = true;
                submitFlow = 'executeRecaptcha';
                window.executeRecaptcha(action);
                return 'executeRecaptcha';
            }
            if (typeof window.rcBuscar === 'function') {
                submitted = true;
                submitFlow = 'rcBuscar';
                window.rcBuscar();
                return 'rcBuscar';
            }
            if (typeof window.onSubmit === 'function') {
                submitted = true;
                submitFlow = 'onSubmit';
                window.onSubmit();
                return 'onSubmit';
            }
            return '';
        };

        const isRelevantBody = (body) => (
            typeof body === 'string'
            && (
                body.includes('frmPrincipal')
                || body.includes('javax.faces.ViewState')
                || body.includes('javax.faces.partial.ajax=true')
            )
        );

        XMLHttpRequest.prototype.open = function(method, url) {
            this.__codexMethod = method;
            this.__codexUrl = url;
            return xhrOpen.apply(this, arguments);
        };
        XMLHttpRequest.prototype.send = function(body) {
            if (
                String(this.__codexMethod || '').toUpperCase() === 'POST'
                && isRelevantBody(body)
            ) {
                network.requests += 1;
                network.inFlight += 1;
                this.addEventListener('loadend', () => {
                    network.inFlight = Math.max(0, network.inFlight - 1);
                    captureResponse(
                        this.responseURL || this.__codexUrl || '',
                        this.status,
                        this.responseText || '',
                    );
                });
            }
            return xhrSend.apply(this, arguments);
        };

        window.fetch = async function(resource, options = {}) {
            const url = typeof resource === 'string'
                ? resource
                : (resource && resource.url) || '';
            const method = String(options.method || 'GET').toUpperCase();
            const body = options.body || '';
            const relevant = method === 'POST' && isRelevantBody(body);
            if (relevant) {
                network.requests += 1;
                network.inFlight += 1;
            }
            try {
                const response = await origFetch.apply(this, arguments);
                if (relevant) {
                    const clone = response.clone();
                    let text = '';
                    try {
                        text = await clone.text();
                    } catch (_error) {
                        text = '';
                    }
                    captureResponse(clone.url || url, clone.status, text);
                }
                return response;
            } finally {
                if (relevant) {
                    network.inFlight = Math.max(0, network.inFlight - 1);
                }
            }
        };

        window.rcBuscar = function() {
            submitted = true;
            submitFlow = submitFlow || 'rcBuscar';
            try {
                if (typeof origRcBuscar === 'function') {
                    origRcBuscar.apply(this, arguments);
                }
            } catch (err) {
                finish({ error: 'rcBuscar:' + err.message });
                return;
            }
        };

        observer = new MutationObserver(() => {
            const snapshot = mergeCollectedState(collect());
            if (shouldFinish(snapshot)) {
                finish();
            }
        });
        observer.observe(document.body, {
            subtree: true,
            childList: true,
            characterData: true,
        });

        pollId = setInterval(() => {
            const snapshot = mergeCollectedState(collect());
            if (shouldFinish(snapshot)) {
                finish();
                return;
            }
            if (
                submitted
                && network.lastResponseAt
                && Date.now() - network.lastResponseAt > 2000
                && network.inFlight === 0
                && queueIdle()
            ) {
                if (network.partialResponse || snapshot.messages || snapshot.panelLen > 0) {
                    finish();
                }
            }
        }, pollIntervalMs);

        timeoutId = setTimeout(() => {
            finish({
                error: network.lastResponseAt ? 'timeout_after_submit' : 'timeout_before_submit',
            });
        }, timeoutMs);

        try {
            let finalToken = token;

            if (finalToken) {
                window.__captchaToken = finalToken;
                setToken(finalToken);
                if (
                    typeof grecaptcha !== 'undefined'
                    && grecaptcha.enterprise
                ) {
                    grecaptcha.enterprise.execute = () =>
                        Promise.resolve(finalToken);
                    grecaptcha.enterprise.getResponse = () =>
                        finalToken;
                    grecaptcha.enterprise.reset = () => {};
                }
                window.executeRecaptcha = function() {
                    submitted = true;
                    submitFlow = submitFlow || 'executeRecaptcha';
                    setToken(finalToken);
                    if (typeof origExecuteRecaptcha === 'function') {
                        return origExecuteRecaptcha.apply(this, arguments);
                    }
                    if (typeof window.rcBuscar === 'function') {
                        return window.rcBuscar();
                    }
                    if (typeof window.onSubmit === 'function') {
                        return window.onSubmit();
                    }
                    if (clickBuscarButton()) {
                        return true;
                    }
                };
            } else if (
                typeof window.executeRecaptcha !== 'function'
                && typeof grecaptcha !== 'undefined'
                && grecaptcha.enterprise
                && typeof grecaptcha.enterprise.execute === 'function'
            ) {
                const siteKey = getSiteKey();
                if (!siteKey) {
                    finish({ error: 'sitekey_no_encontrada' });
                    return;
                }
                finalToken = await grecaptcha.enterprise.execute(
                    siteKey,
                    { action }
                );
                window.__captchaToken = finalToken;
                setToken(finalToken);
                if (
                    typeof grecaptcha !== 'undefined'
                    && grecaptcha.enterprise
                ) {
                    grecaptcha.enterprise.getResponse = () =>
                        finalToken;
                }
            }
            setToken(finalToken);
            const flow = submit();
            if (!flow) {
                finish({
                    error: 'no_submit_flow',
                    tokenLen: finalToken ? finalToken.length : 0,
                });
                return;
            }
        } catch (err) {
            finish({ error: 'controlled_submit:' + err.message });
        }
    });
}
