import path from 'node:path'
import { fileURLToPath } from 'node:url'
import dotenv from 'dotenv'
import express from 'express'
import pino from 'pino'
import qrcode from 'qrcode-terminal'
import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
dotenv.config({ path: path.resolve(__dirname, '..', '.env') })

function normalizePhoneDigits(raw) {
  const digits = String(raw || '').replace(/\D/g, '')
  if (digits.length === 11 && /^0[789][01]\d{8}$/.test(digits)) {
    return '234' + digits.slice(1)
  }
  return digits
}

const BRIDGE_SECRET = process.env.BRIDGE_SECRET || 'dev-bridge-secret'
const DJANGO_INBOUND_URL =
  process.env.DJANGO_INBOUND_URL || 'http://127.0.0.1:8000/bridge/inbound/'
const BRIDGE_PORT = Number(process.env.BRIDGE_PORT || 3001)
const PAIRING_PHONE = normalizePhoneDigits(process.env.WA_PAIRING_PHONE || '')
const PAUSE_ALL = ['1', 'true', 'yes', 'on'].includes(
  (process.env.PAUSE_ALL || '').trim().toLowerCase()
)

const ALLOWLIST = new Set(
  (process.env.BAILEYS_ALLOWLIST || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => (s.includes('@') ? s : normalizePhoneDigits(s)))
)

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

let sock = null
let isConnected = false
let pairingRequested = false
const outQueue = []
let draining = false

function extractPhoneDigits(jid, altJid = '') {
  for (const candidate of [jid, altJid]) {
    if (candidate && candidate.endsWith('@s.whatsapp.net')) {
      return normalizePhoneDigits(candidate.split('@')[0].split(':')[0])
    }
  }
  return ''
}

async function resolvePhoneDigits(jid, altJid = '') {
  const direct = extractPhoneDigits(jid, altJid)
  if (direct) return direct

  if (jid && jid.endsWith('@lid') && sock?.signalRepository?.lidMapping) {
    try {
      const mappedPn = await sock.signalRepository.lidMapping.getPNForLID(jid)
      if (mappedPn) {
        return normalizePhoneDigits(mappedPn.split('@')[0].split(':')[0])
      }
    } catch (err) {
      console.warn('[Dayleey Bridge] LID reverse lookup warning:', err.message)
    }
  }
  return ''
}

async function warmAllowlistLidMappings() {
  if (!sock?.signalRepository?.lidMapping || ALLOWLIST.size === 0) return
  const pnJids = [...ALLOWLIST]
    .filter((entry) => /^\d+$/.test(entry))
    .map((digits) => `${digits}@s.whatsapp.net`)
  if (pnJids.length === 0) return
  try {
    await sock.signalRepository.lidMapping.getLIDsForPNs(pnJids)
    console.log(`[Dayleey Bridge] Pre-warmed LID mappings for ${pnJids.length} allowlisted phone(s).`)
  } catch (err) {
    console.warn('[Dayleey Bridge] Could not pre-warm LID mappings:', err.message)
  }
}

function isAllowed(jid, phoneDigits) {
  if (ALLOWLIST.size === 0) return true
  if (phoneDigits && ALLOWLIST.has(phoneDigits)) return true
  if (jid && ALLOWLIST.has(jid)) return true
  const rawPrefix = normalizePhoneDigits((jid || '').split('@')[0].split(':')[0])
  return Boolean(rawPrefix && ALLOWLIST.has(rawPrefix))
}

function extractText(message) {
  if (!message) return ''
  if (message.conversation) return message.conversation
  if (message.extendedTextMessage?.text) return message.extendedTextMessage.text
  if (message.ephemeralMessage?.message) return extractText(message.ephemeralMessage.message)
  if (message.viewOnceMessage?.message) return extractText(message.viewOnceMessage.message)
  return ''
}

