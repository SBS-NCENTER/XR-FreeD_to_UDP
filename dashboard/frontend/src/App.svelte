<script>
  import { onMount, onDestroy } from 'svelte'
  import { status, fpsHistory } from './stores.js'
  import { connect, postCmd } from './lib/api.js'
  import StatusBar from './lib/StatusBar.svelte'
  import TargetCard from './lib/TargetCard.svelte'
  import EventLog from './lib/EventLog.svelte'
  import ThemeToggle from './lib/ThemeToggle.svelte'
  import RateChart from './lib/RateChart.svelte'
  let es
  onMount(() => { es = connect() })
  onDestroy(() => es && es.close())
  function reboot(){ if(confirm('Reboot the device? FreeD output stops briefly.')) postCmd('reboot').then(r=>alert(r)) }
</script>

<h1>
  <span class="dot" class:live={$status.live} class:dead={!$status.live}></span>
  XRFD Dashboard
  <span class="dev">device: {$status.deviceIp || '-'}{$status.live ? '' : `  (no signal ${$status.ageSec}s)`}</span>
  <ThemeToggle/>
</h1>
<StatusBar s={$status}/>
<RateChart history={$fpsHistory}/>
<div class="grid">
  {#each $status.targets as t}<TargetCard {t}/>{:else}<div class="card">No target info yet...</div>{/each}
</div>
<div class="foot"><h2>Event log</h2><button class="b-reboot" on:click={reboot}>Reboot device</button></div>
<EventLog log={$status.log}/>
