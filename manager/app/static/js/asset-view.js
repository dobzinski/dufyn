/* Read-only asset details follow the certificate View dialog. */
function attachAssetView(page) {
    const modal = document.getElementById("asset-view-modal");
    page.menu.querySelector('[data-action="view"]').addEventListener("click", () => {
        if (page.deleting) return;
        const record = page.selectedRecord();
        page.closeActions(true);
        if (!record) return;
        const details = {...record};
        for (const key of ["first_seen", "last_seen", "last_seen_active"]) {
            details[key] = details[key] ? formatDate(details[key]) : null;
        }
        for (const field of modal.querySelectorAll("[data-detail]")) {
            const key = field.dataset.detail;
            const value = details[key];
            field.replaceChildren();
            if (key === "status") {
                const badge = document.createElement("span");
                badge.className = "status-badge " + (["active", "inactive", "unknown"].includes(value) ? value : "unknown");
                badge.textContent = value || "\u2014";
                field.append(badge);
            } else if (key === "discovered_by_icmp") {
                field.textContent = value == null ? "\u2014" : value ? "Yes" : "No";
            } else {
                field.textContent = value == null || value === "" ? "\u2014" : String(value);
            }
        }
        modal.showModal();
    });
}
