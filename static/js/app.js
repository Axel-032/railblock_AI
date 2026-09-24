/**
 * RailBlock AI — Vanilla JavaScript Engine
 * Multi-Department Railway Block Scheduling Console (TMS, SMMS, TRD)
 * Strict Rules: Square borders, 0px border-radius, flat solid colors (no gradients),
 * efficient, lightweight execution without external libraries.
 */

let currentTimelineData = null;
let activeFilter = 'all';

document.addEventListener('DOMContentLoaded', function () {
    initAlerts();
    if (document.getElementById('timelineContainer')) {
        loadTimeline();
    }
    initTableFilter();
});

function initAlerts() {
    const alerts = document.querySelectorAll('.alert');
    if (alerts.length > 0) {
        setTimeout(function () {
            alerts.forEach(function (alert) {
                alert.style.display = 'none';
            });
        }, 5000);
    }
}

function initTableFilter() {
    const filterInput = document.getElementById('tableFilterInput');
    if (!filterInput) return;

    filterInput.addEventListener('input', function () {
        const term = this.value.toLowerCase().trim();
        const rows = document.querySelectorAll('.table-filterable tbody tr');
        rows.forEach(function (row) {
            row.style.display = row.innerText.toLowerCase().includes(term) ? '' : 'none';
        });
    });
}

// -----------------------------------------------------------------------------
// Timeline Loading & DOM Rendering (Square Geometry, Pure HTML5)
// -----------------------------------------------------------------------------

function loadTimeline() {
    const container = document.getElementById('timelineContainer');
    if (!container) return;

    fetch('/api/timeline-data/')
        .then(res => res.json())
        .then(data => {
            currentTimelineData = data;
            renderTimeline(data);
        })
        .catch(err => {
            console.error('Failed to load timeline data:', err);
            container.innerHTML = '<div style="color:var(--red-secondary); padding:12px;"><span class="ui-icon" aria-hidden="true">⚠️</span> Failed to load corridor schedule timeline. Click Reset.</div>';
        });
}

