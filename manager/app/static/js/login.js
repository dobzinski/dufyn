document.getElementById("login-form")?.addEventListener("submit", event => {
    const button = event.currentTarget.querySelector('button[type="submit"]');
    button.disabled = true;
    button.querySelector(".login-button-label").textContent = "Signing in...";
});
