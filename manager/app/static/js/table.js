const DEFAULT_CELL_TEXT_LIMIT = 17;
// Override per table with newers: {field: "first_seen", showLabel: false}.
const DEFAULT_NEWERS_LABEL = true;

class DataTable {
    constructor(options) {
        this.element = document.querySelector(options.table);

        if (!this.element) {
            return;
        }

        this.emptyMessage = options.emptyMessage;
        this.message = options.message ? document.querySelector(options.message) : null;
        this.numbered = options.numbered === true;
        this.data = Array.isArray(options.data) ? options.data : [];

        this.cellTextLimit = Number.isInteger(options.cellTextLimit) && options.cellTextLimit > 0
            ? options.cellTextLimit : DEFAULT_CELL_TEXT_LIMIT;

        this.showNewersLabel = (options.newers?.showLabel ?? DEFAULT_NEWERS_LABEL) === true;
        this.serverTimeOffset = 0;
        this.setServerTime(options.serverTime);
        this.newersOnly = false;
        this.newersField = options.newers?.field;
        this.newersDays = Number(options.newers?.days ?? document.body.dataset.newersDays);

        this.columns = options.columns || [];

        this.visibleColumns = this.columns.filter((column) => column.hidden !== true);

        this.pageSize = options.pageSize || 10;

        this.pageSizes = options.pageSizes || [10, 25, 50, 100];

        this.currentPage = 1;

        this.searchTerm = "";

        this.searchTerms = [];

        this.sortColumn = options.sortColumn || null;

        this.sortDirection = options.sortDirection === "desc" ? "desc" : "asc";

        this.filteredData = [];

        this.initialize();
        if (options.endpoint) this.load(options.endpoint);
    }

    async load(endpoint) {
        try {
            const response = await fetch(endpoint);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            if (!Array.isArray(data)) throw new Error("Expected a record list");
            if (this.message) showMessage(this.message, data.length ? "" : this.emptyMessage, "info");
            this.setData(this.numbered ? data.map((record, index) => ({ ...record, sequence: index + 1 })) : data, response.headers?.get?.("X-Server-Time"));
            return true;
        } catch (error) {
            console.error("Unable to load table data:", endpoint, error);
            return false;
        }
    }

    initialize() {
        this.createControls();

        this.createHeader();

        this.render();
    }

