// Shared searchable dropdown; the original select retains all options and optgroups.
class SelectFilter {
    static instances = new WeakMap();

    static attach(select) {
        if (!this.instances.has(select)) this.instances.set(select, new SelectFilter(select));
        return this.instances.get(select);
    }

    constructor(select) {
        this.select = select;
        this.searchable = select.dataset.selectSearch !== "false";
        this.container = document.createElement("div");
        this.container.className = "select-filter";
        select.before(this.container);
        this.trigger = document.createElement("button");
        this.trigger.type = "button";
        this.trigger.className = "select-filter-trigger";
        this.trigger.setAttribute("aria-haspopup", "listbox");
        this.trigger.setAttribute("aria-expanded", "false");
        const label = select.labels?.[0];
        if (!label) this.trigger.setAttribute("aria-label", select.getAttribute("aria-label") || "Select option");
        if (label) {
            if (!label.id) label.id = select.id + "-label";
            this.trigger.setAttribute("aria-labelledby", label.id);
            label.addEventListener("click", (event) => {
                event.preventDefault();
                this.trigger.focus();
            });
        }
        this.panel = document.createElement("div");
        this.panel.className = "select-filter-panel";
        this.panel.hidden = true;
        this.input = document.createElement("input");
        this.input.type = "search";
        this.input.placeholder = "Filter options...";
        this.input.autocomplete = "off";
        this.input.setAttribute("aria-label", "Filter " + (label?.textContent.trim() || "options"));
        this.list = document.createElement("div");
        this.list.className = "select-filter-options";
        this.list.id = (select.id || "select-filter-" + document.querySelectorAll(".select-filter").length) + "-options";
        this.list.setAttribute("role", "listbox");
        this.list.setAttribute("aria-label", label?.textContent.trim() || "Options");
        this.trigger.setAttribute("aria-controls", this.list.id);
        this.input.setAttribute("aria-controls", this.list.id);
        if (this.searchable) this.panel.append(this.input);
        this.panel.append(this.list);
        this.container.append(select, this.trigger, this.panel);
        select.classList.add("select-filter-native");
        select.tabIndex = -1;
        this.trigger.addEventListener("click", () => this.panel.hidden ? this.open() : this.close());
        this.input.addEventListener("input", () => this.apply());
        this.container.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                event.preventDefault();
                this.close(true);
            }
            if (event.key === "Enter" && event.target === this.input) {
                event.preventDefault();
                this.list.querySelector("button:not(:disabled)")?.click();
            }
            if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
                event.preventDefault();
                if (this.panel.hidden) { this.open(); return; }
                const items = [...this.list.querySelectorAll("button:not(:disabled)")];
                const index = items.indexOf(document.activeElement);
                const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
                    : event.key === "ArrowDown" ? (index + 1) % items.length
                    : (index < 0 ? items.length - 1 : (index - 1 + items.length) % items.length);
                items[next]?.focus();
            }
        });
        this.container.addEventListener("focusout", () => {
            setTimeout(() => {
                if (!this.container.contains(document.activeElement)) this.close();
            }, 0);
        });
        document.addEventListener("click", (event) => {
            if (!this.container.contains(event.target)) this.close();
        });
        select.addEventListener("change", () => this.apply());
        select.addEventListener("invalid", () => {
            select.closest("details")?.setAttribute("open", "");
            this.trigger.focus();
        });
        select.form?.addEventListener("reset", () => setTimeout(() => this.clear(), 0));
        new MutationObserver(() => this.apply()).observe(select, {
            childList: true, subtree: true, characterData: true, attributes: true,
            attributeFilter: ["disabled", "selected", "label", "value"]
        });
        this.apply();
    }

    normalize(value) {
        return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
    }

    open() {
        if (this.select.matches(":disabled")) return;
        this.clear();
        this.panel.hidden = false;
        this.trigger.setAttribute("aria-expanded", "true");
        if (this.searchable) this.input.focus();
        else (this.list.querySelector('[aria-selected="true"]:not(:disabled)') || this.list.querySelector("button:not(:disabled)"))?.focus();
    }

    close(focus = false) {
        this.panel.hidden = true;
        this.trigger.setAttribute("aria-expanded", "false");
        if (focus) this.trigger.focus();
    }

    clear() {
        this.input.value = "";
        this.apply();
    }

    apply() {
        this.trigger.textContent = this.select.selectedOptions[0]?.textContent || "Select ...";
        this.trigger.disabled = this.select.matches(":disabled");
        if (this.trigger.disabled) this.close();
        const query = this.normalize(this.input.value);
        this.list.replaceChildren();
        const appendOption = (option, parent, group) => {
            if (option.hidden || (query && !this.normalize(group + " " + option.textContent).includes(query))) return;
            const button = document.createElement("button");
            button.type = "button";
            button.className = "select-filter-option";
            button.textContent = option.textContent;
            button.disabled = option.disabled || option.parentElement.disabled === true;
            button.setAttribute("role", "option");
            button.setAttribute("aria-selected", String(option.selected));
            button.addEventListener("click", () => {
                option.selected = true;
                this.select.dispatchEvent(new Event("input", { bubbles: true }));
                this.select.dispatchEvent(new Event("change", { bubbles: true }));
                this.apply();
                this.close(true);
            });
            parent.append(button);
        };
        for (const child of this.select.children) {
            if (child.tagName === "OPTGROUP") {
                if (child.hidden) continue;
                const group = document.createElement("div");
                group.setAttribute("role", "group");
                group.setAttribute("aria-label", child.label);
                const title = document.createElement("div");
                title.className = "select-filter-group";
                title.textContent = child.label;
                group.append(title);
                [...child.children].forEach((option) => appendOption(option, group, child.label));
                if (group.children.length > 1) this.list.append(group);
            } else if (child.tagName === "OPTION") appendOption(child, this.list, "");
        }
        if (!this.list.children.length) {
            const empty = document.createElement("div");
            empty.className = "select-filter-empty";
            empty.textContent = "No matching options.";
            empty.setAttribute("role", "status");
            this.list.append(empty);
        }
    }
}

