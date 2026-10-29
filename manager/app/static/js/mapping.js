class MappingPage extends RecordPage {
    async save(event) {
        if (!this.form.reportValidity()) { event.preventDefault(); return; }
        this.saving = true;
        try { await super.save(event); }
        finally { this.saving = false; }
    }

    fillSelect(source) {
        const viewId = this.form.elements.namedItem("topology_view_id").value;
        const view = this.view;
        let records = source.records || [];
        if (source.name === "asset_id") {
            const mapped = new Set((this.records || [])
                .filter(item => String(item.topology_view_id) === viewId && item.id !== this.recordId)
                .map(item => String(item.asset_id)));
            records = records.filter(item => view && String(item.site_id) === String(view.site_id) && !mapped.has(String(item.id)));
        }
        if (source.name === "parent_id") {
            const excluded = new Set(this.recordId === null ? [] : [String(this.recordId)]);
            // Descendants cannot become parents. The database independently enforces this.
            let changed = true;
            while (changed) {
                changed = false;
                for (const item of records) {
                    if (excluded.has(String(item.parent_id)) && !excluded.has(String(item.id))) {
                        excluded.add(String(item.id));
                        changed = true;
                    }
                }
            }
            records = records.filter(item => String(item.topology_view_id) === viewId && !excluded.has(String(item.id)));
        }
        super.fillSelect({...source, records});
    }

    edit(record) {
        // Populate dependent options before the shared form assigns their values.
        this.recordId = record.id;
        this.form.elements.namedItem("topology_view_id").value = String(record.topology_view_id);
        this.syncFields();
        super.edit(record);
        this.selectFilters.get(this.form.elements.asset_id)?.trigger.focus();
    }

    async load() {
        if (!this.view) return false;
        this.loading = true;
        try {
            const loaded = await super.load();
            this.syncFields();
            return loaded;
        } finally { this.loading = false; }
    }
}

function createTopologyMapping(owner) {
    const page = new MappingPage(document.getElementById("mapping-form"), {
        newers: {field: "created_at"},
        endpoint: "", autoLoad: false, actions: "#mapping-actions",
        table: "#mapping-table", message: "#mapping-message", retry: "#mapping-retry", sortColumn: "ip",
        selects: [
            {name: "asset_id", endpoint: "", placeholder: "Select ...", emptyPlaceholder: "No available assets in this Site", emptyMessage: "Add assets before creating associations."},
            {name: "parent_id", endpoint: "", placeholder: "None (root)", required: false},
        ],
        columns: [
            {key: "display_name", label: "Asset", searchValue: record => record.display_name || record.discovered_hostname || "", render: record => escapeHtml(record.display_name || record.discovered_hostname || "\u2014")},
            {key: "ip", label: "IP Address", type: "ip"},
            {key: "type_name", label: "Type"},
            {key: "parent_name", label: "Parent"},
            {key: "created_at", label: "Created", type: "date", render: record => formatDate(record.created_at)},
        ],
    });
    const modal = document.getElementById("mapping-modal");
    const busy = () => page.loading || page.saving || page.deleting;
    document.getElementById("mapping-close").addEventListener("click", () => { if (!busy()) modal.close(); });
    modal.addEventListener("cancel", event => { if (busy()) event.preventDefault(); });
    modal.addEventListener("close", () => {
        page.closeActions(); page.view = null;
        owner.deleting = false;
        owner.load();
    });
    page.open = async record => {
        if (modal.open) return;
        owner.deleting = true;
        page.view = record;
        page.options.endpoint = `/api/views/${encodeURIComponent(record.id)}/mappings`;
        page.options.selects[0].endpoint = `/api/mapping/assets?site_id=${encodeURIComponent(record.site_id)}`;
        page.options.selects[1].endpoint = page.options.endpoint;
        page.form.elements.topology_view_id.defaultValue = String(record.id);
        page.clearMessages(); page.reset(); page.records = []; page.table?.setData([]);
        document.getElementById("mapping-subtitle").textContent = `${record.site_name} - ${record.name}`;
        modal.showModal();
        await page.load();
    };
    return page;
}
