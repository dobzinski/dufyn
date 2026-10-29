class TargetPage extends RecordPage {
    syncFields() {
        super.syncFields();
        const status = this.form.querySelector('[data-target-status]');
        status.hidden = this.recordId === null;
        status.disabled = status.hidden;
        if (status.hidden) this.form.elements.namedItem('status').value = 'U';
    }
}

const targetsPage = new TargetPage(document.getElementById("target-form"), {
    endpoint: "/api/targets", table: "#targets-table", message: "#targets-message", retry: "#targets-retry",
    sortColumn: "fqdn",
    newers: {field: "created_at"},
    selects: [{name: "site_id", endpoint: "/api/sites", placeholder: "Select ...", emptyMessage: "Create a site before adding targets."}],
    columns: [
        { key: "site_name", label: "Site" },
        { key: "fqdn", label: "FQDN" },
        { key: "ports_count", label: "Ports", type: "number" },
        { key: "latency_ms", label: "Latency", type: "number", render: r => r.latency_ms == null ? "\u2014" : escapeHtml(r.latency_ms) + " ms" },
        { key: "last_seen", label: "Last Seen", type: "date", render: (record) => record.last_seen ? formatDate(record.last_seen) : "\u2014" },
        { key: "created_at", label: "Created", type: "date", render: (record) => formatDate(record.created_at) },
        { key: "status", label: "Status", render: (r) => `<span class="status-badge ${r.status === "A" ? "active" : r.status === "I" ? "inactive" : "unknown"}">${r.status === "A" ? "Active" : r.status === "I" ? "Inactive" : "Unknown"}</span>` },
    ],
});

new PortSelectionModal(targetsPage, "targets");
