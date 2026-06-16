<script>
  import { postCmd } from './api.js'
  export let t
  function toggle(){ postCmd('target '+t.n+' '+(t.on?'off':'on')) }
  function edit(){
    const ip=prompt('Target '+t.n+' IP:',t.ip); if(ip===null)return
    const port=prompt('Target '+t.n+' port:',t.port); if(port===null)return
    postCmd('target '+t.n+' set '+ip.trim()+' '+port.trim()).then(r=>alert(r))
  }
  $: badge = t.on ? (t.state==='B'?'BACKOFF':(t.state==='C'?'NO SOCKET':'ON')) : 'OFF'
  $: cls = t.on ? ((t.state==='B'||t.state==='C')?'backoff':'on') : 'off'
</script>

<div class="card" class:dim={!t.on}>
  <div class="hd"><span class="nm">Target {t.n}</span><span class="badge {cls}">{badge}</span></div>
  <div class="addr">{t.ip} : {t.port}</div>
  <div class="cnt">ok {t.ok.toLocaleString()} &nbsp; fail <span class:bad={t.fail>0}>{t.fail}</span> &nbsp; skip {t.skip}</div>
  <div class="btns">
    <button class={t.on?'b-off':'b-on'} on:click={toggle}>{t.on?'Turn OFF':'Turn ON'}</button>
    <button class="b-edit" on:click={edit}>Edit IP/Port</button>
  </div>
</div>
