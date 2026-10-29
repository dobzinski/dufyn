class PkiPage extends RecordPage {
    fillSelect(source) {
        if (source.records && !source.allRecords) source.allRecords = source.records;
        if (source.allRecords) source.records = source.allRecords.filter(record => this.choiceValue("kind") !== "server" || record.server_eligible);
        super.fillSelect(source);
    }

    async load() {
        for (const source of this.options.selects || []) delete source.allRecords;
        const loaded = await super.load();
        this.syncFields();
        return loaded;
    }

    syncFields() {
        const kind = this.choiceValue("kind");
        const days = this.form.elements.validity_days;
        if (this.previousKind && this.previousKind !== kind && ["365", "3650"].includes(days.value)) days.value = kind === "server" ? "365" : "3650";
        this.previousKind = kind;
        super.syncFields();
        this.submit.disabled = !this.optionsReady || (kind !== "root" && !this.options.selects?.[0]?.records?.length);
        const importing = this.choiceValue("mode") === "import";
        this.title.textContent = importing ? "Import" : "New";
        this.submit.textContent = importing ? "Import" : "Add";
        this.submit.dataset.buttonIcon = importing ? "upload" : "add";
        for (const name of ["certificate", "private_key"]) {
            const input = this.form.elements.namedItem(name);
            this.form.querySelector(`#pki-${name}-name`).textContent = input.files[0]?.name || "No file selected";
        }
    }

    async save(event) {
        if (!this.choiceValue("mode")) {
            event.preventDefault();
            return;
        }
        if (this.choiceValue("mode") !== "import") return super.save(event);
        event.preventDefault();
        if (this.submit.disabled) return;
        this.clearMessages();
        const name = this.form.elements.name.value.trim();
        const kind = this.form.elements.kind.value;
        const parent = this.form.elements.parent_id;
        const parentId = parent.matches(":disabled") ? null : parent.value || null;
        if (!name || (kind !== "root" && !parentId)) {
            this.showMessage(this.message, "Complete the required fields.", "error");
            return;
        }
        const files = ["certificate", "private_key"].map(field => this.form.elements.namedItem(field).files[0]);
        if (files.some(file => !file)) {
            this.showMessage(this.message, "Choose a certificate file and its private key.", "error");
            return;
        }
        if (files.some(file => !file.size || file.size > 256 * 1024)) {
            this.showMessage(this.message, "Choose nonempty certificate and key files of at most 256 KB each.", "error");
            return;
        }
        const password = this.form.elements.key_password.value || null;
        this.submit.disabled = true;
        this.showMessage(this.message, "Validating and importing certificate...", "info");
        try {
            const readFile = file => new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(String(reader.result).split(",")[1]);
                reader.onerror = () => reject(new Error("Unable to read the selected file."));
                reader.readAsDataURL(file);
            });
            const [certificate, privateKey] = await Promise.all(files.map(readFile));
            const response = await fetch(this.options.endpoint + "/import", {
                method: "POST", headers: {"Content-Type":"application/json"}, cache: "no-store",
                body: JSON.stringify({name, kind, parent_id: parentId, certificate, private_key: privateKey, key_password: password}),
            });
            if (!response.ok) {
                let error = {};
                try { error = await response.json(); } catch { /* A proxy may return HTML. */ }
                throw new Error(typeof error?.detail === "string" ? error.detail : "Unable to import certificate. Please retry.");
            }
            this.reset();
            if (await this.load()) this.showMessage(this.message, "Certificate imported successfully.", "success");
        } catch (error) {
            this.showMessage(this.message, error.message, "error");
        } finally {
            this.syncFields();
        }
    }

    openActions(event) {
        const button = event.target.closest(".row-actions-button");
        if (!button || this.deleting) return;
        const record = this.records.find(item => String(item.id) === button.dataset.recordId);
        if (!record) return;
        this.chainAction ||= this.menu.querySelector('[data-action="download-chain"]');
        if (record.kind === "root") {
            this.chainAction.remove();
        } else {
            this.menu.insertBefore(this.chainAction, this.menu.querySelector('[data-action="download-key"]'));
        }
        super.openActions(event);
    }
}

