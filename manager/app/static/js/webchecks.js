const targetResultsTable = new DataTable({
    newers: {field: "first_seen"},
    emptyMessage: "Create targets and configure scanner target checks to collect port and HTTP results.", message: "#webchecks-message",
    table: "#webchecks-table", endpoint: "/api/targets/results", numbered: true, sortColumn: "fqdn", pageSize: 10,
    columns: [
            {key: "sequence", label: "#", type: "number"},
            {key: "site_name", label: "Site"}, {key: "fqdn", label: "FQDN"},
            {key: "port", label: "Port", type: "number", render: r => `<span class="status-badge ${r.protocol === "T" ? "tcp" : "udp"}">${escapeHtml(r.port)}</span>`},
            {key: "protocol", label: "Protocol", hidden: true, value: r => r.protocol === "T" ? "TCP" : "UDP"},
            {key: "check_protocol", label: "Check", hidden: true, value: r => r.check_protocol?.toUpperCase() || "Unchecked"},
            {key: "last_http_status", label: "HTTP Status", type:"number", render: r => {
                const code = Number(r.last_http_status);
                const group = r.last_http_status == null ? "unchecked" : code >= 200 && code < 300 ? "success" : code >= 300 && code < 400 ? "redirect" : code >= 400 && code < 500 ? "client-error" : code >= 500 && code < 600 ? "server-error" : "other";
                return `<span class="https-status ${group}">${escapeHtml(r.last_http_status ?? "\u2014")}</span>`;
            }},
            {key: "response_time_ms", label: "Response Time", type:"number", render:r=>r.response_time_ms == null ? "\u2014" : escapeHtml(r.response_time_ms.toFixed(1))+" ms"},
            {key: "last_seen", label: "Last Seen", type:"date", render:r=>r.last_seen ? formatDate(r.last_seen) : "\u2014"},
            {key: "state", label: "State", value: r => r.state === "O" ? "Open" : "Closed", render: r => `<span class="status-badge ${r.state === "O" ? "open" : "closed"}">${r.state === "O" ? "Open" : "Closed"}</span>`},
        ]
});
