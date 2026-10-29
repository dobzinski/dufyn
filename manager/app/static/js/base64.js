(() => {
    const form = document.getElementById("base64-form");
    const conversion = document.getElementById("base64-conversion");
    const from = document.getElementById("base64-from");
    const to = document.getElementById("base64-to");
    const submit = document.getElementById("base64-submit");
    const copy = document.getElementById("base64-copy");
    const useResult = document.getElementById("base64-use");
    const message = document.getElementById("base64-message");
    let revision = 0;
    function clear() {
        revision++;
        from.setCustomValidity("");
        to.value = "";
        window.ToolResults?.render(to);
        copy.disabled = true;
        useResult.disabled = true;
        showMessage(message);
    }
    from.addEventListener("input", clear);
    function updateAction() {
        submit.textContent = conversion.value === "decode" ? "Decode" : "Convert";
    }
    conversion.addEventListener("change", () => {
        clear();
        updateAction();
    });
    updateAction();
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (submit.disabled) return;
        from.setCustomValidity(from.value.trim() ? "" : "Enter content to convert.");
        if (!form.reportValidity()) return;
        clear();
        const current = revision;
        submit.disabled = true;
        showMessage(message, "Converting...", "info");
        try {
            const response = await fetch("/api/base64/convert", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                cache: "no-store",
                body: JSON.stringify({conversion: conversion.value, text: from.value}),
            });
            if (current !== revision) return;
            let result = {};
            try { result = await response.json(); } catch { /* A proxy may return HTML. */ }
            if (!response.ok) throw new Error(response.status === 401 ? "Session expired. Sign in again." : typeof result.detail === "string" ? result.detail : "Unable to convert. Please retry.");
            to.value = result.text;
            window.ToolResults?.render(to);
            copy.disabled = !to.value;
            useResult.disabled = !to.value;
            showMessage(message, conversion.value === "decode" ? "Decoding completed." : "Conversion completed.", "success");
        } catch (error) {
            if (current === revision) showMessage(message, error.message, "error");
        } finally { submit.disabled = false; }
    });
    copy.addEventListener("click", async () => {
        if (!to.value) return;
        copy.disabled = true;
        showMessage(message);
        try {
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(to.value);
            } else {
                window.ToolResults?.showPlain(to); to.focus();
                to.select();
                if (!document.execCommand("copy")) throw new Error("Clipboard unavailable");
            }
            showMessage(message, "Result copied.", "success");
        } catch {
            window.ToolResults?.showPlain(to); to.focus();
            to.select();
            showMessage(message, "Select the result and copy it manually with Ctrl+C or Cmd+C.", "info");
        } finally { copy.disabled = !to.value; }
    });
    useResult.addEventListener("click", () => {
        if (useResult.disabled || !to.value) return;
        if (new Blob([to.value]).size > 1048576) {
            showMessage(message, "Result exceeds the 1 MiB input limit.", "error");
            return;
        }
        from.value = to.value;
        conversion.value = conversion.value === "encode" ? "decode" : "encode";
        conversion.dispatchEvent(new Event("change", {bubbles: true}));
        from.dispatchEvent(new Event("input", {bubbles: true}));
        from.focus();
    });
    window.addEventListener("pagehide", () => { from.value = ""; clear(); });
})();

