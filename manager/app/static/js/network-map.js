/* SVG hierarchy viewer: no form dependencies or inventory mutations. */
class NetworkMap {
    constructor(modal, options = {}) {
        this.options = options;
        this.modal = modal;
        this.svg = modal.querySelector("#network-map-svg");
        this.scene = modal.querySelector("#network-map-scene");
        this.viewport = modal.querySelector("#network-map-viewport");
        this.body = modal.querySelector("#network-map-body");
        this.message = modal.querySelector("#network-map-message");
        this.details = modal.querySelector("#network-map-details");
        this.controls = [...modal.querySelectorAll(".network-map-toolbar button")];
        this.scale = 1;
        this.x = this.y = 0;
        this.nodes = new Map();
        modal.querySelector("#network-map-close").addEventListener("click", () => this.requestClose());
        modal.addEventListener("close", () => {
            this.request?.abort();
            this.request = null;
            this.pan = null;
            this.svg.classList.remove("is-panning");
            this.scene.replaceChildren();
            this.nodes.clear();
            if (this.opener?.isConnected) this.opener.focus();
        });
        modal.querySelector("#network-map-zoom-in").addEventListener("click", () => this.zoom(1.25));
        modal.querySelector("#network-map-zoom-out").addEventListener("click", () => this.zoom(0.8));
        modal.querySelector("#network-map-fit").addEventListener("click", () => this.fit());
        modal.querySelector("#network-map-download").addEventListener("click", () => this.download());
        modal.querySelector("#network-map-fullscreen").addEventListener("click", event => {
            const expanded = modal.classList.toggle("is-fullscreen");
            event.currentTarget.setAttribute("aria-pressed", String(expanded));
            const label = expanded ? "Exit full screen" : "Full screen";
            event.currentTarget.setAttribute("aria-label", label);
            event.currentTarget.title = label;
            event.currentTarget.querySelector("[data-map-icon]").dataset.mapIcon = expanded ? "window-minimize" : "window-maximize";
            this.fit();
        });
        this.svg.addEventListener("wheel", event => {
            if (!this.scene.childElementCount) return;
            event.preventDefault();
            const rect = this.svg.getBoundingClientRect();
            this.zoom(event.deltaY < 0 ? 1.15 : 1 / 1.15, event.clientX - rect.left, event.clientY - rect.top);
        }, {passive: false});
        this.svg.addEventListener("pointerdown", event => {
            if (event.button !== 0 || event.target.closest(".network-map-node") || !this.scene.childElementCount) return;
            this.pan = {id: event.pointerId, x: event.clientX, y: event.clientY, startX: this.x, startY: this.y};
            this.svg.setPointerCapture(event.pointerId);
            this.svg.classList.add("is-panning");
        });
        this.svg.addEventListener("pointermove", event => {
            if (!this.pan || this.pan.id !== event.pointerId) return;
            this.x = this.pan.startX + event.clientX - this.pan.x;
            this.y = this.pan.startY + event.clientY - this.pan.y;
            this.transform();
        });
        const stopPan = () => { this.pan = null; this.svg.classList.remove("is-panning"); };
        this.svg.addEventListener("pointerup", stopPan);
        this.svg.addEventListener("pointercancel", stopPan);
        this.svg.addEventListener("lostpointercapture", stopPan);
        if (typeof ResizeObserver !== "undefined") {
            this.observer = new ResizeObserver(() => { if (modal.open && this.scene.childElementCount) this.fit(); });
            this.observer.observe(this.viewport);
        }
    }

    requestClose() {
        this.modal.close();
    }

    async open(viewId, opener) {
        this.request?.abort();
        const request = new AbortController();
        this.request = request;
        this.opener = opener;
        this.nodes.clear();
        this.scene.replaceChildren();
        this.details.replaceChildren();
        this.modal.querySelector("#network-map-selection").textContent = this.options.selectionMessage || "Select an asset in the map.";
        this.modal.querySelector("#network-map-title").textContent = this.options.title || "Logical topology";
        this.modal.querySelector("#network-map-subtitle").textContent = "";
        this.body.hidden = true;
        this.controls.forEach(button => { button.disabled = true; });
        showMessage(this.message, "Loading topology...", "info");
        if (!this.modal.open) this.modal.showModal();
        try {
            const response = await fetch(this.options.endpoint ? this.options.endpoint(viewId) : `/api/logical/${encodeURIComponent(viewId)}`, {signal: request.signal});
            if (!response.ok) throw new Error(response.status === 404 ? (this.options.missingMessage || "Logical view no longer exists.") : "Unable to load topology. Close and try again.");
            const result = await response.json();
            if (this.request !== request || !this.modal.open) return;
            this.modal.querySelector("#network-map-title").textContent = result.view.name;
            this.modal.querySelector("#network-map-subtitle").textContent = this.options.subtitle ? this.options.subtitle(result) : `${result.view.site_name} \u00b7 ${result.nodes.length} assets`;
            if (!result.nodes.length) {
                showMessage(this.message, (this.options.emptyMessage || "No assets associated with this view. Add associations first."), "info");
                return;
            }
            this.render(result.nodes, result);
            this.body.hidden = false;
            this.controls.forEach(button => { button.disabled = false; });
            showMessage(this.message);
            this.fit();
        } catch (error) {
            if (error.name !== "AbortError" && this.request === request) showMessage(this.message, error.message, "error");
        }
    }