    createControls() {
        const container = document.createElement("div");

        container.className = "table-controls";
        this.controlsContainer = container;

        /*
         * Page size
         */

        const pageSizeContainer = document.createElement("div");

        pageSizeContainer.className = "table-page-size";

        const pageSizeLabel = document.createElement("span");

        pageSizeLabel.textContent = "Show";

        this.pageSizeSelect = document.createElement("select");

        this.pageSizeSelect.className = "table-page-size-select";
        this.pageSizeSelect.dataset.selectSearch = "false";
        this.pageSizeSelect.setAttribute("aria-label", "Entries per page");

        this.pageSizes.forEach((size) => {
            const option = document.createElement("option");

            option.value = size;

            option.textContent = size;

            if (size === this.pageSize) {
                option.selected = true;
            }

            this.pageSizeSelect.appendChild(option);
        });

        const entriesLabel = document.createElement("span");

        entriesLabel.textContent = "entries";

        pageSizeContainer.appendChild(pageSizeLabel);

        pageSizeContainer.appendChild(this.pageSizeSelect);

        pageSizeContainer.appendChild(entriesLabel);

        /*
         * Search
         */

        const searchContainer = document.createElement("div");

        searchContainer.className = "table-search";

        const searchLabel = document.createElement("label");

        searchLabel.textContent = "";

        this.searchInput = document.createElement("input");

        this.searchInput.type = "search";

        this.searchInput.className = "table-search-input";

        this.searchInput.placeholder = "Search ...";

        this.searchInput.setAttribute("aria-label", "Search text or column=value; press Enter to combine filters with AND");

        this.searchInput.autocomplete = "off";

        searchLabel.appendChild(this.searchInput);

        this.searchTags = document.createElement("div");
        this.searchTags.className = "table-search-tags";
        this.searchTags.setAttribute("aria-label", "Active search filters");
        searchContainer.appendChild(this.searchTags);
        searchContainer.appendChild(searchLabel);

        if (this.newersField && Number.isInteger(this.newersDays) && this.newersDays > 0) {
            searchContainer.classList.add("table-search-newers");
            const newerLabel = document.createElement("label");
            newerLabel.className = "scanner-network-option table-newers-filter";
            newerLabel.title = `Last ${this.newersDays} ${this.newersDays === 1 ? "day" : "days"}`;
            this.newersCheckbox = document.createElement("input");
            this.newersCheckbox.type = "checkbox";
            this.newersCheckbox.checked = false;
            this.newersCheckbox.setAttribute("aria-label", `Newers: ${newerLabel.title}`);
            newerLabel.appendChild(this.newersCheckbox);
            if (this.showNewersLabel) newerLabel.appendChild(document.createTextNode("Newers"));
            searchContainer.appendChild(newerLabel);
            this.newersCheckbox.addEventListener("change", () => {
                this.newersOnly = this.newersCheckbox.checked;
                this.currentPage = 1;
                this.render();
            });
        }

        /*
         * Controls
         */

        container.appendChild(pageSizeContainer);

        container.appendChild(searchContainer);

        /*
         * Insert before table wrapper
         */

        const wrapper = this.element.closest(".table-wrapper");

        if (wrapper) {
            wrapper.parentNode.insertBefore(container, wrapper);
        } else {
            this.element.parentNode.insertBefore(container, this.element);
        }

        /*
         * Events
         */

        this.pageSizeSelect.addEventListener("change", () => {
            this.pageSize = parseInt(this.pageSizeSelect.value, 10);

            this.currentPage = 1;

            this.render();
        });

        this.searchInput.addEventListener("keydown", (event) => {
            if (event.key === "Enter" && !event.isComposing) {
                event.preventDefault();
                this.addSearchTerm();
            }
        });

        this.searchInput.addEventListener("input", () => {
            this.searchTerm = this.searchInput.value.trim().toLowerCase();

            this.currentPage = 1;

            this.render();
        });
    }

    addSearchTerm() {
        const term = this.searchInput.value.trim().toLowerCase();
        if (!term) return;
        if (!this.searchTerms.includes(term)) this.searchTerms.push(term);
        this.searchInput.value = "";
        this.searchTerm = "";
        this.currentPage = 1;
        this.renderSearchTags();
        this.render();
    }

