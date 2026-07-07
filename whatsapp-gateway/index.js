// WhatsApp gateway for the Parser2GIS outreach platform.
//
// Responsibilities:
//   * Log in to WhatsApp via a QR code (scanned once; session persisted on disk).
//   * Expose a tiny HTTP API the Python backend uses:
//       GET  /            -> human page with the live QR + status
//       GET  /status      -> { connected, hasQr, user }
//       GET  /qr          -> { qr: <dataURL|null> }
//       POST /send        -> { phone, message } -> sends a WhatsApp text
//       POST /logout      -> drop the session (forces a fresh QR)
//
// Anti-ban niceties (typing simulation, delays, daily limit) live in the
// Python campaign orchestrator; this service just sends what it is told to.

import { fileURLToPath } from 'url'
import path from 'path'
import fs from 'fs'
import express from 'express'
import pino from 'pino'
import QRCode from 'qrcode'
import qrcodeTerminal from 'qrcode-terminal'
import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} from '@whiskeysockets/baileys'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const PORT = parseInt(process.env.PORT || '8667', 10)
const AUTH_DIR = process.env.AUTH_DIR || path.join(__dirname, 'auth')

const logger = pino({ level: process.env.LOG_LEVEL || 'warn' })

// --- live connection state (read by the HTTP endpoints) ------------------
let sock = null
let qrString = null      // raw QR payload from Baileys
let qrDataUrl = null     // QR rendered as a data: URL for the web page
let connected = false
let meUser = null        // { id, name } once logged in
let starting = false

async function connectToWhatsApp() {
  if (starting) return
  starting = true
  try {
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)
    const { version } = await fetchLatestBaileysVersion()

    sock = makeWASocket({
      version,
      auth: state,
      logger,
      // We render the QR ourselves (web page + terminal), so keep Baileys quiet.
      printQRInTerminal: false,
      browser: ['Parser2GIS Outreach', 'Chrome', '1.0.0'],
      syncFullHistory: false,
      markOnlineOnConnect: false,
    })

    sock.ev.on('creds.update', saveCreds)

    sock.ev.on('connection.update', async (update) => {
      const { connection, lastDisconnect, qr } = update

      if (qr) {
        qrString = qr
        connected = false
        try {
          qrDataUrl = await QRCode.toDataURL(qr, { margin: 1, width: 320 })
        } catch (e) {
          qrDataUrl = null
        }
        qrcodeTerminal.generate(qr, { small: true })
        console.log('\n[qr] Ашыңыз / Открой: http://localhost:' + PORT + '  → отсканируй QR в WhatsApp.\n')
      }

      if (connection === 'open') {
        connected = true
        qrString = null
        qrDataUrl = null
        meUser = sock.user || null
        console.log('[ok] WhatsApp подключён:', meUser && meUser.id)
      }

      if (connection === 'close') {
        connected = false
        const statusCode =
          lastDisconnect && lastDisconnect.error && lastDisconnect.error.output
            ? lastDisconnect.error.output.statusCode
            : null
        const loggedOut = statusCode === DisconnectReason.loggedOut
        console.log('[close] Соединение закрыто. code=', statusCode, 'loggedOut=', loggedOut)
        starting = false
        if (loggedOut) {
          // Session invalidated: wipe creds so the next start shows a fresh QR.
          try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }) } catch (_) {}
          meUser = null
        }
        // Reconnect (a fresh QR will be emitted if creds were wiped).
        setTimeout(connectToWhatsApp, 2000)
        return
      }
    })
  } finally {
    starting = false
  }
}

// --- normalize a phone into a WhatsApp jid -------------------------------
function toJid(phone) {
  const digits = String(phone || '').replace(/\D/g, '')
  if (!digits) return null
  return digits + '@s.whatsapp.net'
}

// --- HTTP API ------------------------------------------------------------
const app = express()
app.use(express.json())

app.get('/status', (req, res) => {
  res.json({
    connected,
    hasQr: !!qrDataUrl,
    user: meUser ? { id: meUser.id, name: meUser.name || null } : null,
  })
})

app.get('/qr', (req, res) => {
  res.json({ qr: qrDataUrl })
})

