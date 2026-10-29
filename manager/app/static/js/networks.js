const networksPage = new RecordPage(document.getElementById("network-form"), {
    newers: {field: "created_at"},
    endpoint: "/api/networks",
    table: "#networks-table",
    message: "#networks-message",
    retry: "#networks-retry",
    sortColumn: "site_name",
    selects: [
        {
            name: "site_id",
            endpoint: "/api/sites",
            placeholder: "Select ...",
            emptyMessage: "Create a site before adding networks.",
        },
    ],
    columns: [
        { key: "site_name", label: "Site", render: (record) => escapeHtml(record.site_name) },
        { key: "cidr", label: "CIDR" },
        { key: "name", label: "Name", render: (record) => escapeHtml(record.name || "\u2014") },
        {
            key: "port_mode",
            label: "Type",
            render: (record) => 
                `<span class="status-badge ${record.port_mode === "I" ? "default" : "custom"}">${record.port_mode === "I" ? "Inherited" : "Custom"}</span>`,
        },
        { key: "ports_count", label: "Ports", type: "number" },
        {
            key: "created_at",
            label: "Created",
            type: "date",
            render: (record) => formatDate(record.created_at),
        },
        { key: "ptr_enabled", label: "PTR", searchValue: record => record.ptr_enabled === "Y" ? "Yes" : "No",
            render: record => `<span class="status-badge ${record.ptr_enabled === "Y" ? "yes" : "no"}">${record.ptr_enabled === "Y" ? "Yes" : "No"}</span>` },
        {
            key: "enabled",
            label: "Enabled",
            searchValue: (record) => (record.enabled === "Y" ? "Yes" : "No"),
            render: (record) =>
                `<span class="status-badge ${record.enabled === "Y" ? "yes" : "no"}">${record.enabled === "Y" ? "Yes" : "No"}</span>`,
        },
    ],
});

new PortSelectionModal(networksPage, "networks");
