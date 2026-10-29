// Shared request, result, validation and copy behavior for stateless Tools pages.
(() => {
    const form = document.getElementById("tool-form");
    const result = form.querySelector("[data-tool-result]");
    const submit = form.querySelector("[data-tool-submit]");
    const copy = form.querySelector("[data-tool-copy]");
    const message = form.querySelector("[data-tool-message]");
    const stats = form.querySelector("[data-texts-stats]");
    const useResult = form.querySelector("[data-texts-use]");
    let revision = 0;
    function clear() {
        revision++;
        result.value = "";
        window.ToolResults?.render(result);
        copy.disabled = true;
        if (stats) stats.textContent = "";
        if (useResult) useResult.disabled = true;
        showMessage(message);
    }
    form.addEventListener("input", event => {
        if (!event.target.name) return;
        event.target.setCustomValidity("");
        clear();
    });
    function syncTimeMode() {
        if (form.dataset.tool !== "time") return;
        const direction = form.elements.namedItem("conversion").value;
        const between = direction === "between_zones";
        const unit = form.elements.namedItem("unit");
        const source = form.elements.namedItem("source_time_zone");
        form.querySelector("[data-time-unit]").hidden = between;
        unit.disabled = between;
        unit.required = !between;
        form.querySelector("[data-time-source]").hidden = !between;
        source.disabled = !between;
        source.required = between;
        document.getElementById("time-zone-label").textContent = between ? "To time zone" : "Time zone";
        const date = direction === "to_timestamp";
        document.getElementById("time-from").placeholder = between ? "2026-10-26T10:30:00" : date ? "2026-10-26T10:30:00-03:00" : "1793021400";
        document.getElementById("time-hint").textContent = between
            ? "Enter the local ISO 8601 date in the From time zone. Include its UTC offset when the local time occurs twice during a clock change."
            : date ? "Use an ISO 8601 date with Z or an explicit UTC offset, for example 2026-10-26T10:30:00-03:00."
            : direction === "to_date" ? "Enter a Unix timestamp in the selected unit (seconds or milliseconds)."
            : "Select a conversion, then complete the required fields.";
    }
    function syncTextsMode() {
        if (form.dataset.tool !== "texts") return;
        const format = form.elements.namedItem("format").value;
        const action = form.elements.namedItem("action").value;
        const labels = {validate: "Validate", format: "Format", minify: "Minify", convert: "Convert"};
        submit.textContent = labels[action] || "Process";
        const indentation = form.querySelector("[data-texts-indentation]");
        indentation.hidden = !["format", "convert"].includes(action);
        indentation.disabled = indentation.hidden;
        const sorting = form.querySelector("[data-texts-sort]");
        sorting.hidden = !action || action === "validate";
        sorting.disabled = sorting.hidden;
        form.querySelector("[data-texts-hint]").textContent = !format || !action ? "Select a format and action."
            : action === "convert" ? (format === "json" ? "JSON to YAML. Values and key order are preserved by default." : "YAML to JSON. Types without a direct JSON equivalent are rejected.")
            : action === "minify" ? (format === "json" ? "Remove unnecessary spacing and line breaks to produce one JSON line." : "Remove YAML comments and decorative blank lines. Whitespace and # inside values are preserved.")
            : action === "format" ? "Apply the selected indentation. YAML comments and quoted values are preserved."
            : "Check syntax and duplicate keys. This does not validate application-specific settings.";
    }
    form.addEventListener("change", event => {
        if (!event.target.name) return;
        clear();
        if (event.target.name === "conversion") syncTimeMode();
        if (["format", "action"].includes(event.target.name)) syncTextsMode();
    });
    syncTimeMode();
    syncTextsMode();
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (submit.disabled) return;
        for (const field of form.querySelectorAll("input[required], textarea[required]")) {
            field.setCustomValidity(field.value.trim() ? "" : "Complete this field.");
        }
        if (!form.reportValidity()) return;
        clear();
        const current = revision;
        const data = Object.fromEntries(new FormData(form));
        if (form.dataset.tool === "texts") {
            if (data.indentation) data.indentation = Number(data.indentation);
            data.sort_keys = !form.querySelector("[data-texts-sort]").disabled && form.elements.namedItem("sort_keys").checked;
        }
        submit.disabled = true;
        showMessage(message, "Processing...", "info");
        try {
            const response = await fetch(form.dataset.endpoint, {
                method: "POST", headers: {"Content-Type": "application/json"},
                cache: "no-store", body: JSON.stringify(data),
            });
            if (current !== revision) return;
            let body = {};
            try { body = await response.json(); } catch { /* A proxy may return HTML. */ }
            if (!response.ok && response.status === 422 && form.dataset.tool === "texts" && data.action === "validate" && typeof body.detail === "string") {
                result.value = body.detail;
                window.ToolResults?.render(result);
                copy.disabled = !result.value;
                showMessage(message, "Validation failed.", "error");
                return;
            }
            if (!response.ok) throw new Error(response.status === 401 ? "Session expired. Sign in again." : typeof body.detail === "string" ? body.detail : "Unable to process. Please retry.");
            result.value = body.text;
            window.ToolResults?.render(result);
            copy.disabled = !result.value;
            if (stats && body.stats) {
                const sizes = body.stats;
                stats.textContent = `Input: ${sizes.input_bytes} bytes`;
                if (sizes.output_bytes !== undefined) {
                    stats.textContent += ` | Output: ${sizes.output_bytes} bytes | ${sizes.reduction_percent >= 0 ? "Reduction" : "Increase"}: ${Math.abs(sizes.reduction_percent)}%`;
                }
            }
            if (useResult) {
                useResult.disabled = data.action === "validate" || !result.value;
            }
            showMessage(message, body.message || form.dataset.success, "success");
        } catch (error) {
            if (current === revision) showMessage(message, error.message, "error");
        } finally { submit.disabled = false; }
    });
    copy.addEventListener("click", async () => {
        if (!result.value) return;
        copy.disabled = true;
        showMessage(message);
        try {
            if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(result.value);
            else {
                window.ToolResults?.showPlain(result); result.focus();
                result.select();
                if (!document.execCommand("copy")) throw new Error("Clipboard unavailable");
            }
            showMessage(message, "Result copied.", "success");
        } catch {
            window.ToolResults?.showPlain(result); result.focus(); result.select();
            showMessage(message, "Select the result and copy it manually with Ctrl+C or Cmd+C.", "info");
        } finally { copy.disabled = !result.value; }
    });
    useResult?.addEventListener("click", () => {
        if (useResult.disabled) return;
        const value = result.value;
        if (new Blob([value]).size > 262144) {
            showMessage(message, "Result exceeds the 256 KiB input limit.", "error");
            return;
        }
        form.elements.namedItem("text").value = value;
        form.elements.namedItem("format").value = form.elements.namedItem("format").value === "json" ? "yaml" : "json";
        form.elements.namedItem("format").dispatchEvent(new Event("change", {bubbles: true}));
        form.elements.namedItem("text").dispatchEvent(new Event("input", {bubbles: true}));
        form.elements.namedItem("text").focus();
    });
    window.addEventListener("pagehide", () => {
        for (const field of form.querySelectorAll("input[name], textarea[name]")) field.value = "";
        clear();
    });
})();