    element(tag, attributes = {}, text) {
        const item = document.createElementNS("http://www.w3.org/2000/svg", tag);
        for (const [name, value] of Object.entries(attributes)) item.setAttribute(name, value);
        if (text !== undefined) item.textContent = text;
        return item;
    }

    layout(records) {
        const width = 220, height = 94, gap = 40, row = 154;
        this.nodes = new Map(records.map(record => [String(record.id), {...record, children: [], width}]));
        const roots = [];
        for (const node of this.nodes.values()) {
            const parent = node.parent_id == null ? null : this.nodes.get(String(node.parent_id));
            if (parent) parent.children.push(node);
            else roots.push(node);
        }
        // Iterative postorder keeps deep valid hierarchies off the call stack.
        const stack = roots.map(node => [node, false]);
        const visited = new Set();
        while (stack.length) {
            const [node, ready] = stack.pop();
            if (ready) {
                if (node.children.length) node.width = Math.max(width, node.children.reduce((sum, child) => sum + child.width, 0) + gap * (node.children.length - 1));
            } else {
                if (visited.has(node.id)) throw new Error("Invalid topology hierarchy.");
                visited.add(node.id);
                stack.push([node, true]);
                node.children.forEach(child => stack.push([child, false]));
            }
        }
        if (visited.size !== this.nodes.size) throw new Error("Invalid topology hierarchy.");
        const queue = [];
        let left = 0;
        roots.forEach(node => { queue.push({node, left, depth: 0}); left += node.width + gap; });
        this.bounds = {width: Math.max(width, left - gap), height};
        for (let index = 0; index < queue.length; index++) {
            const {node, left, depth} = queue[index];
            node.x = left + (node.width - width) / 2;
            node.y = depth * row;
            this.bounds.height = Math.max(this.bounds.height, node.y + height);
            let childLeft = left;
            node.children.forEach(child => { queue.push({node: child, left: childLeft, depth: depth + 1}); childLeft += child.width + gap; });
        }
    }

    edgeGradient(defs, id, source, destination, x1, y1, x2, y2) {
        const palette = getComputedStyle(document.documentElement);
        const color = (name, fallback) => palette.getPropertyValue(name).trim() || fallback;
        const statusColor = status => status === "A" ? color("--green", "#10B981")
            : status === "I" ? color("--action-pink", "#B84788")
            : color("--text-muted", "#667087");
        const gradient = this.element("linearGradient", {id, gradientUnits: "userSpaceOnUse", x1, y1, x2, y2});
        gradient.append(this.element("stop", {offset: "0%", "stop-color": statusColor(source.status)}),
            this.element("stop", {offset: "100%", "stop-color": statusColor(destination.status)}));
        defs.append(gradient);
        return `url(#${id})`;
    }