function renderTimeline(data) {
    const container = document.getElementById('timelineContainer');
    if (!container || !data) return;

    const sections = data.sections || [];
    const items = data.items || [];
    const slots = data.slots || [];

    let html = '';

    // 1. TRAIN TRAFFIC CRITICALITY HEATMAP STRIP (Solid Flat Color Blocks)
    html += `
        <div class="traffic-heatmap-wrapper">
            <div class="traffic-heatmap-title">
                <span>Train Traffic Density (24h)</span>
            </div>
            <div class="traffic-heatmap-strip">
    `;

    slots.forEach(s => {
        let heatClass = 'heat-low';
        let label = 'Low traffic';
        if (s.traffic_criticality > 12) {
            heatClass = 'heat-peak';
            label = 'High passenger peak';
        } else if (s.traffic_criticality > 5) {
            heatClass = 'heat-med';
            label = 'Moderate traffic';
        }
        html += `<div class="heat-cell ${heatClass}" title="${s.time}: Criticality Score ${s.traffic_criticality} (${label})"></div>`;
    });

    html += `
            </div>
        </div>
    `;

    // 2. 24-HOUR TIME AXIS
    html += `
        <div class="timeline-axis-row">
            <div class="lane-label-col">Corridor Section</div>
            <div class="lane-track-area axis-area">
    `;

    for (let h = 0; h <= 24; h += 2) {
        const leftPct = (h / 24) * 100;
        const timeStr = `${String(h % 24).padStart(2, '0')}:00`;
        html += `
            <div class="axis-tick" style="left: ${leftPct}%;">
                <span class="axis-time">${timeStr}</span>
            </div>
        `;
    }

    html += `
            </div>
        </div>
    `;

    // 3. SECTION LANES
    sections.forEach(secName => {
        const secItems = items.filter(it => it.location.toLowerCase() === secName.toLowerCase());

        html += `
            <div class="timeline-section-row" data-section="${secName}">
                <div class="lane-label-col">
                    <span class="lane-name">${secName}</span>
                    <span class="lane-count">${secItems.length} tasks</span>
                </div>
                <div class="lane-track-area">
        `;

        for (let h = 0; h <= 24; h += 2) {
            const leftPct = (h / 24) * 100;
            html += `<div class="grid-line" style="left: ${leftPct}%;"></div>`;
        }

        // Shaded Off-Peak Maintenance Windows
        html += `<div class="lean-band" style="left: ${(1/24)*100}%; width: ${(4/24)*100}%;" title="Deep Night Maintenance Window (01:00-05:00)"></div>`;
        html += `<div class="lean-band" style="left: ${(11.5/24)*100}%; width: ${(2.5/24)*100}%;" title="Midday Lean Window (11:30-14:00)"></div>`;

        // Group by start_slot to render Joint Bundled Windows
        const slotGroups = {};
        secItems.forEach(it => {
            if (!slotGroups[it.start_slot]) slotGroups[it.start_slot] = [];
            slotGroups[it.start_slot].push(it);
        });

        Object.keys(slotGroups).forEach(startSlotKey => {
            const group = slotGroups[startSlotKey];
            const startSlot = parseInt(startSlotKey);
            const maxEndSlot = Math.max(...group.map(g => g.end_slot));
            const isJointGroup = group.length > 1;

            const leftPct = (startSlot / 48) * 100;
            const widthPct = Math.max(2.5, ((maxEndSlot - startSlot) / 48) * 100);

            if (isJointGroup) {
                // Joint Multi-Agency Container (Sharp Solid Purple Border)
                html += `
                    <div class="joint-block-wrapper" 
                         style="left: ${leftPct}%; width: ${widthPct}%;" 
                         title="⚡ Joint Window: ${group.map(g => g.code).join(' + ')}">
                        <div class="joint-block-badge">⚡ Joint Window (${group.length})</div>
                        <div class="joint-inner-tasks">
                `;
                group.forEach(task => {
                    const isVisible = (activeFilter === 'all' || activeFilter === 'joint' || activeFilter === task.department);
                    const taskWidthPct = Math.max(5, ((task.end_slot - task.start_slot) / (maxEndSlot - startSlot)) * 100);
                    html += `
                        <div class="block-bar bar-${task.department} ${isVisible ? '' : 'dimmed'}"
                             style="width: ${taskWidthPct}%;"
                             onclick="showTaskDetails(${JSON.stringify(task).replace(/"/g, '&quot;')})">
                            <span class="bar-tag">${task.department.toUpperCase()}</span>
                            <span class="bar-title">${task.code}: ${task.type}</span>
                            <span class="bar-time">${task.start_time} - ${task.end_time}</span>
                        </div>
                    `;
                });
                html += `
                        </div>
                    </div>
                `;
            } else {
                // Solo Department Block (Sharp Square Corners)
                const task = group[0];
                const isVisible = (activeFilter === 'all' || activeFilter === task.department);
                html += `
                    <div class="block-bar bar-${task.department} ${isVisible ? '' : 'dimmed'}" 
                         style="left: ${leftPct}%; width: ${widthPct}%;" 
                         onclick="showTaskDetails(${JSON.stringify(task).replace(/"/g, '&quot;')})"
                         title="${task.code} [${task.department.toUpperCase()}]: ${task.type}">
                        <span class="bar-tag">${task.department.toUpperCase()}</span>
                        <span class="bar-title">${task.code}: ${task.type}</span>
                        <span class="bar-time">${task.start_time} - ${task.end_time}</span>
                    </div>
                `;
            }
        });

        html += `
                </div>
            </div>
        `;
    });

    container.innerHTML = html;
}

// -----------------------------------------------------------------------------
// Filter Handler
// -----------------------------------------------------------------------------

function setTimelineFilter(dept, btnElement) {
    activeFilter = dept;

    const pills = document.querySelectorAll('.filter-pill');
    pills.forEach(p => p.classList.remove('active'));
    if (btnElement) btnElement.classList.add('active');

    if (currentTimelineData) {
        renderTimeline(currentTimelineData);
    }

    const rows = document.querySelectorAll('.table-filterable tbody tr');
    rows.forEach(row => {
        if (dept === 'all') {
            row.style.display = '';
        } else if (dept === 'joint') {
            row.style.display = row.classList.contains('bundled-row') ? '' : 'none';
        } else {
            row.style.display = row.innerText.toLowerCase().includes(dept) ? '' : 'none';
        }
    });
}