app.post('/send', async (req, res) => {
  if (!connected || !sock) {
    return res.status(503).json({ ok: false, error: 'WhatsApp не подключён' })
  }
  const { phone, message } = req.body || {}
  const jid = toJid(phone)
  if (!jid || !message) {
    return res.status(400).json({ ok: false, error: 'Нужны phone и message' })
  }
  try {
    // Verify the number is actually on WhatsApp before sending.
    const [info] = await sock.onWhatsApp(jid)
    if (!info || !info.exists) {
      return res.status(404).json({ ok: false, error: 'Номер не в WhatsApp' })
    }
    const sent = await sock.sendMessage(info.jid, { text: String(message) })
    res.json({ ok: true, id: sent && sent.key ? sent.key.id : null })
  } catch (e) {
    res.status(500).json({ ok: false, error: String(e && e.message ? e.message : e) })
  }
})

app.post('/logout', async (req, res) => {
  try {
    if (sock) await sock.logout()
  } catch (_) {}
  try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }) } catch (_) {}
  connected = false
  meUser = null
  res.json({ ok: true })
  setTimeout(connectToWhatsApp, 500)
})

// Human-friendly QR / status page.
app.get('/', (req, res) => {
  res.type('html').send(PAGE_HTML)
})

const PAGE_HTML = `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WhatsApp — подключение</title>
<style>
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f5f6fa;color:#0f172a;
       display:flex;min-height:100vh;margin:0;align-items:center;justify-content:center}
  .card{background:#fff;border:1px solid #e6e8ef;border-radius:16px;padding:28px 32px;max-width:420px;
        text-align:center;box-shadow:0 8px 30px rgba(15,23,42,.06)}
  h1{font-size:20px;margin:0 0 4px}
  p{color:#64748b;font-size:14px;margin:6px 0 18px}
  .qr{width:300px;height:300px;object-fit:contain;border-radius:12px;background:#fff}
  .qrbox{min-height:300px;display:flex;align-items:center;justify-content:center}
  .badge{display:inline-block;font-weight:700;font-size:13px;padding:6px 14px;border-radius:20px;margin-top:6px}
  .ok{background:#dcfce7;color:#15803d}.wait{background:#eef2ff;color:#4f51e0}.err{background:#fef2f2;color:#dc2626}
  .steps{text-align:left;font-size:13px;color:#475569;margin:16px auto 0;max-width:320px;line-height:1.6}
  .spin{width:34px;height:34px;border:3px solid #e6e8ef;border-top-color:#6366f1;border-radius:50%;animation:s 1s linear infinite}
  @keyframes s{to{transform:rotate(360deg)}}
  button{margin-top:16px;font-size:13px;font-weight:700;color:#64748b;background:transparent;
         border:1px dashed #e6e8ef;border-radius:9px;padding:8px 14px;cursor:pointer}
</style></head><body>
<div class="card">
  <h1>💬 Подключение WhatsApp</h1>
  <p id="sub">Отсканируйте QR-код в приложении WhatsApp</p>
  <div class="qrbox" id="qrbox"><div class="spin"></div></div>
  <div><span class="badge wait" id="badge">Ожидание QR…</span></div>
  <div class="steps">
    1. Открой WhatsApp на телефоне<br>
    2. Настройки → <b>Связанные устройства</b><br>
    3. <b>Привязка устройства</b> → наведи на QR
  </div>
  <button onclick="fetch('/logout',{method:'POST'}).then(()=>location.reload())">Сбросить сессию</button>
</div>
<script>
async function tick(){
  try{
    const s = await (await fetch('/status')).json();
    const badge=document.getElementById('badge'), box=document.getElementById('qrbox'), sub=document.getElementById('sub');
    if(s.connected){
      badge.className='badge ok'; badge.textContent='✓ Подключено'+(s.user&&s.user.id?(' · '+s.user.id.split(':')[0].split('@')[0]):'');
      sub.textContent='Готово! Номер подключён. Эту вкладку можно закрыть.';
      box.innerHTML='<div style="font-size:64px">✅</div>';
      return; // stop polling once connected
    }
    const q = await (await fetch('/qr')).json();
    if(q.qr){
      badge.className='badge wait'; badge.textContent='Ожидание сканирования…';
      box.innerHTML='<img class="qr" src="'+q.qr+'" alt="QR">';
    }else{
      badge.className='badge wait'; badge.textContent='Готовим QR…';
      box.innerHTML='<div class="spin"></div>';
    }
  }catch(e){
    document.getElementById('badge').className='badge err';
    document.getElementById('badge').textContent='Шлюз недоступен';
  }
  setTimeout(tick, 2000);
}
tick();
</script>
</body></html>`

app.listen(PORT, () => {
  console.log('[gateway] http://localhost:' + PORT + '  (порт для QR и API)')
  connectToWhatsApp()
})
