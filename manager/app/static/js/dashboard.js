function formatCount(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "\u2014";
    const suffixes = ["", "K", "M", "B", "T", "Q", "Qi"];
    let scaled = Math.abs(number);
    let unit = 0;
    while (scaled >= 1000 && unit < suffixes.length - 1) {
        scaled /= 1000;
        unit++;
    }
    let rounded = Number(scaled.toPrecision(3));
    if (rounded >= 1000 && unit < suffixes.length - 1) {
        rounded /= 1000;
        unit++;
    }
    return `${number < 0 ? "-" : ""}${rounded}${suffixes[unit]}`;
}

let certificateAttentionTable = null;
let ptrAttentionTable = null;
let certificateAttentionCount = null;
let ptrAttentionCount = null;

function updateNeedAttention() {
    const complete = certificateAttentionCount !== null && ptrAttentionCount !== null;
    document.getElementById("summary-attention").textContent = complete
        ? formatCount(certificateAttentionCount + ptrAttentionCount) : "\u2014";
}

async function loadDashboard() {
    await Promise.all([loadSummary(), loadCertificateAttention(), loadPtrAttention(), loadNewers(), loadTopPorts(), loadTopOperatingSystems()]);
}

async function loadSummary() {
    try {
        const response = await fetch("/api/summary");

        if (!response.ok) {
            throw new Error("Unable to load dashboard summary");
        }

        const data = await response.json();

        updateSummary(data);
    } catch (error) {
        console.error("Failed to load dashboard summary:", error);

        showSummaryError();
    }
}

function updateSummary(data) {
    for (const group of [data.assets, data.targets, data.ports, data.certificates, data.targets?.http_status, data.certificates?.connections]) {
        if (!group) continue;
        for (const key of Object.keys(group)) {
            if (group[key] == null) group[key] = 0;
        }
    }
    const assets = data.assets;

    const ports = data.ports;
    const targets = data.targets;

    const certificates = data.certificates;

    certificateAttentionCount = Number(certificates.warning) + Number(certificates.critical) + Number(certificates.expired);

    /*
     * Assets
     */

    document.getElementById("summary-assets").textContent = formatCount(assets.total);

    /*
     * Open ports
     */

    document.getElementById("summary-ports").textContent = formatCount(ports.total);

    /*
     * Certificates
     */

    document.getElementById("summary-certificates").textContent = formatCount(certificates.total);

    /*
     * Attention
     */

    updateNeedAttention();

    document.getElementById("summary-targets").textContent = formatCount(targets.total);
    updateDistributionChart("target", targets, [["active", "Active", "var(--green)"], ["inactive", "Inactive", "var(--action-pink)"], ["unknown", "Unknown", "var(--text-muted-dark)"]]);
    updateBinaryChart("port", ports);
    updateDistributionChart("target-http", targets.http_status, [
        ["success", "2xx", "var(--green)"],
        ["redirect", "3xx", "var(--action-blue)"],
        ["client_error", "4xx", "var(--chart-warning)"],
        ["server_error", "5xx", "var(--action-pink)"],
        ["other", "Other", "var(--highlight)"],
        ["unchecked", "Unchecked", "var(--text-muted-dark)"],
    ]);
    updateDistributionChart("port-types", ports, [
        ["tcp", "TCP", "var(--primary)"],
        ["udp", "UDP", "var(--action-blue)"],
    ]);
    updateAssetChart(assets);

    updateCertificateChart(certificates);
}

function showSummaryError() {
    certificateAttentionCount = null;
    updateNeedAttention();
    const values = document.querySelectorAll(".summary-card .card-value");

    const descriptions = document.querySelectorAll(".summary-card .card-description");

    values.forEach(function (element) {
        element.textContent = "\u2014";
    });

    descriptions.forEach(function (element) {
        element.textContent = "Unable to load data";
    });
}

function updateDistributionChart(name, counts, segments) {
    const values = Object.fromEntries(segments.map(([key]) => [key, Number(counts?.[key] ?? 0)]));
    const total = segments.reduce((sum, [key]) => sum + values[key], 0);
    const legend = document.getElementById(`${name}-legend`);
    legend.replaceChildren();
    let end = 0;
    const stops = segments.map(([key, label, color]) => {
        const start = end;
        end += total > 0 ? (values[key] / total) * 100 : 0;
        const item = document.createElement("div");
        item.className = "legend-item";
        const swatch = document.createElement("span");
        swatch.className = "legend-color";
        swatch.style.background = color;
        const text = document.createElement("span");
        text.className = "legend-label";
        text.textContent = label;
        const value = document.createElement("strong");
        value.textContent = values[key];
        item.append(swatch, text, value);
        legend.appendChild(item);
        return `${color} ${start}% ${end}%`;
    });
    document.getElementById(`${name}-chart`).style.background = total > 0
        ? `conic-gradient(${stops.join(", ")})` : "var(--text-muted-dark)";
    document.getElementById(`${name}-total`).textContent = formatCount(total);
}

