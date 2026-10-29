class PortSelectionModal {
    constructor(page, kind) {
        this.page = page;
        this.kind = kind;
        this.modal = document.getElementById("ports-modal");
        this.form = document.getElementById("ports-form");
        this.list = document.getElementById("ports-list");
        this.message = document.getElementById("ports-message");
        this.save = document.getElementById("ports-save");
        this.cancel = document.getElementById("ports-cancel");
        this.mode = document.getElementById("ports-mode");
        this.version = 0;
        this.saving = false;
        this.page.menu.querySelector('[data-action="ports"]').addEventListener("click", () => this.open());
        this.cancel.addEventListener("click", () => this.modal.close());
        this.modal.addEventListener("cancel", (event) => { if (this.saving) event.preventDefault(); });
        this.modal.addEventListener("close", () => {
            this.version++;
            this.page.deleting = false;
            this.list.replaceChildren();
            this.ownerId = null;
        });
        this.mode?.addEventListener("change", () => this.render());
        this.form.addEventListener("submit", (event) => this.submit(event));
    }

    render() {
        this.list.replaceChildren();
        const inherited = this.mode?.value === "I";
        for (const service of this.services) {
            const label = document.createElement("label");
            label.className = "scanner-network-option";
            const input = document.createElement("input");
            input.type = "checkbox";
            input.checked = inherited ? service.inherited : service.assigned;
            input.disabled = inherited;
            input.addEventListener("change", () => { service.assigned = input.checked; });
            const text = document.createElement("span");
            text.textContent = `${service.port}/${service.protocol === "T" ? "TCP" : "UDP"}${service.service ? " - " + service.service : ""}`;
            label.append(input, text);
            this.list.appendChild(label);
        }
        showMessage(this.message, !this.services.length ? "No services available. Create them in Services first." : inherited ? "These ports are inherited from the site. Select Custom to choose ports for this network." : "", "info");
    }

    async open() {
        if (this.page.deleting) return;
        const record = this.page.selectedRecord();
        this.page.closeActions(true);
        if (!record) return;
        this.page.deleting = true;
        this.ownerId = record.id;
        const version = ++this.version;
        this.modal.querySelector("h2").textContent = `Ports: ${record.fqdn || record.name || record.cidr}`;
        this.list.replaceChildren();
        this.save.disabled = true;
        if (this.mode) this.mode.disabled = true;
        showMessage(this.message, "Loading services...", "info");
        this.modal.showModal();
        try {
            const response = await fetch(`/api/${this.kind}/${record.id}/ports`);
            if (!response.ok) throw new Error("Unable to load ports. Close and try again.");
            const result = await response.json();
            if (version !== this.version) return;
            this.services = result.services;
            if (this.mode) { this.mode.value = result.port_mode; this.mode.disabled = false; }
            this.render();
            this.save.disabled = false;
        } catch (error) {
            if (version === this.version) showMessage(this.message, error.message, "error");
        }
    }

    async submit(event) {
        event.preventDefault();
        if (this.saving || this.save.disabled || this.ownerId === null) return;
        this.saving = true;
        this.save.disabled = true;
        this.cancel.disabled = true;
        this.list.disabled = true;
        if (this.mode) this.mode.disabled = true;
        const body = { service_ids: this.services.filter((s) => s.assigned).map((s) => s.id) };
        if (this.mode) body.port_mode = this.mode.value;
        showMessage(this.message, "Saving ports...", "info");
        try {
            const response = await fetch(`/api/${this.kind}/${this.ownerId}/ports`, {
                method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
            });
            if (!response.ok) {
                const error = await response.json();
                throw new Error(typeof error.detail === "string" ? error.detail : "Unable to save ports.");
            }
            this.modal.close();
            if (await this.page.load()) this.page.showMessage(this.page.listMessage, "Ports updated successfully.", "success");
        } catch (error) {
            showMessage(this.message, error.message, "error");
        } finally {
            this.saving = false;
            this.save.disabled = false;
            this.cancel.disabled = false;
            this.list.disabled = false;
            if (this.mode) this.mode.disabled = false;
        }
    }
}
