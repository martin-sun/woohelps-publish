() => {
    const data = {};
    const log = {};

    const KEY_MAP = {
        'property type': 'property_type',
        'building type': 'building_type',
        'storeys': 'storeys',
        'square footage': 'square_footage',
        'neighbourhood': 'neighbourhood',
        'neighborhood': 'neighbourhood',
        'title': 'title',
        'land size': 'land_size',
        'built in': 'built_in',
        'annual property taxes': 'annual_property_taxes',
        'property taxes': 'annual_property_taxes',
        'parking type': 'parking_type',
        'time on realtor': 'time_on_realtor',
        'time on realtor.ca': 'time_on_realtor',
        'bathrooms (total)': 'bathrooms_total',
        'bathrooms total': 'bathrooms_total',
        'appliances': 'appliances',
        'basement type': 'basement_type',
        'features': 'features',
        'style': 'style',
        'architecture style': 'architecture_style',
        'structures': 'structures',
        'heating type': 'heating_type',
        'cooling type': 'cooling_type',
        'lot features': 'lot_features',
        'fence': 'fence',
        'frontage': 'frontage',
        'landscape features': 'landscape_features',
        'dimensions': 'dimensions',
    };

    function normalizeKey(text) {
        const lower = text.trim().toLowerCase();
        if (KEY_MAP[lower]) return KEY_MAP[lower];
        return lower.replace(/[^a-z0-9]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
    }

    const sectionDefs = [
        {title: 'Property Summary', key: 'summary'},
        {title: 'Building', key: 'building'},
        {title: 'Measurements', key: 'measurements'},
        {title: 'Rooms', key: 'rooms'},
        {title: 'Land', key: 'land'},
    ];

    const allHeadings = Array.from(
        document.querySelectorAll('h1, h2, h3, h4, h5, h6, [role="heading"]')
    );

    sectionDefs.forEach(({title, key}) => {
        try {
            const heading = allHeadings.find(h =>
                h.textContent.trim().toLowerCase().includes(title.toLowerCase())
            );

            if (!heading) {
                log[key] = {status: 'not_found'};
                return;
            }

            let container = heading.closest('section');
            if (!container) {
                container = heading.parentElement;
                let depth = 0;
                while (container && container !== document.body && depth < 5) {
                    const text = container.innerText || '';
                    const colonCount = (text.match(/:/g) || []).length;
                    if (colonCount >= 2) break;
                    container = container.parentElement;
                    depth++;
                }
            }
            if (!container) {
                log[key] = {status: 'no_container'};
                return;
            }

            if (key === 'rooms') {
                const tables = container.querySelectorAll('table');
                const rooms = [];
                tables.forEach(table => {
                    const rows = table.querySelectorAll('tr');
                    rows.forEach((tr) => {
                        if (tr.parentElement && tr.parentElement.tagName === 'THEAD') return;
                        const tds = tr.querySelectorAll('td');
                        if (tds.length < 2) return;
                        rooms.push({
                            level: tds[0]?.textContent?.trim() || '',
                            name: tds[1]?.textContent?.trim() || '',
                            size: tds[2]?.textContent?.trim() || '',
                        });
                    });
                });

                if (rooms.length > 0) {
                    data[key] = rooms;
                    log[key] = {status: 'ok', rows_count: rooms.length};
                } else {
                    log[key] = {status: 'empty'};
                }
            } else {
                const result = {};

                const labelLike = Array.from(container.querySelectorAll('*')).filter(el => {
                    const cls = el.className || '';
                    return typeof cls === 'string' && (
                        cls.includes('label') || cls.includes('Label') ||
                        cls.includes('key') || cls.includes('Key')
                    ) && el.children.length <= 1 && el.textContent.trim().length > 0;
                });

                labelLike.forEach(label => {
                    const keyText = label.textContent.trim();
                    if (!keyText || keyText.length > 60 || keyText.includes('\n')) return;

                    let valueEl = null;

                    const next = label.nextElementSibling;
                    if (next && next !== label && !label.contains(next)) {
                        valueEl = next;
                    }

                    if (!valueEl) {
                        const parent = label.parentElement;
                        if (parent) {
                            const siblings = Array.from(parent.children);
                            const idx = siblings.indexOf(label);
                            if (idx >= 0 && idx + 1 < siblings.length) {
                                const cand = siblings[idx + 1];
                                if (cand !== label) valueEl = cand;
                            }
                        }
                    }

                    if (!valueEl) {
                        const grandparent = label.parentElement?.parentElement;
                        if (grandparent) {
                            const all = Array.from(grandparent.querySelectorAll('*'));
                            const idx = all.indexOf(label);
                            for (let i = idx + 1; i < Math.min(idx + 6, all.length); i++) {
                                const cand = all[i];
                                const txt = cand.textContent.trim();
                                if (txt && txt !== keyText && txt.length < 200 && !cand.contains(label)) {
                                    valueEl = cand;
                                    break;
                                }
                            }
                        }
                    }

                    if (valueEl) {
                        const value = valueEl.textContent.trim();
                        if (value && value !== keyText && value.length < 200) {
                            result[normalizeKey(keyText)] = value;
                        }
                    }
                });

                if (Object.keys(result).length === 0) {
                    const dts = container.querySelectorAll('dt');
                    dts.forEach(dt => {
                        const dd = dt.nextElementSibling;
                        if (dd && (dd.tagName === 'DD' || dd.className.includes('value'))) {
                            result[normalizeKey(dt.textContent.trim())] = dd.textContent.trim();
                        }
                    });
                }

                if (Object.keys(result).length === 0) {
                    const rows = container.querySelectorAll('tr');
                    rows.forEach(tr => {
                        const tds = tr.querySelectorAll('td, th');
                        if (tds.length === 2) {
                            const k = tds[0].textContent.trim();
                            const v = tds[1].textContent.trim();
                            if (k && v && k !== v) {
                                result[normalizeKey(k)] = v;
                            }
                        }
                    });
                }

                if (Object.keys(result).length > 0) {
                    data[key] = result;
                    log[key] = {status: 'ok', fields_count: Object.keys(result).length};
                } else {
                    log[key] = {status: 'empty'};
                }
            }
        } catch (e) {
            log[key] = {status: 'error', message: String(e.message || e)};
        }
    });

    return {data, log};
}