function updateBinaryChart(name, counts) {
    const chart = document.getElementById(`${name}-status-chart`);
    if (!chart) return;
    const openEnd = counts.total > 0 ? (counts.open / counts.total) * 100 : 0;
    const closedEnd = counts.total > 0 ? ((counts.open + counts.closed) / counts.total) * 100 : 0;
    chart.style.background = counts.total > 0
        ? `conic-gradient(var(--green) 0% ${openEnd}%, var(--action-pink) ${openEnd}% ${closedEnd}%, var(--text-muted-dark) ${closedEnd}% 100%)`
        : "var(--text-muted-dark)";
    for (const key of ["total", "open", "closed"]) {
        document.getElementById(`${name}-chart-${key}`).textContent = key === "total" ? formatCount(counts[key]) : (counts[key] ?? 0);
    }
}

function updateAssetChart(assets) {
    const chart = document.getElementById("asset-status-chart");

    if (!chart) {
        return;
    }

    const total = assets.total;

    const activeEnd = total > 0 ? (assets.active / total) * 100 : 0;
    const inactiveEnd = total > 0 ? ((assets.active + assets.inactive) / total) * 100 : 0;

    chart.style.background = total > 0 ? `conic-gradient(
        var(--green) 0% ${activeEnd}%,
        var(--action-pink) ${activeEnd}% ${inactiveEnd}%,
        var(--text-muted-dark) ${inactiveEnd}% 100%
    )` : "var(--text-muted-dark)";

    document.getElementById("asset-chart-total").textContent = formatCount(total);

    document.getElementById("asset-chart-active").textContent = assets.active;

    document.getElementById("asset-chart-inactive").textContent = assets.inactive;
    document.getElementById("asset-chart-unknown").textContent = assets.unknown;
}

function updateCertificateChart(certificates) {
    const chart = document.getElementById("certificate-status-chart");

    if (!chart) {
        return;
    }

    const total = certificates.total;

    let validPercentage = 0;
    let warningPercentage = 0;
    let criticalPercentage = 0;
    let expiredPercentage = 0;

    if (total > 0) {
        validPercentage = (certificates.valid / total) * 100;

        warningPercentage = (certificates.warning / total) * 100;

        criticalPercentage = (certificates.critical / total) * 100;

        expiredPercentage = (certificates.expired / total) * 100;
    }

    const validEnd = validPercentage;

    const warningEnd = validEnd + warningPercentage;

    const criticalEnd = warningEnd + criticalPercentage;

    const expiredEnd = criticalEnd + expiredPercentage;

    if (total > 0) {
        chart.style.background = `conic-gradient(
                var(--green)
                    0% ${validEnd}%,

                var(--action-blue)
                    ${validEnd}% ${warningEnd}%,

                var(--action-pink)
                    ${warningEnd}% ${criticalEnd}%,

                var(--text-muted-dark)
                    ${criticalEnd}% ${expiredEnd}%
            )`;
    } else {
        chart.style.background = "var(--text-muted-dark)";
    }

    document.getElementById("certificate-chart-total").textContent = formatCount(total);

    document.getElementById("certificate-chart-valid").textContent = certificates.valid;

    document.getElementById("certificate-chart-warning").textContent = certificates.warning;

    document.getElementById("certificate-chart-critical").textContent = certificates.critical;

    document.getElementById("certificate-chart-expired").textContent = certificates.expired;
}

async function loadCertificateAttention() {
    try {
        const response = await fetch("/api/certificates");

        if (!response.ok) {
            throw new Error("Unable to load certificates");
        }

        const certificates = await response.json();

        updateCertificateAttention(certificates);
    } catch (error) {
        console.error("Failed to load certificates:", error);

        showCertificateAttentionError();
    }
}

