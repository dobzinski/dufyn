class PhysicalMap extends NetworkMap {
    constructor(modal, onSaved) {
        super(modal);
        this.onSaved = onSaved;
        this.draft = new Map();
        this.saveButton = modal.querySelector("#physical-save");
        this.deleteImageButton = modal.querySelector("#physical-delete-image");
        this.deleteImagePending = false;
        this.unplaceButton = modal.querySelector("#physical-unplace");
        this.assetList = modal.querySelector("#physical-assets");
        this.labelsVisible = true;
        this.labelsButton = modal.querySelector("#physical-labels");
        this.closeButton = modal.querySelector("#network-map-close");
        this.closeButton.classList.add("physical-close");
        this.syncCloseButton();
        this.labelsButton.addEventListener("click", () => {
            this.labelsVisible = !this.labelsVisible;
            this.syncLabels();
        });
        this.saveButton.addEventListener("click", () => this.save());
        this.deleteImageButton.addEventListener("click", () => this.stageImageDeletion());
        this.unplaceButton.addEventListener("click", () => {
            if (!this.editing || this.saving || !this.selectedId) return;
            this.draft.delete(this.selectedId);
            this.setDirty();
            this.renderPhysical();
        });
        modal.addEventListener("cancel", event => {
            event.preventDefault();
            this.requestClose();
        });
        modal.addEventListener("close", () => {
            this.dirty = false;
            this.syncCloseButton();
            this.draft.clear();
            this.plan = null;
            this.imageData = null;
            this.deleteImagePending = false;
            this.drag = this.pending = null;
            this.pointerStartedOnAsset = this.dragMoved = false;
        });
        // Pointer capture can retarget the final click to the SVG rather than the asset.
        this.svg.addEventListener("pointerdown", event => {
            this.pointerStartedOnAsset = !!event.target.closest(".network-map-node");
            this.dragMoved = false;
        }, true);
        this.svg.addEventListener("pointermove", event => {
            if (!this.drag || this.drag.pointerId !== event.pointerId) return;
            const cursor = this.coordinates(event);
            const point = {x:Math.max(0,Math.min(1,cursor.x + this.drag.offsetX)), y:Math.max(0,Math.min(1,cursor.y + this.drag.offsetY))};
            this.dragMoved = true;
            this.draft.set(this.drag.id, point);
            this.scene.querySelector(`[data-mapping-id="${this.drag.id}"]`)
                ?.setAttribute("transform", `translate(${point.x * this.plan.width} ${point.y * this.plan.height})`);
            this.renderPhysicalEdges();
            this.setDirty();
        });
        this.svg.addEventListener("pointerup", () => { this.drag = null; });
        this.svg.addEventListener("pointercancel", () => { this.drag = null; this.pointerStartedOnAsset = false; this.dragMoved = false; });
        this.svg.addEventListener("lostpointercapture", () => { this.drag = null; });
        this.svg.addEventListener("dragover", event => {
            if (this.editing && !this.saving) { event.preventDefault(); event.dataTransfer.dropEffect = "move"; }
        });
        this.svg.addEventListener("drop", event => {
            event.preventDefault();
            const id = event.dataTransfer.getData("application/x-discovery-asset");
            if (this.editing && !this.saving && this.nodes.has(id)) this.place(id, this.coordinates(event));
        });
        this.svg.addEventListener("click", event => {
            if (this.pointerStartedOnAsset) {
                this.pointerStartedOnAsset = false;
                this.dragMoved = false;
                return;
            }
            if (this.dragMoved) { this.dragMoved = false; return; }
            if (event.target.closest(".network-map-node")) return;
            if (this.editing && !this.saving && this.pending) {
                this.place(this.pending, this.coordinates(event));
                return;
            }
            this.clearSelection();
        });
    }

    syncCloseButton() {
        this.closeButton.textContent = this.dirty ? "Discard and close" : "Close";
        this.closeButton.classList.toggle("has-unsaved-changes", !!this.dirty);
        this.closeButton.disabled = !!this.saving;
    }

    syncLabels() {
        const label = this.labelsVisible ? "Hide labels" : "Show labels";
        this.labelsButton.setAttribute("aria-label", label);
        this.labelsButton.title = label;
        this.labelsButton.querySelector("[data-map-icon]").dataset.mapIcon = this.labelsVisible ? "label" : "label-off";
        this.labelsButton.setAttribute("aria-pressed", String(this.labelsVisible));
        this.scene.querySelectorAll(".physical-map-node").forEach(node => {
            node.classList.toggle("labels-hidden", !this.labelsVisible);
        });
    }

    requestClose() {
        if (this.saving) return;
        this.modal.close();
    }

    async open(viewId, opener, editing = false) {
        this.request?.abort();
        const request = new AbortController();
        this.request = request;
        this.opener = opener;
        this.viewId = viewId;
        this.editing = editing;
        this.deleteImagePending = false;
        this.modal.classList.toggle("physical-editing", editing);
        this.dirty = false;
        this.saving = false;
        this.syncCloseButton();
        this.draft.clear();
        this.nodes.clear();
        this.scene.replaceChildren();
        this.details.replaceChildren();
        this.assetList.replaceChildren();
        this.selectedId = this.pending = this.drag = null;
        this.unplaceButton.hidden = true;
        this.modal.querySelector("#network-map-selection").textContent = "Select an asset in the map.";
        this.modal.querySelector("#network-map-title").textContent = "Physical topology";
        this.modal.querySelector("#network-map-subtitle").textContent = "";
        this.body.hidden = true;
        this.controls.forEach(button => { button.disabled = true; });
        this.saveButton.hidden = !editing;
        this.deleteImageButton.hidden = !editing;
        this.modal.querySelector("#physical-save-separator").hidden = !editing;
        this.modal.querySelector("#physical-unplaced").hidden = !editing;
        showMessage(this.message, "Loading image...", "info");
        if (!this.modal.open) this.modal.showModal();
        try {
            const response = await fetch(`/api/physical/${encodeURIComponent(viewId)}`, {signal: request.signal});
            if (!response.ok) throw new Error("Unable to load physical view. Close and try again.");
            const result = await response.json();
            if (this.request !== request || !this.modal.open) return;
            this.plan = result.plan;
            this.modal.querySelector("#network-map-title").textContent = result.view.name;
            this.modal.querySelector("#network-map-subtitle").textContent = `${result.view.site_name} - ${editing ? "Edit positions" : "Read only"}`;
            if (!this.plan) throw new Error("Upload an image for this view first.");
            const image = await fetch(`/api/physical/${encodeURIComponent(viewId)}/image?revision=${this.plan.revision}`, {signal: request.signal});
            if (!image.ok) throw new Error("Image changed or is unavailable. Close and reopen the map.");
            const imageData = await this.readImage(await image.blob());
            if (this.request !== request || !this.modal.open) return;
            this.imageData = imageData;
            this.nodes = new Map(result.nodes.map(node => [String(node.id), node]));
            result.nodes.forEach(node => { if (node.x != null && node.y != null) this.draft.set(String(node.id), {x: node.x, y: node.y}); });
            this.renderPhysical();
            this.body.hidden = false;
            this.controls.forEach(button => { button.disabled = false; });
            this.saveButton.disabled = true;
            showMessage(this.message);
            this.fit();
        } catch (error) {
            if (error.name !== "AbortError" && this.request === request) showMessage(this.message, error.message, "error");
        }
    }

    readImage(blob) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(new Error("Unable to read image."));
            reader.readAsDataURL(blob);
        });
    }

    coordinates(event) {
        const rect = this.svg.getBoundingClientRect();
        return {
            x: Math.max(0, Math.min(1, (event.clientX - rect.left - this.x) / this.scale / this.plan.width)),
            y: Math.max(0, Math.min(1, (event.clientY - rect.top - this.y) / this.scale / this.plan.height)),
        };
    }

    place(id, point) {
        this.draft.set(id, point);
        this.pending = null;
        this.setDirty();
        this.renderPhysical();
        const item = this.scene.querySelector(`[data-mapping-id="${id}"]`);
        if (item) this.select(this.nodes.get(id), item);
    }

    setDirty() {
        this.dirty = true;
        this.syncCloseButton();
        this.saveButton.disabled = false;
        showMessage(this.message, "Unsaved positions.", "info");
    }

    renderPhysicalEdges() {
        const layer = this.scene.querySelector(".physical-map-edges");
        if (!layer) return;
        const fragment = document.createDocumentFragment();
        const defs = this.element("defs");
        fragment.append(defs);
        const palette = getComputedStyle(document.documentElement);
        const color = (name, fallback) => palette.getPropertyValue(name).trim() || fallback;
        const statusColor = status => status === "A" ? color("--green", "#10B981")
            : status === "I" ? color("--action-pink", "#B84788")
            : color("--text-muted", "#667087");
        for (const [id, child] of this.nodes) {
            if (child.parent_id == null) continue;
            const parentId = String(child.parent_id);
            const parent = this.nodes.get(parentId);
            const from = this.draft.get(parentId), to = this.draft.get(id);
            if (!parent || !from || !to || parentId === id) continue;
            const coordinates = {x1: from.x * this.plan.width, y1: from.y * this.plan.height,
                x2: to.x * this.plan.width, y2: to.y * this.plan.height};
            const gradientId = `physical-edge-${this.viewId}-${id}`;
            const gradient = this.element("linearGradient", {id: gradientId, gradientUnits: "userSpaceOnUse", ...coordinates});
            gradient.append(this.element("stop", {offset: "0%", "stop-color": statusColor(parent.status)}),
                this.element("stop", {offset: "100%", "stop-color": statusColor(child.status)}));
            defs.append(gradient);
            fragment.append(this.element("line", {...coordinates, class: "physical-map-edge",
                "data-parent-id": parentId, "data-child-id": id,
                stroke: `url(#${gradientId})`, "stroke-width": id === this.selectedId ? 4 : 2, "vector-effect": "non-scaling-stroke"}));
        }
        layer.replaceChildren(fragment);
    }

    renderPhysical() {
        this.bounds = {width: this.plan.width + 200, height: this.plan.height + 45};
        const fragment = document.createDocumentFragment();
        fragment.append(this.element("image", {href: this.imageData, width: this.plan.width, height: this.plan.height, preserveAspectRatio: "none"}));
        fragment.append(this.element("g", {class: "physical-map-edges", "pointer-events": "none"}));
        for (const [id, point] of this.draft) {
            const node = this.nodes.get(id);
            if (!node) continue;
            const item = this.element("g", {class: "network-map-node physical-map-node" + (this.editing ? " is-draggable" : ""), transform: `translate(${point.x * this.plan.width} ${point.y * this.plan.height})`, "data-mapping-id": id, "data-status": node.status, role: "button", tabindex: "0", "aria-pressed": "false", "aria-label": `${node.ip}, ${node.type_name}, ${this.assetName(node)}`});
            item.append(this.element("title", {}, `${node.ip}\n${node.type_name}\n${this.assetName(node)}`));
            item.append(this.element("circle", {class: "network-map-status", r: 9}));
            item.append(this.element("rect", {x: 14, y: -23, width: 176, height: 64, rx: 6}));
            item.append(this.element("text", {class: "network-map-node-title", x: 22, y: -5}, this.shorten(node.ip, 20)));
            item.append(this.element("text", {class: "network-map-node-type", x: 22, y: 14}, this.shorten(node.type_name, 24)));
            item.append(this.element("text", {x: 22, y: 32}, this.shorten(this.assetName(node), 24)));
            item.addEventListener("click", () => this.select(node, item));
            item.addEventListener("keydown", event => {
                if (event.key === "Enter" || event.key === " ") { event.preventDefault(); this.select(node, item); }
                if (this.editing && !this.saving && ["ArrowUp","ArrowDown","ArrowLeft","ArrowRight"].includes(event.key)) {
                    event.preventDefault();
                    const p = this.draft.get(id), step = event.shiftKey ? 0.02 : 0.005;
                    const x = Math.max(0,Math.min(1,p.x + (event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0)));
                    const y = Math.max(0,Math.min(1,p.y + (event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0)));
                    this.draft.set(id,{x,y}); item.setAttribute("transform",`translate(${x*this.plan.width} ${y*this.plan.height})`); this.renderPhysicalEdges(); this.setDirty();
                }
            });
            item.addEventListener("pointerdown", event => {
                if (!this.editing || this.saving || event.button !== 0) return;
                event.stopPropagation();
                this.select(node, item);
                const cursor = this.coordinates(event), position = this.draft.get(id);
                this.dragMoved = false;
                this.drag = {id, pointerId: event.pointerId, offsetX:position.x-cursor.x, offsetY:position.y-cursor.y};
                this.svg.setPointerCapture(event.pointerId);
            });
            fragment.append(item);
        }
        this.scene.replaceChildren(fragment);
        this.renderPhysicalEdges();
        this.syncLabels();
        this.assetList.replaceChildren();
        this.unplaceButton.hidden = true;
        const selected = this.selectedId && this.scene.querySelector(`[data-mapping-id="${this.selectedId}"]`);
        if (selected) this.select(this.nodes.get(this.selectedId), selected);
        else this.clearSelection();
        for (const [id,node] of this.nodes) {
            if (this.draft.has(id)) continue;
            const button = document.createElement("button");
            button.type = "button";
            button.className = "physical-asset-option" + (id === this.pending ? " is-pending" : "");
            button.draggable = this.editing;
            button.disabled = !this.editing || this.saving;
            const ip = document.createElement("strong"), name = document.createElement("span");
            ip.textContent = node.ip; name.textContent = this.assetName(node);
            button.append(ip, name);
            button.addEventListener("dragstart", event => {
                event.dataTransfer.setData("application/x-discovery-asset", id);
                event.dataTransfer.effectAllowed = "move";
            });
            button.addEventListener("click", () => { this.pending = id; this.renderPhysical(); });
            this.assetList.append(button);
        }
        if (!this.assetList.children.length) {
            const text = document.createElement("p"); text.textContent = this.nodes.size ? "All assets positioned." : "No assets associated with this view."; this.assetList.append(text);
        }
    }

    select(node, item) {
        super.select(node, item);
        this.selectedId = String(node.id);
        this.syncEdgeSelection();
        this.unplaceButton.hidden = !this.editing;
        this.unplaceButton.disabled = this.saving;
    }

    syncEdgeSelection() {
        this.scene.querySelectorAll(".physical-map-edge").forEach(edge => {
            edge.setAttribute("stroke-width", edge.getAttribute("data-child-id") === this.selectedId ? "4" : "2");
        });
    }

    clearSelection() {
        this.selectedId = null;
        this.scene.querySelectorAll(".network-map-node").forEach(item => {
            item.classList.remove("is-selected");
            item.setAttribute("aria-pressed", "false");
        });
        this.details.replaceChildren();
        this.modal.querySelector("#network-map-selection").textContent = "Select an asset in the map.";
        this.unplaceButton.hidden = true;
        this.syncEdgeSelection();
    }

    stageImageDeletion() {
        if (!this.editing || this.saving || !this.plan || this.deleteImagePending) return;
        this.deleteImagePending = true;
        this.drag = this.pending = null;
        this.body.hidden = true;
        this.setDirty();
        this.controls.forEach(button => { button.disabled = button !== this.saveButton; });
        showMessage(this.message, "Image marked for deletion. Click Save to remove the image and its asset positions.", "info");
    }

    async save() {
        if (!this.editing || !this.dirty || this.saving) return;
        this.saving = true;
        this.syncCloseButton();
        this.controls.forEach(button => { button.disabled = true; });
        this.unplaceButton.disabled = true;
        this.assetList.querySelectorAll("button").forEach(button => { button.disabled = true; });
        showMessage(this.message, this.deleteImagePending ? "Deleting image..." : "Saving positions...", "info");
        try {
            const response = this.deleteImagePending
                ? await fetch(`/api/physical/${encodeURIComponent(this.viewId)}/image?${new URLSearchParams({revision: this.plan.revision, updated_at: this.plan.updated_at})}`, {method: "DELETE"})
                : await fetch(`/api/physical/${encodeURIComponent(this.viewId)}/positions`, {
                    method: "PUT", headers: {"Content-Type":"application/json"},
                    body: JSON.stringify({revision: this.plan.revision, updated_at: this.plan.updated_at,
                        positions: [...this.draft].map(([id,p]) => ({id: Number(id), x:p.x, y:p.y}))}),
                });
            if (!response.ok) {
                const error = await response.json();
                throw new Error(typeof error.detail === "string" ? error.detail : this.deleteImagePending ? "Unable to delete image. Please retry." : "Unable to save positions.");
            }
            const result = await response.json();
            const removed = this.deleteImagePending;
            if (removed) {
                this.plan = this.imageData = null;
                this.deleteImagePending = false;
                this.draft.clear();
                this.scene.replaceChildren();
                this.assetList.replaceChildren();
                this.clearSelection();
                this.body.hidden = true;
            } else {
                this.plan.revision = result.revision;
                this.plan.updated_at = result.updated_at;
            }
            this.dirty = false;
            this.syncCloseButton();
            showMessage(this.message, removed ? "Image deleted successfully." : "Positions saved.", "success");
            await this.onSaved();
        } catch (error) {
            showMessage(this.message, error.message, "error");
        } finally {
            this.saving = false;
            this.syncCloseButton();
            const unavailable = !this.plan || this.deleteImagePending;
            this.controls.forEach(button => { button.disabled = unavailable; });
            this.saveButton.disabled = !this.dirty;
            this.unplaceButton.disabled = unavailable;
            this.assetList.querySelectorAll("button").forEach(button => { button.disabled = unavailable; });
        }
    }
}