    renderSearchTags() {
        this.searchTags.replaceChildren();
        this.searchTerms.forEach((term) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "pagination-button table-search-tag";
            button.textContent = term;
            button.setAttribute("aria-label", `Remove filter: ${term}`);
            button.addEventListener("click", () => {
                this.searchTerms = this.searchTerms.filter((value) => value !== term);
                this.currentPage = 1;
                this.renderSearchTags();
                this.render();
                this.searchInput.focus();
            });
            this.searchTags.appendChild(button);
        });
    }

    createHeader() {
        const head = this.element.querySelector("thead");

        if (!head) {
            return;
        }

        head.innerHTML = "";

        const row = document.createElement("tr");

        this.visibleColumns.forEach((column) => {
            const cell = document.createElement("th");
            if (column.colSpan > 1) cell.colSpan = Math.floor(column.colSpan);

            cell.textContent = column.label;

            if (column.sortable !== false) {
                cell.addEventListener("click", () => {
                    if (this.filteredData.length) this.sortBy(column.key);
                });
            }

            cell.dataset.column = column.key;

            row.appendChild(cell);
        });

        head.appendChild(row);
    }

    setServerTime(value) {
        const timestamp = typeof value === "string" ? Date.parse(value) : NaN;
        if (Number.isFinite(timestamp)) this.serverTimeOffset = timestamp - Date.now();
    }

    now() {
        return Date.now() + this.serverTimeOffset;
    }

    setData(data, serverTime) {
        this.setServerTime(serverTime);
        this.data = Array.isArray(data) ? data : [];

        this.currentPage = 1;

        this.render();
    }

    filterData() {
        const terms = [...this.searchTerms];
        if (this.searchTerm) terms.push(this.searchTerm);
        const now = this.now();
        const records = this.newersOnly ? this.data.filter(row => this.isNewer(row, now)) : [...this.data];
        if (!terms.length) return records;

        const columns = this.columns.filter((column) => column.searchable !== false);
        const normalize = value => String(value).trim().toLowerCase();
        const getValue = (row, column) => typeof column.searchValue === "function"
            ? column.searchValue(row) : this.getColumnValue(row, column);
        const filters = terms.map((term) => {
            const separator = term.indexOf("=");
            if (separator > 0) {
                const field = normalize(term.slice(0, separator));
                const column = columns.find(column => normalize(column.key) === field) ||
                    columns.find(column => normalize(column.label) === field);
                if (column) return {column, value: normalize(term.slice(separator + 1))};
            }
            // Unrecognized fields remain literal text searches, including the equals sign.
            return {value: normalize(term)};
        });

        return records.filter(row => filters.every(filter => {
            if (filter.column) {
                const value = getValue(row, filter.column);
                return value !== null && value !== undefined && normalize(value) === filter.value;
            }
            return columns.some(column => {
                const value = getValue(row, column);
                return value !== null && value !== undefined && normalize(value).includes(filter.value);
            });
        }));
    }

    sortBy(columnKey) {
        if (this.sortColumn === columnKey) {
            this.sortDirection = this.sortDirection === "asc" ? "desc" : "asc";
        } else {
            this.sortColumn = columnKey;

            this.sortDirection = "asc";
        }

        this.currentPage = 1;

        this.render();
    }

    sortData(data) {
        if (!this.sortColumn) {
            return data;
        }

        const column = this.columns.find((item) => {
            return item.key === this.sortColumn;
        });

        if (!column) {
            return data;
        }

        const direction = this.sortDirection === "asc" ? 1 : -1;

        return [...data].sort((a, b) => {
            const valueA = this.getColumnValue(a, column);

            const valueB = this.getColumnValue(b, column);

            return this.compareValues(valueA, valueB, column.type) * direction;
        });
    }

    compareValues(valueA, valueB, type) {
        if (valueA === null || valueA === undefined) {
            valueA = "";
        }

        if (valueB === null || valueB === undefined) {
            valueB = "";
        }

        /*
         * Numbers
         */

        if (type === "number") {
            return Number(valueA) - Number(valueB);
        }

        /*
         * IP addresses
         */

        if (type === "ip") {
            return this.compareIpAddresses(valueA, valueB);
        }

        /*
         * Dates
         */

        if (type === "date") {
            const dateA = new Date(valueA).getTime();

            const dateB = new Date(valueB).getTime();

            return (dateA || 0) - (dateB || 0);
        }

        /*
         * Text
         */

        return String(valueA).localeCompare(String(valueB), undefined, {
            numeric: true,
            sensitivity: "base",
        });
    }

    compareIpAddresses(valueA, valueB) {
        const partsA = String(valueA).split(".").map(Number);

        const partsB = String(valueB).split(".").map(Number);

        /*
         * IPv4 numeric comparison.
         *
         * IPv6 falls back to normal
         * text comparison for now.
         */

        if (
            partsA.length !== 4 ||
            partsB.length !== 4 ||
            partsA.some(Number.isNaN) ||
            partsB.some(Number.isNaN)
        ) {
            return String(valueA).localeCompare(String(valueB), undefined, {
                numeric: true,
            });
        }

        for (let index = 0; index < 4; index++) {
            if (partsA[index] !== partsB[index]) {
                return partsA[index] - partsB[index];
            }
        }

        return 0;
    }

    getColumnValue(row, column) {
        if (typeof column.value === "function") {
            return column.value(row);
        }

        return row[column.key];
    }

    paginateData(data) {
        const start = (this.currentPage - 1) * this.pageSize;

        const end = start + this.pageSize;

        return data.slice(start, end);
    }

    getTotalPages() {
        return Math.max(1, Math.ceil(this.filteredData.length / this.pageSize));
    }

    goToPage(page) {
        const totalPages = this.getTotalPages();

        if (page < 1 || page > totalPages) {
            return;
        }

        this.currentPage = page;

        this.render();
    }

    render() {
        let data = this.filterData();

        data = this.sortData(data);

        this.filteredData = data;
        // Keep search available when a filter has no matches, so it can be cleared.
        this.controlsContainer.hidden = this.data.length === 0;

        const totalPages = this.getTotalPages();

        if (this.currentPage > totalPages) {
            this.currentPage = totalPages;
        }

        const pageData = this.paginateData(data);

        this.renderRows(pageData);

        this.renderSortIndicators();

        this.renderPagination();
    }

    isNewer(row, now = this.now()) {
        if (!this.newersField || !Number.isInteger(this.newersDays) || this.newersDays <= 0) return false;
        const value = typeof this.newersField === "function" ? this.newersField(row) : row[this.newersField];
        if (typeof value !== "string" || !value.trim()) return false;
        const firstSeen = Date.parse(value);
        if (!Number.isFinite(firstSeen)) return false;
        const age = now - firstSeen;
        return age >= 0 && age <= this.newersDays * 86400000;
    }

    renderRows(data) {
        const body = this.element.querySelector("tbody");

        if (!body) {
            return;
        }

        if (data.length === 0) {
            body.innerHTML = `
                <tr>
                    <td
                        colspan="${Math.max(1, this.visibleColumns.reduce((sum, column) => sum + (Math.floor(column.colSpan) || 1), 0))}"
                        class="table-message"
                    >
                        No matching records found.
                    </td>
                </tr>
            `;

            return;
        }

        const now = this.now();
        body.innerHTML = data
            .map((row) => {
                const cells = this.visibleColumns
                    .map((column) => {
                        let value;

                        if (typeof column.render === "function") {
                            value = column.render(row);
                        } else {
                            value = this.escapeHtml(this.getColumnValue(row, column));
                        }

                        return `
                                    <td${column.colSpan > 1 ? ` colspan="${Math.floor(column.colSpan)}"` : ""}>
                                        ${value}
                                    </td>
                                `;
                    })
                    .join("");

                return `
                        <tr${this.isNewer(row, now) ? ' class="table-newer"' : ""}>
                            ${cells}
                        </tr>
                    `;
            })
            .join("");

        body.querySelectorAll("td").forEach((cell) => this.truncateCell(cell));
    }

    truncateCell(cell) {
        // Preserve badges and action controls, including their accessible labels.
        if (cell.querySelector(".status-badge, .badge, button, input, select, textarea, [role='button']")) return;
        const text = cell.textContent.trim();
        const characters = Array.from(text);
        if (characters.length <= this.cellTextLimit) return;

        cell.title = text;
        const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
        const nodes = [];
        while (walker.nextNode()) nodes.push(walker.currentNode);
        let remaining = this.cellTextLimit;
        let started = false;
        let truncated = false;
        nodes.forEach((node) => {
            let value = node.textContent;
            if (!started) value = value.trimStart();
            if (value) started = true;
            const chars = Array.from(value);
            if (truncated) {
                node.textContent = "";
            } else if (chars.length > remaining) {
                node.textContent = chars.slice(0, remaining).join("") + "...";
                truncated = true;
            } else {
                node.textContent = value;
                remaining -= chars.length;
            }
        });
    }

    renderSortIndicators() {
        const headers = this.element.querySelectorAll("th");

        headers.forEach((header) => {
            header.classList.remove("active", "sort-asc", "sort-desc");
            const column = this.columns.find((item) => item.key === header.dataset.column);
            const sortable = this.filteredData.length > 0 && column && column.sortable !== false;
            header.classList.toggle("sortable", Boolean(sortable));

            if (sortable && header.dataset.column === this.sortColumn) {
                header.classList.add("active");

                header.classList.add(this.sortDirection === "asc" ? "sort-asc" : "sort-desc");
            }
        });
    }

    renderPagination() {
        if (!this.paginationContainer) {
            this.createPagination();
        }

        const totalRecords = this.filteredData.length;
        this.paginationContainer.hidden = totalRecords === 0;
        if (totalRecords === 0) {
            this.paginationInfo.textContent = "";
            this.paginationButtons.replaceChildren();
            return;
        }

        const totalPages = this.getTotalPages();

        let start = 0;
        let end = 0;

        if (totalRecords > 0) {
            start = (this.currentPage - 1) * this.pageSize + 1;

            end = Math.min(this.currentPage * this.pageSize, totalRecords);
        }

        this.paginationInfo.textContent = `Showing ${start} to ${end} of ${totalRecords}`;

        this.paginationButtons.innerHTML = "";

        /*
         * Previous
         */

        this.paginationButtons.appendChild(
            this.createPageButton("Previous", this.currentPage - 1, this.currentPage === 1),
        );

        /*
         * Page numbers
         */

        const pages = this.getVisiblePages(totalPages);

        pages.forEach((page) => {
            if (page === "...") {
                const ellipsis = document.createElement("span");

                ellipsis.className = "pagination-ellipsis";

                ellipsis.textContent = "...";

                this.paginationButtons.appendChild(ellipsis);

                return;
            }

            const button = this.createPageButton(page, page, false);

            if (page === this.currentPage) {
                button.classList.add("active");
            }

            this.paginationButtons.appendChild(button);
        });

        /*
         * Next
         */

        this.paginationButtons.appendChild(
            this.createPageButton("Next", this.currentPage + 1, this.currentPage === totalPages),
        );
    }

    createPagination() {
        this.paginationContainer = document.createElement("div");

        this.paginationContainer.className = "table-pagination";

        this.paginationInfo = document.createElement("div");

        this.paginationInfo.className = "pagination-info";

        this.paginationButtons = document.createElement("div");

        this.paginationButtons.className = "pagination-buttons";

        this.paginationContainer.appendChild(this.paginationInfo);

        this.paginationContainer.appendChild(this.paginationButtons);

        const wrapper = this.element.closest(".table-wrapper");

        if (wrapper) {
            wrapper.insertAdjacentElement("afterend", this.paginationContainer);
        } else {
            this.element.insertAdjacentElement("afterend", this.paginationContainer);
        }
    }

    createPageButton(label, page, disabled) {
        const button = document.createElement("button");

        button.type = "button";

        button.className = "pagination-button";

        button.textContent = label;

        button.disabled = disabled;

        button.addEventListener("click", () => {
            this.goToPage(page);
        });

        return button;
    }

    getVisiblePages(totalPages) {
        if (totalPages <= 7) {
            return Array.from(
                {
                    length: totalPages,
                },
                (_, index) => index + 1,
            );
        }

        if (this.currentPage <= 4) {
            return [1, 2, 3, 4, 5, "...", totalPages];
        }

        if (this.currentPage >= totalPages - 3) {
            return [
                1,
                "...",
                totalPages - 4,
                totalPages - 3,
                totalPages - 2,
                totalPages - 1,
                totalPages,
            ];
        }

        return [
            1,
            "...",
            this.currentPage - 1,
            this.currentPage,
            this.currentPage + 1,
            "...",
            totalPages,
        ];
    }

    escapeHtml(value) {
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
}
