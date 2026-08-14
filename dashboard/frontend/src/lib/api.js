import { status, fpsHistory } from '../stores.js'

export function connect() {
  fetch('/api/status').then(r => r.json()).then(apply).catch(() => {})
  const es = new EventSource('/events')
  es.onmessage = (e) => apply(JSON.parse(e.data))
  return es
}

function apply(s) {
  status.set(s)
  fpsHistory.update(h => [...h.slice(-59), s.fps || 0])
}

export async function postCmd(cmd) {
  const r = await fetch('/api/cmd', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd }),
  })
  return (await r.json()).reply
}

export async function selectDevice(ip) {
  const r = await fetch('/api/device', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ip }),
  })
  // The chart plots the *selected* device's fps; carrying the old series over
  // would draw a cliff that looks like a fault on the new device.
  if (r.ok) fpsHistory.set([])
  return r.ok
}
