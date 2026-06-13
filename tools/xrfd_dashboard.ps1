# XRFD Dashboard - web GUI bridge (Windows PowerShell, no install required)
#
# Runs a tiny HTTP server on this PC and bridges it to the Arduino:
#   - caches the device's diag broadcast (UDP 50999)  -> live status
#   - relays control commands to the device (UDP 50998)
# Anyone on the LAN can open the dashboard in a browser:
#   http://<this-pc-ip>:10000     (or http://localhost:10000 on this PC)
#
# Launch via xrfd_dashboard.bat (double-click) or:
#   powershell -ExecutionPolicy Bypass -File .\xrfd_dashboard.ps1 [-Port 10000]
#
# One-time firewall (admin): run xrfd_firewall_setup.bat
#   (inbound UDP 50999 for diag, inbound TCP 10000 for other PCs' browsers)

param(
  [int]$Port = 10000,
  [int]$CtrlPort = 50998,
  [int]$DiagPort = 50999
)

$ErrorActionPreference = 'Continue'

$script:StartTime = Get-Date
# --- 표준 규약: pidfile(메타데이터) 기록 ---
$RepoRoot = Split-Path -Parent $PSScriptRoot              # tools/ 의 상위 = 프로젝트 루트
$DataDir  = Join-Path $RepoRoot 'data'
$script:PidFile = Join-Path $DataDir 'xrfd.pid'
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$pidMeta = [ordered]@{
  pid        = $PID
  command    = "powershell -File tools/xrfd_dashboard.ps1 -Port $Port"
  started_at = (Get-Date).ToString('o')
} | ConvertTo-Json -Compress
Set-Content -Path $script:PidFile -Value $pidMeta -Encoding UTF8
Register-EngineEvent PowerShell.Exiting -Action {
  if (Test-Path $script:PidFile) { Remove-Item $script:PidFile -Force -ErrorAction SilentlyContinue }
} | Out-Null

# ── shared state ─────────────────────────────────────────────────────────────
$S = @{
  deviceIp = ''
  lastSeen = [DateTime]::MinValue
  up = 0; rx = 0; dhcpOk = 0; dhcpFail = 0; rtr = '?'
  fps = 0.0; prevRx = [long]-1; prevMs = [long]-1; prevTime = $null
  diagState = @{}    # per-target letter/counters from diag (t0 -> A,ok,fail,skip)
  targets = @()      # per-target config from 'targets' command (on/ip/port)
  log = New-Object System.Collections.ArrayList
  lastTargetsRefresh = [DateTime]::MinValue
  prevFail = @{}
  ignoredIps = @{}
  conflict = $false
}

function Add-Log([string]$kind, [string]$msg) {
  [void]$S.log.Insert(0, @{ t = (Get-Date -Format 'HH:mm:ss'); k = $kind; m = $msg })
  while ($S.log.Count -gt 80) { $S.log.RemoveAt($S.log.Count - 1) }
}

# ── UDP: diag listener + control sender ──────────────────────────────────────
$diag = New-Object Net.Sockets.UdpClient
$diag.ExclusiveAddressUse = $false
$diag.Client.SetSocketOption([Net.Sockets.SocketOptionLevel]::Socket,
  [Net.Sockets.SocketOptionName]::ReuseAddress, $true)
$diag.Client.Bind((New-Object Net.IPEndPoint([Net.IPAddress]::Any, $DiagPort)))

function Send-Ctrl([string]$cmd, [int]$timeoutMs = 3000) {
  if ($S.deviceIp -eq '') { return $null }
  $u = New-Object Net.Sockets.UdpClient
  $u.Client.ReceiveTimeout = $timeoutMs
  $b = [Text.Encoding]::ASCII.GetBytes($cmd)
  [void]$u.Send($b, $b.Length, $S.deviceIp, $CtrlPort)
  $ep = New-Object Net.IPEndPoint(0, 0)
  try { return [Text.Encoding]::ASCII.GetString($u.Receive([ref]$ep)) }
  catch { return $null }
  finally { $u.Close() }
}

