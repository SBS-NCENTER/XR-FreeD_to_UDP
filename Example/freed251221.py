import socket
import serial
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox
from datetime import datetime
import time
import os

# --- 설정 파일 관리 ---
CONFIG_FILE = "config.txt"
DEFAULT_CONFIG = {
    "UDP_PORT": "50001",
    "SERIAL_PORT": "COM8",
    "INPUT_MIN": "0",
    "INPUT_MAX": "65445",
    "OFFSET_HEX": "080000",
    "OUTPUT_SCALE": "50000"
}

def load_config():
    """텍스트 파일에서 설정을 읽어옵니다."""
    config = DEFAULT_CONFIG.copy()
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            for k, v in DEFAULT_CONFIG.items():
                f.write(f"{k}={v}\n")
        return config
    
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=")
                    if k in config:
                        config[k] = v
    except:
        pass
    return config

class FreeDCompleteMonitor:
    def __init__(self, root):
        self.root = root
        self.root.title("Free-D Monitor (Config File Enabled)")
        self.root.geometry("1300x950")

        # 설정 로드
        self.cfg = load_config()

        self.running = False
        self.ser = None
        self.udp = None
        self.packet_count = 0

        # --- 1. 통신 및 렌즈 설정 UI ---
        config_frame = tk.Frame(self.root, padx=20, pady=10)
        config_frame.pack(fill=tk.X)

        tk.Label(config_frame, text="UDP:").pack(side=tk.LEFT)
        self.u_ent = tk.Entry(config_frame, width=7)
        self.u_ent.insert(0, self.cfg["UDP_PORT"])
        self.u_ent.pack(side=tk.LEFT, padx=5)

        tk.Label(config_frame, text="Serial:").pack(side=tk.LEFT, padx=10)
        self.s_ent = tk.Entry(config_frame, width=8)
        self.s_ent.insert(0, self.cfg["SERIAL_PORT"])
        self.s_ent.pack(side=tk.LEFT, padx=5)

        self.btn = tk.Button(config_frame, text="CONNECT", command=self.toggle, bg="#dcdde1", width=10, font=("Arial", 9, "bold"))
        self.btn.pack(side=tk.LEFT, padx=15)
        
        self.pps_label = tk.Label(config_frame, text="[ 0 PPS ]", font=("Arial", 10, "bold"), fg="#d63031")
        self.pps_label.pack(side=tk.LEFT)

        # 렌즈 설정 UI (config.txt 값 적용)
        lens_frame = tk.LabelFrame(self.root, text=f"Lens Mapping (Settings from {CONFIG_FILE})", padx=20, pady=10)
        lens_frame.pack(fill=tk.X, padx=20, pady=5)
        
        tk.Label(lens_frame, text="Input Min:").grid(row=0, column=0)
        self.z_min = tk.Entry(lens_frame, width=10); self.z_min.insert(0, self.cfg["INPUT_MIN"]); self.z_min.grid(row=0, column=1, padx=5)
        
        tk.Label(lens_frame, text="Max:").grid(row=0, column=2)
        self.z_max = tk.Entry(lens_frame, width=10); self.z_max.insert(0, self.cfg["INPUT_MAX"]); self.z_max.grid(row=0, column=3, padx=5)
        
        tk.Label(lens_frame, text="Offset(Hex):", padx=15).grid(row=0, column=4)
        self.off_ent = tk.Entry(lens_frame, width=10); self.off_ent.insert(0, self.cfg["OFFSET_HEX"]); self.off_ent.grid(row=0, column=5, padx=5)
        
        tk.Label(lens_frame, text="Output Scale:", padx=15).grid(row=0, column=6)
        self.scale_ent = tk.Entry(lens_frame, width=10); self.scale_ent.insert(0, self.cfg["OUTPUT_SCALE"]); self.scale_ent.grid(row=0, column=7, padx=5)

        # --- 2. 로그 UI ---
        log_label_frame = tk.LabelFrame(self.root, text="Real-time Data Stream", padx=10, pady=10)
        log_label_frame.pack(expand=True, fill=tk.BOTH, padx=20, pady=10)

        self.txt = scrolledtext.ScrolledText(log_label_frame, bg="#1e1e2e", fg="#00ff00", font=("Consolas", 10), wrap=tk.NONE)
        self.txt.pack(expand=True, fill=tk.BOTH)

        h_scroll = tk.Scrollbar(log_label_frame, orient=tk.HORIZONTAL, command=self.txt.xview)
        self.txt.configure(xscrollcommand=h_scroll.set)
        h_scroll.pack(fill=tk.X)

    def toggle(self):
        if not self.running:
            try:
                # 38400, Odd Parity, 8 Data bits, 1 Stopbit
                self.ser = serial.Serial(self.s_ent.get(), 38400, parity=serial.PARITY_ODD, stopbits=1, timeout=0.05)
                self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.udp.bind(('0.0.0.0', int(self.u_ent.get())))
                self.running = True
                self.packet_count = 0
                threading.Thread(target=self.worker, daemon=True).start()
                threading.Thread(target=self.pps_loop, daemon=True).start()
                self.btn.config(text="STOP", bg="#e84118", fg="white")
                self.write_log(f">>> CONFIG LOADED: {self.cfg}\n")
            except Exception as e: messagebox.showerror("Error", str(e))
        else:
            self.running = False
            if self.ser: self.ser.close()
            if self.udp: self.udp.close()
            self.btn.config(text="CONNECT", bg="#dcdde1", fg="black")

    def pps_loop(self):
        while self.running:
            time.sleep(1.0)
            self.pps_label.config(text=f"[ {self.packet_count} PPS ]")
            self.packet_count = 0

    def worker(self):
        ANG_F, POS_F = 32768.0, 640.0
        while self.running:
            try:
                data, addr = self.udp.recvfrom(1024)
                if len(data) == 29:
                    z_in = int.from_bytes(data[20:23], 'big')
                    f_in = int.from_bytes(data[23:26], 'big')
                    # ... (생략: 좌표 파싱 로직은 동일) ...
                    
                    z_out = self.apply_scaling(z_in)
                    f_out = self.apply_scaling(f_in)

                    new_packet = bytearray(data)
                    new_packet[20:23] = z_out.to_bytes(3, 'big')
                    new_packet[23:26] = f_out.to_bytes(3, 'big')
                    new_packet[28] = (64 - sum(new_packet[0:28])) % 256

                    self.ser.write(new_packet); self.ser.flush()
                    self.packet_count += 1

                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    log_entry = f"[{ts}] IN(Z:{z_in:d}) -> OUT(Z:{z_out:d}) | HEX:{new_packet.hex(' ').upper()}\n"
                    self.root.after(0, self.write_log, log_entry)
            except: continue

    def apply_scaling(self, val):
        try:
            in_min = float(self.z_min.get())
            in_max = float(self.z_max.get())
            offset = int(self.off_ent.get(), 16)
            scale = float(self.scale_ent.get())
            ratio = max(0.0, min(1.0, (val - in_min) / (in_max - in_min)))
            return int(offset + (ratio * scale))
        except: return 0

    def write_log(self, msg):
        self.txt.insert(tk.END, msg)
        if float(self.txt.index('end-1c')) > 1000: self.txt.delete('1.0', '100.0')
        self.txt.see(tk.END)

if __name__ == "__main__":
    root = tk.Tk(); app = FreeDCompleteMonitor(root); root.mainloop()