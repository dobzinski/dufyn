// Keep the original textarea as the source for copying and reusing results.
(() => {
    const viewers = new WeakMap();
    function updateHighlights(view) {
        const gradients = [...view.selected].sort((a, b) => a - b).map(line => {
            const start = `calc(16px + ${line - 1} * 1.65em)`;
            const end = `calc(16px + ${line} * 1.65em)`;
            return `linear-gradient(to bottom, transparent ${start}, var(--highlight) ${start}, var(--highlight) ${end}, transparent ${end})`;
        });
        view.output.style.backgroundImage = gradients.length ? gradients.join(', ') : 'none';
    }
    function render(field) {
        const view = viewers.get(field);
        if (!view) return;
        const value = field.value;
        const lineCount = value ? value.split('\n').length : 0;
        view.selected.clear();
        view.numbers.replaceChildren();
        const lineButtons = document.createDocumentFragment();
        for (let line = 1; line <= lineCount; line++) {
            const number = document.createElement('button');
            number.type = 'button';
            number.className = 'tool-result-line-number';
            number.dataset.line = String(line);
            number.textContent = String(line);
            number.setAttribute('aria-label', `Highlight line ${line}`);
            number.setAttribute('aria-pressed', 'false');
            lineButtons.append(number);
        }
        view.numbers.append(lineButtons);
        updateHighlights(view);
        view.gutter.style.width = `calc(${Math.max(2, String(lineCount).length)}ch + 20px)`;
        view.numbers.style.transform = 'translateY(0)';
        view.output.replaceChildren();
        if (value.length > 65536 || view.mode === 'plain') {
            view.output.textContent = value;
        } else {
            const fragment = document.createDocumentFragment();
            const tokens = view.mode === 'structured'
                ? /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|\b(true|false|null)\b|(^[ \t]*(?:-[ \t]+)?[^\s{}\[\],:#][^\n:\r]*:)(?=[ \t]|$)/gm
                : /(^[ \t]*[^\n:\r]+:)(?=[ \t]|$)/gm;
            let position = 0;
            for (const match of value.matchAll(tokens)) {
                fragment.append(document.createTextNode(value.slice(position, match.index)));
                const span = document.createElement('span');
                span.className = view.mode !== 'structured' || match[1] || match[4] ? 'tool-result-key' : match[2] ? 'tool-result-string' : 'tool-result-literal';
                span.textContent = match[0];
                fragment.append(span);
                position = match.index + match[0].length;
            }
            fragment.append(document.createTextNode(value.slice(position)));
            view.output.append(fragment);
        }
        view.output.scrollTop = view.output.scrollLeft = 0;
    }
    function showPlain(field) {
        const view = viewers.get(field);
        if (!view) return;
        view.formatted.hidden = true;
        view.output.hidden = true;
        field.wrap = 'soft';
        field.hidden = false;
        view.button.setAttribute('aria-pressed', 'true');
    }
    document.querySelectorAll('textarea[data-result-view]').forEach(field => {
        const shell = document.createElement('div');
        shell.className = 'tool-result-view';
        shell.dataset.resultMode = field.dataset.resultView;
        const toolbar = document.createElement('div');
        toolbar.className = 'tool-result-toolbar';
        const title = document.createElement('span');
        title.textContent = 'OUTPUT / READ ONLY';
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = 'Plain text';
        button.setAttribute('data-button-icon', 'result-columns');
        button.setAttribute('aria-pressed', 'false');
        const formatted = document.createElement('div');
        formatted.className = 'tool-result-formatted';
        const gutter = document.createElement('div');
        gutter.className = 'tool-result-gutter';
        gutter.setAttribute('role', 'group');
        gutter.setAttribute('aria-label', 'Toggle line highlights');
        const numbers = document.createElement('div');
        numbers.className = 'tool-result-line-numbers';
        gutter.append(numbers);
        const output = document.createElement('pre');
        output.id = `${field.id}-formatted`;
        output.className = 'tool-result-content';
        output.tabIndex = 0;
        output.setAttribute('role', 'region');
        output.setAttribute('aria-label', field.getAttribute('aria-label') || 'Result');
        output.style.minHeight = `${Number(field.rows) * 1.65 + 2}em`;
        button.setAttribute('aria-controls', `${field.id} ${output.id}`);
        toolbar.append(title, button);
        field.before(shell);
        formatted.append(gutter, output);
        shell.append(toolbar, field, formatted);
        const view = {output, button, formatted, numbers, gutter, mode: field.dataset.resultView, selected: new Set()};
        viewers.set(field, view);
        numbers.addEventListener('click', event => {
            const number = event.target;
            if (!number.dataset.line) return;
            const line = Number(number.dataset.line);
            if (view.selected.has(line)) view.selected.delete(line);
            else view.selected.add(line);
            number.setAttribute('aria-pressed', String(view.selected.has(line)));
            updateHighlights(view);
        });
        output.addEventListener('scroll', () => {
            numbers.style.transform = `translateY(${-output.scrollTop}px)`;
        }, {passive: true});
        field.hidden = true;
        button.addEventListener('click', () => {
            if (field.hidden) showPlain(field);
            else {
                field.hidden = true;
                formatted.hidden = false;
                output.hidden = false;
                button.setAttribute('aria-pressed', 'false');
            }
        });
        document.querySelector(`label[for="${field.id}"]`)?.addEventListener('click', event => {
            if (field.hidden) { event.preventDefault(); output.focus(); }
        });
        render(field);
    });
    window.ToolResults = {render, showPlain};
})();




