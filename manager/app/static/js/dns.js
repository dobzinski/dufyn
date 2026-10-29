function dnsPtrStatus(record) {
    if (!["A", "AAAA"].includes(record.record_type)) {
        return {status: "unknown", icon: "minus", detail: "PTR does not apply to CNAME records"};
    }
    const names = record.ptr_names || [];
    if (record.ptr_result === "answered" && names.length) {
        const normalize = name => name.toLowerCase().replace(/\.$/, "");
        const matches = names.some(name => normalize(name) === normalize(record.name));
        return {status: matches ? "valid" : "critical", icon: matches ? "check" : "x",
            detail: (matches ? "PTR matches: " : "PTR differs: ") + names.join(", ")};
    }
    const detail = !record.ptr_result ? "PTR not checked" :
        ["no_ptr", "nxdomain"].includes(record.ptr_result) ? "No PTR record" :
        "PTR query failed: " + record.ptr_result;
    return {status: "unknown", icon: "minus", detail};
}

function renderDnsPtr(record) {
    const ptr = dnsPtrStatus(record);
    const detail = ptr.detail + (record.ptr_checked_at ? " | " + formatDate(record.ptr_checked_at) : "");
    return `<span class="status-badge ${ptr.status}" role="img" aria-label="${escapeHtml(detail)}" title="${escapeHtml(detail)}"><span class="dns-ptr-icon dns-ptr-${ptr.icon}" aria-hidden="true"></span></span>`;
}

const dnsTable = new DataTable({
    newers: {field: "first_seen"},
    emptyMessage: "Configure DNS zone imports on a scanner to collect A, AAAA and CNAME records.", message: "#dns-message",
    table: "#dns-table",
    endpoint: "/api/dns/records",
    numbered: true,
    pageSize: 10,
    sortColumn: "name",
    columns: [
        {key: "sequence", label: "#", type: "number"},
        {key: "site_name", label: "Site"},
        {key: "zone_name", label: "Zone", hidden: true},
        {key: "name", label: "Name"},
        {key: "record_type", label: "Type", render: record =>
            '<span class="status-badge default">' + escapeHtml(record.record_type) + '</span>'},
        {key: "value", label: "IP / Target", value: record => record.address || record.target_name || "\u2014"},
        {key: "ttl", label: "TTL", type: "number"},
        {key: "last_seen", label: "Last Seen", type: "date", render: record =>
            record.last_seen ? formatDate(record.last_seen) : "\u2014"},
        {key: "dns_server", label: "DNS Server", hidden: true},
        {key: "dns_port", label: "DNS Port", hidden: true, type: "number"},
        {key: "scanner_name", label: "Scanner", hidden: true},
        {key: "serial", label: "SOA Serial", hidden: true, type: "number"},
        {key: "ptr", label: "PTR", value: record => dnsPtrStatus(record).detail, render: renderDnsPtr},
        {key: "is_target", label: "Target", searchValue: record => record.is_target ? "Yes" : "No",
            render: record => `<span class="status-badge ${record.is_target ? "yes" : "no"}">${record.is_target ? "Yes" : "No"}</span>`},
        {key: "actions", label: "Actions", sortable: false, searchable: false, render: record =>
            `<button type="button" class="row-actions-button" data-record-id="${escapeHtml(record.id)}" aria-label="Record actions" aria-haspopup="menu" aria-expanded="false"><img src="/static/icons/dots-vertical.svg" alt=""></button>`},
    ],
});