class PhysicalViewPage extends TopologyViewPage {
    constructor(form, options, map) {
        super(form, options, map);
        this.fileInput = form.elements.image;
        this.fileButton = form.querySelector("#background-image-file-button");
        this.fileName = form.querySelector("#background-image-file-name");
        this.fileButton.addEventListener("click", () => this.fileInput.click());
        this.fileInput.addEventListener("change", () => this.syncFileName());
        form.addEventListener("reset", () => setTimeout(() => this.syncFileName(), 0));
    }

    syncFileName() {
        this.fileName.textContent = this.fileInput.files[0]?.name || "No file selected";
    }

    reset() {
        super.reset();
        this.syncFileName();
    }

    edit(record) {
        super.edit(record);
        this.syncFileName();
    }

    async request(url, method, data) {
        const file = this.fileInput.files[0];
        if (file && file.size > 10*1024*1024) throw new Error("Images must be at most 10 MB");
        const body = new FormData();
        body.set("site_id", data.site_id);
        body.set("name", data.name);
        body.set("revision", String(this.records.find(record => record.id === this.recordId)?.revision || 0));
        if (file) body.set("image", file);
        return fetch(url, {method, body});
    }
}

const physicalMap = new PhysicalMap(document.getElementById("network-map-modal"), () => physicalPage.load());
const physicalPage = new PhysicalViewPage(document.getElementById("physical-form"), {
    newers: {field: "created_at"},
    endpoint: "/api/physical", table: "#physical-table", message: "#physical-message", retry: "#physical-retry", sortColumn: "name",
    selects: [{name: "site_id", endpoint: "/api/sites", placeholder: "Select ...", emptyMessage: "Create a site before adding views."}],
    columns: [
        {key:"site_name",label:"Site"}, {key:"name",label:"Name"},
        {key:"mapping_count",label:"Assoc.",type:"number"},
        {key:"positioned_count",label:"Pos.",type:"number"},
        {key:"has_plan",label:"Image",searchValue: row => row.has_plan ? "Yes" : "No",render: row => `<span class="status-badge ${row.has_plan ? "yes" : "no"}">${row.has_plan ? "Yes" : "No"}</span>`},
        {key:"updated_at",label:"Updated",type:"date",render: row => row.updated_at ? formatDate(row.updated_at) : "\u2014"},
    ],
}, physicalMap);
