const scannerPage = new RecordPage(document.getElementById("scanner-form"), {
    newers: {field: "created_at"},
    endpoint: "/api/scanners", table: "#scanners-table", message: "#scanners-message", retry: "#scanners-retry",
    sortColumn: "site_name",
    selects: [{name: "site_id", endpoint: "/api/sites", placeholder: "Select ...", emptyMessage: "Create a site before adding scanners."}],
    columns: [
        { key: "site_name", label: "Site", render: (record) => escapeHtml(record.site_name) },
        { key: "name", label: "Name" },
        { key: "networks_count", label: "Networks", type: "number" },
        { key: "config_received_at", label: "Last Sync", type: "date", render: (record) => record.config_received_at ? formatDate(record.config_received_at) : "\u2014" },
        { key: "created_at", label: "Created", type: "date", render: (record) => formatDate(record.created_at) },
        { key: "enabled", label: "Enabled", searchValue: (record) => record.enabled === "Y" ? "Yes" : "No", render: (record) => `<span class="status-badge ${record.enabled === "Y" ? "yes" : "no"}">${record.enabled === "Y" ? "Yes" : "No"}</span>` },
    ],
});


const tokenConfirmation = document.getElementById("generate-token-modal");
const tokenModal = document.getElementById("scanner-token-modal");
const tokenInput = document.getElementById("scanner-token-value");
const copyToken = document.getElementById("scanner-token-copy");
const copyMessage = document.getElementById("scanner-token-copy-message");

function confirmTokenGeneration() {
    tokenConfirmation.returnValue = "cancel";
    return new Promise((resolve) => {
        tokenConfirmation.addEventListener("close", () => resolve(tokenConfirmation.returnValue === "generate"), { once: true });
        tokenConfirmation.showModal();
    });
}

tokenModal.addEventListener("close", () => {
    tokenInput.value = "";
    showMessage(copyMessage);
});

copyToken.addEventListener("click", async () => {
    copyToken.disabled = true;
    showMessage(copyMessage);
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(tokenInput.value);
        } else {
            tokenInput.focus();
            tokenInput.select();
            if (!document.execCommand("copy")) throw new Error("Clipboard unavailable");
        }
        showMessage(copyMessage, "Token copied.", "success");
    } catch {
        tokenInput.focus();
        tokenInput.select();
        showMessage(copyMessage, "Select the token and copy it manually with Ctrl+C or Cmd+C.", "info");
    } finally {
        copyToken.disabled = false;
    }
});

scannerPage.menu.querySelector('[data-action="generate-token"]').addEventListener("click", async () => {
    if (scannerPage.deleting) return;
    const record = scannerPage.selectedRecord();
    scannerPage.closeActions(true);
    if (!record) return;
    scannerPage.deleting = true;
    scannerPage.clearMessages();
    try {
        if (!(await confirmTokenGeneration())) return;
        scannerPage.showMessage(scannerPage.listMessage, "Generating token...", "info");
        const response = await fetch(`/api/scanners/${record.id}/token`, { method: "POST", cache: "no-store" });
        if (!response.ok) {
            if (response.status === 404) throw new Error("Scanner no longer exists. Reload the list.");
            throw new Error("Unable to generate token. Please retry.");
        }
        const result = await response.json();
        tokenInput.value = result.token;
        tokenModal.querySelector("h2").textContent = `Token: ${record.name}`;
        showMessage(copyMessage);
        scannerPage.clearMessages();
        tokenModal.showModal();
    } catch (error) {
        scannerPage.showMessage(scannerPage.listMessage, error.message, "error");
    } finally {
        scannerPage.deleting = false;
    }
});


const networksModal = document.getElementById("scanner-networks-modal");
const networksForm = document.getElementById("scanner-networks-form");
const networksList = document.getElementById("scanner-networks-list");
const networksMessage = document.getElementById("scanner-networks-message");
const networksSave = document.getElementById("scanner-networks-save");
const networksCancel = document.getElementById("scanner-networks-cancel");
let networksScannerId = null;
let networksSaving = false;
let networksLoadVersion = 0;

networksCancel.addEventListener("click", () => networksModal.close());
networksModal.addEventListener("cancel", (event) => {
    if (networksSaving) event.preventDefault();
});
networksModal.addEventListener("close", () => {
    networksLoadVersion++;
    networksScannerId = null;
    networksList.replaceChildren();
    showMessage(networksMessage);
    scannerPage.deleting = false;
});

scannerPage.menu.querySelector('[data-action="networks"]').addEventListener("click", async () => {
    if (scannerPage.deleting) return;
    const record = scannerPage.selectedRecord();
    scannerPage.closeActions(true);
    if (!record) return;
    scannerPage.deleting = true;
    networksScannerId = record.id;
    const loadVersion = ++networksLoadVersion;
    networksModal.querySelector("h2").textContent = `Networks: ${record.name}`;
    networksList.replaceChildren();
    networksList.disabled = true;
    networksSave.disabled = true;
    showMessage(networksMessage, "Loading networks...", "info");
    networksModal.showModal();
    try {
        const response = await fetch(`/api/scanners/${record.id}/networks`);
        if (!response.ok) throw new Error(response.status === 404 ? "Scanner no longer exists." : "Unable to load networks. Close and try again.");
        const result = await response.json();
        if (loadVersion !== networksLoadVersion) return;
        for (const network of result.networks) {
            const label = document.createElement("label");
            label.className = "scanner-network-option";
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.name = "network_id";
            checkbox.value = network.id;
            checkbox.checked = network.assigned;
            const text = document.createElement("span");
            text.textContent = `${network.cidr}${network.name ? " - " + network.name : ""}${network.enabled === "Y" ? "" : " (disabled)"}`;
            label.append(checkbox, text);
            networksList.appendChild(label);
        }
        networksList.disabled = false;
        networksSave.disabled = false;
        showMessage(networksMessage, result.networks.length ? "" : "No networks in this site. Create one in Networks first.", "info");
    } catch (error) {
        if (loadVersion === networksLoadVersion) showMessage(networksMessage, error.message, "error");
    }
});

networksForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (networksSaving || networksSave.disabled || networksScannerId === null) return;
    networksSaving = true;
    networksSave.disabled = true;
    networksCancel.disabled = true;
    networksList.disabled = true;
    const networkIds = [...networksList.querySelectorAll("input:checked")].map((input) => Number(input.value));
    showMessage(networksMessage, "Saving networks...", "info");
    try {
        const response = await fetch(`/api/scanners/${networksScannerId}/networks`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ network_ids: networkIds }),
        });
        if (!response.ok) {
            const error = await response.json();
            throw new Error(typeof error.detail === "string" ? error.detail : "Unable to save networks.");
        }
        networksModal.close();
        if (await scannerPage.load()) scannerPage.showMessage(scannerPage.listMessage, "Scanner networks updated successfully.", "success");
    } catch (error) {
        showMessage(networksMessage, error.message, "error");
    } finally {
        networksSaving = false;
        networksSave.disabled = false;
        networksCancel.disabled = false;
        networksList.disabled = false;
    }
});

attachScannerView(scannerPage);