document.querySelectorAll("form select, select[data-select-search]").forEach((select) => SelectFilter.attach(select));
new MutationObserver(() => {
    document.querySelectorAll("form select, select[data-select-search]").forEach((select) => SelectFilter.attach(select));
}).observe(document.body, { childList: true, subtree: true });

// Shared create/edit form behavior. Page scripts supply the endpoint and refresh callback.
class RecordForm {
    constructor(form, options) {
        this.form = form;
        this.options = options;
        this.recordId = null;
        this.optionsReady = true;
        this.accordion = form.closest("details");
        this.title = this.accordion.querySelector("h2");
        this.submit = form.querySelector('[type="submit"]');
        this.cancel = form.querySelector("[data-form-cancel]");
        this.message = form.querySelector(".message");
        this.messages = [this.message, ...(options.messages || [])];
        this.cancel.addEventListener("click", () => {
            this.clearMessages();
            this.reset();
            this.accordion.open = false;
        });
        form.addEventListener("invalid", () => this.clearMessages(), true);
        this.submit.addEventListener("click", () => this.clearMessages());
        form.addEventListener("submit", (event) => this.save(event));
        form.addEventListener("change", (event) => {
            // The dropdown search commits a change on blur, before an option's
            // click. Rebuilding here would detach the option and lose the click.
            if (event.target.matches("[name], [data-choice]")) this.syncFields();
        });
        this.selectFilters = new Map([...this.form.querySelectorAll("select")].map((select) => [select, SelectFilter.attach(select)]));
        this.syncFields();
    }

    choiceValue(name) {
        return this.form.querySelector(`[data-choice="${name}"]`)?.value
            ?? this.form.elements.namedItem(name)?.value ?? "";
    }