    render(records) {
        this.layout(records);
        const fragment = document.createDocumentFragment();
        const defs = this.element("defs");
        fragment.append(defs);
        for (const node of this.nodes.values()) {
            const parent = node.parent_id == null ? null : this.nodes.get(String(node.parent_id));
            if (parent) {
                const px = parent.x + 110, py = parent.y + 94, cx = node.x + 110, cy = node.y;
                const gradientId = `logical-edge-${node.id}`;
                const stroke = this.edgeGradient(defs, gradientId, parent, node, px, py, cx, cy);
                fragment.append(this.element("path", {class: "network-map-edge", d: `M ${px} ${py} V ${(py + cy) / 2} H ${cx} V ${cy}`,
                    style: `stroke:${stroke};stroke-width:2;opacity:1`, "pointer-events": "none", "vector-effect": "non-scaling-stroke"}));
            }
        }
        for (const node of this.nodes.values()) {
            const name = this.assetName(node);
            const item = this.element("g", {class: "network-map-node", transform: `translate(${node.x} ${node.y})`, tabindex: "0", role: "button", "aria-label": `${name}, ${node.type_name}, ${node.ip}`, "aria-pressed": "false", "data-status": node.status});
            const tooltip = [...new Set([node.ip, node.type_name, name === "\u2014" ? null : name].filter(Boolean))].join("\n");
            item.setAttribute("aria-label", tooltip.replaceAll("\n", ", "));
            item.append(this.element("title", {}, tooltip));
            item.append(this.element("rect", {width: 220, height: 94, rx: 10}));
            item.append(this.element("circle", {class: "network-map-status", cx: 201, cy: 18, r: 4}));
            item.append(this.element("text", {class: "network-map-node-title", x: 14, y: 26}, this.shorten(node.ip, 23)));
            item.append(this.element("text", {class: "network-map-node-type", x: 14, y: 49}, this.shorten(node.type_name, 28)));
            item.append(this.element("text", {x: 14, y: 73}, this.shorten(this.nodeFooter(node), 28)));
            item.addEventListener("click", () => this.select(node, item));
            item.addEventListener("keydown", event => {
                if (event.key === "Enter" || event.key === " ") { event.preventDefault(); this.select(node, item); }
            });
            fragment.append(item);
        }
        this.scene.replaceChildren(fragment);
    }

    nodeFooter(node) {
        return this.assetName(node);
    }

    assetName(node) {
        return node.display_name?.trim() || node.discovered_hostname?.trim() || "\u2014";
    }

    shorten(text, length) {
        return text.length > length ? text.slice(0, length - 1) + "\u2026" : text;
    }

    clearSelection() {
        this.scene.querySelectorAll(".is-selected").forEach(item => {
            item.classList.remove("is-selected");
            item.setAttribute("aria-pressed", "false");
        });
        this.details.replaceChildren();
        this.modal.querySelector("#network-map-selection").textContent = this.options.selectionMessage || "Select an asset in the map.";
    }

    select(node, item) {
        this.scene.querySelectorAll(".is-selected").forEach(other => {
            other.classList.remove("is-selected"); other.setAttribute("aria-pressed", "false");
        });
        item.classList.add("is-selected");
        item.setAttribute("aria-pressed", "true");
        this.modal.querySelector("#network-map-selection").textContent = node.ip;
        this.details.replaceChildren();
        const parent = node.parent_id == null ? null : this.nodes.get(String(node.parent_id));
        const fields = {
            "IP Address": node.ip, "Type": node.type_name,
            ...(node.kind === "target" ? {} : {"OS": node.operating_system_name}),
            "Name": this.assetName(node),
            "Status": ({A: "Active", I: "Inactive", U: "Unknown"})[node.status] || node.status,
            "Parent": parent ? parent.display_name || parent.discovered_hostname || parent.ip : "None (root)",
            "Last Seen": node.last_seen ? formatDate(node.last_seen) : null,
            "Latency": node.latency_ms == null ? null : `${node.latency_ms} ms`,
        };
        for (const [label, value] of Object.entries(fields)) {
            const dt = document.createElement("dt"), dd = document.createElement("dd");
            dt.textContent = label;
            if (label === "Status") {
                const badge = document.createElement("span");
                badge.className = "status-badge " + ({A:"active",I:"inactive",U:"unknown"}[node.status] || "unknown");
                badge.textContent = value || "Unknown";
                dd.append(badge);
            } else {
                dd.textContent = value || "\u2014";
            }
            this.details.append(dt, dd);
        }
        this.renderPorts(node.ports || []);
    }

    renderPorts(ports) {
        const label = document.createElement("dt"), value = document.createElement("dd");
        label.textContent = "Ports";
        label.className = "network-map-ports-title";
        value.className = "network-map-ports";
        if (!ports.length) value.textContent = "No recorded ports.";
        if (ports.length) {
            const table = document.createElement("table");
            table.className = "network-map-port-table";
            const head = document.createElement("thead"), header = document.createElement("tr");
            for (const title of ["Port / Protocol", "Status"]) {
                const cell = document.createElement("th");
                cell.scope = "col";
                cell.textContent = title;
                header.append(cell);
            }
            head.append(header);
            const body = document.createElement("tbody");
            for (const port of ports) {
                const row = document.createElement("tr");
                row.className = "network-map-port";
                const number = document.createElement("td");
                number.textContent = `${port.port}/${port.protocol}`;
                const status = document.createElement("td");
                const badge = document.createElement("span");
                badge.className = "status-badge " + (port.state === "open" ? "open" : "closed");
                badge.textContent = port.state;
                status.append(badge);
                row.append(number, status);
                body.append(row);
            }
            table.append(head, body);
            value.append(table);
        }
        this.details.append(label, value);
    }