function Refresh-Targets {
  $S.lastTargetsRefresh = Get-Date # 시도 시각 기록 — 장비 무응답이어도 30s에 1회만
  $r = Send-Ctrl 'targets' 2000
  if ($null -eq $r) { return }
  $list = @()
  foreach ($line in ($r -split "`n")) {
    if ($line -match '^t(\d) (on|off) (\d+\.\d+\.\d+\.\d+):(\d+)') {
      $list += @{ n = [int]$Matches[1]; on = ($Matches[2] -eq 'on')
                  ip = $Matches[3]; port = [int]$Matches[4] }
    }
  }
  if ($list.Count -gt 0) { $S.targets = $list; $S.lastTargetsRefresh = Get-Date }
}

function Process-DiagLine([string]$line, [string]$srcIp) {
  $now = Get-Date
  if ($line -notmatch 'XRFD up=(\d+) (?:ms=(\d+) )?ip=(\S+) rx=(\d+) dhcp=(\d+)/(\d+) rtr=(\w)') { return }
  # 두 번째 XRFD 장비 무시(현재 장비가 live인 동안) — last-broadcaster-wins면
  # up/fps가 요동치고 명령이 엉뚱한 장비로 간다. 현재 장비가 12초 이상
  # 침묵하면 새 장비를 채택한다.
  if ($S.deviceIp -ne '' -and $srcIp -ne $S.deviceIp -and
      ((Get-Date) - $S.lastSeen).TotalSeconds -le 12) {
    if (-not $S.ignoredIps.ContainsKey($srcIp)) {
      $S.ignoredIps[$srcIp] = $true
      Add-Log 'warn' "ignoring second XRFD device at $srcIp (active: $($S.deviceIp))"
    }
    return
  }
  $up = [long]$Matches[1]; $rx = [long]$Matches[4]
  $ms = [long]-1; if ($Matches[2]) { $ms = [long]$Matches[2] }
  # 진짜 재부팅은 up과 rx가 함께 리셋된다 — millis() wrap(49.7일)은 up만
  # 리셋되므로 오경보하지 않는다
  if ($S.deviceIp -ne '' -and $up -lt $S.up -and $rx -lt $S.rx) {
    Add-Log 'warn' "DEVICE REBOOTED (uptime reset: $($S.up)s -> ${up}s)"
    $S.prevRx = [long]-1; $S.prevMs = [long]-1
  }
  if ($S.deviceIp -eq '') { Add-Log 'info' "device found: $srcIp" }
  $S.deviceIp = $srcIp; $S.lastSeen = $now
  $S.up = $up; $S.rx = $rx
  $S.dhcpOk = [long]$Matches[5]; $S.dhcpFail = [long]$Matches[6]
  $S.rtr = $Matches[7]
  $cf = $line.Contains(' CONFLICT')
  if ($cf -and -not $S.conflict) { Add-Log 'warn' 'IP CONFLICT detected on device LAN!' }
  $S.conflict = $cf
  # fps는 장치 자체 시계(ms=)로 계산 — rx와 ms가 같은 패킷에 실려 짝이 맞으므로
  # PC측 도착/처리 지연(HTTP 작업 등)이 결과에 영향을 주지 않는다.
  if ($ms -ge 0 -and $S.prevMs -ge 0 -and $ms -gt $S.prevMs -and $rx -ge $S.prevRx) {
    $dms = $ms - $S.prevMs
    if ($dms -ge 1000) { $S.fps = [math]::Round(($rx - $S.prevRx) * 1000.0 / $dms, 2) }
  }
  elseif ($ms -lt 0 -and $S.prevRx -ge 0 -and $rx -gt $S.prevRx -and $null -ne $S.prevTime) {
    # 구버전 firmware (ms= 없음): PC 도착 시각 fallback — 부정확할 수 있음
    $dt = ($now - $S.prevTime).TotalSeconds
    if ($dt -gt 0.5) { $S.fps = [math]::Round(($rx - $S.prevRx) / $dt, 2) }
  }
  $S.prevRx = $rx; $S.prevMs = $ms; $S.prevTime = $now

  $S.diagState = @{}
  foreach ($m in [regex]::Matches($line, ' t(\d)=(off|[ABC]),?(\d+)?,?(\d+)?,?(\d+)?')) {
    $i = [int]$m.Groups[1].Value
    $st = $m.Groups[2].Value
    $fail = 0; if ($m.Groups[4].Success) { $fail = [uint32]$m.Groups[4].Value }
    $S.diagState["$i"] = @{
      state = $st
      ok = $(if ($m.Groups[3].Success) { [uint32]$m.Groups[3].Value } else { 0 })
      fail = $fail
      skip = $(if ($m.Groups[5].Success) { [uint32]$m.Groups[5].Value } else { 0 })
    }
    if ($S.prevFail.ContainsKey("$i") -and $fail -gt $S.prevFail["$i"]) {
      Add-Log 'warn' "target $i sendFail increased -> $fail"
    }
    $S.prevFail["$i"] = $fail
  }
}

