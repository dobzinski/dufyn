const menuToggle = document.getElementById("menu-toggle");

const sidebar = document.querySelector(".sidebar");

const sidebarOverlay = document.getElementById("sidebar-overlay");

function openMenu() {
    document.body.classList.add("menu-open");
    menuToggle.classList.add("active");

    sidebar.classList.add("open");

    sidebarOverlay.classList.add("active");

    menuToggle.setAttribute("aria-expanded", "true");

    menuToggle.setAttribute("aria-label", "Close navigation menu");
    (document.getElementById("sidebar-close") || sidebar.querySelector(".nav-group-toggle") || menuToggle).focus();
}

function closeMenu() {
    const wasOpen = sidebar.classList.contains("open");
    document.body.classList.remove("menu-open");
    menuToggle.classList.remove("active");

    sidebar.classList.remove("open");

    sidebarOverlay.classList.remove("active");

    menuToggle.setAttribute("aria-expanded", "false");

    menuToggle.setAttribute("aria-label", "Open navigation menu");
    if (wasOpen && window.innerWidth <= 800) menuToggle.focus();
}

function toggleMenu() {
    if (sidebar.classList.contains("open")) {
        closeMenu();

        return;
    }

    openMenu();
}

menuToggle.addEventListener("click", toggleMenu);

sidebarOverlay.addEventListener("click", closeMenu);
document.getElementById("sidebar-close")?.addEventListener("click", closeMenu);
document.addEventListener("keydown", event => {
    if (event.key === "Escape" && sidebar.classList.contains("open")) closeMenu();
});
window.addEventListener("resize", () => {
    if (window.innerWidth > 800 && sidebar.classList.contains("open")) closeMenu();
});

function formatDate(value) {
    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
        return "—";
    }

    return date.toLocaleDateString("en-US", {
        year: "numeric",
        month: "short",
        day: "2-digit",
    });
}

function formatRemainingDays(days) {
    if (days < 0) {
        return `${Math.abs(days)} days overdue`;
    }

    if (days === 0) {
        return "Expires today";
    }

    if (days === 1) {
        return "1 day";
    }

    return `${days} days`;
}

function escapeHtml(value) {
    if (value === null || value === undefined) {
        return "";
    }

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

/* Shared feedback for forms and administrative pages. */
function showMessage(element, text = "", type = "info") {
    element.classList.add("message");
    element.classList.remove("message-success", "message-error", "message-info");
    if (text) element.classList.add(`message-${type}`);
    element.textContent = text;
}

/* Navigation accordion: derive initial state from the current page. */
const navigationGroups = Array.from(document.querySelectorAll(".nav-group-toggle"))
    .map(button => ({button, items: document.getElementById(button.getAttribute("aria-controls"))}))
    .filter(group => group.items);
const activeNavigationGroup = navigationGroups.find(group => group.items.querySelector(".nav-item.active"))
    || navigationGroups.find(group => group.button.id === "nav-discovery");

function setNavigationGroup(group, expanded) {
    group.button.setAttribute("aria-expanded", String(expanded));
    group.items.hidden = !expanded;
}

navigationGroups.forEach(group => {
    setNavigationGroup(group, group === activeNavigationGroup);
    group.button.addEventListener("click", () => {
        const expand = group.button.getAttribute("aria-expanded") !== "true";
        navigationGroups.forEach(other => setNavigationGroup(other, other === group && expand));
    });
});


/* The manager checks the controller over its internal service URL. */
async function updateControllerStatus() {
    const indicator = document.getElementById("controller-status");
    if (!indicator) return;
    let available = false;
    try {
        const response = await fetch("/api/controller/status", {
            cache: "no-store",
            signal: AbortSignal.timeout(5000),
        });
        available = response.ok && (await response.json()).available === true;
    } catch (_) {
        available = false;
    }
    indicator.dataset.available = String(available);
    const label = available ? "Controller health: available" : "Controller health: unavailable";
    indicator.setAttribute("aria-label", label);
    indicator.title = label;
}

updateControllerStatus();
setInterval(updateControllerStatus, 30000);
