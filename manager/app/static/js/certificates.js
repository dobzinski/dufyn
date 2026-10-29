const certificatePage = new RecordPage(document.getElementById("certificate-form"), {
    prerequisiteMessage: "Create an asset for internal certificates or a port for external certificates.",
    newers: {field: "first_seen"},
    endpoint: "/api/certificates",
    table: "#certificates-table",
    message: "#certificates-message",
    retry: "#certificates-retry",
    sortColumn: "days_remaining",
    choices: (record) => ({scope: record.asset_id ? "internal" : "external", owner: record.target ? "target" : "asset"}),
    selects: [
        {name: "asset_id", endpoint: "/api/ports/asset-options", placeholder: "Select ...", group: "network_group", groupKey: "network_group_id", required: false},
        {name: "port_id", endpoint: "/api/certificates/port-options", placeholder: "Select ...", group: "network_group", groupKey: "network_group_id", filter: {key: "owner_type", choice: "owner"}, emptyPlaceholder: "No ports registered for this owner type", required: false},
    ],
    columns: [
        { key: "site_name", label: "Site" },
        { key: "owner", label: "Owner", value: (record) => record.target?.fqdn || [record.asset?.ip, record.asset?.name].filter(Boolean).join(" | ") },
        { key: "port", label: "Port", type: "number", value: (record) => record.port?.port ?? null, searchValue: (record) => record.port?.port ?? "Internal", render: (record) => record.port?.port == null ? '<span class="status-badge default">Internal</span>' : `<span class="status-badge ${record.port.protocol === 'UDP' ? 'udp' : 'tcp'}">${escapeHtml(record.port.port)}</span>` },
        { key: "protocol", label: "Protocol", hidden: true, value: (record) => record.port?.protocol || "" },
        { key: "subject_cn", label: "CN", render: (record) => escapeHtml(record.subject_cn || "\u2014") },
        { key: "tls_version", label: "TLS", render: (record) => `<span class="status-badge ${escapeHtml(record.tls_status || "unknown")}">${escapeHtml(record.tls_version?.replace(/^(?:TLS)?v?(?=\d)/i, "") || "\u2014")}</span>` },
        { key: "issuer_cn", label: "Issuer", hidden: true, render: (record) => escapeHtml(record.issuer_cn || "\u2014") },
        { key: "note", label: "Note", hidden: true, render: (record) => escapeHtml(record.note || "\u2014") },
        { key: "valid_until", label: "Expires", type: "date", render: (record) => formatDate(record.valid_until) },
        { key: "days_remaining", label: "Remaining", type: "number", render: (record) => formatRemainingDays(record.days_remaining) },
        { key: "status", label: "Status", render: (record) => `<span class="status-badge ${escapeHtml(record.status)}">${escapeHtml(record.status)}</span>` },
    ],
});

attachCertificateView(certificatePage, "discovered");
