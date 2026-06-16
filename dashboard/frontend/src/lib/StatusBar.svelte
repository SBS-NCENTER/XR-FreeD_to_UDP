<script>
  export let s
  function fmtUp(x){const d=Math.floor(x/86400),h=Math.floor(x%86400/3600),m=Math.floor(x%3600/60);
    return (d?d+'d ':'')+(h?h+'h ':'')+m+'m '+(x%60)+'s'}
</script>

<div class="bar">
  <div class="chip">Uptime<b>{fmtUp(s.up)}</b></div>
  <div class="chip">FreeD rate<b>{(s.fps||0).toFixed(2)} fps</b></div>
  <div class="chip" class:warn={s.dhcpFail>0}>DHCP renew<b>{s.dhcpOk} / {s.dhcpFail} fail</b></div>
  <div class="chip" class:bad={s.rtr!=='Y'}>RTR patch<b>{s.rtr==='Y'?'OK (80ms)':'FAILED'}</b></div>
  <div class="chip">Frames<b>{(s.rx||0).toLocaleString()}</b></div>
  {#if s.conflict}<div class="chip bad">IP conflict<b>DETECTED</b></div>{/if}
</div>