function updateCertificateAttention(certificates) {
    /*
     * Only certificates that
     * require attention.
     */

    const attentionCertificates = certificates.filter(function (certificate) {
        return (
            certificate.status === "warning" ||
            certificate.status === "critical" ||
            certificate.status === "expired"
        );
    });

    /*
     * Initial table creation.
     */

    if (!certificateAttentionTable) {
        certificateAttentionTable = new DataTable({
            table: "#certificate-attention-table",

            data: attentionCertificates,

            pageSize: 10,

            sortColumn: "days_remaining",

            sortDirection: "asc",

            columns: [
                { key: "site_name", label: "Site" },
                { key: "owner", label: "Owner", value: (record) => record.target?.fqdn || [record.asset?.ip, record.asset?.name].filter(Boolean).join(" | ") || "Desconhecido" },
                { key: "port", label: "Port", type: "number", value: (record) => record.port?.port ?? null, searchValue: (record) => record.port?.port ?? "Internal", render: (record) => record.port?.port == null ? '<span class="status-badge default">Internal</span>' : `<span class="status-badge ${record.port.protocol === 'UDP' ? 'udp' : 'tcp'}">${escapeHtml(record.port.port)}</span>` },
                { key: "protocol", label: "Protocol", hidden: true, value: (record) => record.port?.protocol || "" },
                { key: "subject_cn", label: "CN", value: (record) => record.subject_cn || "\u2014", render: (record) => `<span class="certificate-name">${escapeHtml(record.subject_cn || "\u2014")}</span>` },
                { key: "valid_until", label: "Expiration", type: "date", render: (record) => formatDate(record.valid_until) },
                { key: "days_remaining", label: "Remaining", type: "number", render: (record) => formatRemainingDays(record.days_remaining) },
                { key: "status", label: "Status", render: (record) => `<span class="status-badge ${escapeHtml(record.status)}">${escapeHtml(record.status)}</span>` },
            ],
        });

        return;
    }

    /*
     * Table already exists.
     * Only update its data.
     */

    certificateAttentionTable.setData(attentionCertificates);
}

function showCertificateAttentionError() {
    const tableBody = document.getElementById("certificate-attention-body");

    if (!tableBody) {
        return;
    }

    tableBody.innerHTML = `
        <tr>

            <td
                colspan="7"
                class="table-message"
            >
                Unable to load certificate data.
            </td>

        </tr>
    `;
}

function renderPtrBadge(value) {
    const styles = {
        Match: "match", Mismatch: "mismatch", Unnamed: "unnamed",
        Unregistered: "unregistered", Undiscovered: "undiscovered", Inactive: "inactive",
    };
    return `<span class="status-badge ${styles[value] || "default"}">${escapeHtml(value)}</span>`;
}

function ptrAttentionReasons(record) {
    return record.attention_reasons || [record.undiscovered ? "Undiscovered" : "", record.asset_status === "Inactive" ? "Inactive" : "", record.comparison === "Mismatch" ? "Mismatch" : ""].filter(Boolean);
}

async function loadPtrAttention() {
    const retry = document.getElementById("ptr-attention-retry");
    retry.hidden = true;
    try {
        const response = await fetch("/api/dns/ptr/attention");
        if (!response.ok) throw new Error("Unable to load PTR alerts");
        const records = await response.json();
        ptrAttentionCount = records.length;
        updateNeedAttention();
        if (ptrAttentionTable) {
            ptrAttentionTable.setData(records);
        } else {
            ptrAttentionTable = new DataTable({
                table: "#ptr-attention-table", data: records, pageSize: 10,
                sortColumn: "ip", sortDirection: "asc",
                columns: [
                    { key: "site_name", label: "Site" },
                    { key: "ip", label: "IP" },
                    { key: "ptr_names", label: "PTR Names", value: r => r.ptr_names.join(", ") },
                    { key: "expected_names", label: "Expected Names", value: r => r.expected_names.join(", ") || "\u2014" },
                    { key: "comparison", label: "Comparison", render: r => renderPtrBadge(r.comparison) },
                    { key: "reason", label: "Attention", value: r => ptrAttentionReasons(r).join("; "), render: r => ptrAttentionReasons(r).map(renderPtrBadge).join(" ") },
                    { key: "checked_at", label: "Checked", type: "date", render: r => formatDate(r.checked_at) },
                ],
            });
        }
    } catch (error) {
        console.error("Failed to load PTR alerts:", error);
        ptrAttentionCount = null;
        updateNeedAttention();
        const cell = document.createElement("td");
        cell.colSpan = 7;
        cell.className = "table-message";
        cell.textContent = "Unable to load PTR alerts.";
        const row = document.createElement("tr");
        row.append(cell);
        document.getElementById("ptr-attention-body").replaceChildren(row);
        retry.hidden = false;
    }
}

