import { writable } from 'svelte/store'

export const status = writable({
  deviceIp: '', ageSec: 999999, live: false, up: 0, rx: 0, fps: 0,
  dhcpOk: 0, dhcpFail: 0, rtr: '?', conflict: false, targets: [], log: [],
})
export const fpsHistory = writable([])   // for RateChart
