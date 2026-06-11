# XRFD 운영 매뉴얼

FreeD Serial → UDP Forwarder의 **운영자용** 안내서입니다. 모니터링·제어 도구의 사용법과 상황별 대응 절차를 다룹니다. (개발·기술 상세는 `README.md` 참고)

---

## 1. 시스템 한눈에

카메라 트래킹(FreeD) 데이터를 RS-422로 받아 최대 4개 목적지(XR engine)로 UDP 전송하는 컨버터입니다. 장비 상태는 5초마다 broadcast되고, 네트워크로 원격 제어할 수 있습니다.

```text
┌──────────┬────────┬──────────────────────────────────────────┐
│ Port     │ Proto  │ Role                                     │
├──────────┼────────┼──────────────────────────────────────────┤
│ 50001    │ UDP    │ FreeD data -> XR engines (59.94 fps)     │
│ 50998    │ UDP    │ remote control (commands to the device)  │
│ 50999    │ UDP    │ diagnostics broadcast (device -> all)    │
│ 10000    │ TCP    │ web dashboard (ops PC hosts it)          │
└──────────┴────────┴──────────────────────────────────────────┘
```

장비의 IP는 DHCP로 받으며, **몰라도 됩니다** — 모든 도구가 진단 broadcast에서 자동 발견합니다.

---

## 2. 도구 모음 (`tools/`)

### Windows용 (더블클릭)

| 파일 | 역할 |
|------|------|
| `xrfd_firewall_setup.bat` | **최초 1회**, 우클릭→관리자 실행. 진단 수신(UDP 50999)과 dashboard 외부접속(TCP 10000) 허용 |
| `xrfd_dashboard.bat` | **웹 GUI 서버 시작** (이 PC가 호스트). 창을 닫으면 종료 |
| `xrfd_shell.bat` | 대화형 텍스트 shell (`help` 입력) |
| `xrfd_monitor.bat` | 진단 라인 실시간 출력 (원시 텍스트) |
| `xrfd_status.bat` | 상태 1회 조회 |
| `xrfd_target1_on.bat` / `xrfd_target1_off.bat` | Target 1 켜기/끄기 원클릭 |
| `xrfd_ctl.bat` | 임의 명령 입력 (IP/명령 직접 타이핑) |
| `xrfd_send.bat` | (내부 공용 — 직접 실행하지 않음) |

바탕화면 바로가기: .bat 우클릭 → 보내기 → 바탕 화면에 바로 가기 만들기.

### macOS / Linux용 (터미널)

| 파일 | 역할 |
|------|------|
| `xrfd_dashboard.sh` | 웹 GUI 서버 시작 (`./xrfd_dashboard.sh`, python3 필요 — 기본 설치됨) |
| `xrfd_monitor.sh` | 진단 라인 실시간 출력 |
| `xrfd_ctl.sh` | 명령 전송: `./xrfd_ctl.sh "target 1 off"` (IP 자동 발견) |
| `xrfd_firewall_setup.sh` | 방화벽 안내/설정 (macOS는 보통 불필요 — §9) |
| `xrfd_dashboard.py` | dashboard 본체 (`.sh`가 실행해줌) |

---

## 3. 웹 Dashboard 사용법

### 시작하기

1. (Windows 최초 1회) `xrfd_firewall_setup.bat` 관리자 실행
2. `xrfd_dashboard.bat` 더블클릭 (Mac/Linux: `./tools/xrfd_dashboard.sh`)
3. 콘솔에 표시되는 주소로 접속 — 같은 LAN의 **모든 PC/iPad/폰 브라우저**에서 가능:
   - 호스트 PC 자신: `http://localhost:10000`
   - 다른 기기: `http://<호스트PC IP>:10000`

### 화면 읽는 법

```text
┌────────────────────────────────────────────────────────────┐
│ ● XRFD Dashboard           device: 10.10.204.123        ☽  │ <- dot / device IP / theme
├────────────────────────────────────────────────────────────┤
│ Uptime │ FreeD rate │ DHCP renew │ RTR patch │ Frames      │ <- status chips
├──────────────────────┬─────────────────────────────────────┤
│ Target 0      [ON]   │ Target 1                  [OFF]     │ <- target cards
│ 10.10.204.184:50001  │ 10.10.204.175:50001                 │
│ ok 482k fail 0 skip 0│ (dimmed)                            │
│ [Turn OFF][Edit]     │ [Turn ON][Edit]                     │
├──────────────────────┴─────────────────────────────────────┤
│ Event log                              [Reboot device]     │
└────────────────────────────────────────────────────────────┘
```

