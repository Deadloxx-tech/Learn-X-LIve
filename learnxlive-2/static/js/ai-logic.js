/**
 * LearnXLive — Client-side API helpers and UI utilities
 */

const API = {
    async get(url) {
        const res = await fetch(url);
        if (!res.ok) throw await res.json();
        return res.json();
    },
    async post(url, data) {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!res.ok) throw await res.json();
        return res.json();
    },
    async put(url, data) {
        const res = await fetch(url, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!res.ok) throw await res.json();
        return res.json();
    },
    async upload(url, formData) {
        const res = await fetch(url, { method: 'POST', body: formData });
        if (!res.ok) throw await res.json();
        return res.json();
    }
};

// ── Toast ────────────────────────────────────────────────────────────────
function showToast(msg, type = 'success') {
    let toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        toast.className = 'toast';
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.className = 'toast toast-' + type + ' show';
    setTimeout(() => toast.classList.remove('show'), 3500);
}

// ── Section navigation ──────────────────────────────────────────────────
function showSection(id) {
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    const el = document.getElementById(id);
    if (el) el.classList.add('active');

    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.classList.remove('active');
        if (btn.dataset.section === id) btn.classList.add('active');
    });
}

// ── Render helpers ──────────────────────────────────────────────────────
function renderTermTags(terms, container, className = 'tag') {
    container.innerHTML = '';
    terms.forEach(term => {
        const span = document.createElement('span');
        span.className = className;
        span.textContent = term;
        container.appendChild(span);
    });
}

function renderScoreBar(score, container) {
    container.innerHTML = `
        <div class="progress-bar" style="margin-top:8px;">
            <div class="fill" style="width:${score}%;
                background: linear-gradient(90deg,
                    ${score > 70 ? '#00b894' : score > 50 ? '#feca57' : '#ff6b6b'},
                    ${score > 70 ? '#55efc4' : score > 50 ? '#fdcb6e' : '#fc5c65'}
                );"></div>
        </div>
        <span style="font-size:0.8rem; color:var(--text-muted); margin-top:4px; display:block;">
            ${score}%
        </span>`;
}

function renderConfidenceMeter(confidence, container) {
    container.innerHTML = `
        <div class="confidence-meter">
            <div class="meter-bar">
                <div class="meter-fill" style="width:${confidence}%"></div>
            </div>
            <span class="meter-label">${confidence}%</span>
        </div>`;
}

function getScoreColor(score) {
    if (score >= 70) return 'var(--success)';
    if (score >= 50) return 'var(--warning)';
    return 'var(--danger)';
}

function getScoreBadge(score) {
    if (score >= 85) return '<span class="badge badge-success">Excellent</span>';
    if (score >= 70) return '<span class="badge badge-success">Good</span>';
    if (score >= 50) return '<span class="badge badge-warning">Needs Improvement</span>';
    return '<span class="badge badge-danger">Unsatisfactory</span>';
}

// ── Format date ──────────────────────────────────────────────────────────
function formatDate(dateStr) {
    if (!dateStr) return '—';
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

// ── Spinner ──────────────────────────────────────────────────────────────
function showSpinner(container) {
    container.innerHTML = '<div class="spinner"></div>';
}

function showEmpty(container, emoji, title, desc) {
    container.innerHTML = `
        <div class="empty-state">
            <span class="emoji">${emoji}</span>
            <h3>${title}</h3>
            <p>${desc}</p>
        </div>`;
}
