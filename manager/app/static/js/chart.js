/* Daily stacked bars or lines with points, without external chart libraries. */
window.InventoryChart = (() => {
    const categories = [
        ["assets", "Assets", "#A78BFA"], ["targets", "Targets", "#10B981"],
        ["ports", "Ports", "#2878C7"], ["certificates", "Certificates", "#B84788"],
        ["dns", "DNS", "#D89ABE"], ["ptr", "PTR", "#DDD0FF"],
    ];
    const observers = new WeakMap();
    const ns = "http://www.w3.org/2000/svg";
    function svgElement(tag, attributes, content) {
        const element = document.createElementNS(ns, tag);
        for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
        if (content !== undefined) element.textContent = content;
        return element;
    }
    function dayLabel(day, today) {
        // These are calendar dates already grouped by the server, not UTC instants.
        const age = Math.round((Date.parse(today + "T00:00:00Z") - Date.parse(day + "T00:00:00Z")) / 86400000);
        if (age === 0) return "Today";
        if (age === 1) return "Yesterday";
        return new Intl.DateTimeFormat("en-US", {month: "short", day: "numeric", timeZone: "UTC"})
            .format(new Date(day + "T00:00:00Z"));
    }
    function draw(container, daily, generatedAt, mode, timeZone) {
        container.replaceChildren();
        if (!daily.length) return;
        const parts = new Intl.DateTimeFormat("en-US", {
            timeZone, year: "numeric", month: "2-digit", day: "2-digit",
        }).formatToParts(new Date(generatedAt));
        const dateParts = Object.fromEntries(parts.map(part => [part.type, part.value]));
        const today = `${dateParts.year}-${dateParts.month}-${dateParts.day}`;
        const totals = daily.map(day => categories.reduce((sum, [key]) => sum + Number(day.totals[key] || 0), 0));
        const peaks = mode === "lines"
            ? daily.map(day => Math.max(0, ...categories.map(([key]) => Number(day.totals[key] || 0))))
            : totals;
        const maximum = Math.max(4, Math.ceil(Math.max(0, ...peaks) / 4) * 4);
        const width = Math.max(240, container.clientWidth || 600);
        const left = 65, right = 25, top = 28, bottom = 220;
        const step = (width - left - right) / daily.length;
        const svg = svgElement("svg", {viewBox: `0 0 ${width} 275`, role: "img", "aria-label": `New inventory records by day in ${timeZone}, oldest to newest`, class: "inventory-daily-svg"});
        svg.append(svgElement("title", {}, "Daily new inventory: Assets, Targets, Ports, Certificates, DNS and PTR"));
        for (let tick = 0; tick <= 4; tick++) {
            const value = maximum * tick / 4;
            const y = bottom - (bottom - top) * tick / 4;
            svg.append(svgElement("line", {x1: left, x2: width - right, y1: y, y2: y, class: "inventory-chart-grid"}));
            svg.append(svgElement("text", {x: left - 9, y: y + 4, "text-anchor": "end", class: "inventory-chart-label"},
                new Intl.NumberFormat("en-US", {notation: "compact", maximumFractionDigits: 1}).format(value)));
        }
        const labelEvery = Math.max(1, Math.ceil(76 / step));
        if (mode === "lines") {
            for (const [key, name, color] of categories) {
                const points = daily.map((day, index) => {
                    const x = left + step * (index + 0.5);
                    const y = bottom - Number(day.totals[key] || 0) / maximum * (bottom - top);
                    return `${x},${y}`;
                }).join(" ");
                const line = svgElement("polyline", {points, fill: "none", stroke: color,
                    "stroke-width": 2, "stroke-linejoin": "round", "aria-label": name,
                    "pointer-events": "none"});
                svg.append(line);
            }
        }
        daily.forEach((day, index) => {
            const x = left + step * (index + 0.5);
            let y = bottom;
            const detail = `${day.date} — Total: ${totals[index].toLocaleString("en-US")}\n` +
                categories.map(([key, name]) => `${name}: ${Number(day.totals[key] || 0).toLocaleString("en-US")}`).join("\n");
            const group = svgElement("g", {tabindex: "0", role: "img", "aria-label": detail, class: "inventory-day"});
            group.append(svgElement("title", {}, detail));
            // Full-height transparent target also exposes zero-count days to pointer users.
            group.append(svgElement("rect", {x: x - step / 2, y: top, width: step, height: bottom - top, fill: "transparent"}));
            for (const [key, name, color] of categories) {
                const height = Number(day.totals[key] || 0) / maximum * (bottom - top);
                if (mode === "lines") {
                    const point = svgElement("circle", {cx: x, cy: bottom - height,
                        r: Math.max(1.5, Math.min(4, step * 0.15)), fill: color});
                    point.append(svgElement("title", {}, `${day.date} — ${name}: ${Number(day.totals[key] || 0).toLocaleString("en-US")}`));
                    group.append(point);
                } else {
                    y -= height;
                    if (height > 0) group.append(svgElement("rect", {x: x - step * 0.3, y, width: step * 0.6, height, fill: color}));
                }
            }
            svg.append(group);
            // Reserve room for Today (and Yesterday when there is enough space).
            const recentStart = daily.length - (step >= 76 ? 2 : 1);
            if (index >= recentStart || (index % labelEvery === 0 && index <= recentStart - labelEvery)) {
                svg.append(svgElement("text", {x, y: bottom + 24, "text-anchor": "middle", class: "inventory-chart-label"}, dayLabel(day.date, today)));
                if (mode !== "lines" && step >= 60) svg.append(svgElement("text", {x, y: Math.max(16, y - 7), "text-anchor": "middle", class: "inventory-chart-label"}, totals[index].toLocaleString("en-US")));
            }
        });
        const scroll = document.createElement("div");
        scroll.className = "inventory-chart-scroll";
        scroll.setAttribute("tabindex", "0");
        scroll.setAttribute("role", "region");
        scroll.setAttribute("aria-label", "Daily inventory chart, oldest to newest");
        scroll.append(svg);
        const legend = document.createElement("ul");
        legend.className = "inventory-chart-legend";
        for (const [, name, color] of categories) {
            const item = document.createElement("li");
            const swatch = document.createElement("span");
            swatch.style.backgroundColor = color;
            swatch.setAttribute("aria-hidden", "true");
            item.append(swatch, document.createTextNode(name));
            legend.append(item);
        }
        container.append(scroll, legend);
    }
    function render(container, daily, generatedAt, chart = "bars", timeZone = "UTC") {
        const mode = chart === "lines" ? "lines" : "bars";
        observers.get(container)?.disconnect();
        observers.delete(container);
        draw(container, daily, generatedAt, mode, timeZone);
        if (!daily.length || typeof ResizeObserver === "undefined") return;
        let width = container.clientWidth;
        const observer = new ResizeObserver(() => {
            if (container.clientWidth === width) return;
            width = container.clientWidth;
            draw(container, daily, generatedAt, mode, timeZone);
        });
        observer.observe(container);
        observers.set(container, observer);
    }
    return {render};
})();
