/* Read-only parameter filtering; configuration is rendered by the Manager. */
(() => {
    const view = document.getElementById("settings-view");
    if (!view) return;
    const search = view.querySelector("#settings-search");
    const cards = Array.from(view.querySelectorAll(".settings-card"));
    const results = view.querySelector("#settings-results");
    const empty = view.querySelector("#settings-empty");
    function filterParameters() {
        const query = search.value.trim().toLowerCase();
        let visible = 0;
        let total = 0;
        cards.forEach(card => {
            let matches = 0;
            const heading = card.querySelector(".panel-header").textContent.toLowerCase();
            card.querySelectorAll(".settings-parameter").forEach(row => {
                const match = (heading + " " + row.textContent.toLowerCase()).includes(query);
                row.hidden = !match;
                total++;
                if (match) matches++;
            });
            card.hidden = matches === 0;
            visible += matches;
        });
        results.textContent = query ? `${visible} of ${total} parameters` : `${total} parameters`;
        empty.hidden = visible !== 0;
    }
    view.querySelector(".settings-search").hidden = false;
    search.addEventListener("input", filterParameters);
    filterParameters();
})();
