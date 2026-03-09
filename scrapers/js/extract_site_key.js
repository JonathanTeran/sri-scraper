() => {
    const iframe = document.querySelector(
        'iframe[src*="recaptcha/enterprise"], '
        + 'iframe[src*="recaptcha"]'
    );
    if (iframe) {
        const match = iframe.src.match(/[?&]k=([^&]+)/);
        return match ? match[1] : null;
    }
    const widget = document.querySelector('.g-recaptcha');
    if (widget) {
        return widget.getAttribute('data-sitekey');
    }
    return null;
}