# ── HTTP server (raw TcpListener - no admin URL ACL needed) ──────────────────
$listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Any, $Port)
try { $listener.Start() }
catch {
  Write-Host "ERROR: TCP port $Port already in use - another dashboard window open?" -ForegroundColor Red
  Write-Host "       Close it, or relaunch with: xrfd_dashboard.bat -Port <other>" -ForegroundColor Red
  exit 1
}

function Send-Http($client, [string]$status, [string]$ctype, [byte[]]$body) {
  try {
    $hdr = "HTTP/1.1 $status`r`nContent-Type: $ctype; charset=utf-8`r`n" +
           "Content-Length: $($body.Length)`r`nConnection: close`r`n" +
           "Cache-Control: no-store`r`n`r`n"
    $hb = [Text.Encoding]::ASCII.GetBytes($hdr)
    $st = $client.GetStream()
    $st.Write($hb, 0, $hb.Length)
    $st.Write($body, 0, $body.Length)
    $st.Flush()
  } catch { }
  finally { $client.Close() }
}

function Get-StatusJson {
  # clamp: lastSeen=MinValue(첫 패킷 전)이면 Int32 범위 초과로 throw됨
  $age = [int][math]::Min(999999, ((Get-Date) - $S.lastSeen).TotalSeconds)
  $tl = @()
  foreach ($t in $S.targets) {
    $d = $S.diagState["$($t.n)"]
    $entry = @{ n = $t.n; on = $t.on; ip = $t.ip; port = $t.port
                state = 'off'; ok = 0; fail = 0; skip = 0 }
    if ($null -ne $d) {
      $entry.state = $d.state; $entry.ok = $d.ok
      $entry.fail = $d.fail; $entry.skip = $d.skip
    }
    $tl += $entry
  }
  return (@{
    deviceIp = $S.deviceIp; ageSec = $age
    live = ($S.deviceIp -ne '' -and $age -le 12)
    up = $S.up; rx = $S.rx; fps = $S.fps
    dhcpOk = $S.dhcpOk; dhcpFail = $S.dhcpFail; rtr = $S.rtr
    conflict = $S.conflict
    targets = $tl; log = @($S.log | Select-Object -First 40)
  } | ConvertTo-Json -Depth 6 -Compress)
}

