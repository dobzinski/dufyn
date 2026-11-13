/* The browser only reads the Manager's cached update status. */
async function loadUpdateStatus() {
    const message = document.getElementById("update-notice");
    if (!message) return;
    let checkUrl;
    try {
        const response = await fetch("/api/updates", {cache: "no-store"});
        if (!response.ok) throw new Error("Update check failed");
        const result = await response.json();
        checkUrl = result.check_url;
        if (result.status === "error") throw new Error("Update check failed");
        if (result.status !== "available") {
            showMessage(message);
            return;
        }
        showMessage(message, `New version available: v${result.latest_version}.`, "info");
        const link = document.createElement("a");
        link.href = result.release_url;
        link.textContent = "View release notes";
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        message.append(link);
    } catch (error) {
        showMessage(message, "", "error");
        message.classList.add("message-error");
        const text = document.createElement("span");
        text.textContent = "Unable to check for the ";
        message.append(text);
        const link = document.createElement("a");
        link.textContent = "latest version";
        try {
            const url = new URL(checkUrl);
            if (url.protocol !== "https:") throw new Error("Invalid update URL");
            link.href = url.href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            text.append(link, ". Please try again later.");
        } catch {
            text.append("latest version. Please try again later.");
        }
    }
}

loadUpdateStatus();
