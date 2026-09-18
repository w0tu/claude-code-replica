/**
 * device-agent.js — Standalone Base44 UsageGuard Agent
 * 
 * Reports this computer's AI usage to https://claudecode.base44.app every 2 minutes,
 * respects per-device limits dynamically configured in the dashboard,
 * and mirrors telemetry to lovable.dev when an apiToken is provided.
 * 
 * Implemented using only built-in Node.js "os" and "fs" modules and global fetch.
 */

const os = require('os');
const fs = require('fs');
const path = require('path');

// CONFIGURATION
const CENTRAL_API_URL = process.env.BASE44_URL || 'https://claudecode.base44.app';
const APP_ID = process.env.BASE44_APP_ID || '6aa01ac3955aac8f2331761f';
const HEARTBEAT_URL = `${CENTRAL_API_URL.replace(/\/$/, '')}/api/apps/${APP_ID}/functions/heartbeat`;
const LOVABLE_BASE_URL = process.env.LOVABLE_BASE_URL || '';
const STATE_FILE = path.join(__dirname, 'usage.json');
const HEARTBEAT_INTERVAL_MS = 2 * 60 * 1000; // 2 minutes

// 1. Stable Device Identifier
const DEVICE_NAME = os.hostname() || 'unknown-device';

// 2. Persistent Usage Counter & State
let localState = {
  deviceName: DEVICE_NAME,
  usage: 0,
  maxUsage: 1000,
  apiToken: '',
  status: 'active',
  lastSeen: null
};

function loadState() {
  try {
    if (fs.existsSync(STATE_FILE)) {
      const data = JSON.parse(fs.readFileSync(STATE_FILE, 'utf-8'));
      localState.usage = data.usage || 0;
      localState.maxUsage = data.maxUsage || 1000;
      localState.status = data.status || 'active';
      localState.apiToken = data.apiToken || '';
      console.log(`[UsageGuard] Loaded state from ${STATE_FILE} (Usage: ${localState.usage} / ${localState.maxUsage})`);
    }
  } catch (err) {
    console.warn(`[UsageGuard] Could not read ${STATE_FILE}, starting fresh:`, err.message);
  }
}

function saveState() {
  try {
    const toSave = {
      ...localState,
      apiToken: localState.apiToken ? '***' : ''
    };
    fs.writeFileSync(STATE_FILE, JSON.stringify(toSave, null, 2), 'utf-8');
  } catch (err) {
    console.warn(`[UsageGuard] Failed to save state to ${STATE_FILE}:`, err.message);
  }
}

// 6. Exponential Backoff Helper (1s, 2s, 4s)
async function fetchWithRetry(url, options, maxRetries = 3) {
  const delays = [1000, 2000, 4000];
  let lastError = null;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const res = await fetch(url, options);
      if (res.ok) {
        return await res.json();
      }
      const errBody = await res.text().catch(() => '');
      lastError = new Error(`HTTP ${res.status}: ${errBody.slice(0, 100)}`);
    } catch (err) {
      lastError = err;
    }

    if (attempt < maxRetries) {
      const delay = delays[attempt] || 4000;
      await new Promise(r => setTimeout(r, delay));
    }
  }

  throw lastError;
}

// 3. Heartbeat Transmission
async function sendHeartbeat() {
  console.log(`[UsageGuard] Transmitting heartbeat for '${DEVICE_NAME}' (Usage: ${localState.usage})...`);

  const headers = {
    'Content-Type': 'application/json',
    'X-App-Id': APP_ID,
    'Base44-Functions-Version': 'prod',
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
  };

  const body = JSON.stringify({
    deviceName: DEVICE_NAME,
    usage: localState.usage
  });

  try {
    const response = await fetchWithRetry(HEARTBEAT_URL, {
      method: 'POST',
      headers: headers,
      body: body
    });

    if (response && typeof response === 'object') {
      localState.maxUsage = typeof response.maxUsage === 'number' ? response.maxUsage : localState.maxUsage;
      localState.apiToken = response.apiToken || localState.apiToken || '';
      localState.status = response.status || 'active';
      localState.lastSeen = new Date().toISOString();

      saveState();

      // Log response with redacted apiToken
      const safeLog = {
        maxUsage: localState.maxUsage,
        status: localState.status,
        apiToken: localState.apiToken ? '***' : ''
      };
      console.log(`[UsageGuard] Heartbeat success. Dashboard status:`, safeLog);

      // 4. Rate Limit Status Check
      if (localState.status === 'over_limit' || (localState.maxUsage > 0 && localState.usage >= localState.maxUsage)) {
        console.warn(`[UsageGuard] usage limit reached for ${DEVICE_NAME} (${localState.usage} >= ${localState.maxUsage})`);
      }

      // 5. Mirror to lovable.dev if apiToken delivered
      if (localState.apiToken && LOVABLE_BASE_URL) {
        await forwardToLovable();
      }
    }
  } catch (err) {
    console.error(`[UsageGuard] Heartbeat failed after retries:`, err.message);
  }
}

// 5. Push to lovable.dev
async function forwardToLovable() {
  if (!LOVABLE_BASE_URL || !localState.apiToken) return;

  const url = `${LOVABLE_BASE_URL.replace(/\/$/, '')}/usage`;
  const body = JSON.stringify({
    deviceId: DEVICE_NAME,
    usage: localState.usage,
    timestamp: new Date().toISOString()
  });

  try {
    await fetchWithRetry(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${localState.apiToken}`,
        'Content-Type': 'application/json'
      },
      body: body
    });
    console.log(`[UsageGuard] Successfully mirrored telemetry to lovable.dev`);
  } catch (err) {
    console.warn(`[UsageGuard] Failed to forward to lovable.dev:`, err.message);
  }
}

// MAIN ENTRYPOINT
loadState();

// Initial heartbeat immediately on startup
sendHeartbeat();

// Continuous heartbeat loop every 2 minutes
setInterval(sendHeartbeat, HEARTBEAT_INTERVAL_MS);

console.log(`[UsageGuard] Agent active for ${DEVICE_NAME}. Reporting to ${CENTRAL_API_URL} every 2 minutes.`);