function Handle-Request($client) {
  try {
    $stream = $client.GetStream()
    $stream.ReadTimeout = 1000
    $buf = New-Object byte[] 8192
    $n = $stream.Read($buf, 0, $buf.Length)
    if ($n -le 0) { $client.Close(); return }
    $req = [Text.Encoding]::ASCII.GetString($buf, 0, $n)
    $line1 = ($req -split "`r`n")[0]
    if ($line1 -notmatch '^(GET|POST) (\S+) HTTP') {
      Send-Http $client '400 Bad Request' 'text/plain' ([Text.Encoding]::UTF8.GetBytes('bad request')); return
    }
    $path = $Matches[2]

    if ($path -eq '/health') {
      # raw TcpListener라 HttpListener의 IsLocal이 없음 — 원격 IP가 루프백인지로 판별
      $isLocal = $false
      try { $isLocal = [Net.IPAddress]::IsLoopback($client.Client.RemoteEndPoint.Address) } catch { }
      $obj = [ordered]@{ status = 'ok' }
      if ($isLocal) {
        $obj.pid = $PID
        $obj.uptime_seconds = [math]::Round(((Get-Date) - $script:StartTime).TotalSeconds, 1)
        $obj.device = $S.deviceIp
      }
      $json = $obj | ConvertTo-Json -Compress
      Send-Http $client '200 OK' 'application/json' ([Text.Encoding]::UTF8.GetBytes($json))
      return
    }
    elseif ($path -eq '/' -or $path -eq '/index.html') {
      Send-Http $client '200 OK' 'text/html' ([Text.Encoding]::UTF8.GetBytes($script:HTML))
    }
    elseif ($path -eq '/api/status') {
      Send-Http $client '200 OK' 'application/json' ([Text.Encoding]::UTF8.GetBytes((Get-StatusJson)))
    }
    elseif ($path -match '^/api/cmd\?c=(.+)$') {
      $cmd = [Uri]::UnescapeDataString($Matches[1])
      $reply = Send-Ctrl $cmd
      if ($null -eq $reply) { $reply = '(no reply from device)' }
      Add-Log 'cmd' "$cmd  ->  $reply"
      if ($cmd -match '^target ') { Refresh-Targets }
      Send-Http $client '200 OK' 'text/plain' ([Text.Encoding]::UTF8.GetBytes($reply))
    }
    else {
      Send-Http $client '404 Not Found' 'text/plain' ([Text.Encoding]::UTF8.GetBytes('not found'))
    }
  }
  catch { try { $client.Close() } catch { } }
}