async function startSocket() {
  const authDir = path.join(__dirname, 'auth')
  const { state, saveCreds } = await useMultiFileAuthState(authDir)
  const { version } = await fetchLatestBaileysVersion()

  sock = makeWASocket({
    version,
    auth: state,
    logger: pino({ level: 'warn' }),
    markOnlineOnConnect: false,
  })

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update

    if (qr) {
      if (PAIRING_PHONE && !state.creds.registered && !pairingRequested) {
        pairingRequested = true
        try {
          const code = await sock.requestPairingCode(PAIRING_PHONE)
          console.log(`\n[Dayleey Bridge] Pairing code for +${PAIRING_PHONE}: ${code}\n`)
        } catch (err) {
          console.error('[Dayleey Bridge] Pairing code failed, falling back to QR:', err.message)
          qrcode.generate(qr, { small: true })
        }
      } else if (!PAIRING_PHONE) {
        console.log('\n[Dayleey Bridge] Scan this QR code with your dedicated WhatsApp SIM:\n')
        qrcode.generate(qr, { small: true })
      }
    }

    if (connection === 'open') {
      isConnected = true
      pairingRequested = false
      console.log('[Dayleey Bridge] WhatsApp connected.')
      warmAllowlistLidMappings()
      drainQueue()
    }

    if (connection === 'close') {
      isConnected = false
      const statusCode = lastDisconnect?.error?.output?.statusCode
      if (statusCode === DisconnectReason.loggedOut) {
        console.error(
          '[Dayleey Bridge] Logged out. Delete bridge/auth/ and restart to link again.'
        )
      } else {
        console.warn(`[Dayleey Bridge] Disconnected (code=${statusCode}). Reconnecting in 5s...`)
        setTimeout(startSocket, 5000)
      }
    }
  })

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return

    for (const m of messages) {
      if (!m.message || m.key?.fromMe) continue

      const jid = m.key.remoteJid || ''
      const altJid = m.key.remoteJidAlt || ''
      if (!jid || jid.endsWith('@g.us') || jid === 'status@broadcast') continue

      const phoneDigits = await resolvePhoneDigits(jid, altJid)
      if (!isAllowed(jid, phoneDigits)) {
        console.log(
          `[Dayleey Bridge] Ignored non-allowlisted inbound from jid=${jid} phone=${phoneDigits || 'unknown'}`
        )
        continue
      }

      const text = extractText(m.message).trim()
      if (!text) continue

      const payload = {
        id: m.key.id,
        jid,
        phone: phoneDigits,
        text,
        pushName: m.pushName || '',
        ts: m.messageTimestamp,
      }

      try {
        const resp = await fetch(DJANGO_INBOUND_URL, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Bridge-Secret': BRIDGE_SECRET,
          },
          body: JSON.stringify(payload),
        })
        if (!resp.ok) {
          console.error(`[Dayleey Bridge] Django inbound returned ${resp.status}`)
        }
      } catch (err) {
        console.error('[Dayleey Bridge] Failed forwarding to Django:', err.message)
      }
    }
  })
}

async function drainQueue() {
  if (draining || !isConnected || !sock) return
  draining = true
  try {
    while (outQueue.length > 0 && isConnected) {
      const item = outQueue.shift()
      try {
        await sock.sendPresenceUpdate('composing', item.jid)
        await sleep(800 + Math.random() * 1500)
        await sock.sendMessage(item.jid, { text: item.text })
        await sock.sendPresenceUpdate('paused', item.jid)
      } catch (err) {
        console.error(`[Dayleey Bridge] Send failed to ${item.jid}:`, err.message)
      }
      await sleep(1500 + Math.random() * 2500)
    }
  } finally {
    draining = false
  }
}

const app = express()
app.use(express.json())

app.get('/health', (_req, res) => {
  res.json({
    ok: true,
    connected: isConnected,
    queued: outQueue.length,
    allowlist: [...ALLOWLIST],
  })
})

app.post('/send', async (req, res) => {
  if (req.get('X-Bridge-Secret') !== BRIDGE_SECRET) {
    return res.sendStatus(401)
  }
  if (PAUSE_ALL) {
    return res.status(503).json({ error: 'PAUSE_ALL is enabled' })
  }

  const { jid, phone, text } = req.body || {}
  if (!jid || !text) {
    return res.status(400).json({ error: 'jid and text are required' })
  }

  const digits = normalizePhoneDigits(phone || '') || (await resolvePhoneDigits(jid))
  if (!isAllowed(jid, digits)) {
    return res.status(403).json({ error: 'Recipient not in BAILEYS_ALLOWLIST' })
  }

  outQueue.push({ jid, text: String(text) })
  drainQueue()
  return res.json({ queued: true, queueLength: outQueue.length })
})

app.listen(BRIDGE_PORT, '127.0.0.1', () => {
  console.log(
    `[Dayleey Bridge] Listening on http://127.0.0.1:${BRIDGE_PORT} (allowlist=${[...ALLOWLIST].join(',') || 'UNRESTRICTED'})`
  )
})

startSocket()
