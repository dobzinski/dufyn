// Reuse the shared table, form, edit, delete and confirmation behavior.
(() => {
    const form = document.getElementById("password-form");
    const secret = document.getElementById("password-value");
    const length = document.getElementById("password-length");
    const flagFields = {upper: "use_uppercase", lower: "use_lowercase", digits: "use_numbers", symbols: "use_symbols"};
    const refreshCount = () => form.dispatchEvent(new Event("password:update"));

    class PasswordRecords extends RecordPage {
        reset() {
            this.editVersion = (this.editVersion || 0) + 1;
            super.reset();
            length.setCustomValidity("");
            secret.value = "";
            this.accordion.open = false;
            refreshCount();
        }

        async edit(record) {
            if (this.editing || this.deleting || this.submit.disabled) return;
            const version = this.editVersion = (this.editVersion || 0) + 1;
            this.editing = true;
            this.submit.disabled = true;
            this.clearMessages();
            try {
                const response = await fetch(`/api/passwords/${record.id}`, {cache: "no-store"});
                if (!response.ok) {
                    if (response.status === 404) throw new Error("Record no longer exists. Reload the list.");
                    throw new Error("Unable to open password. Check the encryption key and retry.");
                }
                const detail = await response.json();
                if (version !== this.editVersion) return;
                super.edit(detail);
                length.setCustomValidity("");
                for (const checkbox of form.querySelectorAll("input[data-character]")) {
                    checkbox.checked = detail[flagFields[checkbox.value]] === "Y";
                }
                refreshCount();
            } catch (error) {
                this.showMessage(this.listMessage, error.message, "error");
            } finally {
                this.editing = false;
                this.submit.disabled = !this.optionsReady;
            }
        }

        openActions(event) {
            const trigger = event.target.closest(".row-actions-button");
            if (!trigger || this.deleting) return;
            const record = this.records.find(item => String(item.id) === trigger.dataset.recordId);
            this.menu.querySelector('[data-action="copy-account"]').hidden = !record?.account;
            this.menu.querySelector('[data-action="copy-url"]').hidden = !record?.url;
            super.openActions(event);
        }

        request(url, method, data) {
            // Shared RecordForm trims strings; passwords must preserve every byte.
            data.password = secret.value;
            data.character_count = Number(length.value);
            for (const checkbox of form.querySelectorAll("input[data-character]")) {
                data[flagFields[checkbox.value]] = checkbox.checked ? "Y" : "N";
            }
            return super.request(url, method, data);
        }
    }

    const page = new PasswordRecords(form, {
        newers: {field: "created_at"},
        endpoint: "/api/passwords",
        table: "#passwords-table",
        message: "#passwords-message",
        retry: "#passwords-retry",
        emptyMessage: "Create a password record to start organizing credentials.",
        columns: [
            {key: "name", label: "Name", render: record => escapeHtml(record.name)},
            {key: "account", label: "Account", render: record => escapeHtml(record.account || "\u2014")},
            {key: "url", label: "URL", render: record => escapeHtml(record.url || "\u2014")},
            {key: "note", label: "Note", hidden: true, render: record => escapeHtml(record.note || "\u2014")},
            {key: "created_at", label: "Created", type: "date", render: record => formatDate(record.created_at)},
            {key: "updated_at", label: "Updated", type: "date", render: record => formatDate(record.updated_at)},
        ],
    });
    const copyFields = {account: "Account", password: "Password", url: "URL"};
    for (const [field, label] of Object.entries(copyFields)) {
        page.menu.querySelector(`[data-action="copy-${field}"]`).addEventListener("click", async () => {
            if (page.deleting || page.editing) return;
            const record = page.selectedRecord();
            page.closeActions(true);
            if (!record) return;
            page.deleting = true;
            page.clearMessages();
            let temporary;
            try {
                let value = record[field];
                if (field === "password") {
                    const response = await fetch(`/api/passwords/${record.id}`, {cache: "no-store"});
                    if (!response.ok) throw new Error("Unable to open password");
                    value = (await response.json()).password;
                }
                if (!value) throw new Error("Nothing to copy");
                if (navigator.clipboard && window.isSecureContext) {
                    await navigator.clipboard.writeText(value);
                } else {
                    temporary = document.createElement("textarea");
                    temporary.value = value;
                    temporary.setAttribute("aria-label", `${label} to copy`);
                    temporary.style.position = "fixed";
                    temporary.style.opacity = "0";
                    document.body.append(temporary);
                    temporary.focus();
                    temporary.select();
                    if (!document.execCommand("copy")) throw new Error("Clipboard unavailable");
                }
                page.showMessage(page.listMessage, `${label} copied.`, "success");
            } catch {
                page.showMessage(page.listMessage, `Unable to copy ${label === "URL" ? label : label.toLowerCase()}. Open Edit and copy it manually with Ctrl+C or Cmd+C.`, "error");
            } finally {
                temporary?.remove();
                page.deleting = false;
                page.tableElement.querySelector(`button[data-record-id="${record.id}"]`)?.focus();
            }
        });
    }
})();