    exportSVG() {
        if (!this.scene.childElementCount || !this.bounds) return null;
        const width = Math.ceil(this.bounds.width + 64), height = Math.ceil(this.bounds.height + 120);
        const svg = this.element("svg", {width, height, viewBox: `0 0 ${width} ${height}`, role: "img"});
        const title = this.modal.querySelector("#network-map-title").textContent;
        svg.append(this.element("title", {}, title));
        const palette = getComputedStyle(document.documentElement);
        const color = (name, fallback) => palette.getPropertyValue(name).trim() || fallback;
        const text = color("--text-primary", "#172033"), muted = color("--text-secondary", "#566078");
        const surface = color("--surface", "#FFFFFF"), border = color("--border", "#DDE0ED");
        const green = color("--green", "#10B981"), pink = color("--action-pink", "#B84788");
        const unknown = color("--text-muted", "#667087");
        const style = this.element("style", {}, `
            text { fill: ${text}; font-family: Arial, sans-serif; font-size: 12px; }
            .network-map-edge { fill: none; stroke: ${muted}; stroke-width: 1.5; opacity: 0.6; }
            .network-map-node rect { fill: ${surface}; stroke: ${border}; stroke-width: 1.5; }
            .physical-map-node rect { fill-opacity: 0.5; }
            .physical-map-node.labels-hidden rect, .physical-map-node.labels-hidden text { display: none; }
            .network-map-node-title { font-size: 14px; font-weight: 600; }
            .network-map-node-type { fill: ${muted}; }
            .network-map-status { fill: ${unknown}; }
            .network-map-node[data-status="A"] .network-map-status { fill: ${green}; }
            .network-map-node[data-status="I"] .network-map-status { fill: ${pink}; }
        `);
        svg.append(style);
        svg.append(this.element("rect", {width, height, fill: color("--background", "#F5F6FC")}));
        svg.append(this.element("text", {x: 32, y: 30, style: "font-size:18px;font-weight:600"}, title));
        svg.append(this.element("text", {x: 32, y: 52, style: `fill:${muted}`}, this.modal.querySelector("#network-map-subtitle").textContent));
        const scene = this.scene.cloneNode(true);
        scene.removeAttribute("id");
        // Export the full layout, independent of the current viewport pan/zoom.
        scene.setAttribute("transform", "translate(32 88)");
        scene.querySelectorAll(".network-map-node").forEach(node => {
            node.classList.remove("is-selected");
            node.removeAttribute("tabindex");
            node.removeAttribute("role");
            node.removeAttribute("aria-pressed");
        });
        scene.querySelectorAll(".physical-map-edge").forEach(edge => edge.setAttribute("stroke-width", "2"));
        svg.append(scene);
        return new XMLSerializer().serializeToString(svg);
    }

    download() {
        const source = this.exportSVG();
        if (!source) return;
        const url = URL.createObjectURL(new Blob([source], {type: "image/svg+xml;charset=utf-8"}));
        const link = document.createElement("a");
        const name = this.modal.querySelector("#network-map-title").textContent
            .normalize("NFKD").replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 100) || "topology";
        link.href = url;
        link.download = name + ".svg";
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    transform() {
        this.scene.setAttribute("transform", `translate(${this.x} ${this.y}) scale(${this.scale})`);
        this.modal.querySelector("#network-map-zoom").textContent = `${Math.round(this.scale * 100)}%`;
    }

    fit() {
        if (!this.scene.childElementCount || !this.bounds) return;
        const width = this.viewport.clientWidth || 800, height = this.viewport.clientHeight || 500;
        this.scale = Math.min(1, Math.max(0.01, Math.min((width - 48) / this.bounds.width, (height - 48) / this.bounds.height)));
        this.x = (width - this.bounds.width * this.scale) / 2;
        this.y = (height - this.bounds.height * this.scale) / 2;
        this.transform();
    }

    zoom(factor, anchorX = this.viewport.clientWidth / 2, anchorY = this.viewport.clientHeight / 2) {
        if (!this.scene.childElementCount) return;
        const next = Math.max(0.01, Math.min(3, this.scale * factor));
        const ratio = next / this.scale;
        this.x = anchorX - (anchorX - this.x) * ratio;
        this.y = anchorY - (anchorY - this.y) * ratio;
        this.scale = next;
        this.transform();
    }
}
