/* Shared presentation only; each page supplies its own selected record. */
function attachCertificateView(page, source = "managed") {
    const modal = document.getElementById("pki-view-modal");
    page.menu.querySelector('[data-action="view"]').addEventListener("click", () => {
        if (page.deleting) return;
        const record = page.selectedRecord();
        page.closeActions(true);
        if (!record) return;
        const discovered = source === "discovered";
        const names = discovered ? (record.sans || []).map(value => typeof value === "string" ? value : `${value.type}:${value.value}`)
            : (record.alt_names || []).map(name => `${name.type}:${name.value}`);
        const details = {
            ...record.subject, ...record,
            type_label: {root: "Root CA", intermediate: "Intermediate CA", server: "Server"}[record.kind],
            alt_names: names,
        };
        if (discovered) {
            Object.assign(details, {
                common_name: record.subject_cn,
                serial: record.serial_number,
                not_before: record.valid_from,
                not_after: record.valid_until,
                owner: record.target?.fqdn || [record.asset?.ip, record.asset?.name].filter(Boolean).join(" | "),
                port_label: record.port ? `${record.port.port} / ${record.port.protocol}` : "Internal",
            });
        }
        for (const key of ["not_before", "not_after", "created_at", "last_seen"]) {
            details[key] = details[key] ? formatDate(details[key]) : null;
        }
        for (const field of modal.querySelectorAll("[data-detail]")) {
            const key = field.dataset.detail;
            const value = details[key];
            field.replaceChildren();
            if (key === "type_label" || key === "status" || key === "tls_status") {
                const badge = document.createElement("span");
                const status = key === "type_label" ? record.kind : value;
                badge.className = "status-badge " + (["root", "intermediate", "server", "valid", "warning", "critical", "expired", "unknown"].includes(status) ? status : "unknown");
                badge.textContent = value || "\u2014";
                field.append(badge);
            } else if (key === "alt_names" && names.length) {
                names.forEach((name, index) => {
                    if (index) field.append(document.createElement("br"));
                    field.append(document.createTextNode(name));
                });
            } else {
                field.textContent = value == null || value === "" || (Array.isArray(value) && !value.length) ? "\u2014" : String(value);
            }
        }
        modal.showModal();
    });
}
