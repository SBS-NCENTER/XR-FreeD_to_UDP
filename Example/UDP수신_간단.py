import socket
import struct

HOST = '0.0.0.0'
PORT = 50001 
BUFFER_SIZE = 29 # D1 Format은 29바이트 고정

# D1 Format은 빅 엔디안(>)을 사용하고, 29바이트 전체를 읽기 위한 최소한의 포맷
# B: D1(1B), B: CA(1B), 25x: 나머지 25바이트 건너뛰고, B: CK(1B) -> 28x로 대체
# 1(B) + 1(B) + 26x + 1(B) = 29바이트. (나머지 26바이트는 수동 처리)
# 언팩킹 변수: D1, CA, Checksum = 3개

DATA_FORMAT = '>BB26xB' 

def parse_24bit_signed(data_slice):                 # 맨앞자리 "f"음수  "0"양수
                                                    # 3바이트 슬라이스를 24비트 부호 있는 정수로 변환합니다.
                                                    # 3바이트 앞에 부호 확장 비트(0x00 또는 0xFF)를 추가하여 4바이트로 만듭니다.
                                                    # MSB(가장 중요한 바이트)의 최상위 비트(0x80)를 확인하여 부호를 판단
    if data_slice[0] & 0x80:
        full_bytes = b'\xFF' + data_slice           # 음수: 0xFF(255)를 앞에 추가하여 2의 보수 확장
    else:
        full_bytes = b'\x00' + data_slice           # 양수: 0x00(0)을 앞에 추가
    return struct.unpack('>i', full_bytes)[0]       # 빅 엔디안 i (4바이트 부호 있는 정수)로 변환

def run_d1_receiver():
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        udp_socket.bind((HOST, PORT))
        print(f"✅ D1 Format 수신 대기 시작: {HOST}:{PORT} (29바이트)")
        expected_size = 29
        
        while True:
            data, addr = udp_socket.recvfrom(BUFFER_SIZE)
            
            if len(data) != expected_size:
                print(f"\n 길이 불일치 (D1): 설정 29 바이트, 수신 {len(data)} 바이트")
                continue

            # 1. 헤더, ID, 체크섬 추출 (BB26xB)
            d1_type, cam_id, checksum = struct.unpack(DATA_FORMAT, data)
            
            # 2. 24비트 데이터 슬라이싱 및 변환
            pan = parse_24bit_signed(data[2:5]) # Bytes 3-5
            tilt = parse_24bit_signed(data[5:8]) # Bytes 6-8
            Roll = parse_24bit_signed(data[8:11]) # Bytes 9-11
            pos_x = parse_24bit_signed(data[11:14]) # Bytes 12-14
            pos_y = parse_24bit_signed(data[14:17]) # Bytes 15-17
            pos_z = parse_24bit_signed(data[17:20]) # Bytes 18-20
            
            # 3. 줌/포커스 (24비트 양수)는 부호 확장이 필요 없으므로, 0x00을 앞에 붙입니다.
            zoom = struct.unpack('>i', b'\x00' + data[20:23])[0] # Bytes 21-23
            focus = struct.unpack('>i', b'\x00' + data[23:26])[0] # Bytes 24-26

            output_string = (
                # f"\r{data.hex()[:]} | "
                # f"ID: {cam_id} | "
                f"\rX: {pos_x / 640.0:.2f} cm |" # 단위 1/64mm 적용
                f"y: {pos_y / 640.0:.2f} cm |" # 단위 1/64mm 적용
                f"z: {pos_z / 640.0:.2f} cm |" # 단위 1/64mm 적용
                f"Pan: {pan / 32768.0:.2f}° | " # 단위 1/32768° 적용
                f"Tilt: {tilt / 32768.0:.2f}° | "
                f"Roll: {Roll / 32768.0:.2f}° | "
                f"Zoom: {zoom} | "
                f"focus: {focus} | "

            )
            
            print(output_string, end='', flush=True)

    except Exception as e:
        print(f"\r🚨 오류 발생 또는 종료: {e} ")
        
    finally:
        udp_socket.close()

if __name__ == "__main__":
    run_d1_receiver()
    