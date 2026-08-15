# FreeD Serial → UDP Forwarder

RS-422 시리얼로 수신한 FreeD 카메라 트래킹 데이터를 UDP 패킷으로 변환하여 네트워크로 전송하는 Arduino 기반 프로토콜 컨버터입니다.

> **운영자용 사용 설명서는 [MANUAL.md](MANUAL.md)** — dashboard/도구 사용법, 상황별 대응 절차(playbook) 포함.

## 주요 기능

- **FreeD D1 프로토콜** 수신 및 파싱 (29바이트 패킷)
- **RS-422 → UDP** 실시간 프로토콜 변환
- **멀티 타겟 지원**: 최대 4개 목적지로 동시 전송 (unicast, 타겟별 전용 socket + 동기 송신)
- **타겟별 socket ARP cache**: socket마다 destination이 고정되어 W5500이 resolved MAC을 cache — ARP는 타겟당 최초 1회뿐 (단일 socket 시절의 타겟당 60 ARP/s 폭격과 한계 응답 host의 주기적 timeout 제거). 타겟 장비의 NIC 교체(같은 IP, 다른 MAC) 시에는 dashboard에서 해당 타겟 off→on 토글 또는 reboot로 재ARP
- **UDP 원격 제어**: 같은 LAN의 아무 host에서 타겟 on/off — `echo "target 1 off" | nc -u -w1 <arduino-ip> 50998` (EEPROM 자동 저장, production 빌드에서도 동작)
- **도달 불가 타겟 escalating backoff**: `endPacket()` 실패(ARP timeout) 시 500ms→1s→2s→4s(상한)로 점점 드물게 재시도 — 죽은 타겟의 송출 영향은 4초당 60ms 블로킹 1회로 제한, 복구(전원 재투입) 시 최대 ~4초 내 자동 재개. millis() wrap(24.9일)에도 안전한 경과시간 비교 사용
- **운영 진단 broadcast**: 5초마다 `subnet broadcast:50999`로 상태 한 줄 송신 (uptime, IP, frame 수, DHCP renew, 타겟별 ok/fail/skip 카운터) — production 빌드에서도 reflash 없이 현장 진단 가능
- **체크섬 검증** 및 자동 오류 복구
- **Zoom/Focus 리매핑**: 렌즈 데이터 스케일링
- **EEPROM 설정 저장**: 재부팅 후에도 설정 유지
- **고유 MAC 자동 유도**: MAC을 하드코딩하지 않고 MCU의 factory unique ID에서 유도 — 같은 firmware를 여러 보드에 올려도 MAC이 겹치지 않아 장비 교체 중 신·구 장비가 함께 켜져 있어도 안전. `set mac`으로 명시 지정 가능(`000000000000`이면 유도로 복귀, **reboot 후 적용**)
- **지능형 static fallback**: DHCP 실패 시 **마지막 lease IP를 재사용** (EEPROM 기억). **known-good 주소가 없으면 fallback하지 않고** ~38초마다(30초 gate + 시도 자체 소요시간 ~8초) DHCP만 재시도 — 주소를 추측해 남의 장비 IP를 밟느니 대기한다. fallback 중 5분마다 DHCP 복귀 시도, **W5500 내장 IP 충돌 감지**(IR) 시 즉시 새 lease 요청 + 진단에 `CONFLICT` 표시
- **미설정 상태로 출고**: target 기본값은 `0.0.0.0:0` / 전부 off — 새 보드는 **아무 데도 송출하지 않는 상태로 켜진다**. 대시보드에서 주소를 지정해야 시작하며, 미설정 target은 켤 수 없다(`ERR target N not configured (set ip/port first)`). 교체 중 새 장비가 운영 target에 끼어드는 것을 원천 차단
- **DHCP maintain 통제**: library의 갱신 실패 storm(매 pass ~3초 블로킹 반복)을 1초 gate + 실패 시 60초 holdoff로 유계화
- **하드웨어 워치독**: loop hang 시 MCU 자동 재시작 (RA4M1 WDT, ~5.59초)
- **W5500 RTR/RCR raw register 설정**: Ethernet library의 W5500 register 주소 버그(#84)를 우회해 ARP timeout을 실제로 80ms로 단축 (readback 검증 포함)
- **실시간 통계**: PPS, BPS, 에러 카운터, TX 실패 카운터

## 하드웨어 요구사항

### 필수 구성품

| 부품 | 사양 |
|------|------|
| Arduino UNO R4 WiFi | Renesas RA4M1 MCU |
| Ethernet Shield v2 | W5500 칩셋 |
| RS-422 to TTL 모듈 | YL-128 또는 호환 모듈 |
| 저항 | 120Ω~1kΩ × 2개 (바이어스용) |

### 하드웨어 연결

```
RS-422 신호 소스                    YL-128 모듈              Arduino UNO R4 WiFi
================                    ===========              ===================

                                         VCC ───────────────→ 3.3V (권장)
                                          │
                                       [120Ω] ← 바이어스 풀업
                                          │
RS-422 TX+ ───────────────────────→ A (R+)

RS-422 TX- ───────────────────────→ B (R-)
                                          │
                                       [120Ω] ← 바이어스 풀다운
                                          │
                                         GND ───────────────→ GND

                                         RXD ───────────────→ D0 (RX1)
                                         TXD                   D1 (TX1) [미사용]
```

> **중요**: YL-128 모듈의 핀 표기가 반대일 수 있습니다. TXD가 아닌 RXD 핀이 실제 출력일 수 있으니 테스트 후 확인하세요.

### 바이어스 저항의 필요성

RS-422 입력이 연결되지 않은 상태(플로팅)에서 수신기 칩이 노이즈를 픽업하여 과열/손상될 수 있습니다. 바이어스 저항은 이를 방지합니다.

## 빌드 및 업로드

### PlatformIO 환경

```ini
[env:uno_r4_wifi]
platform = renesas-ra
board = uno_r4_wifi
framework = arduino
monitor_speed = 115200
; 정확히 고정 (caret 금지): main.cpp가 <utility/w5100.h> 내부에 의존
lib_deps = arduino-libraries/Ethernet@2.0.2
```

### 빌드 모드

PlatformIO 환경으로 전환합니다 (네트워킹은 두 모드 모두 Ethernet Shield 사용). `src/main.cpp`의 `DEBUG_SERIAL_MONITOR` 매크로 값은 직접 고치지 않습니다 — 그 파일은 git 추적 대상이라, 고쳐서 커밋하면 hot-path 지터가 있는 debug 이미지가 실수로 production에 배포될 수 있습니다. 대신 `[env:uno_r4_wifi_debug]`의 build flag로만 전환합니다.

| 환경 | 명령 | 설명 |
|------|------|------|
| Production (`default_envs`) | `pio run` | 시리얼 로그 OFF (`DEBUG_SERIAL_MONITOR=0`) — hot-path 부하 제거로 카덴스 안정, 실 배포 대상 |
| Debug (`uno_r4_wifi_debug`) | `pio run -e uno_r4_wifi_debug` | 시리얼 콘솔/통계 ON (`-D DEBUG_SERIAL_MONITOR=1`) — 개발 중이거나 현장에서 치명적 오류를 진단할 때만. 왜 production엔 없는지는 "설계 노트" 참고 |

### 업로드

```bash
pio run -t upload                       # production (기본)
pio run -e uno_r4_wifi_debug -t upload  # debug (serial 콘솔 필요할 때)
pio device monitor -b 115200
```

## 시리얼 콘솔 명령어

### 상태 확인

| 명령어 | 설명 |
|--------|------|
| `status` | 전체 상태 표시 |
| `info` | 펌웨어 버전 / MAC / 유도 MAC / IP / fallback 후보 조회 |
| `help` 또는 `?` | 명령어 목록 |
| `dump [n]` | n개 패킷 16진수 덤프 (기본 5개) |

### 타겟 설정 (멀티 타겟)

| 명령어 | 설명 |
|--------|------|
| `target <0-3> ip <a.b.c.d>` | 타겟 IP 설정 |
| `target <0-3> port <n>` | 타겟 포트 설정 |
| `target <0-3> on` | 타겟 활성화 |
| `target <0-3> off` | 타겟 비활성화 |
| `target <0-3>` | 타겟 정보 조회 |

### 레거시 명령어 (Target 0 전용)

| 명령어 | 설명 |
|--------|------|
| `set ip <a.b.c.d>` | Target 0 IP 설정 |
| `set port <n>` | Target 0 포트 설정 |

### 디바이스 설정

| 명령어 | 설명 |
|--------|------|
| `set mac <hex>` | MAC 명시 지정 (`000000000000` = MCU 유도로 복귀, **reboot 후 적용**) |
| `set local <a.b.c.d>` | static fallback IP 지정 |

### 시리얼 설정

| 명령어 | 설명 |
|--------|------|
| `set parity n` | 패리티 없음 (8N1) |
| `set parity o` | 홀수 패리티 (8O1) - 기본값 |
| `set parity e` | 짝수 패리티 (8E1) |

### Zoom/Focus 리매핑

| 명령어 | 설명 |
|--------|------|
| `set remap on` | 리매핑 활성화 |
| `set remap off` | 리매핑 비활성화 |
| `set inmin <n>` | 입력 최소값 |
| `set inmax <n>` | 입력 최대값 |
| `set offset <hex>` | 출력 오프셋 (16진수) |
| `set scale <n>` | 출력 스케일 |

### 시스템

| 명령어 | 설명 |
|--------|------|
| `save` | 설정을 EEPROM에 저장 |
| `load` | EEPROM에서 설정 로드 |
| `reboot` | 시스템 재시작 |

## 사용 예시

### 기본 설정

```
# 상태 확인
status

# Target 0 설정
target 0 ip 192.168.1.100
target 0 port 50001
target 0 on

# 설정 저장
save
```

### 멀티 타겟 설정 (4개 목적지)

```
# 4개 Unreal Engine 인스턴스로 동시 전송
target 0 ip 10.10.204.101
target 0 port 50001
target 0 on

target 1 ip 10.10.204.102
target 1 port 50001
target 1 on

target 2 ip 10.10.204.103
target 2 port 50001
target 2 on

target 3 ip 10.10.204.104
target 3 port 50001
target 3 on

save
```

### 패킷 디버깅

```
# 5개 패킷 덤프
dump 5

# 출력 예시
[DUMP OK] D1 01 0B 9D B9 EE 68 89 00 00 00 FC F6 90 F7 BE 90 05 03 52 08 00 2D 08 BD D0 00 00 43 | calc=43 pkt=43
```

## FreeD D1 패킷 구조

```
Offset  Size  Field           Description
------  ----  -----           -----------
0       1     Type            0xD1 (고정)
1       1     Camera ID       카메라 식별자
2-4     3     Pan             회전 X (signed 24-bit, 1/32768°)
5-7     3     Tilt            회전 Y
8-10    3     Roll            회전 Z
11-13   3     Position X      위치 X (signed 24-bit, 1/64mm)
14-16   3     Position Y      위치 Y
17-19   3     Position Z      위치 Z
20-22   3     Zoom            줌 값 (unsigned 24-bit)
23-25   3     Focus           포커스 값 (unsigned 24-bit)
26-27   2     Status          상태 비트
28      1     Checksum        (64 - sum[0:28]) & 0xFF
```

## 실시간 통계

1초마다 다음 정보가 출력됩니다:

```
[STAT] PPS:60 BPS:1740 ERR:0 TXF:0 SYNC:HUNT
```

| 필드 | 설명 |
|------|------|
| PPS | Packets Per Second (초당 패킷 수) |
| BPS | Bytes Per Second (초당 바이트 수) |
| ERR | 체크섬 에러 수 (**부팅 후 누적** — PPS/BPS와 달리 매초 리셋되지 않는다. 부팅 직후 스트림 중간부터 읽기 시작해 1건이 잡히는 것은 정상이고, 그 값이 **더 이상 늘지 않으면** 결선이 깨끗한 것) |
| TXF | 누적 UDP send 실패 수 (전 타겟 합, ARP timeout 등) |
| SYNC | 동기화 상태 (HUNT: 헤더 탐색, COLL: 수집 중) |

`status` 명령은 타겟별 `TX stats [ok/fail/dropBusy/dropGate]`, W5500 RTR/RCR patch 적용 여부, DHCP renew 카운터도 표시합니다.

`info` 명령은 장비의 펌웨어 버전, MAC, 유도 MAC, 현재 IP, fallback 후보 주소를 한 줄로 회신합니다. 진단 broadcast에는 MAC이 실리지 않으므로, 라우터 너머 장비의 MAC은 이 명령으로 확인합니다.

```
XRFD info fw=1.7 mac=02f0edcafe01 derived=02f0ed7a3b91 ip=10.10.204.123 fb=10.10.204.123
                     └─ 실제 사용 중       └─ 이 보드의 UID에서 유도한 값
```

`mac`과 `derived`가 **다르면** EEPROM에 명시 MAC이 저장돼 있다는 뜻입니다(과거 장비이거나 `set mac`으로 지정한 경우). **같으면** 유도값을 그대로 쓰고 있는 것입니다.

`derived`는 EEPROM 설정과 무관하게 **항상 계산**되어 실립니다. 덕분에 운영 중인 장비를 멈추거나 MAC을 바꿔보지 않고도 유도가 동작하는지 확인할 수 있고, **장비를 추가할 때 두 대의 `derived`를 비교하는 것만으로 보드별 고유성이 확정**됩니다. 한 대만으로는 비교 대상이 없어 고유성을 증명할 수 없습니다.

### 운영 진단 broadcast (production에서도 동작)

5초마다 subnet broadcast(예: `10.10.204.255`) port `50999`로 ASCII 한 줄을 송신합니다:

```
XRFD up=123 ms=123456 ip=10.10.204.100 rx=7380 dhcp=0/0 rtr=Y t0=A,7380,0,0 t1=B,12,3,45 t2=off t3=off
```

| 필드 | 의미 |
|------|------|
| up | uptime (초) — 갑자기 0으로 리셋되면 재부팅(WDT 포함) 발생 |
| ms | 장치 millis() — rx와 짝지어 frame rate를 정확히 계산 (Δrx/Δms, 수신측 지연과 무관) |
| ip | Arduino의 현재 IP (DHCP 할당 또는 static fallback) |
| rx | 파싱 성공한 FreeD frame 누적 수 |
| dhcp | DHCP renew 성공/실패 누적 |
| rtr | W5500 RTR/RCR raw patch 적용 여부 (Y/N) |
| t&lt;i&gt; | state(A=alive/B=backoff/C=no-socket/off), sentOk, sendFail, skipped |
| CONFLICT | (조건부, 라인 끝) 최근 60초 내 IP 충돌 감지 — dashboard가 경고 표시 |

수신 방법 — 같은 LAN의 아무 host에서:

```bash
tcpdump -A -n udp port 50999        # 또는
socat -u UDP4-RECVFROM:50999,reuseaddr,fork -
```

### UDP 원격 제어 (port 50998, production에서도 동작)

같은 LAN의 아무 host에서 타겟을 on/off할 수 있습니다. 변경은 즉시 적용되고 EEPROM에 자동 저장되어 재부팅 후에도 유지됩니다.

```bash
echo "target 1 off" | nc -u -w1 10.10.204.100 50998   # Target 1 비활성화
echo "target 1 on"  | nc -u -w1 10.10.204.100 50998   # Target 1 활성화
echo "status"       | nc -u -w1 10.10.204.100 50998   # 상태 조회 (진단 라인 회신)
```

전체 명령 목록 (설정 변경은 EEPROM 자동 저장):

| 명령 | 동작 |
|------|------|
| `status` | 진단 라인 회신 |
| `info` | 펌웨어 버전 / MAC / 유도 MAC / IP / fallback 후보 회신 |
| `targets` | 타겟 0~3 목록 (활성화/IP/port) |
| `target <0-3> on\|off` | 활성화/비활성화 |
| `target <0-3> ip <a.b.c.d>` | IP 변경 |
| `target <0-3> port <n>` | port 변경 |
| `target <0-3> set <ip> <port>` | IP+port 설정 + 활성화 (한 번에) |
| `reboot` | 응답 회신 후 MCU 재시작 |

응답은 명령을 보낸 host로 unicast 회신됩니다 (`OK target 1 off (saved)` 형식).

### Windows에서 사용하기 (`tools/` — 더블클릭 .bat 바로가기)

운영 환경이 Windows인 경우 — 설치 없이 내장 PowerShell만으로 동작합니다.
`tools/` 폴더를 운영 PC에 복사한 뒤 더블클릭으로 사용:

| 파일 | 동작 |
|------|------|
| `xrfd_firewall_setup.bat` | **최초 1회**, 우클릭 → 관리자 권한 실행 (UDP 50999 + TCP 10000 허용) |
| `xrfd_dashboard.bat` | **웹 GUI** (권장) — 이 PC에 web server를 띄우고 LAN의 모든 브라우저에서 접속 |
| `xrfd_shell.bat` | 대화형 shell (TUI) — `help`, `diagnosis`, `targets`, `ChangeTarget`, `reboot` 등 |
| `xrfd_monitor.bat` | 진단 모니터 — 5초마다 상태 라인 표시 (IP 포함), 창 닫으면 종료 |
| `xrfd_status.bat` | 상태 1회 조회 (IP 자동 발견) |
| `xrfd_target1_off.bat` / `xrfd_target1_on.bat` | Target 1 끄기/켜기 (EEPROM 저장) |
| `xrfd_ctl.bat` | 임의 명령 입력용 (IP/명령 직접 입력) |

**웹 GUI — XRFD Dashboard** (`xrfd_dashboard.bat` 더블클릭):

운영 PC 한 대에서 실행하면 LAN의 모든 PC/iPad/폰 브라우저에서 `http://<운영PC IP>:10000`으로 접속할 수 있습니다. firmware는 건드리지 않고, 진단 broadcast(50999) 수신 + 제어(50998) 중계만 하는 bridge입니다.

- **상태 패널**: uptime(재부팅 감지), 실시간 FreeD rate(fps), DHCP renew, RTR patch 상태, LIVE 표시등
- **타겟 카드 (0~3)**: ON/OFF/BACKOFF 상태 badge, IP:port, ok/fail/skip 카운터(fail 빨강 강조), Turn ON/OFF 버튼, Edit IP/Port
- **Event log**: 명령 이력, 재부팅 감지, sendFail 증가 경고
- **Reboot device** 버튼 (확인 후 실행)

**대화형 shell** (`xrfd_shell.bat` 더블클릭 — 시작 시 장치 IP 자동 발견):

```
XRFD> help                                     # 명령/문법 안내 (-h, -help, ? 동일)
XRFD> diagnosis                                # 진단 스트림 (ESC/Q/Ctrl+C로 중지)
XRFD> targets                                  # 타겟 0~3 활성화/IP/port 목록
XRFD> ChangeTarget -n 1 -i 10.10.204.175 -p 50001   # 타겟 1 IP+port 설정 + 활성화
XRFD> off 1                                    # 타겟 1 비활성화
XRFD> on 1                                     # 타겟 1 활성화
XRFD> reboot                                   # 장치 재부팅 (확인 후)
XRFD> exit
```

바탕화면에 두고 싶으면: .bat 우클릭 → 보내기 → 바탕 화면에 바로 가기 만들기.

**최초 1회**: 진단 broadcast 수신용 방화벽 허용 (관리자 PowerShell):

```powershell
New-NetFirewallRule -DisplayName "XRFD diag" -Direction Inbound -Protocol UDP -LocalPort 50999 -Action Allow
```

**진단 모니터** — Arduino의 IP를 몰라도 됩니다 (수신된 패킷의 source 주소 = Arduino IP):

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\xrfd_monitor.ps1
# [14:02:31.120] [10.10.204.100] XRFD up=123 ip=10.10.204.100 rx=7380 ...
```

**원격 제어** — `-Ip`를 생략하면 진단 broadcast로 자동 발견(최대 12초) 후 명령 송신:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\xrfd_ctl.ps1 "target 1 off"
powershell -ExecutionPolicy Bypass -File .\tools\xrfd_ctl.ps1 "status" -Ip 10.10.204.100
```

명령 송신·응답 수신에는 방화벽 규칙이 필요 없습니다 (outbound 흐름의 응답이라 자동 허용). 자동 발견만 위의 50999 규칙이 필요합니다.

> **주의**: 원격 제어로 저장된 EEPROM 설정은 코드 기본값보다 우선합니다 (`CONFIG_MAGIC` 일치 시). 이후 코드에서 기본 타겟을 바꿀 때는 `CONFIG_MAGIC`을 bump해야 적용됩니다.
> 폐쇄 show LAN 전제로 인증이 없습니다 — 외부 접근 가능한 네트워크에서는 사용하지 마세요.

## 산업 현장 시나리오 검증

### ✅ 라이브 방송 스튜디오

| 시나리오 | 검증 결과 |
|----------|----------|
| 24/7 연속 운용 | ✅ 동적 메모리 할당 없음, 워치독 보호 |
| 멀티캠 환경 (4대 동시) | ✅ 멀티 타겟 지원, 지연 < 1ms |
| 핫 플러그 (케이블 분리/연결) | ✅ 바이어스 저항으로 안전, 자동 재동기화 |
| 일부 타겟 전원 꺼짐 | ✅ 타겟별 socket + 비동기 SEND — 나머지 타겟 60Hz 유지, 켜지면 0.5초 내 자동 재개 |
| 네트워크 순단 | ✅ 링크 모니터링, DHCP 갱신, send deadline 안전망(socket wedge 방지) |

### ✅ 가상 스튜디오 / XR Stage

| 시나리오 | 검증 결과 |
|----------|----------|
| 60fps 실시간 트래킹 | ✅ PPS 60 안정, 지연 < 1 프레임 |
| Unreal Engine 연동 | ✅ 표준 FreeD UDP 프로토콜 |
| PIXOTOPE/Vizrt 연동 | ✅ 체크섬 호환, 멀티 타겟(unicast) 지원 |
| 렌즈 인코더 리매핑 | ✅ Zoom/Focus 스케일링 기능 |

### ✅ 중계차 / OB Van

| 시나리오 | 검증 결과 |
|----------|----------|
| 전원 불안정 | ✅ 워치독 ~5.59초 자동 복구 (hang 한정) |
| 장기간 대기 후 운용 | ✅ EEPROM 설정 유지 |
| 긴 RS-422 케이블 | ✅ 바이어스 저항 + 종단 저항 지원 |

### ✅ 메모리 안정성

| 항목 | 상태 |
|------|------|
| 힙 사용 | ✅ 없음 (String 클래스 미사용) |
| 스택 오버플로 | ✅ 최대 깊이 제한, 재귀 없음 |
| 버퍼 오버플로 | ✅ 모든 버퍼 경계 검사 |
| millis() 오버플로 | ✅ 차이 계산 방식으로 안전 (49.7일) |

## 문제 해결

### 모듈이 뜨거워지거나 고장남

1. **바이어스 저항 확인**: A→VCC, B→GND에 120Ω~1kΩ 연결
2. **전압 레벨 확인**: 모듈 VCC를 3.3V로 연결 (5V 아님)
3. **핀 연결 확인**: 모듈 RXD(또는 TXD)가 실제 출력 핀인지 테스트

### PPS가 0으로 표시됨

1. **핀 연결 확인**: TXD와 RXD 바꿔서 테스트
2. **패리티 설정**: `set parity o` (홀수) 또는 `set parity n` (없음)
3. **신호 확인**: `dump 5`로 수신 데이터 확인

### UDP 패킷이 수신되지 않음

1. **네트워크 확인**: `status`로 IP 할당 확인
2. **타겟 설정**: `target 0`으로 IP/포트 확인
3. **방화벽**: 수신측 UDP 포트 개방 확인
4. **Wireshark**: 패킷 캡처로 전송 확인

### 간헐적 체크섬 에러

1. **케이블 확인**: RS-422 케이블 접촉 불량
2. **종단 저항**: 긴 케이블 시 A-B 사이 120Ω 추가
3. **노이즈**: 전원/모터 케이블과 분리

## 기술 사양

| 항목 | 사양 |
|------|------|
| 시리얼 입력 | RS-422, 38400 bps, 8O1 |
| 패킷 크기 | 29 바이트 (FreeD D1) |
| 최대 PPS | 60+ (실측) |
| UDP 전송 지연 | < 1ms |
| 최대 타겟 수 | 4개 |
| RAM 사용량 | ~5.4KB (빌드 실측, 32KB 중 16.6%) |
| 워치독 타임아웃 | ~5.59초 (RA4M1 WDT HW 최대) |
| 전송 구조 | 타겟별 전용 socket + 동기 송신 (MAC cache) + 실패 backoff |
| W5500 ARP timeout | 20ms × (3+1) = 80ms — raw register(0x0019/0x001B) 직접 설정 |
| 도달 불가 타겟 재시도 | escalating backoff 500ms→4s(상한), 복구 감지 최대 ~4초 |
| 원격 제어 | UDP port 50998 — `target <0-3> on\|off`, `status` |
| 진단 broadcast | 5초마다 subnet broadcast:50999, ASCII 한 줄 |

## 라이선스

이 프로젝트는 내부 사용을 위해 개발되었습니다.

## 버전 이력

| 버전 | 날짜 | 변경사항 |
|------|------|----------|
| 1.0 | 2024-01 | 초기 릴리스 |
| 1.1 | 2024-02 | 멀티 타겟 지원 추가 |
| 1.2 | 2025-02 | 워치독 타이머 추가, 안정성 개선 |
| 1.3 | 2026-06 | 하드웨어 워치독 실제 구현(~5.59초), W5500 재전송·DHCP 타임아웃 단축 시도 |
| 1.4 | 2026-06 | (회수됨) 타겟별 전용 socket + 비동기 SEND 시도. RTR/RCR 주소 버그(#84) 우회로 "2Hz 버그"의 근본 원인은 해결했으나, 현장에서 0.5초 단위 간헐 수신 끊김이 두 차례 패치로도 해소되지 않아 v1.5에서 동기 구성으로 원복 (원인 미확정 — W5500 비표준 사용 의심). 이 버전에서 도입되어 유지되는 것: RTR/RCR raw 설정, escalating backoff, serial 처리 budget, 진단 broadcast, Target 1 → 10.10.204.175, CONFIG_MAGIC bump |
| 1.5 | 2026-06 | 검증된 단일 socket 동기 송신으로 원복. 죽은 타겟 3중 방어: RTR/RCR raw 설정 + endPacket() 실패 시 wrap-safe escalating backoff(500ms→4s) + **UDP 원격 제어**(port 50998, 전체 명령 세트 + EEPROM 저장). 진단 broadcast에 ms/ip/타겟 상태 표시, 웹 dashboard·대화형 shell 도구 추가 |
| 1.6 | 2026-06 | 타겟별 전용 socket (송신은 동기 유지): 단일 socket의 DIPR 교대가 만들던 타겟당 60 ARP/s 폭격 제거 — 같은 switch의 Windows Server(.175)가 ~5초당 1회 ARP timeout 되던 문제의 근본 수정. ARP timeout 60→80ms 완화. NIC 교체 시 절차: dashboard에서 타겟 off→on 토글 또는 reboot. **사전 commit 검증(11-agent) 후 강화**: DHCP rebind storm 유계화(blocker), ephemeral port가 50998/50999를 밟아 원격제어가 먹통 되는 결함 차단(자체 port 할당기), RTR patch 실패+전 타겟 사망 시 WDT 부트루프 방지(per-target refresh), 지능형 fallback(마지막 lease IP 재사용 + 5분 DHCP 재시도 + IP 충돌 감지 시 즉시 재시도), 진단 buffer 256B, cross-platform 도구(macOS/Linux bash+python) 추가 |
| 1.7 | 2026-08 | **장비 정체성 분리** — 교체·이중화 시 두 대가 같은 LAN에서 MAC·IP·target 어느 층에서도 부딪히지 않게. MAC을 하드코딩 대신 **MCU factory unique ID에서 유도**(FNV-1a; `set mac`으로 명시 지정 가능, `000000000000`이면 유도 복귀) · fallback IP를 **추측하지 않음**(지난 DHCP lease 또는 `set local` 지정값만, 둘 다 없으면 주소를 잡지 않고 ~38초마다 DHCP 재시도 — 하드코딩 fallback이 하필 운영 IP와 같았다. 재시도 경로가 없으면 링크가 UP인 채 주소만 없을 때 영구 복구 불가라 무주소 재시도 분기 신설) · target **미설정 출고**(`0.0.0.0:0`/전부 off, 미설정 target 활성화는 거부 — MAC/IP 분리로는 막을 수 없는 응용계층 충돌) · **`info` 명령**(fw/mac/derived/ip/fb 회신; `status`는 진단 broadcast와 공유해 256B 중 여유가 9B뿐이라 별도 명령) · **`[env:uno_r4_wifi_debug]`**(serial 콘솔은 hot path 지터 때문에 production에서 컴파일 아웃) · 대시보드 다중 장치 드롭다운 + 이벤트 로그 절대날짜. `CONFIG_MAGIC`은 **의도적으로 유지** — 새 기본값이 EEPROM이 빈 보드에만 적용되어 운영 장비의 저장 설정이 보존된다 |

### 설계 노트 — 왜 이 구조인가

코드 주석에는 현재 유효한 제약만 남기고, 아래의 경위는 여기에 모았습니다.

- **동기 송신 유지 + 죽은 target 대응**: target이 모두 살아있을 때는 동기 `endPacket()`으로 59.94Hz를 장기간 안정적으로 유지했습니다 — 이것이 "정상"을 판단하는 기준점입니다. 문제는 target이 죽었을 때였습니다: 동기 `endPacket()`은 죽은 target으로 보낼 때마다 ARP timeout만큼 loop를 블로킹합니다. Ethernet library의 W5500 register 주소 버그(#84)를 그대로 두면 이 timeout이 417.6ms까지 늘어나, target 하나만 죽어도 나머지 target들의 프레임레이트까지 "2Hz 버그" 수준으로 무너졌습니다. raw register로 timeout을 80ms까지 줄이고, 도달 불가 target에는 500ms→4s escalating backoff를 적용해 죽은 target의 영향을 4초당 60ms 블로킹 1회로 제한했습니다. 비동기 fire-and-forget 송신으로 바꿔본 적도 있지만 간헐적 수신 끊김이 발생해 동기 방식으로 되돌렸습니다 — 원인은 끝내 확정되지 않았고, "async 기계장치 자체가 원인"이라는 가설과 "W5500 비표준 사용(RTR/RCR raw 설정)이 원인"이라는 가설 두 가지만 남았습니다.
- **target별 전용 socket**: 단일 socket으로 여러 target에 보내면 매 전송마다 W5500의 DIPR(목적지 IP 레지스터)이 교대되어, chip이 캐시한 MAC이 무효화되고 target당 초당 60회 ARP가 발생했습니다. 같은 switch에 있던 수신 host 한 대가 ARP 응답 한계로 주기적으로 timeout을 겪던 원인이었습니다. socket을 target별로 고정하니 ARP는 target당 최초 1회로 줄었습니다. 대신 캐시된 MAC은 만료되지 않으므로, 수신측 NIC을 교체하면(같은 IP, 다른 MAC) 대시보드에서 해당 target을 off→on 토글하거나 컨버터를 재부팅해야 합니다. 캐시를 자동으로 갱신하는 방식은 과거 시도에서 간헐적 mute를 유발한 적이 있어 넣지 않았습니다.
- **DHCP maintain 유계화**: Ethernet 라이브러리의 `Dhcp.cpp`는 renew/rebind 실패 후 내부 state가 풀리지 않아, 이후 `maintain()` 호출마다 ~3초 블로킹 DISCOVER를 반복합니다(storm). DHCP 장애 동안 FreeD 송출이 사실상 멈추므로, 1초 gate + 실패 시 60초 holdoff로 "분당 한 번의 3초 공백"으로 묶었습니다. static fallback 모드에서는 아예 호출하지 않습니다 — 라이브러리가 static `begin()` 이후에도 `_dhcp`를 유지하는 버그가 있기 때문입니다.
- **production은 serial 콘솔이 꺼져 있다(의도적)**: `DEBUG_SERIAL_MONITOR`를 켜고 측정해보니 serial 출력 자체가 FreeD hot path를 방해해 59.94Hz 송출에 지터가 눈에 띄게 발생했습니다. 그래서 production 빌드는 `processCommand()`/`printNetworkStatus()`와 모든 `DEBUG_PRINT*`를 통째로 컴파일에서 제외합니다 — 오버사이트가 아니라 실측에 근거한 결정입니다. 개발 중이거나 현장에서 치명적 오류를 진단해야 할 때만 `pio run -e uno_r4_wifi_debug`로 별도 빌드해 씁니다.
- **자체 ephemeral port 할당기**: 라이브러리 기본 할당기는 49152~65535를 순회하다 제어 port(50998)와 진단 port(50999)를 밟습니다. target socket이 그 port를 차지하면 W5500의 inbound demux(낮은 socket index 우선)가 제어/진단 수신을 그 socket으로 삼켜버려 원격 제어가 조용히 먹통이 됩니다. 두 port를 건너뛰는 할당기를 따로 둡니다.
- **serial 콘솔은 2단계 확인**: serial의 `target` 명령은 즉시 적용되지만 `save`를 쳐야 EEPROM에 남습니다. 오타 하나로 운영 설정이 영구히 바뀌지 않도록 의도적으로 그렇게 두었습니다(웹 UI에서 입력 후 OK를 눌러야 반영되는 것과 대칭). UDP 제어는 즉시 저장되는데, 이 비대칭은 버그가 아닙니다.
- **진단을 unicast가 아닌 broadcast로 유지하는 이유**: 원래 목적은 장비가 서버 주소를 몰라도 발견되게 하는 것이었습니다. 지금은 dashboard 서버 주소가 `10.10.204.229`로 고정돼 있어 그 근거는 약해졌지만, broadcast를 그대로 두기로 했습니다 — dashboard가 유일한 수신자가 아니기 때문입니다. `tools/`의 CLI 도구들도 같은 port 50999를 듣고 있어, unicast로 바꾸면 그중 한쪽만 받고 현장 진단 경로가 끊깁니다. 비용도 무시할 수준입니다(~250B/5s ≈ 0.4kbps). Windows 방화벽 회피는 애초 판단에 곁들여진 이유였을 뿐 핵심은 아니었습니다.