| 표시 | 의미 | 정상 기준 |
|------|------|----------|
| ● dot | 초록 = 장비 수신 중(LIVE), 빨강 = 12초 이상 무신호 | 초록 |
| Uptime | 장비 가동 시간 — 갑자기 줄면 재부팅 발생 | 계속 증가 |
| FreeD rate | 실시간 수신 frame rate (장비 시계 기준 정밀 계산) | **59.94** 부근 고정 |
| DHCP renew | 임대 갱신 성공/실패 누적 | 실패 0 |
| RTR patch | 칩 타이밍 패치 적용 여부 | OK (80ms) |
| IP conflict | **빨간 칩 출현 = IP 충돌 감지** | 안 보임 |
| badge ON/OFF | 타겟 활성화 여부 | 운영 의도대로 |
| badge BACKOFF (주황) | 타겟 도달 불가 — 재시도 대기 중 | 해당 장비 점검 |
| badge NO SOCKET | 칩 socket 미확보 (일시적, 자동 복구) | 지속되면 reboot |
| ok / fail / skip | 송신 성공 / ARP timeout 실패 / backoff로 건너뜀 | fail·skip 정지 상태 |

### 조작

- **Turn ON / OFF**: 타겟 활성화 토글. 즉시 적용 + 장비 EEPROM에 저장(재부팅 후 유지)
- **Edit IP/Port**: 타겟 주소 변경 (변경 + 자동 활성화 + 저장)
- **Reboot device**: 장비 재시작 (확인창 → 수 초간 FreeD 중단)
- **☽**: 테마 전환 (dark=Monokai / light=Solarized, 브라우저별 기억)

---

## 4. 대화형 Shell (`xrfd_shell.bat`)

시작하면 장비를 자동 발견하고 `XRFD>` 프롬프트가 뜹니다.

```text
help                                    명령 안내 (-h, -help, ? 동일)
diagnosis                               진단 스트림 (ESC/Q/Ctrl+C 중지)
status                                  상태 1회 조회
targets                                 타겟 0~3 목록
ChangeTarget -n 1 -i 10.10.204.175 -p 50001    타겟 설정+활성화
on 1  /  off 1                          타겟 켜기/끄기
reboot                                  장비 재부팅 (확인 후)
discover                                장비 IP 재탐색
raw <text>                              임의 명령 직접 전송
exit                                    종료
```

---

## 5. UDP 원격 제어 직접 사용

스크립트 없이도 모든 OS에서 가능 (장비 IP:50998로 UDP 텍스트 전송):

```bash
# macOS/Linux
echo "target 1 off" | nc -u -w1 10.10.204.123 50998
```

| 명령 | 동작 (설정 변경은 EEPROM 자동 저장) |
|------|------|
| `status` | 진단 라인 회신 |
| `targets` | 타겟 목록 (활성화/IP/port) |
| `target <0-3> on` / `off` | 활성화 / 비활성화 |
| `target <0-3> ip <a.b.c.d>` | IP 변경 |
| `target <0-3> port <n>` | port 변경 |
| `target <0-3> set <ip> <port>` | IP+port 설정 + 활성화 |
| `reboot` | 응답 후 장비 재시작 |

---

## 6. 진단 broadcast 읽는 법

장비가 5초마다 subnet broadcast(`:50999`)로 한 줄을 보냅니다:

```
XRFD up=123 ms=123456 ip=10.10.204.123 rx=7380 dhcp=1/0 rtr=Y t0=A,7380,0,0 t1=off t2=off t3=off
```

| 필드 | 의미 |
|------|------|
| up | 가동 시간(초). 0 근처로 리셋 = 재부팅 발생 |
| ms | 장비 내부 시계(밀리초) — rate 정밀 계산용 |
| ip | 장비 현재 IP |
| rx | 수신·파싱한 FreeD frame 누적 수 |
| dhcp | DHCP 갱신 성공/실패 누적 |
| rtr | 칩 타이밍 패치 (Y 정상) |
| t0~t3 | 타겟 상태: `A`(정상)/`B`(도달불가-재시도중)/`C`(socket 미확보)/`off`, 뒤로 ok,fail,skip |
| CONFLICT | (출현 시) 최근 60초 내 IP 충돌 감지 |

