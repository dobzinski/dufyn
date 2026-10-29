new RecordPage(document.getElementById("service-form"), {
    emptyMessage: "Add services to define the ports available for scanning.",
    endpoint: "/api/services", table: "#services-table", message: "#services-message", retry: "#services-retry",
    sortColumn: "port",
    columns: [
        { key: "port", label: "Port", type: "number" },
        { key: "protocol", label: "Protocol", value: (record) => record.protocol === "T" ? "TCP" : "UDP", render: (record) => `<span class="status-badge ${record.protocol === 'T' ? 'tcp' : 'udp'}">${record.protocol === 'T' ? 'TCP' : 'UDP'}</span>` },
        { key: "service", label: "Service", render: (record) => escapeHtml(record.service || "\u2014") },
    ],
});
