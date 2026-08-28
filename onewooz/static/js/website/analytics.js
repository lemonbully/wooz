(function () {
    const configNode = document.getElementById('analytics-config');
    if (!configNode) {
        return;
    }
    let config = {};
    try {
        config = JSON.parse(configNode.textContent || '{}');
    } catch (err) {
        console.warn('Analytics config parse failed', err);
        return;
    }
    if (!config.track_url || !config.session_id) {
        return;
    }

    const basePayload = {
        session_id: config.session_id,
        visitor_id: config.visitor_id,
        website_id: config.website_id,
        page_type: config.page_type || null,
        page_meta: config.page_meta || {},
    };

    const sendEvent = (eventType, payload) => {
        if (!eventType) return;
        const body = JSON.stringify({
            ...basePayload,
            event_type: eventType,
            payload: payload || {},
            timestamp: new Date().toISOString(),
        });
        if (navigator.sendBeacon) {
            const blob = new Blob([body], { type: 'application/json' });
            navigator.sendBeacon(config.track_url, blob);
        } else {
            fetch(config.track_url, {
                method: 'POST',
                body,
                headers: {
                    'Content-Type': 'application/json',
                },
                credentials: 'include',
                keepalive: true,
            }).catch(() => {});
        }
    };

    let maxScroll = 0;
    const trackScroll = () => {
        const doc = document.documentElement;
        const body = document.body;
        const scrollTop = doc.scrollTop || body.scrollTop;
        const scrollHeight = (doc.scrollHeight || body.scrollHeight) - doc.clientHeight;
        if (scrollHeight <= 0) {
            maxScroll = 100;
            return;
        }
        const current = Math.round((scrollTop / scrollHeight) * 100);
        if (current > maxScroll) {
            maxScroll = current;
        }
    };
    window.addEventListener('scroll', () => {
        window.requestAnimationFrame(trackScroll);
    }, { passive: true });

    const navEntry = performance.getEntriesByType && performance.getEntriesByType('navigation')[0];
    let pageLoadMs = navEntry ? navEntry.loadEventEnd : null;
    let fcpMs = null;
    if ('PerformanceObserver' in window) {
        try {
            const po = new PerformanceObserver((list) => {
                const entries = list.getEntries();
                for (const entry of entries) {
                    if (entry.name === 'first-contentful-paint') {
                        fcpMs = entry.startTime;
                        po.disconnect();
                        break;
                    }
                }
            });
            po.observe({ type: 'paint', buffered: true });
        } catch (err) {
            // no-op
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        sendEvent('page_view', {
            path: window.location.pathname,
            title: document.title,
            referrer: document.referrer,
        });
        if (pageLoadMs !== null || fcpMs !== null) {
            sendEvent('performance', {
                page_load_ms: pageLoadMs,
                fcp_ms: fcpMs,
                page_type: config.page_type || null,
            });
        }
    });

    let sessionStart = performance.now();
    window.addEventListener('beforeunload', () => {
        const durationMs = performance.now() - sessionStart;
        sendEvent('session_summary', {
            duration_ms: Math.round(durationMs),
            max_scroll_percent: maxScroll,
        });
        if ((config.page_type || '').toLowerCase() === 'product') {
            const engagementScore = Math.min(100, Math.round((maxScroll * 0.6) + (Math.min(durationMs / 1000, 180) * 0.2)));
            sendEvent('product_engagement', {
                score: engagementScore,
                max_scroll_percent: maxScroll,
            });
        }
    });
})();

