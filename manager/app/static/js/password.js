(() => {
    "use strict";
    const groups = {
        upper: "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        lower: "abcdefghijklmnopqrstuvwxyz",
        digits: "0123456789",
        symbols: "!@#$%&*()-_=+",
    };

    // Rejection sampling avoids the bias introduced by applying modulo directly.
    function randomIndex(size) {
        const limit = 0x100000000 - (0x100000000 % size);
        const buffer = new Uint32Array(1);
        do { globalThis.crypto.getRandomValues(buffer); } while (buffer[0] >= limit);
        return buffer[0] % size;
    }

    function generatePassword(length, selected) {
        if (!Number.isInteger(length) || length < 1 || length > 999) throw new Error("Choose a whole number from 1 to 999.");
        if (!selected.length || selected.some(key => !Object.hasOwn(groups, key))) throw new Error("Select at least one character group.");
        if (length < selected.length) throw new Error("Use at least one character for each selected group.");
        if (!globalThis.crypto?.getRandomValues) throw new Error("Secure random generation is unavailable in this browser.");
        const alphabet = selected.map(key => groups[key]).join("");
        let password;
        // Reject incomplete candidates so all valid passwords remain equally likely.
        do {
            password = Array.from({length}, () => alphabet[randomIndex(alphabet.length)]).join("");
        } while (!selected.every(key => Array.from(password).some(char => groups[key].includes(char))));
        return password;
    }

    if (typeof module !== "undefined" && module.exports) module.exports = {generatePassword, groups};
    if (typeof document === "undefined") return;

    const form = document.getElementById("password-form");
    const input = document.getElementById("password-value");
    const length = document.getElementById("password-length");
    const count = document.getElementById("password-count");
    const copy = document.getElementById("password-copy");
    const recording = form.dataset.record === "true";
    const message = document.getElementById("password-message");
    function update() {
        const size = Array.from(input.value).length;
        count.textContent = `${size} ${size === 1 ? "character" : "characters"}`;
        if (copy) copy.disabled = size === 0;
        showMessage(message);
    }
    input.addEventListener("input", update);
    length.addEventListener("input", () => {
        // Reject decimals, exponent notation and more than three digits.
        length.setCustomValidity(/^\d{1,3}$/.test(length.value) && Number(length.value) >= 1 ? "" : "Choose a whole number from 1 to 999 (up to three digits).");
    });
    function generate() {
        showMessage(message);
        try {
            if (!/^\d{1,3}$/.test(length.value)) throw new Error("Choose a whole number from 1 to 999.");
            const selected = Array.from(form.querySelectorAll('input[data-character]:checked'), checkbox => checkbox.value);
            input.value = generatePassword(Number(length.value), selected);
            update();
            return true;
        } catch (error) {
            showMessage(message, error.message, "error");
            return false;
        }
    }
    form.addEventListener("password:update", update);
    if (recording) {
        // Only explicit generator changes replace an edited or stored password.
        form.addEventListener("change", event => {
            if (event.target === length || event.target.matches("input[data-character]")) generate();
        });
        form.closest("details").addEventListener("toggle", () => {
            if (form.closest("details").open && !input.value) generate();
        });
        form.addEventListener("submit", event => {
            if (!input.value && !generate()) {
                event.preventDefault();
                event.stopImmediatePropagation();
            }
        }, true);
    } else {
        form.addEventListener("submit", event => {
            event.preventDefault();
            generate();
        });
    }
    copy?.addEventListener("click", async () => {
        copy.disabled = true;
        showMessage(message);
        try {
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(input.value);
            } else {
                input.focus();
                input.select();
                if (!document.execCommand("copy")) throw new Error("Clipboard unavailable");
            }
            showMessage(message, "Password copied.", "success");
        } catch {
            input.focus();
            input.select();
            showMessage(message, "Select the password and copy it manually with Ctrl+C or Cmd+C.", "info");
        } finally { copy.disabled = input.value.length === 0; }
    });
    // Clear transient secret values when leaving the page.
    window.addEventListener("pagehide", () => { input.value = ""; update(); });
    update();
})();
