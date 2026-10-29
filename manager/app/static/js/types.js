new RecordPage(document.getElementById("type-form"), {
    emptyMessage: "Add types to classify assets.",
    endpoint: "/api/types", table: "#types-table", message: "#types-message", retry: "#types-retry",
    columns: [
        {key: "name", label: "Name", render: record => escapeHtml(record.name)},
        {key: "asset_count", label: "Assets", type: "number"},
    ],
});
