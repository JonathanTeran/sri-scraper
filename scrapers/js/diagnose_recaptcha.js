() => {
    const result = {
        siteKey: null,
        siteKeySource: null,
        executeRecaptchaExists: typeof window.executeRecaptcha === 'function',
        executeRecaptchaSource: null,
        extractedAction: null,
        rcBuscarExists: typeof window.rcBuscar === 'function',
        rcBuscarSource: null,
        onSubmitExists: typeof window.onSubmit === 'function',
        onSubmitSource: null,
        grecaptchaExists: typeof grecaptcha !== 'undefined',
        grecaptchaEnterpriseExists: false,
        iframeCount: 0,
        iframeSrcs: [],
        recaptchaTextareas: 0,
        buttonId: null,
        buttonOnclick: null,
        widgetIds: [],
        grecaptchaCfgKeys: [],
    };

    // Check grecaptcha.enterprise
    if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise) {
        result.grecaptchaEnterpriseExists = true;
    }

    // Extract site key from iframes
    const iframes = document.querySelectorAll(
        'iframe[src*="recaptcha"]'
    );
    result.iframeCount = iframes.length;
    for (const iframe of iframes) {
        const src = iframe.getAttribute('src') || iframe.src || '';
        result.iframeSrcs.push(src.slice(0, 200));
        const match = src.match(/[?&]k=([^&]+)/);
        if (match && !result.siteKey) {
            result.siteKey = match[1];
            result.siteKeySource = 'iframe';
        }
    }

    // From data-sitekey
    const widgets = document.querySelectorAll('[data-sitekey], .g-recaptcha');
    for (const w of widgets) {
        const sk = w.getAttribute('data-sitekey');
        if (sk && !result.siteKey) {
            result.siteKey = sk;
            result.siteKeySource = 'data-sitekey';
        }
    }

    // From ___grecaptcha_cfg
    if (window.___grecaptcha_cfg) {
        result.grecaptchaCfgKeys = Object.keys(window.___grecaptcha_cfg).slice(0, 20);
        const cfg = window.___grecaptcha_cfg;
        // Try to find sitekey in clients
        if (cfg.clients) {
            for (const [clientId, client] of Object.entries(cfg.clients)) {
                result.widgetIds.push(clientId);
                // Deep search for sitekey
                const search = (obj, depth = 0) => {
                    if (depth > 5 || !obj || typeof obj !== 'object') return;
                    for (const [key, val] of Object.entries(obj)) {
                        if (typeof val === 'string' && val.length > 20 && val.length < 60 && /^[A-Za-z0-9_-]+$/.test(val)) {
                            if (key.toLowerCase().includes('sitekey') || key === 'key' || key === 'k') {
                                if (!result.siteKey) {
                                    result.siteKey = val;
                                    result.siteKeySource = 'grecaptcha_cfg.' + key;
                                }
                            }
                        }
                        if (typeof val === 'object') {
                            search(val, depth + 1);
                        }
                    }
                };
                search(client);
            }
        }
    }

    // Extract action from executeRecaptcha source
    if (typeof window.executeRecaptcha === 'function') {
        const src = window.executeRecaptcha.toString();
        result.executeRecaptchaSource = src.slice(0, 500);
        // Look for action patterns
        const actionMatch = src.match(/action\s*[=:]\s*["']([^"']+)["']/);
        if (actionMatch) {
            result.extractedAction = actionMatch[1];
        }
        // Also look for the parameter name used
        const paramMatch = src.match(/function\s*\(?\s*(\w+)/);
        if (paramMatch) {
            result.extractedAction = result.extractedAction || 'parameter:' + paramMatch[1];
        }
    }

    // Extract rcBuscar source
    if (typeof window.rcBuscar === 'function') {
        result.rcBuscarSource = window.rcBuscar.toString().slice(0, 500);
    }

    // Extract onSubmit source
    if (typeof window.onSubmit === 'function') {
        result.onSubmitSource = window.onSubmit.toString().slice(0, 500);
    }

    // Check button
    const btn = document.getElementById('frmPrincipal:btnBuscar')
        || document.querySelector('[id="frmPrincipal:btnBuscar"]');
    if (btn) {
        result.buttonId = btn.id;
        result.buttonOnclick = btn.getAttribute('onclick') || (btn.onclick ? btn.onclick.toString().slice(0, 300) : null);
    }

    // Count recaptcha textareas
    result.recaptchaTextareas = document.querySelectorAll(
        '[name="g-recaptcha-response"]'
    ).length;

    // Check all script tags for action references
    const scripts = document.querySelectorAll('script');
    const actionPatterns = [];
    for (const script of scripts) {
        const text = script.textContent || '';
        if (text.includes('recaptcha') || text.includes('executeRecaptcha') || text.includes('rcBuscar')) {
            const matches = text.match(/action\s*[=:]\s*["']([^"']+)["']/g);
            if (matches) {
                actionPatterns.push(...matches.map(m => m.slice(0, 80)));
            }
            // Also look for grecaptcha.enterprise.execute calls
            const execMatch = text.match(/grecaptcha\.enterprise\.execute\s*\([^)]+\)/g);
            if (execMatch) {
                actionPatterns.push(...execMatch.map(m => m.slice(0, 200)));
            }
        }
    }
    result.actionPatternsInScripts = actionPatterns.slice(0, 10);

    return result;
}
