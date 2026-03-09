() => {
    const keyPattern = /\b[0-9A-Za-z_-]{20,}\b/;

    const normalizeCandidate = (value) => {
        if (typeof value !== 'string') {
            return null;
        }
        const trimmed = value.trim();
        return keyPattern.test(trimmed) ? trimmed : null;
    };

    const extractKeyFromUrl = (url) => {
        if (typeof url !== 'string' || !url) {
            return null;
        }
        try {
            const parsed = new URL(url, window.location.href);
            return normalizeCandidate(parsed.searchParams.get('k'));
        } catch (_error) {
            const match = url.match(/[?&]k=([^&]+)/);
            return normalizeCandidate(match ? decodeURIComponent(match[1]) : null);
        }
    };

    const findInObject = (value, visited = new WeakSet()) => {
        if (value === null || value === undefined) {
            return null;
        }
        if (typeof value === 'string') {
            return normalizeCandidate(value);
        }
        if (typeof value !== 'object') {
            return null;
        }
        if (visited.has(value)) {
            return null;
        }
        visited.add(value);

        for (const [key, nested] of Object.entries(value)) {
            if (key.toLowerCase().includes('sitekey')) {
                const sitekey = normalizeCandidate(nested);
                if (sitekey) {
                    return sitekey;
                }
            }
            const nestedKey = findInObject(nested, visited);
            if (nestedKey) {
                return nestedKey;
            }
        }
        return null;
    };

    const iframes = document.querySelectorAll(
        'iframe[src*="recaptcha/enterprise"], iframe[src*="recaptcha"]'
    );
    for (const iframe of iframes) {
        const sitekey = extractKeyFromUrl(iframe.getAttribute('src') || iframe.src);
        if (sitekey) {
            return sitekey;
        }
    }

    const widgets = document.querySelectorAll('[data-sitekey], .g-recaptcha');
    for (const widget of widgets) {
        const sitekey = normalizeCandidate(widget.getAttribute('data-sitekey'));
        if (sitekey) {
            return sitekey;
        }
    }

    const cfgSitekey = findInObject(window.___grecaptcha_cfg);
    if (cfgSitekey) {
        return cfgSitekey;
    }

    const executeRecaptchaFn = window.executeRecaptcha;
    if (typeof executeRecaptchaFn === 'function') {
        const sitekey = normalizeCandidate(executeRecaptchaFn.toString().match(keyPattern)?.[0]);
        if (sitekey) {
            return sitekey;
        }
    }

    const scripts = document.querySelectorAll('script');
    for (const script of scripts) {
        const content = script.textContent || '';
        const urlKey = extractKeyFromUrl(content);
        if (urlKey) {
            return urlKey;
        }
        const configMatch = content.match(/sitekey["'\s:=]+([0-9A-Za-z_-]{20,})/i);
        if (configMatch) {
            const sitekey = normalizeCandidate(configMatch[1]);
            if (sitekey) {
                return sitekey;
            }
        }
        const rawMatch = content.match(keyPattern);
        if (rawMatch) {
            return rawMatch[0];
        }
    }

    return null;
}