async function loadNewers() {
    const message = document.getElementById("newers-message");
    const retry = document.getElementById("newers-retry");
    const chart = document.getElementById("newers-chart");
    retry.hidden = true;
    showMessage(message, "Loading recent inventory...", "info");
    try {
        const response = await fetch("/api/newers/totals", {cache: "no-store"});
        if (!response.ok) throw new Error("Unable to load recent inventory.");
        const data = await response.json();
        if (!data.enabled) {
            chart.replaceChildren();
            showMessage(message, "Recent inventory is disabled (newers.days: 0).", "info");
            return;
        }
        const categories = {assets: "Assets", targets: "Targets", ports: "Ports", certificates: "Certificates", dns: "DNS", ptr: "PTR"};
        const items = Object.entries(categories).map(([key, name]) => ({name, total: data.totals[key]}));
        renderTopRanking(items, {
            chartId: "newers-chart", messageId: "newers-message",
            label: item => item.name,
        });
        if (items.every(item => Number(item.total) === 0)) {
            showMessage(message, "No new data in this period.", "info");
        }
    } catch (error) {
        chart.replaceChildren();
        showMessage(message, "Unable to load recent inventory.", "error");
        retry.hidden = false;
    }
}

async function loadTopPorts() {
    const message = document.getElementById("top-ports-message");
    const retry = document.getElementById("top-ports-retry");
    const chart = document.getElementById("top-ports-chart");
    retry.hidden = true;
    showMessage(message, "Loading discovered ports...", "info");
    try {
        const response = await fetch("/api/ports/top-discovered");
        if (!response.ok) throw new Error("Unable to load discovered ports.");
        renderTopPorts(await response.json());
    } catch (error) {
        chart.replaceChildren();
        showMessage(message, "Unable to load discovered ports.", "error");
        retry.hidden = false;
    }
}

function renderTopPorts(ports) {
    renderTopRanking(ports, {
        chartId: "top-ports-chart", messageId: "top-ports-message",
        label: port => String(port.port) + "/" + port.protocol,
        barClass: port => port.protocol === "UDP" ? " top-ports-bar-udp" : "",
        emptyMessage: "No discovered ports yet.",
    });
}

async function loadTopOperatingSystems() {
    const message = document.getElementById("top-os-message");
    const retry = document.getElementById("top-os-retry");
    const chart = document.getElementById("top-os-chart");
    retry.hidden = true;
    showMessage(message, "Loading operating systems...", "info");
    try {
        const response = await fetch("/api/os/top-assets");
        if (!response.ok) throw new Error("Unable to load operating systems.");
        renderTopRanking(await response.json(), {
            chartId: "top-os-chart", messageId: "top-os-message",
            label: operatingSystem => operatingSystem.name,
            emptyMessage: "No assets with an operating system assigned yet.",
        });
    } catch (error) {
        chart.replaceChildren();
        showMessage(message, "Unable to load operating systems.", "error");
        retry.hidden = false;
    }
}

function renderTopRanking(items, options) {
    const chart = document.getElementById(options.chartId);
    chart.replaceChildren();
    const maximum = Math.max(1, ...items.map((item) => Number(item.total)));
    for (const item of items) {
        const row = document.createElement("li");
        row.className = "top-ports-row";
        const label = document.createElement("span");
        label.className = "top-ports-label";
        label.textContent = options.label(item);
        const track = document.createElement("span");
        track.className = "top-ports-track";
        track.setAttribute("aria-hidden", "true");
        const bar = document.createElement("span");
        bar.className = "top-ports-bar" + (options.barClass?.(item) || "");
        bar.style.width = (Number(item.total) / maximum * 100) + "%";
        track.append(bar);
        const count = document.createElement("strong");
        count.className = "top-ports-count";
        count.textContent = Number(item.total).toLocaleString("en-US");
        row.append(label, track, count);
        chart.append(row);
    }
    showMessage(document.getElementById(options.messageId),
        items.length ? "" : options.emptyMessage, "info");
}

document.getElementById("newers-retry").addEventListener("click", loadNewers);

document.getElementById("top-ports-retry").addEventListener("click", loadTopPorts);
document.getElementById("top-os-retry").addEventListener("click", loadTopOperatingSystems);

document.getElementById("ptr-attention-retry").addEventListener("click", loadPtrAttention);

loadDashboard();