const pkiForm = document.getElementById("pki-form");
const pkiEndpoint = pkiForm.dataset.endpoint;
const pkiPage = new PkiPage(pkiForm, {
    newers: {field: "created_at"},
    endpoint: pkiEndpoint, table: "#pki-table", message: "#pki-list-message", retry: "#pki-retry",
    selects: [{name: "parent_id", endpoint: "/api/selfsignedcerts/issuers", placeholder: "Select ...", required: false,
        emptyMessage: ""}],
    columns: [
        {key: "name", label: "Name"},
        {key: "kind", label: "Type", value: record => ({root: "Root CA", intermediate: "Intermediate CA", server: "Server"}[record.kind]),
            render: record => `<span class="status-badge ${escapeHtml(record.kind)}">${escapeHtml({root: "Root CA", intermediate: "Intermediate CA", server: "Server"}[record.kind] || record.kind)}</span>`},
        {key: "common_name", label: "Common Name"},
        {key: "parent_name", label: "Parent CA", value: record => record.parent_name || "\u2014"},
        {key: "source", label: "Source", value: record => ({generated: "Generated", imported: "Imported"}[record.source] || "\u2014"),
            render: record => ({generated: '<span class="status-badge generated">Generated</span>', imported: '<span class="status-badge custom">Imported</span>'}[record.source] || "\u2014")},
        {key: "not_after", label: "Valid Until", type: "date", hidden: true, render: record => formatDate(record.not_after)},
        {key: "days_remaining", label: "Remaining", type: "number", render: record => formatRemainingDays(record.days_remaining)},
        {key: "status", label: "Status", render: record => `<span class="status-badge ${escapeHtml(record.status)}">${escapeHtml(record.status)}</span>`},
        {key: "country", label: "Country", value: record => record.subject?.country ?? "", hidden: true},
        {key: "state", label: "State", value: record => record.subject?.state ?? "", hidden: true},
        {key: "locality", label: "Locality", value: record => record.subject?.locality ?? "", hidden: true},
        {key: "organization", label: "Organization", value: record => record.subject?.organization ?? "", hidden: true},
        {key: "organizational_unit", label: "Organizational Unit", value: record => record.subject?.organizational_unit ?? "", hidden: true},
        {key: "parent_id", label: "Parent ID", type: "number", hidden: true},
        {key: "not_before", label: "Valid From", type: "date", hidden: true},
        {key: "validity_days", label: "Validity Days", type: "number", hidden: true},
        {key: "alt_names", label: "SAN", value: record => record.alt_names.map(name => name.type + ":" + name.value).join(", "), hidden: true},
        {key: "key_algorithm", label: "Key Algorithm", hidden: true},
        {key: "rsa_key_size", label: "Key Size", type: "number", hidden: true},
        {key: "signature_hash", label: "Signature Hash", hidden: true},
        {key: "serial", label: "Serial", hidden: true},
        {key: "fingerprint_sha256", label: "SHA256 Fingerprint", hidden: true},
        {key: "children_count", label: "Children", type: "number", hidden: true},
        {key: "created_at", label: "Created", type: "date", hidden: true},
    ],
});

for (const artifact of ["certificate", "key", "chain"]) {
    pkiPage.menu.querySelector(`[data-action="download-${artifact}"]`).addEventListener("click", async () => {
        if (pkiPage.deleting) return;
        const record = pkiPage.selectedRecord();
        pkiPage.closeActions(true);
        if (!record) return;
        pkiPage.deleting = true;
        pkiPage.clearMessages();
        try {
            const response = await fetch(`${pkiEndpoint}/${record.id}/download/${artifact}`, {cache: "no-store"});
            if (!response.ok) {
                const error = await response.json();
                throw new Error(typeof error.detail === "string" ? error.detail : "Unable to download file.");
            }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] || "certificate.pem";
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) {
            pkiPage.showMessage(pkiPage.listMessage, error.message, "error");
        } finally {
            pkiPage.deleting = false;
        }
    });
}

attachCertificateView(pkiPage);

for (const button of pkiForm.querySelectorAll("[data-pki-file]")) {
    const input = pkiForm.elements.namedItem(button.dataset.pkiFile);
    button.addEventListener("click", () => input.click());
    input.addEventListener("invalid", event => {
        event.preventDefault();
        pkiPage.accordion.open = true;
        button.focus();
    });
}
