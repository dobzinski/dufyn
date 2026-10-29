const linksPage = new RecordPage(document.getElementById("links-form"), {
    newers: {field: "created_at"},
    emptyMessage: "Create a site link, then associate assets from logical views in different sites.",
    endpoint: "/api/links", table: "#links-table", message: "#links-message", retry: "#links-retry",
    columns: [
        {key: "name", label: "Name"},
        {key: "view_count", label: "Views", type: "number"},
        {key: "association_count", label: "Assoc.", type: "number"},
        {key: "created_at", label: "Created", type: "date", render: record => formatDate(record.created_at)},
    ],
});

class AssociationPage extends RecordPage {
    async load() {
        if (!this.ownerId) return false;
        this.loading = true;
        try { return await super.load(); }
        finally { this.loading = false; }
    }

    async save(event) {
        this.saving = true;
        try { await super.save(event); }
        finally { this.saving = false; }
    }

    edit(record) {
        // Populate dependent view options before the shared form assigns values.
        this.form.querySelector('[data-choice="source_view"]').value = String(record.source_view_id);
        this.syncFields();
        super.edit(record);
    }

    fillSelect(source) {
        const sourceViewId = this.choiceValue("source_view");
        const views = this.options.selects.find(item => item.name === "source_view")?.records || [];
        const view = views.find(item => String(item.id) === sourceViewId);
        let records = source.records || [];
        if (source.name === "destination_view") {
            records = records.filter(item => view && String(item.site_id) !== String(view.site_id));
        }
        if (source.name === "source_id" || source.name === "destination_id") {
            const viewId = source.name === "source_id" ? sourceViewId : this.choiceValue("destination_view");
            records = records.filter(item => String(item.topology_view_id) === viewId);
        }
        super.fillSelect({...source, records});
    }
}

const associationPage = new AssociationPage(document.getElementById("association-form"), {
    newers: {field: "created_at"},
    endpoint: "", autoLoad: false, actions: "#association-actions", table: "#association-table", message: "#association-list-message", retry: "#association-retry",
    sortColumn: "source_site_name",
    choices: record => ({source_view: String(record.source_view_id), destination_view: String(record.destination_view_id)}),
    selects: [
        {name: "source_view", endpoint: "/api/logical", placeholder: "Select ...", group: "site_name", groupKey: "site_id", emptyMessage: "Create logical views before adding site links."},
        {name: "source_id", endpoint: "/api/links/mappings", placeholder: "Select ...", emptyPlaceholder: "No assets associated with this view", emptyMessage: "Associate assets with logical views before adding site links."},
        {name: "destination_view", endpoint: "/api/logical", placeholder: "Select ...", group: "site_name", groupKey: "site_id", required: false, emptyPlaceholder: "No logical views in another Site"},
        {name: "destination_id", endpoint: "/api/links/mappings", placeholder: "Select ...", required: false, emptyPlaceholder: "No assets associated with this view"},
    ],
    columns: [
        {key: "source_site_name", label: "SRC Site"},
        {key: "source_view_name", label: "SRC View"},
        {key: "source_name", label: "SRC Asset"},
        {key: "destination_site_name", label: "DST Site"},
        {key: "destination_view_name", label: "DST View"},
        {key: "destination_name", label: "DST Asset"},
        {key: "created_at", label: "Created", type: "date", render: record => formatDate(record.created_at)},
    ],
});

const associationsModal = document.getElementById("associations-modal");
const associationsBusy = () => associationPage.loading || associationPage.saving || associationPage.deleting;
document.getElementById("associations-close").addEventListener("click", () => {
    if (!associationsBusy()) associationsModal.close();
});
associationsModal.addEventListener("cancel", event => {
    if (associationsBusy()) event.preventDefault();
});
associationsModal.addEventListener("close", () => {
    associationPage.closeActions();
    associationPage.ownerId = null;
    linksPage.deleting = false;
    linksPage.load();
});
linksPage.menu.querySelector('[data-action="associations"]').addEventListener("click", async () => {
    if (linksPage.deleting) return;
    const record = linksPage.selectedRecord();
    linksPage.closeActions(true);
    if (!record) return;
    linksPage.deleting = true;
    associationPage.ownerId = record.id;
    associationPage.options.endpoint = `/api/links/${encodeURIComponent(record.id)}/links`;
    associationPage.clearMessages();
    associationPage.reset();
    associationPage.records = [];
    associationPage.table?.setData([]);
    document.getElementById("associations-subtitle").textContent = record.name;
    associationsModal.showModal();
    await associationPage.load();
});

const sitelinkMap = new SiteLinkMap(document.getElementById("network-map-modal"));
linksPage.menu.querySelector('[data-action="open-topology"]').addEventListener("click", () => {
    if (linksPage.deleting) return;
    const record = linksPage.selectedRecord();
    const opener = linksPage.trigger;
    linksPage.closeActions(true);
    if (record) sitelinkMap.open(record.id, opener);
});
sitelinkMap.viewport.addEventListener("click", event => {
    if (!event.target.closest(".network-map-node")) sitelinkMap.clearSelection();
});