**한눈 정상 판정**: `up` 증가 / `rx` 초당 +60 / 모든 활성 타겟 `A` / `fail` 정지 / `CONFLICT` 없음.

---

## 7. 운영 시나리오 (Playbook)

### 타겟 장비(XR engine)를 끌 예정일 때
끄기 전에 dashboard에서 해당 Target을 **Turn OFF**. (안 꺼도 자동 backoff로 다른 타겟은 보호되지만, OFF가 가장 깔끔 — fail 카운터와 경고 로그도 조용해짐)

### 타겟 장비를 켰을 때
Target이 ON 상태면 **최대 4초 내 자동 재개**. OFF로 꺼뒀다면 Turn ON.

### 타겟 장비의 본체/NIC을 교체했을 때 (IP는 동일)
장비는 이전 하드웨어의 MAC 주소를 기억하고 있어 **그대로 두면 데이터가 새 장비에 도달하지 않습니다.** Dashboard에서 해당 Target을 **OFF → ON 토글** (또는 Reboot device) — 주소를 다시 학습합니다.

### 빨간 "IP conflict DETECTED" 칩이 떴을 때
다른 장비가 컨버터와 같은 IP를 쓰고 있다는 뜻입니다. 컨버터가 DHCP 임대를 보유 중이라면 **상대가 침입자** — 같은 IP로 수동 설정된 장비를 찾아 수정하세요. 컨버터가 fallback(임시 주소) 중이었다면 자동으로 DHCP를 재시도해 스스로 해소합니다.

### fail / skip 카운터가 계속 증가할 때
해당 타겟 장비의 전원·케이블·IP 설정을 확인하세요. badge가 BACKOFF(주황)면 장비 도달 불가 상태입니다. 의도적으로 꺼둔 장비라면 Target을 OFF 하세요.

### Dashboard가 OFFLINE(빨간 dot)일 때
1. 장비 전원/링크 LED 확인
2. 다른 PC에서 `xrfd_monitor.bat` 실행 — 라인이 보이면 dashboard PC 문제(방화벽/재시작), 안 보이면 장비/네트워크 문제
3. 장비 LAN 케이블·switch 포트 확인

### Event log에 "DEVICE REBOOTED"가 찍혔을 때
정전·전원 불안정 또는 watchdog 동작. 시각을 기록해 두고, 잦으면 전원 계통 점검. (설정은 EEPROM에 있어 재부팅 후 자동 복원됩니다)

### 장비 IP가 바뀌었을 때
도구들은 12초 내 자동으로 새 IP를 따라갑니다. 조치 불필요.

---

## 8. 방화벽 설정

| OS | 필요 작업 |
|----|----------|
| Windows | `xrfd_firewall_setup.bat` 관리자 실행 (1회). 진단 수신 + dashboard 외부접속 허용 |
| macOS | 보통 불필요 (기본 방화벽 OFF). 켜져 있으면 첫 실행 때 "허용" 팝업 클릭 |
| Linux | `./tools/xrfd_firewall_setup.sh` — ufw/firewalld 자동 감지 후 규칙 추가 |

원격 **제어**(명령 전송)는 어느 OS든 방화벽 설정 없이 동작합니다 — 규칙이 필요한 건 broadcast **수신**(모니터/자동발견)과 dashboard **외부 접속**뿐입니다.

---

## 9. Firmware 업데이트

```bash
cd XR_FreeD_to_UDP
pio run            # 빌드
pio run -t upload  # USB 연결된 보드에 업로드 (~6초, 직후 자동 재시작)
```

업로드 중 수 초간 FreeD 송출이 멈추므로 방송 중에는 피하세요. 빌드 모드(production/debug)는 `README.md` 참고.

---

## 10. 빠른 참조

```text
┌─────────────────────────┬──────────────────────────────────────┐
│ What                    │ Where                                │
├─────────────────────────┼──────────────────────────────────────┤
│ dashboard URL           │ http://<ops-pc>:10000                │
│ device IP               │ diag line "ip=" / dashboard header   │
│ device MAC              │ 02:F0:ED:CA:FE:01                    │
│ FreeD out               │ <target-ip>:50001 (UDP)              │
│ control                 │ <device-ip>:50998 (UDP text)         │
│ diagnostics             │ broadcast :50999, every 5s           │
│ fallback IP (last resort)│ last DHCP lease, else 10.10.204.123 │
└─────────────────────────┴──────────────────────────────────────┘
```