    matchesConditions(conditions) {
        return !conditions || conditions.split("|").some((branch) => branch.split("&").every((condition) => {
            const [name, value] = condition.split("=");
            const selected = this.choiceValue(name);
            return value === "*" ? selected !== "" : selected === value;
        }));
    }

    fillSelect(source) {
        const select = this.form.elements.namedItem(source.name);
        const selected = select.value;
        select.replaceChildren(new Option(source.placeholder, ""));
        const groups = new Map();
        const records = (source.records || []).filter((record) => !source.filter ||
            record[source.filter.key] === this.choiceValue(source.filter.choice)
        );
        if (!records.length && source.emptyPlaceholder) {
            select.options[0].textContent = source.emptyPlaceholder;
        }
        records.forEach((record) => {
            let parent = select;
            if (source.group) {
                const label = record[source.group];
                const key = source.groupKey ? record[source.groupKey] : label;
                if (!groups.has(key)) {
                    const group = document.createElement("optgroup");
                    group.label = label;
                    select.appendChild(group);
                    groups.set(key, group);
                }
                parent = groups.get(key);
            }
            parent.appendChild(new Option(record.name, String(record.id)));
        });
        select.value = selected;
        this.selectFilters?.get(select)?.apply();
    }

    syncFields() {
        for (const source of this.options.selects || []) {
            if (source.records) this.fillSelect(source);
        }
        this.form.querySelectorAll("[data-when]").forEach((group) => {
            group.hidden = !this.matchesConditions(group.dataset.when);
            group.disabled = group.hidden;
        });
        this.selectFilters?.forEach((filter) => filter.apply());
    }

    clearMessages() {
        this.messages.forEach((element) => showMessage(element));
    }

    showMessage(element, text, type) {
        this.clearMessages();
        showMessage(element, text, type);
    }

    reset() {
        this.form.reset();
        this.selectFilters.forEach((filter) => filter.clear());
        this.recordId = null;
        this.title.textContent = "New";
        this.submit.textContent = "Add";
        this.submit.dataset.buttonIcon = "add";
        this.cancel.hidden = true;
        this.syncFields();
    }

    edit(record) {
        this.clearMessages();
        this.recordId = record.id;
        this.selectFilters.forEach((filter) => filter.clear());
        const choices = this.options.choices?.(record) || {};
        this.form.querySelectorAll("[data-choice]").forEach((field) => {
            field.value = choices[field.dataset.choice] || "";
        });
        this.syncFields();
        for (const field of this.form.elements) {
            if (field.name) {
                const value = record[field.name] ?? "";
                field.value = field.hasAttribute("data-date-only") ? value.slice(0, 10) : value;
            }
        }
        this.syncFields();
        this.title.textContent = "Edit";
        this.submit.textContent = "Save";
        this.submit.dataset.buttonIcon = "save";
        this.cancel.hidden = false;
        this.accordion.open = true;
        this.form.querySelector("input, select, textarea").focus();
    }

    async request(url, method, data) {
        return fetch(url, {
            method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(data),
        });
    }

    async save(event) {
        event.preventDefault();
        if (this.submit.disabled) return;
        this.clearMessages();
        const data = {};
        for (const [name, value] of new FormData(this.form)) {
            if (typeof value === "string") data[name] = value.trim() || null;
        }
        const blankRequired = [...this.form.elements].find(
            (field) => field.name && !field.matches(":disabled") && field.required && !data[field.name],
        );
        if (blankRequired) {
            this.showMessage(this.message, "Complete the required fields.", "error");
            blankRequired.focus();
            return;
        }
        const editing = this.recordId !== null;
        this.submit.disabled = true;
        this.showMessage(this.message, editing ? "Saving record..." : "Creating record...", "info");
        try {
            const url = editing
                ? `${this.options.endpoint}/${this.recordId}`
                : this.options.endpoint;
            const response = await this.request(url, editing ? "PUT" : "POST", data);
            if (!response.ok) {
                if ([409, 422, 503].includes(response.status)) {
                    let error = {};
                    try { error = await response.json(); } catch { /* A proxy may return HTML. */ }
                    throw new Error(
                        typeof error?.detail === "string"
                            ? error.detail
                            : response.status === 503
                                ? "Service unavailable. Please retry."
                                : "Check the field values.",
                    );
                }
                if (response.status === 404)
                    throw new Error("Record no longer exists. Reload the list.");
                throw new Error("Unable to save record. Please retry.");
            }
            this.reset();
            if (await this.options.onSaved()) {
                this.showMessage(
                    this.message,
                    editing ? "Record updated successfully." : "Record created successfully.",
                    "success",
                );
            }
        } catch (error) {
            this.showMessage(this.message, error.message, "error");
        } finally {
            this.submit.disabled = !this.optionsReady;
        }
    }
}

