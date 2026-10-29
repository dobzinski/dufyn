new RecordPage(document.getElementById("operating-system-form"), {
    emptyMessage: "Add operating systems to classify assets.",
    endpoint: "/api/os", table: "#os-table", message: "#os-message", retry: "#os-retry",
    sortColumn: "sequence",
    columns: [
        {key: "name", label: "Name", render: record => escapeHtml(record.name)},
        {key: "asset_count", label: "Assets", type: "number"},
    ],
});
