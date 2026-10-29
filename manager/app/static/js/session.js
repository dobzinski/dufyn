(() => {
    const script = document.querySelector("script[data-session]");
    const timeout = Number(script.dataset.session) * 1000;
    let lastSent = 0;
    let pending = false;
    const login = () => location.replace(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
    async function activity(event) {
        if (!event.isTrusted || pending || Date.now() - lastSent < Math.min(30000, timeout / 3)) return;
        pending = true;
        try {
            const response = await fetch("/auth/activity", {method: "POST", credentials: "same-origin"});
            if (response.status === 401) login();
            if (response.ok) lastSent = Date.now();
        } catch (_) { /* Retry on the next user interaction. */ }
        finally { pending = false; }
    }
    for (const name of ["pointerdown", "pointermove", "keydown", "scroll", "touchstart"]) {
        document.addEventListener(name, activity, {passive: true});
    }
    async function status() {
        try {
            const response = await fetch("/auth/status", {credentials: "same-origin", cache: "no-store"});
            if (response.status === 401) login();
        } catch (_) { /* A network failure does not prove session expiry. */ }
    }
    setInterval(status, Math.max(1000, Math.min(15000, timeout / 3)));
    document.addEventListener("visibilitychange", () => { if (!document.hidden) status(); });
})();