// -----------------------------------------------------------------------------
// Interactive Optimization Trigger (OR-Tools CP-SAT + Lagrangian)
// -----------------------------------------------------------------------------

function triggerLagrangianOptimization() {
    const btn = document.getElementById('btnGlobalOptimize');
    const spinner = document.getElementById('globalOptSpinner');

    if (btn) {
        btn.disabled = true;
        if (spinner) spinner.style.display = 'inline-block';
    }

    fetch('/api/optimize/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(result => {
        if (btn) {
            btn.disabled = false;
            if (spinner) spinner.style.display = 'none';
        }

        if (result.success) {
            const metrics = result.metrics || {};
            setElementText('kpiUptime', `${metrics.track_uptime_percent || 92.2}%`);
            setElementText('kpiSaved', `+${metrics.downtime_saved_minutes || 0} mins`);
            setElementText('kpiBundled', `${metrics.joint_bundles_count || 0} windows`);
            setElementText('kpiConflicts', `${(result.separated_blocks || []).length} conflicts`);
            setElementText('kpiSolveTime', `${metrics.solve_time_ms || 0} ms`);

            loadTimeline();
            updateJointBlocksList(result.joint_blocks);
            updateSeparatedBlocksList(result.separated_blocks);
            showToastNotification(`AI Solved (${result.status}): ${metrics.solve_time_ms}ms, ${metrics.lagrangian_iterations} iterations.`);
        } else {
            alert('Optimization Error: ' + (result.error || 'Unknown error'));
        }
    })
    .catch(err => {
        if (btn) {
            btn.disabled = false;
            if (spinner) spinner.style.display = 'none';
        }
        console.error('API failed:', err);
        alert('Failed to connect to OR-Tools optimization engine.');
    });
}

function setElementText(id, text) {
    const el = document.getElementById(id);
    if (el) el.innerText = text;
}

function updateJointBlocksList(jointBlocks) {
    const list = document.getElementById('jointBlocksList');
    if (!list || !jointBlocks) return;

    if (jointBlocks.length === 0) {
        list.innerHTML = '<div style="font-size:12px; color:var(--text-muted); padding:10px;">No joint blocks combined.</div>';
        return;
    }

    list.innerHTML = jointBlocks.map(jb => `
        <div class="bundle-item">
            <div class="bundle-header">
                <div>
                    <span class="badge badge-joint">${jb.joint_id}</span>
                    <strong style="margin-left: 6px;">${jb.location}</strong>
                </div>
                <span class="badge badge-time">${jb.start_time} – ${jb.end_time} (${jb.duration_minutes}m)</span>
            </div>
            <div class="bundle-details">
                <div class="bundle-teams">
                    ${jb.teams.map(t => `<span class="tag-team">${t}</span>`).join('')}
                </div>
                <div class="bundle-explanation">
                    ${jb.explanation}
                </div>
            </div>
        </div>
    `).join('');
}

function updateSeparatedBlocksList(separatedBlocks) {
    const list = document.getElementById('separatedBlocksList');
    if (!list || !separatedBlocks) return;

    if (separatedBlocks.length === 0) {
        list.innerHTML = '<div style="font-size:12px; color:var(--text-muted); padding:10px;">No safety conflicts detected.</div>';
        return;
    }

    list.innerHTML = separatedBlocks.map(sb => `
        <div class="separation-item">
            <div class="separation-header">
                <span class="badge badge-conflict">SAFETY SEPARATION</span>
                <strong style="margin-left: 6px;">${sb.location}</strong>
            </div>
            <div class="separation-content">
                <div class="task-pair">
                    <div class="task-box">
                        <div style="font-weight: 700; color: var(--tms-rust); font-size: 11px;">${sb.req_a.code} (${sb.req_a.team})</div>
                        <div style="font-size: 11px;">${sb.req_a.type}</div>
                        <div style="font-size: 10px; color: var(--text-dim); font-family: monospace;">Slot: ${sb.req_a.time}</div>
                    </div>
                    <div class="task-vs">&ne;</div>
                    <div class="task-box">
                        <div style="font-weight: 700; color: var(--smms-green); font-size: 11px;">${sb.req_b.code} (${sb.req_b.team})</div>
                        <div style="font-size: 11px;">${sb.req_b.type}</div>
                        <div style="font-size: 10px; color: var(--text-dim); font-family: monospace;">Slot: ${sb.req_b.time}</div>
                    </div>
                </div>
                <div class="separation-reason">
                    <span class="ui-icon" aria-hidden="true">🛑</span>
                    <strong>Reason:</strong> ${sb.separation_reason}
                </div>
            </div>
        </div>
    `).join('');
}

