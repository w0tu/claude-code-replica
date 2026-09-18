// Claude Code / Clawd Real-Time Usage Shower & Device Monitor JS
(function () {
  'use strict';

  // 1. Device Identification & Telemetry
  let deviceId = localStorage.getItem('claude_device_id');
  if (!deviceId) {
    deviceId = 'dev_' + Math.random().toString(36).substring(2, 10) + '_' + Date.now().toString(36);
    localStorage.setItem('claude_device_id', deviceId);
  }

  function getDeviceCategory() {
    const ua = navigator.userAgent.toLowerCase();
    if (/mobile|android|iphone|ipod/.test(ua)) return 'Mobile';
    if (/ipad|tablet/.test(ua)) return 'Tablet';
    return 'Desktop';
  }

  function getPlatformInfo() {
    const ua = navigator.userAgent;
    let os = 'Unknown OS';
    if (/iPhone|iPad|iPod/.test(ua)) os = 'iOS';
    else if (/Android/.test(ua)) os = 'Android';
    else if (/Mac OS X|Macintosh/.test(ua)) os = 'macOS';
    else if (/Windows NT/.test(ua)) os = 'Windows';
    else if (/Linux/.test(ua)) os = 'Linux';

    let browser = 'Browser';
    if (/Edg/.test(ua)) browser = 'Edge';
    else if (/Chrome/.test(ua) && !/Edg/.test(ua)) browser = 'Chrome';
    else if (/Safari/.test(ua) && !/Chrome/.test(ua)) browser = 'Safari';
    else if (/Firefox/.test(ua)) browser = 'Firefox';

    return { os, browser };
  }

  const { os: clientOS, browser: clientBrowser } = getPlatformInfo();
  const clientCategory = getDeviceCategory();

  // Send Heartbeat to server so it knows when this device connects & stays active
  function sendHeartbeat(action = 'pulse') {
    const payload = {
      client_id: deviceId,
      device_type: clientCategory,
      os: clientOS,
      browser: clientBrowser,
      screen: `${window.screen.width}x${window.screen.height}`,
      action: action,
      user_agent: navigator.userAgent
    };

    fetch('/api/heartbeat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).catch(() => {});
  }

  // Initial connection heartbeat + continuous 3s pulse
  sendHeartbeat('connect');
  setInterval(() => sendHeartbeat('pulse'), 3000);

  // Send disconnect beacon on window close
  window.addEventListener('beforeunload', () => {
    if (navigator.sendBeacon) {
      navigator.sendBeacon('/api/disconnect', JSON.stringify({ client_id: deviceId }));
    }
  });

  // 2. Real-Time Session Clock
  let sessionStartTime = null;

  function updateSessionTimer() {
    const timerEl = document.getElementById('sessionTimer');
    if (!timerEl) return;

    if (!sessionStartTime) {
      timerEl.textContent = '00:00:00';
      return;
    }

    const elapsedMs = Math.max(0, Date.now() - sessionStartTime * 1000);
    const totalSec = Math.floor(elapsedMs / 1000);
    const hrs = String(Math.floor(totalSec / 3600)).padStart(2, '0');
    const mins = String(Math.floor((totalSec % 3600) / 60)).padStart(2, '0');
    const secs = String(totalSec % 60).padStart(2, '0');

    timerEl.textContent = `${hrs}:${mins}:${secs}`;
  }

  setInterval(updateSessionTimer, 1000);

  // 3. Number formatting helper
  function fmtNum(n) {
    return Number(n || 0).toLocaleString();
  }

  // 4. Fetch usage data & render DOM
  let lastTurnsCount = -1;

  async function fetchUsage() {
    try {
      const res = await fetch('/api/usage');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      renderDashboard(data);
    } catch (err) {
      console.warn('Telemetry fetch paused:', err);
      const statusText = document.getElementById('liveStatusText');
      if (statusText) statusText.textContent = 'RECONNECTING...';
    }
  }

  function renderDashboard(data) {
    const session = data.session || {};
    const lifetime = data.lifetime || {};
    const quota = data.quota || {};
    const devices = data.connected_devices || [];
    const activeCount = data.active_device_count || devices.filter(d => d.is_online).length || 1;

    // Set session start time
    if (session.start_time && !sessionStartTime) {
      sessionStartTime = session.start_time;
      const startedAtEl = document.getElementById('startedAtTime');
      if (startedAtEl) {
        startedAtEl.textContent = session.start_time_iso ? session.start_time_iso.split(' ')[1] : '--:--:--';
      }
    }

    // Status badge
    const statusText = document.getElementById('liveStatusText');
    if (statusText) statusText.textContent = 'LIVE TELEMETRY';

    // Banner details
    const bannerModel = document.getElementById('bannerModel');
    if (bannerModel) bannerModel.textContent = session.active_model || 'Claude 3.7 Sonnet';

    const bannerWorkspace = document.getElementById('bannerWorkspace');
    if (bannerWorkspace) bannerWorkspace.textContent = `Workspace: ${session.workspace_dir || 'Current Directory'}`;

    // 1b. Clawd Animated Mascot Controller (Jamming / Loading / Idle > 10s)
    const mascotImg = document.getElementById('clawdMascotImg');
    const mascotBadge = document.getElementById('mascotBadge');
    const mascotCaption = document.getElementById('mascotCaption');
    const musicBars = document.getElementById('mascotMusicBars');

    const agentState = session.agent_state || 'idle';
    const lastActivity = session.last_activity ? session.last_activity * 1000 : (session.start_time ? session.start_time * 1000 : Date.now());
    const idleSeconds = Math.max(0, Math.floor((Date.now() - lastActivity) / 1000));

    if (mascotImg && mascotBadge && mascotCaption) {
      if (agentState === 'loading') {
        // Loading / Reasoning / Thinking -> claude-processing.gif
        if (!mascotImg.src.endsWith('/claude-processing.gif')) {
          mascotImg.src = '/claude-processing.gif';
        }
        mascotBadge.textContent = '⚡ THINKING';
        mascotBadge.className = 'mascot-badge badge-thinking';
        mascotCaption.textContent = 'Claude is reasoning & planning...';
        if (musicBars) musicBars.style.display = 'none';
      } else if (agentState === 'answering') {
        // Answering / Generating / Jamming -> claude-jam.gif
        if (!mascotImg.src.endsWith('/claude-jam.gif')) {
          mascotImg.src = '/claude-jam.gif';
        }
        mascotBadge.textContent = '♫ JAMMING';
        mascotBadge.className = 'mascot-badge badge-jamming';
        mascotCaption.textContent = 'Clawd is grooving & generating code!';
        if (musicBars) musicBars.style.display = 'flex';
      } else {
        // Idle state: Check if idle for more than 10 seconds
        if (idleSeconds >= 10) {
          if (!mascotImg.src.endsWith('/claude-jam.gif')) {
            mascotImg.src = '/claude-jam.gif';
          }
          mascotBadge.textContent = '🎧 IDLE JAM';
          mascotBadge.className = 'mascot-badge badge-idle';
          mascotCaption.textContent = `Clawd is idle (${idleSeconds}s)`;
          if (musicBars) musicBars.style.display = 'flex';
        } else {
          if (!mascotImg.src.endsWith('/claude-jam.gif')) {
            mascotImg.src = '/claude-jam.gif';
          }
          mascotBadge.textContent = '● READY';
          mascotBadge.className = 'mascot-badge badge-jamming';
          mascotCaption.textContent = 'Standing by for next prompt';
          if (musicBars) musicBars.style.display = 'none';
        }
      }
    }

    // Calculate speed
    const avgSpeed = document.getElementById('avgSpeed');
    if (avgSpeed) {
      const dur = session.duration_s || 0;
      const toks = session.total_tokens || 0;
      const rate = dur > 0 ? Math.round(toks / dur) : 0;
      avgSpeed.textContent = rate > 0 ? `${fmtNum(rate)} tok/s` : '-- tok/s';
    }

    // Metric 1: Total Credits
    const totalCreditsEl = document.getElementById('totalCredits');
    if (totalCreditsEl) totalCreditsEl.textContent = (lifetime.total_credits_used || 0).toFixed(3);

    const sessionCreditsEl = document.getElementById('sessionCredits');
    if (sessionCreditsEl) sessionCreditsEl.textContent = (session.credits_used || 0).toFixed(3);

    // Metric 2: Session Tokens
    const sessionTokensEl = document.getElementById('sessionTokens');
    if (sessionTokensEl) sessionTokensEl.textContent = fmtNum(session.total_tokens || 0);

    const inputTokensEl = document.getElementById('inputTokens');
    if (inputTokensEl) inputTokensEl.textContent = fmtNum(session.input_tokens || 0);

    const outputTokensEl = document.getElementById('outputTokens');
    if (outputTokensEl) outputTokensEl.textContent = fmtNum(session.output_tokens || 0);

    const tokenRatioBar = document.getElementById('tokenRatioBar');
    if (tokenRatioBar && session.total_tokens > 0) {
      const inPct = Math.min(100, Math.max(5, Math.round((session.input_tokens / session.total_tokens) * 100)));
      tokenRatioBar.style.width = `${inPct}%`;
    }

    // Metric 3: Hourly Quota
    const quotaRem = quota.hourly_remaining !== undefined ? quota.hourly_remaining : 40;
    const quotaLim = quota.hourly_limit || 40;
    const quotaRemEl = document.getElementById('quotaRemaining');
    if (quotaRemEl) quotaRemEl.innerHTML = `${quotaRem} <span class="unit">/ ${quotaLim}</span>`;

    const quotaPct = Math.max(0, Math.min(100, Math.round((quotaRem / quotaLim) * 100)));
    const quotaPercentEl = document.getElementById('quotaPercent');
    if (quotaPercentEl) quotaPercentEl.textContent = `${quotaPct}%`;

    const quotaBar = document.getElementById('quotaBar');
    if (quotaBar) quotaBar.style.width = `${quotaPct}%`;

    const effortBadge = document.getElementById('effortTierBadge');
    if (effortBadge) effortBadge.textContent = (quota.effort_tier || 'NORMAL').toUpperCase();

    // Metric 4: Turns
    const turnsCountEl = document.getElementById('turnsCount');
    if (turnsCountEl) turnsCountEl.textContent = session.turns_count || 0;

    const lifetimeTurnsEl = document.getElementById('lifetimeTurns');
    if (lifetimeTurnsEl) lifetimeTurnsEl.textContent = fmtNum(lifetime.total_turns || 0);

    const activeProv = document.getElementById('activeProvider');
    if (activeProv) activeProv.textContent = `Provider: ${(session.provider || 'GROQ').toUpperCase()} (Fast Fallback)`;

    // Metric 5: Base44 UsageGuard
    const base44 = data.base44 || {};
    const b44Status = base44.status || 'active';
    const b44IsOver = base44.is_over_limit || b44Status === 'over_limit';
    const b44Usage = base44.current_usage || 0;
    const b44Max = base44.max_usage || 1000;
    const b44Device = base44.device_name || 'osint';
    const b44Sync = base44.last_sync_str || '--:--:--';

    const b44TextEl = document.getElementById('base44UsageText');
    if (b44TextEl) {
      b44TextEl.innerHTML = `${fmtNum(b44Usage)} <span class="unit">/ ${fmtNum(b44Max)}</span>`;
    }

    const b44DeviceEl = document.getElementById('base44Device');
    if (b44DeviceEl) b44DeviceEl.textContent = b44Device;

    const b44SyncEl = document.getElementById('base44SyncTime');
    if (b44SyncEl) b44SyncEl.textContent = b44Sync;

    const b44Badge = document.getElementById('base44StatusBadge');
    if (b44Badge) {
      if (b44IsOver) {
        b44Badge.textContent = 'OVER LIMIT';
        b44Badge.style.background = 'rgba(231, 76, 60, 0.2)';
        b44Badge.style.color = '#e74c3c';
      } else {
        b44Badge.textContent = 'ACTIVE';
        b44Badge.style.background = 'rgba(46, 204, 113, 0.15)';
        b44Badge.style.color = '#2ecc71';
      }
    }

    const b44Bar = document.getElementById('base44Bar');
    if (b44Bar) {
      const b44Pct = b44Max > 0 ? Math.min(100, Math.round((b44Usage / b44Max) * 100)) : 0;
      b44Bar.style.width = `${b44Pct}%`;
      if (b44IsOver) {
        b44Bar.style.background = 'linear-gradient(90deg, #e74c3c, #c0392b)';
      } else {
        b44Bar.style.background = 'linear-gradient(90deg, #2ecc71, #27ae60)';
      }
    }

    // Navbar Base44 Pill
    const b44PillLabel = document.getElementById('base44PillLabel');
    const b44Dot = document.getElementById('base44Dot');
    if (b44PillLabel) {
      b44PillLabel.textContent = b44IsOver ? 'Base44: Over Limit' : 'Base44: Active';
    }
    if (b44Dot) {
      b44Dot.style.backgroundColor = b44IsOver ? '#e74c3c' : 'var(--color-green)';
    }

    // Metric 6: Global Groq Usage & Live Quota
    const groq = data.groq_global || {};
    const gTokens = groq.total_tokens || 0;
    const gQuota = groq.live_quota || {};
    const gHealth = gQuota.status || 'healthy';
    const gRemTok = gQuota.remaining_tokens !== undefined ? gQuota.remaining_tokens : 8000;
    const gLimTok = gQuota.limit_tokens || 8000;
    const gRemReq = gQuota.remaining_requests !== undefined ? gQuota.remaining_requests : 1000;
    const gLimReq = gQuota.limit_requests || 1000;
    const gTpmPct = gQuota.tokens_pct_remaining !== undefined ? gQuota.tokens_pct_remaining : 100;
    const gResetTok = gQuota.reset_tokens || '--';
    const gRegion = gQuota.region || 'fra';
    const gSpeed = gQuota.tok_per_sec || 0;

    const groqTokensTextEl = document.getElementById('groqTokensText');
    if (groqTokensTextEl) {
      groqTokensTextEl.innerHTML = `${fmtNum(gTokens)} <span class="unit">tok (${groq.requests_count || 0} req)</span>`;
    }

    const groqTpmTextEl = document.getElementById('groqTpmText');
    if (groqTpmTextEl) {
      groqTpmTextEl.textContent = `${fmtNum(gRemTok)} / ${fmtNum(gLimTok)}`;
    }

    const groqRpmTextEl = document.getElementById('groqRpmText');
    if (groqRpmTextEl) {
      groqRpmTextEl.textContent = `${fmtNum(gRemReq)} / ${fmtNum(gLimReq)}`;
    }

    const groqRegionEl = document.getElementById('groqRegion');
    if (groqRegionEl) groqRegionEl.textContent = gRegion;

    const groqResetTimeEl = document.getElementById('groqResetTime');
    if (groqResetTimeEl) groqResetTimeEl.textContent = gResetTok;

    const groqSpeedEl = document.getElementById('groqSpeed');
    if (groqSpeedEl) groqSpeedEl.textContent = gSpeed > 0 ? `${gSpeed} tok/s` : '-- tok/s';

    const groqHealthBadge = document.getElementById('groqHealthBadge');
    if (groqHealthBadge) {
      if (gHealth === 'throttled') {
        groqHealthBadge.textContent = 'THROTTLED';
        groqHealthBadge.style.background = 'rgba(231, 76, 60, 0.2)';
        groqHealthBadge.style.color = '#e74c3c';
      } else if (gHealth === 'near_limit') {
        groqHealthBadge.textContent = 'NEAR LIMIT';
        groqHealthBadge.style.background = 'rgba(241, 196, 15, 0.2)';
        groqHealthBadge.style.color = '#f1c40f';
      } else {
        groqHealthBadge.textContent = 'HEALTHY';
        groqHealthBadge.style.background = 'rgba(46, 204, 113, 0.15)';
        groqHealthBadge.style.color = '#2ecc71';
      }
    }

    const groqTpmBar = document.getElementById('groqTpmBar');
    if (groqTpmBar) {
      groqTpmBar.style.width = `${Math.min(100, Math.max(0, gTpmPct))}%`;
      if (gHealth === 'throttled') {
        groqTpmBar.style.background = 'linear-gradient(90deg, #e74c3c, #c0392b)';
      } else if (gHealth === 'near_limit') {
        groqTpmBar.style.background = 'linear-gradient(90deg, #f1c40f, #d4ac0d)';
      } else {
        groqTpmBar.style.background = 'linear-gradient(90deg, #2ecc71, #27ae60)';
      }
    }

    // Navbar Groq Pill
    const groqPillLabel = document.getElementById('groqPillLabel');
    const groqDot = document.getElementById('groqDot');
    if (groqPillLabel) {
      groqPillLabel.textContent = `Groq: ${Math.round(gTpmPct)}%`;
    }
    if (groqDot) {
      groqDot.style.backgroundColor = gHealth === 'throttled' ? '#e74c3c' : (gHealth === 'near_limit' ? '#f1c40f' : 'var(--color-green)');
    }

    // Network URL
    const networkUrlEl = document.getElementById('networkUrl');
    if (networkUrlEl && data.network_url) {
      networkUrlEl.textContent = data.network_url;
    }

    // Devices pill & count
    const deviceCountLabel = document.getElementById('deviceCountLabel');
    if (deviceCountLabel) {
      deviceCountLabel.textContent = `${activeCount} ${activeCount === 1 ? 'Device' : 'Devices'} Connected`;
    }
    const devicesBadge = document.getElementById('devicesBadge');
    if (devicesBadge) {
      devicesBadge.textContent = `${activeCount} Connected`;
    }

    // 5. Render Timeline
    const turns = session.turns || [];
    const turnsBadge = document.getElementById('turnsBadge');
    if (turnsBadge) turnsBadge.textContent = `${turns.length} ${turns.length === 1 ? 'Turn' : 'Turns'}`;

    if (turns.length !== lastTurnsCount) {
      lastTurnsCount = turns.length;
      renderTimeline(turns);
    }

    // 6. Render Devices
    renderDevices(devices);
  }

  function renderTimeline(turns) {
    const container = document.getElementById('timelineContainer');
    const emptyState = document.getElementById('timelineEmpty');
    if (!container) return;

    if (!turns || turns.length === 0) {
      if (emptyState) emptyState.style.display = 'block';
      return;
    }

    if (emptyState) emptyState.style.display = 'none';

    // Reverse order: show latest turns at the top
    const reversed = [...turns].reverse();
    let html = '';

    reversed.forEach((turn, idx) => {
      const isLatest = idx === 0;
      const promptSnippet = escapeHtml(turn.prompt || 'Untitled Prompt');
      const toolsHtml = (turn.tools_called || []).map(t => `<span class="tool-chip">⏺ ${escapeHtml(t)}</span>`).join(' ');

      html += `
        <div class="turn-card ${isLatest ? 'highlight-card' : ''}">
          <div class="turn-card-header">
            <div class="turn-meta">
              <span class="turn-number">TURN #${turn.turn_index || (turns.length - idx)}</span>
              <span class="turn-time">${turn.time_str || '--:--'}</span>
              <span class="turn-model-badge">${escapeHtml(turn.model || 'Claude')}</span>
            </div>
            <div class="turn-stats">
              <span class="stat-chip">In: <strong>${fmtNum(turn.input_tokens)}</strong></span>
              <span class="stat-chip">Out: <strong>${fmtNum(turn.output_tokens)}</strong></span>
              <span class="stat-chip">Duration: <strong>${turn.duration || 0}s</strong></span>
              <span class="stat-chip">Credits: <strong>${turn.credits_used || 0}</strong></span>
            </div>
          </div>
          <div class="turn-prompt-box">
            ${promptSnippet}
          </div>
          ${toolsHtml ? `<div class="turn-tools-row">${toolsHtml}</div>` : ''}
        </div>
      `;
    });

    container.innerHTML = html;
  }

  function renderDevices(devices) {
    const listEl = document.getElementById('devicesList');
    if (!listEl) return;

    if (!devices || devices.length === 0) {
      listEl.innerHTML = '<div class="empty-state">No connected devices recorded yet.</div>';
      return;
    }

    let html = '';
    devices.forEach(dev => {
      const isOnline = dev.is_online;
      const isCurrentDevice = dev.client_id === deviceId;
      const icon = dev.icon || (dev.device_type === 'Mobile' ? '📱' : dev.device_type === 'Tablet' ? '📟' : '💻');

      html += `
        <div class="device-card ${isOnline ? 'online' : ''}">
          <div class="device-icon">${icon}</div>
          <div class="device-body">
            <div class="device-title-row">
              <span class="device-name">${escapeHtml(dev.name || 'Remote Browser')}${isCurrentDevice ? ' (This Device)' : ''}</span>
              <span class="device-status-badge ${isOnline ? 'online' : 'offline'}">
                ${isOnline ? '● Active' : '○ Offline'}
              </span>
            </div>
            <div class="device-detail">IP Address: ${escapeHtml(dev.ip || '127.0.0.1')}</div>
            <div class="device-detail">Connected: ${escapeHtml(dev.connected_at_str || 'Just now')}</div>
            ${dev.screen ? `<div class="device-detail">Screen: ${escapeHtml(dev.screen)}</div>` : ''}
          </div>
        </div>
      `;
    });

    listEl.innerHTML = html;
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // Copy button
  const copyBtn = document.getElementById('btnCopyUrl');
  if (copyBtn) {
    copyBtn.addEventListener('click', () => {
      const netUrl = document.getElementById('networkUrl')?.textContent || '';
      if (netUrl && navigator.clipboard) {
        navigator.clipboard.writeText(netUrl).then(() => {
          copyBtn.textContent = 'Copied!';
          setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
        });
      }
    });
  }

  // Initial fetch and start 1.5s refresh loop
  fetchUsage();
  setInterval(fetchUsage, 1500);
})();
