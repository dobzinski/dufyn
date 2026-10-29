(() => {
    const form = document.getElementById("backup-form");
    const option = document.getElementById("backup-option");
    const submit = document.getElementById("backup-submit");
    const message = document.getElementById("backup-message");
    const hint = document.getElementById("backup-hint");
    const descriptions = {
        all: "Configuration files, synchronized scanner settings and the PostgreSQL database.",
        configuration: "Configuration files, environment credentials and the latest synchronized scanner settings.",
        data: "Complete PostgreSQL schema and data. Configuration files and master keys are not included.",
    };
    option.addEventListener("change", () => { hint.textContent = descriptions[option.value] || "Select the backup content."; });
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (submit.disabled || !form.reportValidity()) return;
        submit.disabled = true;
        submit.textContent = "Generating backup...";
        showMessage(message, "Generating backup. Keep this page open until the download starts.", "info");
        let link;
        let url;
        try {
            const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
            const response = await fetch("/api/backup", {
                method: "POST", headers: {"Content-Type": "application/json"}, cache: "no-store",
                body: JSON.stringify({option: option.value, timezone}),
            });
            if (!response.ok) {
                let body = {};
                try { body = await response.json(); } catch { /* A proxy can return HTML. */ }
                throw new Error(response.status === 401 ? "Session expired. Sign in again."
                    : typeof body.detail === "string" ? body.detail : "Unable to generate the backup. Please retry.");
            }
            if (!response.headers.get("content-type")?.includes("application/zip")) throw new Error("The server did not return a backup archive. Please retry.");
            const disposition = response.headers.get("content-disposition") || "";
            const filename = disposition.match(/dufyn-backup-(?:all|configuration|data)-\d{14}\.zip/)?.[0];
            if (!filename) throw new Error("The server did not return a valid backup filename.");
            const blob = await response.blob();
            url = URL.createObjectURL(blob);
            link = document.createElement("a");
            link.href = url;
            link.download = filename;
            document.body.append(link);
            link.click();
            const missing = Number(response.headers.get("x-backup-missing-scanners") || 0);
            showMessage(message, missing ? `Download started. ${missing} scanner(s) have no synchronized configuration; see the archive manifest.` : "Backup download started.", missing ? "info" : "success");
        } catch (error) {
            showMessage(message, error.message, "error");
        } finally {
            link?.remove();
            if (url) setTimeout(() => URL.revokeObjectURL(url), 60000);
            submit.disabled = false;
            submit.textContent = "Download";
        }
    });
})();