// Reuse the existing row-menu positioning and focus behavior.
const dnsActions = {
    menu: document.getElementById("record-actions"), trigger: null, selectedId: null, deleting: false,
    closeActions: RecordPage.prototype.closeActions,
    openActions: RecordPage.prototype.openActions,
};
dnsTable.element.addEventListener("click", event => dnsActions.openActions(event));
document.addEventListener("click", event => {
    if (!event.target.closest(".row-actions-button") && !dnsActions.menu.contains(event.target)) dnsActions.closeActions();
});
document.addEventListener("keydown", event => {
    if (event.key === "Escape") dnsActions.closeActions(true);
    if (!dnsActions.menu.hidden && ["ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        dnsActions.menu.querySelector("button").focus();
    }
});
window.addEventListener("resize", () => dnsActions.closeActions());
window.addEventListener("scroll", () => dnsActions.closeActions(), true);

const dnsTargetModal = document.getElementById("dns-target-modal");
const dnsTargetForm = document.getElementById("dns-target-form");
const dnsTargetSite = dnsTargetForm.elements.namedItem("site_id");
const dnsTargetSubmit = document.getElementById("dns-target-submit");
const dnsTargetCancel = document.getElementById("dns-target-cancel");
const dnsTargetClose = document.getElementById("dns-target-close");
const dnsTargetMessage = document.getElementById("dns-target-message");
const dnsTargetFilter = SelectFilter.attach(dnsTargetSite);
let dnsTargetVersion = 0;
let dnsTargetSaving = false;
dnsTargetCancel.addEventListener("click", () => dnsTargetModal.close());
dnsTargetClose.addEventListener("click", () => dnsTargetModal.close());
dnsTargetModal.addEventListener("cancel", event => { if (dnsTargetSaving) event.preventDefault(); });
dnsTargetModal.addEventListener("close", () => { dnsTargetVersion++; dnsActions.deleting = false; });
dnsActions.menu.querySelector('[data-action="add-target"]').addEventListener("click", async () => {
    const record = dnsTable.data.find(row => String(row.id) === dnsActions.selectedId);
    dnsActions.closeActions(true);
    if (!record || dnsActions.deleting) return;
    dnsActions.deleting = true;
    const version = ++dnsTargetVersion;
    dnsTargetForm.reset();
    dnsTargetSubmit.hidden = false;
    dnsTargetCancel.hidden = false;
    dnsTargetClose.hidden = true;
    dnsTargetForm.elements.namedItem("fqdn").value = record.name;
    dnsTargetSite.replaceChildren(new Option("Select ...", ""));
    dnsTargetFilter.clear();
    dnsTargetSite.disabled = true;
    dnsTargetSubmit.disabled = true;
    showMessage(dnsTargetMessage, "Loading sites...", "info");
    dnsTargetModal.showModal();
    try {
        const response = await fetch("/api/sites");
        if (!response.ok) throw new Error("Unable to load sites. Close and try again.");
        const sites = await response.json();
        if (version !== dnsTargetVersion) return;
        sites.forEach(site => dnsTargetSite.appendChild(new Option(site.name, String(site.id))));
        dnsTargetSite.value = "";
        dnsTargetSite.disabled = !sites.length;
        dnsTargetFilter.apply();
        dnsTargetSubmit.disabled = !sites.length;
        showMessage(dnsTargetMessage, sites.length ? "" : "Create a site before adding targets.", "error");
    } catch (error) {
        if (version === dnsTargetVersion) showMessage(dnsTargetMessage, error.message, "error");
    }
});
dnsTargetForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (dnsTargetSubmit.disabled || dnsTargetSaving || !dnsTargetForm.reportValidity()) return;
    let created = false;
    dnsTargetSaving = true;
    dnsTargetSubmit.disabled = true;
    dnsTargetCancel.disabled = true;
    dnsTargetSite.disabled = true;
    showMessage(dnsTargetMessage, "Creating target...", "info");
    try {
        const response = await fetch("/api/targets", {method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({site_id: Number(dnsTargetSite.value), fqdn: dnsTargetForm.elements.namedItem("fqdn").value, status: "U"})});
        if (!response.ok) {
            let detail;
            try { detail = (await response.json()).detail; } catch (_) { /* Use the fallback below. */ }
            throw new Error(typeof detail === "string" ? detail : "Unable to create target. Please retry.");
        }
        created = true;
        await dnsTable.load("/api/dns/records");
        showMessage(dnsTargetMessage, "Target created successfully.", "success");
        dnsTargetSubmit.disabled = true;
        dnsTargetSubmit.hidden = true;
        dnsTargetCancel.hidden = true;
        dnsTargetClose.hidden = false;
        dnsTargetClose.focus();
    } catch (error) {
        showMessage(dnsTargetMessage, error.message, "error");
        dnsTargetSubmit.disabled = false;
    } finally {
        dnsTargetSaving = false;
        dnsTargetCancel.disabled = false;
        dnsTargetSite.disabled = created;
        dnsTargetFilter.apply();
    }
});
