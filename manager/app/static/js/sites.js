const sitesPage = new RecordPage(document.getElementById("site-form"), {
    newers: {field: "created_at"},
    emptyMessage: "Create a site to start organizing networks, assets and targets.",
    endpoint: "/api/sites",
    table: "#sites-table",
    message: "#sites-message",
    retry: "#sites-retry",
    columns: [
        { key: "name", label: "Name", render: (record) => escapeHtml(record.name) },
        {
            key: "description",
            label: "Description",
            render: (record) => escapeHtml(record.description || "\u2014"),
        },
        { key: "ports_count", label: "Ports", type: "number" },
        {
            key: "created_at",
            label: "Created",
            type: "date",
            render: (record) => formatDate(record.created_at),
        },
    ],
});

new PortSelectionModal(sitesPage, "sites");
