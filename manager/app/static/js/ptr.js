const ptrTable = new DataTable({
    table: "#ptr-table",
    numbered: true,
    newers: {field: "first_seen"},
    pageSize: 10,
    sortColumn: "ip",
    columns: [
        {key: "sequence", label: "#", type: "number"},
        {key: "site_name", label: "Site"},
        {key: "ip", label: "IP", type: "ip"},
        {key: "hostname", label: "Hostname"},
        {key: "ttl", label: "TTL", type: "number"},
        {key: "dns_server", label: "DNS Server", hidden: true},
        {key: "last_seen", label: "Last Seen", type: "date", render: record =>
            record.last_seen ? formatDate(record.last_seen) : "\u2014"},
        {key: "asset_status", label: "Asset Status", render: record => {
            if (!record.asset_status) return "\u2014";
            const style = {Active: "active", Inactive: "inactive", Unknown: "unknown"}[record.asset_status] || "default";
            return `<span class="status-badge ${style}">${escapeHtml(record.asset_status)}</span>`;
        }},
        {key: "asset_name", label: "Asset", hidden: true},
        {key: "scanner_name", label: "Scanner", hidden: true},
        {key: "dns_port", label: "DNS Port", type: "number", hidden: true},
        {key: "first_seen", label: "First Seen", type: "date", hidden: true},
    ],
});

const ptrMessage = document.getElementById("ptr-message");
const ptrRetry = document.getElementById("ptr-retry");
async function loadPtrRecords() {
    ptrRetry.hidden = true;
    ptrRetry.disabled = true;
    showMessage(ptrMessage, "Loading inventory...", "info");
    const loaded = await ptrTable.load("/api/ptr/records");
    showMessage(ptrMessage, loaded ? (ptrTable.data.length ? "" :
        "Enable PTR collection on a scanner and its assigned networks to discover reverse DNS records.") :
        "Unable to load PTR records. Please retry.", loaded ? "info" : "error");
    ptrRetry.hidden = loaded;
    ptrRetry.disabled = false;
}
ptrRetry.addEventListener("click", loadPtrRecords);
loadPtrRecords();
