async ({ token, source, action }) => {
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
            textareas: Array.from(tas).map((t, i) => ({
                index: i,
                len: t.value.length,
                form: (t.closest('form') || {}).id || 'none',
            })),
        };
    };

    return await new Promise(async (resolve) => {
        const origRcBuscar = window.rcBuscar;
        const origExecuteRecaptcha = window.executeRecaptcha;
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

        const cleanup = () => {
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
            resolve({ ...collect(), ...extra });
        };

        const submit = () => {
            if (typeof window.executeRecaptcha === 'function') {
                window.executeRecaptcha(action);
                return 'executeRecaptcha';
            }
            if (typeof window.rcBuscar === 'function') {
                window.rcBuscar();
                return 'rcBuscar';
            }
            if (typeof window.onSubmit === 'function') {
                window.onSubmit();
                return 'onSubmit';
            }
            return '';
        };

        window.rcBuscar = function() {
            try {
                if (typeof origRcBuscar === 'function') {
                    origRcBuscar.apply(this, arguments);
                }
            } catch (err) {
                finish({ error: 'rcBuscar:' + err.message });
                return;
            }
            setTimeout(() => {
                finish({ submitFlow: 'rcBuscar' });
            }, 10000);
        };

        timeoutId = setTimeout(() => {
            finish({ error: 'timeout_30s' });
        }, 30000);

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
                    setToken(finalToken);
                    if (typeof origExecuteRecaptcha === 'function') {
                        return origExecuteRecaptcha.apply(this, arguments);
                    }
                    if (typeof window.onSubmit === 'function') {
                        return window.onSubmit();
                    }
                    if (typeof window.rcBuscar === 'function') {
                        return window.rcBuscar();
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