function confirmDeletion() {
    const modal = document.getElementById("delete-modal");
    modal.returnValue = "cancel";
    return new Promise((resolve) => {
        modal.addEventListener("close", () => resolve(modal.returnValue === "delete"), {
            once: true,
        });
        modal.showModal();
    });
}

class RecordPage extends RecordForm {
    constructor(form, options) {
        const message = document.querySelector(options.message);
        super(form, { ...options, messages: [message], onSaved: () => this.load() });
        this.listMessage = message;
        this.tableElement = document.querySelector(options.table);
        this.retry = document.querySelector(options.retry);
        this.records = [];
        this.table = null;
        this.menu = document.querySelector(options.actions || "#record-actions");
        this.trigger = null;
        this.selectedId = null;
        this.deleting = false;
        this.tableElement.addEventListener("click", (event) => this.openActions(event));
        this.menu.querySelector('[data-action="edit"]')?.addEventListener("click", () => {
            const record = this.selectedRecord();
            this.closeActions();
            if (record) this.edit(record);
        });
        this.menu
            .querySelector('[data-action="delete"]')
            .addEventListener("click", () => this.remove());
        document.addEventListener("click", (event) => {
            if (!event.target.closest(".row-actions-button") && !this.menu.contains(event.target))
                this.closeActions();
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") this.closeActions(true);
            if (!this.menu.hidden && ["ArrowDown", "ArrowUp"].includes(event.key)) {
                event.preventDefault();
                const buttons = [...this.menu.querySelectorAll("button:not([hidden])")];
                const index = buttons.indexOf(document.activeElement);
                buttons[
                    (index + (event.key === "ArrowDown" ? 1 : buttons.length - 1)) % buttons.length
                ].focus();
            }
        });
        window.addEventListener("resize", () => this.closeActions());
        window.addEventListener("scroll", () => this.closeActions(), true);
        this.retry.addEventListener("click", () => this.load());
        if (options.autoLoad !== false) this.load();
    }

    selectedRecord() {
        return this.records.find((record) => String(record.id) === this.selectedId);
    }

