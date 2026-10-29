/* Read-only scanner details use the same dialog and detail-list styles as certificates. */
function attachScannerView(page) {
    const modal = document.getElementById("scanner-view-modal");
    const configuration = document.getElementById("scanner-view-config");
    const message = document.getElementById("scanner-view-message");
    const fields = modal.querySelectorAll("[data-scanner-detail]");
    let revision = 0;
    modal.addEventListener("close", () => {
        revision++;
        for (const field of fields) field.replaceChildren();
        configuration.replaceChildren();
        showMessage(message);
        page.deleting = false;
    });
    page.menu.querySelector('[data-action="view"]').addEventListener("click", async () => {
        if (page.deleting) return;
        const record = page.selectedRecord();
        page.closeActions(true);
        if (!record) return;
        page.deleting = true;
        const current = ++revision;
        for (const field of fields) field.textContent = "\u2014";
        configuration.replaceChildren();
        showMessage(message, "Loading scanner details...", "info");
        modal.showModal();
        try {
            const response = await fetch(`/api/scanners/${record.id}`, {cache: "no-store"});
            if (!response.ok) throw new Error(response.status === 404 ? "Scanner no longer exists." : response.status === 401 ? "Session expired. Sign in again." : "Unable to load scanner details. Close and try again.");
            const details = await response.json();
            if (current !== revision) return;
            for (const field of fields) {
                const key = field.dataset.scannerDetail;
                const value = details[key];
                if (key === "enabled") {
                    const badge = document.createElement("span");
                    badge.className = "status-badge " + (value === "Y" ? "yes" : "no");
                    badge.textContent = value === "Y" ? "Yes" : "No";
                    field.replaceChildren(badge);
                } else {
                    field.textContent = value == null || value === "" ? "\u2014" : (key.endsWith("_at") || key === "last_seen") ? formatDate(value) : String(value);
                }
            }
            const groups = new Map();
            for (const setting of details.configuration || []) {
                const parts = setting.label.split(" / ");
                const group = parts.length > 1 ? parts.shift() : "General";
                if (!groups.has(group)) {
                    const section = document.createElement("section");
                    const heading = document.createElement("h4");
                    const list = document.createElement("dl");
                    heading.textContent = group;
                    list.className = "network-map-details";
                    section.append(heading, list);
                    configuration.append(section);
                    groups.set(group, list);
                }
                const label = document.createElement("dt");
                const value = document.createElement("dd");
                label.textContent = parts.join(" / ");
                value.textContent = typeof setting.value === "boolean" ? (setting.value ? "Yes" : "No")
                    : Array.isArray(setting.value) ? setting.value.join(", ") || "\u2014" : String(setting.value);
                groups.get(group).append(label, value);
            }
            showMessage(message, details.configuration === null ? "Configuration has not been synchronized yet."
                : !details.configuration?.length ? "No displayable configuration settings." : "", "info");
        } catch (error) {
            if (current === revision) showMessage(message, error.message, "error");
        }
    });
}
