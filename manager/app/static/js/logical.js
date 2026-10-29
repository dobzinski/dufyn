const logicalMap = new NetworkMap(document.getElementById("network-map-modal"));
const logicalPage = new TopologyViewPage(document.getElementById("logical-form"), {
    newers: {field: "created_at"},
    endpoint: "/api/logical", table: "#logical-table", message: "#logical-message", retry: "#logical-retry", sortColumn: "name",
    selects: [{name: "site_id", endpoint: "/api/sites", placeholder: "Select ...", emptyMessage: "Create a site before adding views."}],
    columns: [
        {key: "site_name", label: "Site"},
        {key: "name", label: "Name"},
        {key: "mapping_count", label: "Assoc.", type: "number"},
        {key: "created_at", label: "Created", type: "date", render: record => formatDate(record.created_at)},
    ],
}, logicalMap);
logicalMap.viewport.addEventListener("click", event => {
    if (!event.target.closest(".network-map-node")) logicalMap.clearSelection();
});
