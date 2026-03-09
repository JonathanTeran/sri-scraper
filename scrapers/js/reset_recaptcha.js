() => {
    try {
        if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise) {
            grecaptcha.enterprise.reset();
        }
    } catch (e) {}
    document.querySelectorAll('[name="g-recaptcha-response"]')
        .forEach((el) => { el.value = ''; });
    if (window.__captchaToken) {
        window.__captchaToken = '';
    }
}
