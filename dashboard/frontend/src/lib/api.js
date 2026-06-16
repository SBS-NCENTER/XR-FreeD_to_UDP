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