    async load() {
        this.clearMessages();
        this.retry.hidden = true;
        try {
            this.submit.disabled = true;
            this.optionsReady = true;
            for (const source of this.options.selects || []) {
                const select = this.form.elements.namedItem(source.name);
                const selected = select.value;
                const result = await fetch(source.endpoint);
                if (!result.ok) {
                    this.optionsReady = false;
                    throw new Error("Unable to load form options.");
                }
                source.records = await result.json();
                this.fillSelect(source);
                if (!source.records.length && source.required !== false) {
                    this.optionsReady = false;
                    this.showMessage(this.listMessage, source.emptyMessage, "error");
                }
            }
            if (this.options.prerequisiteMessage && this.options.selects?.length && this.options.selects.every(source => !source.records.length)) {
                this.optionsReady = false;
                if (this.options.prerequisiteMessage) this.showMessage(this.listMessage, this.options.prerequisiteMessage, "error");
            }
            this.syncFields();
            this.submit.disabled = !this.optionsReady;
            const response = await fetch(this.options.endpoint);
            if (!response.ok) throw new Error("Unable to load records. Please retry.");
            const records = await response.json();
            const serverTime = response.headers?.get?.("X-Server-Time");
            if (this.options.numberByInitialSort) {
                const column = this.options.columns.find((item) => item.key === this.options.sortColumn);
                if (column) {
                    const comparator = DataTable.prototype;
                    records.sort((a, b) => comparator.compareValues(
                        comparator.getColumnValue(a, column),
                        comparator.getColumnValue(b, column),
                        column.type,
                    ));
                }
            }
            this.records = records.map((record, index) => ({
                ...record,
                sequence: index + 1,
            }));
            if (!this.records.length && this.optionsReady && this.options.emptyMessage) {
                this.showMessage(this.listMessage, this.options.emptyMessage, "info");
            }
            this.closeActions();
            if (this.table) this.table.setData(this.records, serverTime);
            else
                this.table = new DataTable({
                    table: this.options.table,
                    data: this.records,
                    serverTime,
                    pageSize: 10,
                    sortColumn: this.options.sortColumn || "name",
                    cellTextLimit: this.options.cellTextLimit,
                    newers: this.options.newers,
                    columns: [
                        { key: "sequence", label: "#", type: "number" },
                        ...this.options.columns,
                        {
                            key: "actions",
                            label: "Actions",
                            sortable: false,
                            searchable: false,
                            render: (record) =>
                                `<button type="button" class="row-actions-button" data-record-id="${record.id}" aria-label="Record actions" aria-haspopup="menu" aria-expanded="false"><img src="/static/icons/dots-vertical.svg" alt=""></button>`,
                        },
                    ],
                });
            return true;
        } catch (error) {
            this.showMessage(this.listMessage, "Unable to load records. Please retry.", "error");
            this.retry.hidden = false;
            if (!this.table)
                this.tableElement.querySelector("tbody td").textContent =
                    "Record list unavailable.";
            return false;
        }
    }

    closeActions(restoreFocus = false) {
        this.menu.hidden = true;
        if (this.trigger) {
            this.trigger.setAttribute("aria-expanded", "false");
            if (restoreFocus) this.trigger.focus();
        }
        this.trigger = null;
    }

    openActions(event) {
        const button = event.target.closest(".row-actions-button");
        if (!button || this.deleting) return;
        const wasOpen = this.trigger === button && !this.menu.hidden;
        this.closeActions();
        if (wasOpen) return;
        this.selectedId = button.dataset.recordId;
        this.trigger = button;
        button.setAttribute("aria-expanded", "true");
        this.menu.hidden = false;
        const rect = button.getBoundingClientRect();
        this.menu.style.left = `${Math.max(8, Math.min(rect.right - this.menu.offsetWidth, window.innerWidth - this.menu.offsetWidth - 8))}px`;
        this.menu.style.top = `${Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - this.menu.offsetHeight - 8))}px`;
        this.menu.querySelector("button:not([hidden])")?.focus();
    }

    async remove() {
        if (this.deleting) return;
        this.clearMessages();
        const record = this.selectedRecord();
        this.closeActions(true);
        if (!record) return;
        this.deleting = true;
        try {
            if (!(await confirmDeletion())) return;
            this.showMessage(this.listMessage, "Deleting record...", "info");
            const response = await fetch(`${this.options.endpoint}/${record.id}`, {
                method: "DELETE",
            });
            if (!response.ok) {
                if (response.status === 409)
                    throw new Error("Cannot delete a record with linked data.");
                if (response.status === 404)
                    throw new Error("Record no longer exists. Reload the list.");
                throw new Error("Unable to delete record. Please retry.");
            }
            if (this.recordId === record.id) this.reset();
            if (await this.load())
                this.showMessage(this.listMessage, "Record deleted successfully.", "success");
        } catch (error) {
            this.showMessage(this.listMessage, error.message, "error");
        } finally {
            this.deleting = false;
        }
    }
}