# ── embedded dashboard page ──────────────────────────────────────────────────
$HTML = @'
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>XRFD Dashboard</title>
<style>
/* themes: dark = Monokai, light = Solarized */
*{box-sizing:border-box;margin:0;padding:0}
body.dark{--bg:#272822;--fg:#f8f8f2;--head:#f8f8f2;--panel:#3e3d32;--border:#49483e;--muted:#a59f85;--accent:#66d9ef;--good:#a6e22e;--goodtext:#1e1f1c;--bad:#f92672;--badtext:#fff;--warn:#fd971f;--warntext:#1e1f1c;--violet:#ae81ff;--log:#1e1f1c;--logline:#3e3d32;--btn:#49483e;--addr:#e6db74}
body.light{--bg:#fdf6e3;--fg:#657b83;--head:#586e75;--panel:#eee8d5;--border:#d3cbb7;--muted:#93a1a1;--accent:#268bd2;--good:#859900;--goodtext:#fdf6e3;--bad:#dc322f;--badtext:#fdf6e3;--warn:#cb4b16;--warntext:#fdf6e3;--violet:#6c71c4;--log:#f5eed8;--logline:#e0d9c3;--btn:#d3cbb7;--addr:#b58900}
body{font-family:'Segoe UI','Courier New',monospace;padding:14px;max-width:920px;margin:0 auto;background:var(--bg);color:var(--fg);transition:background .3s,color .3s}
h1{font-size:18px;color:var(--accent);display:flex;align-items:center;gap:10px;margin-bottom:10px}
.dot{width:12px;height:12px;border-radius:50%;background:var(--muted);display:inline-block}
.dot.live{background:var(--good);box-shadow:0 0 8px var(--good)}
.dot.dead{background:var(--bad);box-shadow:0 0 8px var(--bad)}
.dev{color:var(--muted);font-size:13px;font-weight:400;margin-left:auto}
.btn-theme{width:36px;height:30px;border:none;border-radius:8px;background:var(--btn);color:var(--fg);font-size:16px;cursor:pointer}
.bar{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}
.chip{background:var(--panel);border-radius:8px;padding:8px 14px;font-size:13px;border:1px solid var(--border)}
.chip b{color:var(--accent);font-size:16px;display:block}
.chip.warn b{color:var(--warn)}
.chip.bad b{color:var(--bad)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
@media(max-width:640px){.grid{grid-template-columns:1fr}}
.card{background:var(--panel);border-radius:12px;padding:14px;border:1px solid var(--border)}
.card.dim{opacity:0.55}
.card .hd{display:flex;align-items:center;margin-bottom:8px}
.card .nm{font-weight:700;font-size:15px;color:var(--head)}
.badge{margin-left:auto;font-size:11px;font-weight:700;border-radius:6px;padding:3px 10px}
.badge.on{background:var(--good);color:var(--goodtext)}
.badge.off{background:var(--muted);color:var(--bg)}
.badge.backoff{background:var(--warn);color:var(--warntext)}
.addr{font-size:14px;color:var(--addr);margin-bottom:6px}
.cnt{font-size:12px;color:var(--muted);margin-bottom:10px}
.cnt .bad{color:var(--bad);font-weight:700}
.btns{display:flex;gap:8px}
button{border:none;border-radius:8px;padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer}
.b-on{background:var(--good);color:var(--goodtext)}
.b-off{background:var(--bad);color:var(--badtext)}
.b-edit{background:var(--btn);color:var(--fg)}
.b-reboot{background:var(--violet);color:#fff}
button:hover{opacity:.85}
.foot{display:flex;align-items:center;margin-bottom:8px}
.foot h2{font-size:14px;color:var(--accent)}
.foot .b-reboot{margin-left:auto}
.log{background:var(--log);border-radius:10px;padding:10px;font-size:12px;font-family:'Courier New',monospace;max-height:220px;overflow-y:auto;border:1px solid var(--border)}
.log div{padding:2px 0;border-bottom:1px solid var(--logline);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.log .warn{color:var(--warn)}
.log .cmd{color:var(--accent)}
</style>
</head>
<body class="dark">
<h1><span class="dot" id="dot"></span>XRFD Dashboard<span class="dev" id="dev">device: -</span><button class="btn-theme" onclick="toggleTheme()" title="Monokai / Solarized">&#9789;</button></h1>
<div class="bar">
<div class="chip">Uptime<b id="up">-</b></div>
<div class="chip">FreeD rate<b id="fps">-</b></div>
<div class="chip" id="dhcpChip">DHCP renew<b id="dhcp">-</b></div>
<div class="chip" id="rtrChip">RTR patch<b id="rtr">-</b></div>
<div class="chip">Frames<b id="rx">-</b></div>
<div class="chip bad" id="cfChip" style="display:none">IP conflict<b>DETECTED</b></div>
</div>
<div class="grid" id="grid"></div>
<div class="foot"><h2>Event log</h2><button class="b-reboot" onclick="doReboot()">Reboot device</button></div>
<div class="log" id="log"></div>
<script>
var theme=localStorage.getItem('xrfd_theme')||'dark';
document.body.className=theme;
function toggleTheme(){theme=(theme==='dark')?'light':'dark';
 document.body.className=theme;localStorage.setItem('xrfd_theme',theme);}
function fmtUp(s){var d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);
 return (d?d+'d ':'')+(h?h+'h ':'')+m+'m '+(s%60)+'s';}
function esc(t){var e=document.createElement('span');e.textContent=t;return e.innerHTML;}
function cmd(c,cb){fetch('/api/cmd?c='+encodeURIComponent(c)).then(function(r){return r.text();})
 .then(function(t){if(cb)cb(t);refresh();});}
function toggleT(n,on){cmd('target '+n+' '+(on?'on':'off'));}
function editT(n,ip,port){
 var i=prompt('Target '+n+' IP address:',ip);if(i===null)return;
 var p=prompt('Target '+n+' port:',port);if(p===null)return;
 cmd('target '+n+' set '+i.trim()+' '+p.trim(),function(t){alert(t);});}
function doReboot(){if(confirm('Reboot the device? FreeD output stops for a few seconds.'))
 cmd('reboot',function(t){alert(t);});}
function refresh(){
 fetch('/api/status').then(function(r){return r.json();}).then(function(s){
  var dot=document.getElementById('dot');
  dot.className='dot '+(s.live?'live':'dead');
  document.getElementById('dev').textContent='device: '+(s.deviceIp||'-')+(s.live?'':'  (no signal '+s.ageSec+'s)');
  document.getElementById('up').textContent=fmtUp(s.up);
  document.getElementById('fps').textContent=(s.fps||0).toFixed(2)+' fps';
  document.getElementById('rx').textContent=s.rx.toLocaleString();
  document.getElementById('dhcp').textContent=s.dhcpOk+' / '+s.dhcpFail+' fail';
  document.getElementById('dhcpChip').className='chip'+(s.dhcpFail>0?' warn':'');
  document.getElementById('rtr').textContent=(s.rtr==='Y'?'OK (80ms)':'FAILED');
  document.getElementById('rtrChip').className='chip'+(s.rtr==='Y'?'':' bad');
  document.getElementById('cfChip').style.display=s.conflict?'':'none';
  var g='';
  (s.targets||[]).forEach(function(t){
   var st=t.on?((t.state==='B'||t.state==='C')?'backoff':'on'):'off';
   var lbl=t.on?(t.state==='B'?'BACKOFF':(t.state==='C'?'NO SOCKET':'ON')):'OFF';
   g+='<div class="card'+(t.on?'':' dim')+'"><div class="hd"><span class="nm">Target '+t.n+'</span>'
    +'<span class="badge '+st+'">'+lbl+'</span></div>'
    +'<div class="addr">'+esc(t.ip)+' : '+t.port+'</div>'
    +'<div class="cnt">ok '+t.ok.toLocaleString()+' &nbsp; fail <span'+(t.fail>0?' class="bad"':'')+'>'+t.fail+'</span> &nbsp; skip '+t.skip+'</div>'
    +'<div class="btns"><button class="'+(t.on?'b-off':'b-on')+'" onclick="toggleT('+t.n+','+(!t.on)+')">'+(t.on?'Turn OFF':'Turn ON')+'</button>'
    +'<button class="b-edit" onclick="editT('+t.n+',\''+esc(t.ip)+'\','+t.port+')">Edit IP/Port</button></div></div>';
  });
  document.getElementById('grid').innerHTML=g||'<div class="card">No target info yet...</div>';
  var lg='';
  (s.log||[]).forEach(function(e){lg+='<div class="'+e.k+'">['+e.t+'] '+esc(e.m)+'</div>';});
  document.getElementById('log').innerHTML=lg;
 }).catch(function(){document.getElementById('dot').className='dot dead';});
}
refresh();setInterval(refresh,2000);
</script>
</body>
</html>
'@

# ── main loop ────────────────────────────────────────────────────────────────
$myIps = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
           Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
           Select-Object -ExpandProperty IPAddress)
Write-Host ""
Write-Host "XRFD Dashboard running:" -ForegroundColor Cyan
Write-Host "  local:   http://localhost:$Port"
foreach ($ip in $myIps) { Write-Host "  LAN:     http://${ip}:$Port" }
Write-Host ""
Write-Host "Waiting for device diag broadcast on UDP $DiagPort ... (Ctrl+C to stop)"
Add-Log 'info' 'dashboard started'

$ep = New-Object Net.IPEndPoint(0, 0)
while ($true) {
  # drain pending diag broadcasts (non-blocking)
  while ($diag.Available -gt 0) {
    try {
      $b = $diag.Receive([ref]$ep)
      Process-DiagLine ([Text.Encoding]::ASCII.GetString($b)) $ep.Address.ToString()
    } catch { break }
  }
  # refresh target config occasionally
  if ($S.deviceIp -ne '' -and ((Get-Date) - $S.lastTargetsRefresh).TotalSeconds -ge 30) {
    Refresh-Targets
  }
  # serve pending HTTP requests
  while ($listener.Pending()) { Handle-Request $listener.AcceptTcpClient() }
  Start-Sleep -Milliseconds 30
}
