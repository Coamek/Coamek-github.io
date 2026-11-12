(() => {
    const page = document.querySelector(".photography-page");
    if (!page) return;

    const gap = 14;
    const galleries = [];

    // Balance rows within each game without merging photographs across headings.
    page.querySelectorAll(".case-main > .case-section, .photo-subsection, .photo-game").forEach(section => {
        const sources = [...section.children].filter(child => child.matches(".case-gallery"));
        const figures = sources.flatMap(source => [...source.querySelectorAll("figure")]);
        if (!figures.length) return;
        const gallery = sources[0];
        gallery.replaceChildren(...figures);
        sources.slice(1).forEach(source => source.remove());
        gallery.classList.remove("instagram-gallery");
        gallery.classList.add("photo-flow");

        const ratios = figures.map(figure => {
            const image = figure.querySelector("img");
            const width = Number(image?.getAttribute("width")) || image?.naturalWidth || 1;
            const height = Number(image?.getAttribute("height")) || image?.naturalHeight || 1;
            const ratio = width / height;
            return ratio;
        });
        galleries.push({ element: gallery, figures, ratios, width: 0, columns: 0 });
    });

    function planRows(ratios, width, columns) {
        if (columns === 1) return ratios.map((_, index) => [index]);
        const sorted = [...ratios].sort((left, right) => left - right);
        const median = sorted[Math.floor(sorted.length / 2)];
        const targetHeight = (width - gap * (columns - 1)) / (columns * median);
        const costs = Array(ratios.length + 1).fill(Infinity);
        const lengths = [];
        costs[ratios.length] = 0;

        // Balance all rows together so neither orientation is left alone in an oversized row.
        for (let start = ratios.length - 1; start >= 0; start--) {
            let sum = 0;
            let widestLandscape = 0;
            for (let count = 1; count <= Math.min(columns * 2, ratios.length - start); count++) {
                const ratio = ratios[start + count - 1];
                sum += ratio;
                if (ratio > 1) widestLandscape = Math.max(widestLandscape, ratio);
                if (count === 1 && ratios.length > 1) continue;
                const rowWidth = limitedRowWidth(sum, widestLandscape, count, width, columns);
                const height = (rowWidth - gap * (count - 1)) / sum;
                if (height <= 0) continue;
                const difference = Math.log(height / targetHeight);
                const unused = (width - rowWidth) / width;
                const cost = (difference * difference + 8 * unused * unused) * count + costs[start + count];
                if (cost < costs[start]) {
                    costs[start] = cost;
                    lengths[start] = count;
                }
            }
        }
        const rows = [];
        for (let start = 0; start < ratios.length;) {
            const count = lengths[start] || 1;
            rows.push(Array.from({ length: count }, (_, offset) => start + offset));
            start += count;
        }
        return rows;
    }

    function limitedRowWidth(total, widestLandscape, count, width, columns) {
        if (!widestLandscape || columns <= 2) return width;
        const columnWidth = (width - gap * (columns - 1)) / columns;
        const landscapeLimit = 2 * columnWidth + gap;
        // Even a short gallery keeps landscapes within two portrait columns.
        return Math.min(width, landscapeLimit * total / widestLandscape + gap * (count - 1));
    }

    function layout() {
        galleries.forEach(gallery => {
            const width = gallery.element.clientWidth;
            const columns = Number(getComputedStyle(gallery.element).getPropertyValue("--gallery-columns")) || 3;
            if (!width || (gallery.width === width && gallery.columns === columns)) return;
            gallery.width = width;
            gallery.columns = columns;
            const focused = gallery.element.contains(document.activeElement) ? document.activeElement : null;
            const rows = planRows(gallery.ratios, width, columns).map(indices => {
                const row = document.createElement("div");
                row.className = "photo-row";
                const total = indices.reduce((sum, index) => sum + gallery.ratios[index], 0);
                const widestLandscape = Math.max(0, ...indices.map(index => gallery.ratios[index] > 1 ? gallery.ratios[index] : 0));
                row.style.maxWidth = `${limitedRowWidth(total, widestLandscape, indices.length, width, columns)}px`;
                indices.forEach(index => {
                    gallery.figures[index].style.setProperty("--photo-share", String(100 * gallery.ratios[index] / total));
                    row.append(gallery.figures[index]);
                });
                return row;
            });
            gallery.element.replaceChildren(...rows);
            gallery.element.dataset.layoutReady = "true";
            focused?.focus({ preventScroll: true });
        });
    }

    let frame;
    function scheduleLayout() {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(layout);
    }
    layout();
    window.addEventListener("resize", scheduleLayout);
    if ("ResizeObserver" in window) {
        const observer = new ResizeObserver(scheduleLayout);
        galleries.forEach(gallery => observer.observe(gallery.element));
    }
})();