// -----------------------------------------------------------------------------
// Modals & Details Inspector (Square Geometry)
// -----------------------------------------------------------------------------

function showTaskDetails(task) {
    const modal = document.getElementById('detailModal');
    const modalTitle = document.getElementById('modalTitle');
    const modalBody = document.getElementById('modalBody');

    if (!modal || !task) return;

    modalTitle.innerText = `${task.code}: ${task.type}`;

    let jointNotice = '';
    if (task.is_joint) {
        jointNotice = `
            <div class="alert alert-info" style="margin-bottom:10px; font-size:12px;">
                ⚡ <strong>Joint Block:</strong> Co-scheduled on ${task.location} with <strong>${task.joined_with}</strong>.
            </div>
        `;
    }

    modalBody.innerHTML = `
        ${jointNotice}
        <div style="display:flex; gap:6px; margin-bottom:12px; flex-wrap:wrap;">
            <span class="badge badge-${task.department}">${task.department_name || task.department.toUpperCase()}</span>
            <span class="badge badge-time">🕒 ${task.start_time} – ${task.end_time} (${task.duration_minutes}m)</span>
            <span class="badge badge-${(task.severity || 'medium').toLowerCase()}">${task.severity || 'MEDIUM'}</span>
            <span class="badge badge-team">${task.location}</span>
        </div>
    `;

    openModal('detailModal');
}

function openLagrangianModal() {
    const modalBody = document.getElementById('lagrangianModalBody');
    if (!modalBody) return;

    modalBody.innerHTML = '<div style="padding: 20px; color: var(--text-muted); font-family: monospace;">Loading telemetry...</div>';
    openModal('lagrangianModal');

    fetch('/api/lagrangian-telemetry/')
        .then(res => {
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return res.json();
        })
        .then(data => {
            const logs = data.lagrangian_logs || [];
            const metrics = data.metrics || {};

            let rowsHtml = '';
            logs.forEach(l => {
                rowsHtml += `
                    <tr>
                        <td><strong>Iter ${l.iteration}</strong></td>
                        <td style="font-family:monospace;">${l.dual_bound}</td>
                        <td style="font-family:monospace;">${l.primal_cost}</td>
                        <td style="color:var(--signal-clear); font-weight:700; font-family:monospace;">${l.uptime_percent}%</td>
                        <td><span class="badge badge-time">${l.duality_gap_percent}%</span></td>
                        <td style="font-family:monospace; color:var(--gold-accent);">λ_max = ${l.max_multiplier}</td>
                        <td style="font-family:monospace;">${l.solve_time_ms} ms</td>
                    </tr>
                `;
            });

            modalBody.innerHTML = `
                <div class="table-responsive">
                    <table class="table-custom">
                        <thead>
                            <tr>
                                <th>Iteration</th>
                                <th>Dual Bound (LB)</th>
                                <th>Primal Cost (UB)</th>
                                <th>Corridor Uptime</th>
                                <th>Duality Gap</th>
                                <th>Multipliers</th>
                                <th>Subproblem Time</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${rowsHtml || '<tr><td colspan="7">No iteration logs recorded.</td></tr>'}
                        </tbody>
                    </table>
                </div>
                <div style="margin-top:10px; font-size:11px; color:var(--text-muted); display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px;">
                    <span><strong style="color:var(--text-bright);">Status:</strong> ${data.status}</span>
                    <span>Solve Time: <span style="color:var(--blue-primary); font-family:monospace;">${metrics.solve_time_ms}ms</span></span>
                    <span>Uptime: <span style="color:var(--signal-clear); font-family:monospace; font-weight:700;">${metrics.track_uptime_percent}%</span></span>
                </div>
            `;

            openModal('lagrangianModal');
        })
        .catch(err => {
            modalBody.innerHTML = '<div style="color:red; padding:12px;">Failed to load Lagrangian telemetry.</div>';
            openModal('lagrangianModal');
        });
}

function openModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.classList.add('open');
}

function closeModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.classList.remove('open');
}

// -----------------------------------------------------------------------------
// Global Add Block Modal & Auto-Scheduling Handlers
// -----------------------------------------------------------------------------

function openGlobalAddBlockModal(dept) {
    const select = document.getElementById('req_dept_select');
    if (select && dept) {
        select.value = dept.toLowerCase();
    }
    openModal('globalAddBlockModal');
}

function handleBlockFormSubmit(event) {
    event.preventDefault();
    const form = event.target;
    const btn = document.getElementById('btnSubmitBlock');
    if (btn) {
        btn.disabled = true;
        btn.innerText = 'Submitting & Optimizing...';
    }

    const formData = new FormData(form);

    fetch(form.action || '/api/submit-request/', {
        method: 'POST',
        body: formData,
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        }
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            showToastNotification(data.message || 'Block request submitted. Schedule automatically re-optimized.');
            closeModal('globalAddBlockModal');
            form.reset();
            // Refresh to update timetable, timeline Gantt, and Control Office approval bar
            setTimeout(() => {
                window.location.reload();
            }, 350);
        } else {
            alert('Error submitting block request: ' + (data.error || 'Unknown error'));
            if (btn) {
                btn.disabled = false;
                btn.innerText = 'Submit Request';
            }
        }
    })
    .catch(err => {
        console.error('Submit error:', err);
        // Fallback to standard form submission if fetch encountered network failure
        form.submit();
    });
}

function cancelRequest(reqId, reqCode) {
    if (!confirm(`Cancel maintenance block request ${reqCode}? The AI solver will automatically re-optimize remaining corridor blocks.`)) {
        return;
    }

    fetch(`/api/cancel-request/${reqId}/`, {
        method: 'POST',
        headers: {
            'X-Requested-With': 'XMLHttpRequest',
            'Content-Type': 'application/json'
        }
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            showToastNotification(data.message || `Request ${reqCode} removed. Schedule recomputed.`);
            setTimeout(() => {
                window.location.reload();
            }, 300);
        } else {
            alert('Error cancelling request: ' + (data.error || 'Server error'));
        }
    })
    .catch(err => {
        console.error('Cancel error:', err);
        alert('Failed to connect to server to cancel request.');
    });
}

function toggleScheduleApproval() {
    fetch('/api/approve-schedule/', {
        method: 'POST',
        headers: {
            'X-Requested-With': 'XMLHttpRequest',
            'Content-Type': 'application/json'
        }
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            showToastNotification(data.message);
            setTimeout(() => {
                window.location.reload();
            }, 300);
        } else {
            alert('Schedule approval error: ' + (data.error || 'Server error'));
        }
    })
    .catch(err => {
        console.error('Approval toggle error:', err);
        alert('Failed to update schedule approval status.');
    });
}

function resetCorridorData() {
    if (!confirm('Reset corridor schedule and requests to baseline data?')) return;

    fetch('/api/reset/', { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                window.location.reload();
            } else {
                alert('Reset error: ' + data.error);
            }
        })
        .catch(err => {
            console.error('Reset failed:', err);
            window.location.reload();
        });
}

function showToastNotification(msg) {
    const toast = document.createElement('div');
    toast.className = 'alert alert-success';
    toast.style.position = 'fixed';
    toast.style.bottom = '20px';
    toast.style.right = '20px';
    toast.style.zIndex = '9999';
    toast.style.border = '2px solid var(--signal-clear)';
    toast.style.backgroundColor = 'var(--bg-panel)';
    toast.style.color = 'var(--text-bright)';
    toast.style.borderRadius = '0';
    toast.style.padding = '10px 16px';
    toast.style.fontFamily = 'monospace';
    toast.style.fontSize = '12px';
    toast.style.boxShadow = 'none';
    toast.innerHTML = `<span><span class="ui-icon" aria-hidden="true">📌</span> ${msg}</span>`;
    document.body.appendChild(toast);

    setTimeout(() => toast.remove(), 3500);
}

