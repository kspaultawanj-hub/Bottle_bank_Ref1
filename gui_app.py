"""
GUI แอปพลิเคชันเต็มระบบสำหรับเครื่องรับขวดพลาสติกอัจฉริยะ (Bottle Bank)

รวมทุกอย่างเข้าด้วยกัน:
- แตะบัตร RFID เพื่อยืนยันตัวตน / ลงทะเบียนใหม่
- กล้อง + โมเดล AI ตรวจจับฉลากขวด + สั่งงาน Servo ทำงานอัตโนมัติเบื้องหลัง
  (ทำงานจริงเฉพาะตอนมีนักเรียนแตะบัตรอยู่เท่านั้น)
- ปุ่มแลกของรางวัล 4 ระดับ: 50 / 100 / 150 / 200 แต้ม
- ธีมสีเขียวอ่อน-ขาว

ติดตั้งก่อนใช้งาน:
    sudo apt install -y python3-tk python3-evdev python3-picamera2
    pip3 install --break-system-packages supabase python-dotenv evdev ai-edge-litert numpy pillow gpiozero requests

*** สำคัญเรื่องสิทธิ์การรัน ***
evdev ต้องการสิทธิ์เข้าถึง /dev/input/ แนะนำให้เพิ่ม user เข้า group 'input'
แทนการใช้ sudo (ใช้ sudo กับ GUI มักเจอปัญหาต่อจอไม่ได้):
    sudo usermod -aG input $USER
แล้ว logout/reboot 1 ครั้ง จากนั้นรันได้ตรงๆ โดยไม่ต้อง sudo:
    python3 gui_app.py
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

import numpy as np
from evdev import InputDevice, categorize, ecodes, list_devices
from gpiozero import Servo
from PIL import Image
from picamera2 import Picamera2

try:
    from ai_edge_litert.interpreter import Interpreter
except ImportError:
    from tflite_runtime.interpreter import Interpreter  # เผื่อรันบนเครื่องที่ยังใช้แพ็กเกจเก่า

from supabase_client import get_student_by_uid, register_student, update_points, log_redemption
from discord_notify import send_discord_notification

# ============================= CONFIG (แก้ค่าตรงนี้ได้ตามต้องการ) =============================

# --- แต้มและของรางวัล ---
POINTS_PER_BOTTLE = 1                  # แต้มที่ได้ต่อขวด 1 ใบที่ผ่านการตรวจสอบ
VOUCHER_TIERS = [50, 100, 150, 200]    # ตัวเลือกแต้มที่ใช้แลกของรางวัลได้

# --- กล้อง + โมเดล AI ---
MODEL_PATH = "model/model.tflite"
LABELS_PATH = "model/labels.txt"
CAMERA_RESOLUTION = (640, 480)
CONFIDENCE_THRESHOLD = 0.5             # ต่ำกว่านี้ถือว่าไม่มั่นใจ ไม่ให้แต้ม ไม่สั่ง servo
REJECT_CLASS_NAMES = {"has_label"}     # คลาสที่ถือว่า "ไม่ผ่าน" (ยังมีฉลากอยู่) ต้องตรงกับ labels.txt เป๊ะๆ
DETECTION_COOLDOWN_SECONDS = 4.0       # เวลาหน่วงขั้นต่ำระหว่างการตัดสินใจแต่ละครั้ง
FRAME_DELAY_SECONDS = 0.3              # หน่วงเวลาระหว่างเฟรม ลดภาระ CPU

# --- Servo Motor ---
SERVO_GPIO_PIN = 17
SERVO_RIGHT_POSITION = -1.0             # ไม่ผ่าน (ยังมีฉลาก)
SERVO_LEFT_POSITION = 1.0             # ผ่าน (ไม่มีฉลาก)
SERVO_CENTER_POSITION = 0.0
SERVO_HOLD_SECONDS = 1.0

# --- ธีมสี (เขียวอ่อน-ขาว) ---
COLOR_BG = "#eafaf1"          # พื้นหลังหลัก เขียวอ่อนมาก
COLOR_CARD = "#ffffff"        # การ์ด/ช่องกรอกข้อมูล สีขาว
COLOR_PRIMARY = "#43a047"     # ปุ่มหลัก/ใช้งานได้ เขียวสด
COLOR_PRIMARY_DARK = "#2e7d32"  # หัวข้อ/ข้อความเน้น เขียวเข้ม
COLOR_DISABLED = "#c8e6c9"    # ปุ่มที่กดไม่ได้ เขียวอ่อนจาง
COLOR_TEXT = "#1b5e20"        # ข้อความหลัก เขียวเข้มมาก
COLOR_SUBTEXT = "#66bb6a"     # ข้อความรอง
COLOR_ERROR = "#c62828"       # ข้อความแจ้งเตือนผิดพลาด แดง
COLOR_SUCCESS = "#2e7d32"     # ข้อความแจ้งเตือนสำเร็จ เขียวเข้ม

WINDOW_TITLE = "Bottle Bank - ระบบสะสมแต้ม"
WINDOW_SIZE = "800x480"       # ปรับตามขนาดจอจริงของคุณ

# ================================================================================================


# ---------------------------- ส่วนอ่าน RFID (แยก thread จาก GUI) ----------------------------

KEYCODE_MAP = {
    "KEY_1": "1", "KEY_2": "2", "KEY_3": "3", "KEY_4": "4", "KEY_5": "5",
    "KEY_6": "6", "KEY_7": "7", "KEY_8": "8", "KEY_9": "9", "KEY_0": "0",
    "KEY_ENTER": "ENTER",
}


def find_rfid_device():
    """
    ค้นหาเครื่องอ่าน RFID อัตโนมัติจากชื่ออุปกรณ์ input ทั้งหมด
    ใช้คำค้นหาที่เจาะจงก่อน (rfid, sycreader, id&ic) เพื่อเลี่ยงการจับอุปกรณ์อื่น
    ที่บังเอิญมีคำว่า "keyboard" หรือ "hid" ในชื่อ (เช่น จอสัมผัส)
    """
    devices = [InputDevice(path) for path in list_devices()]

    specific_keywords = ["rfid", "sycreader", "id&ic"]
    for device in devices:
        name_lower = device.name.lower()
        if any(keyword in name_lower for keyword in specific_keywords):
            return device

    broad_keywords = ["keyboard", "hid"]
    for device in devices:
        name_lower = device.name.lower()
        if any(keyword in name_lower for keyword in broad_keywords):
            print(f"[RFID] คำเตือน: ใช้อุปกรณ์จากการค้นหาแบบกว้าง ({device.name})", flush=True)
            return device

    return None


def rfid_listener_thread(uid_queue):
    """
    ทำงานเบื้องหลังตลอดเวลา อ่านค่าจากเครื่องอ่าน RFID
    พอได้ UID ครบ (เจอ ENTER) จะส่งเข้า queue ให้ฝั่ง GUI ไปประมวลผลต่อ
    """
    print("[RFID] เริ่มค้นหาเครื่องอ่าน...", flush=True)
    try:
        device = find_rfid_device()
        if device is None:
            print("[RFID] ไม่พบเครื่องอ่าน RFID กรุณาเช็คการเชื่อมต่อ USB", flush=True)
            return

        print(f"[RFID] พบอุปกรณ์: {device.name} ({device.path})", flush=True)
        print("[RFID] เริ่มรอรับสัญญาณจากเครื่องอ่าน...", flush=True)

        buffer = ""
        for event in device.read_loop():
            if event.type == ecodes.EV_KEY:
                data = categorize(event)
                if data.keystate == 1:
                    key = data.keycode
                    if key in KEYCODE_MAP:
                        if KEYCODE_MAP[key] == "ENTER":
                            if buffer:
                                uid_queue.put(buffer)
                                buffer = ""
                        else:
                            buffer += KEYCODE_MAP[key]
    except Exception:
        import traceback
        print("[RFID] เกิดข้อผิดพลาดใน thread:", flush=True)
        traceback.print_exc()


# ---------------------------- ส่วนกล้อง + โมเดล AI (แยก thread จาก GUI) ----------------------------

def load_labels(labels_path):
    """อ่านไฟล์ labels.txt แล้วคืนค่า list ชื่อคลาส เรียงตาม index"""
    labels = []
    with open(labels_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)
            name = parts[1] if len(parts) == 2 else parts[0]
            labels.append(name)
    return labels


def load_model(model_path):
    """โหลดโมเดล TensorFlow Lite และเตรียม interpreter"""
    interpreter = Interpreter(model_path=model_path)
    interpreter.allocate_tensors()
    return interpreter, interpreter.get_input_details(), interpreter.get_output_details()


def preprocess_frame(frame, input_details):
    """แปลงภาพจากกล้องให้มีขนาด/ฟอร์แมตตรงกับที่โมเดลต้องการ (บังคับเป็น RGB 3 ช่องสีเสมอ)"""
    input_shape = input_details[0]["shape"]
    target_height, target_width = input_shape[1], input_shape[2]

    image = Image.fromarray(frame).convert("RGB").resize((target_width, target_height))
    input_data = np.expand_dims(np.array(image), axis=0)

    if input_details[0]["dtype"] == np.float32:
        input_data = input_data.astype(np.float32) / 255.0

    return input_data


def run_inference(interpreter, input_details, output_details, input_data):
    """รันภาพเข้าโมเดล 1 ครั้ง คืนค่า (index คลาสที่ทายได้, confidence)"""
    interpreter.set_tensor(input_details[0]["index"], input_data)
    interpreter.invoke()
    output_data = interpreter.get_tensor(output_details[0]["index"])[0]

    if output_data.dtype == np.uint8:
        output_data = output_data.astype(np.float32) / 255.0

    predicted_index = int(np.argmax(output_data))
    confidence = float(output_data[predicted_index])
    return predicted_index, confidence


def camera_detection_thread(app):
    """
    ทำงานเบื้องหลังตลอดเวลาที่โปรแกรมเปิดอยู่ อ่านภาพจากกล้องต่อเนื่อง
    แต่จะ "ตัดสินใจจริง" (ให้แต้ม/สั่ง servo) เฉพาะตอนที่ app.active_uid ไม่ใช่ None เท่านั้น
    (คือตอนมีนักเรียนแตะบัตรอยู่และอยู่หน้าจอผลลัพธ์)

    ผลการตรวจจับจะถูกส่งเข้า app.detection_queue ให้ฝั่ง GUI ไปอัปเดตหน้าจอต่อ
    """
    print("[Vision] กำลังโหลดโมเดล...", flush=True)
    labels = load_labels(LABELS_PATH)
    interpreter, input_details, output_details = load_model(MODEL_PATH)

    servo = Servo(SERVO_GPIO_PIN)
    servo.value = SERVO_CENTER_POSITION

    picam2 = Picamera2()
    picam2.configure(picam2.create_preview_configuration(main={"size": CAMERA_RESOLUTION}))
    picam2.start()
    time.sleep(1)  # รอกล้องปรับแสง

    print(f"[Vision] พร้อมทำงาน | คลาสทั้งหมด: {labels}", flush=True)

    last_action_time = 0.0

    while True:
        # ยังไม่มีนักเรียนแตะบัตรอยู่ -> ไม่ต้องเสียเวลาจับภาพ/ประมวลผล พักรอไปก่อน
        if app.active_uid is None:
            time.sleep(0.3)
            continue

        frame = picam2.capture_array()

        now = time.time()
        if now - last_action_time < DETECTION_COOLDOWN_SECONDS:
            time.sleep(FRAME_DELAY_SECONDS)
            continue

        input_data = preprocess_frame(frame, input_details)
        predicted_index, confidence = run_inference(
            interpreter, input_details, output_details, input_data
        )
        predicted_class = labels[predicted_index]

        if confidence < CONFIDENCE_THRESHOLD:
            app.detection_queue.put(("uncertain", confidence))
            last_action_time = now

        elif predicted_class in REJECT_CLASS_NAMES:
            servo.value = SERVO_RIGHT_POSITION
            time.sleep(SERVO_HOLD_SECONDS)
            servo.value = SERVO_CENTER_POSITION
            app.detection_queue.put(("rejected", predicted_class, confidence))
            last_action_time = now

        else:
            servo.value = SERVO_LEFT_POSITION
            time.sleep(SERVO_HOLD_SECONDS)
            servo.value = SERVO_CENTER_POSITION

            updated = update_points(app.active_uid, POINTS_PER_BOTTLE)
            if updated:
                app.detection_queue.put(("accepted", updated))
            else:
                app.detection_queue.put(("error", "บันทึกแต้มไม่สำเร็จ"))
            last_action_time = now

        time.sleep(FRAME_DELAY_SECONDS)


# ---------------------------------------- ส่วน GUI ----------------------------------------


class BottleBankApp:
    def __init__(self, root):
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.configure(bg=COLOR_BG)

        self.big_font = tkfont.Font(size=28, weight="bold")
        self.mid_font = tkfont.Font(size=18)
        self.small_font = tkfont.Font(size=14)

        self.current_uid = None       # UID บัตรที่กำลังแสดงผลอยู่บนหน้าจอ
        self.current_student = None   # ข้อมูลนักเรียนที่กำลังแสดงอยู่
        self.active_uid = None        # UID ที่ "เปิดใช้งาน" ให้กล้องตัดสินใจให้แต้มได้ (None = ปิด)
        self.redeem_buttons = {}      # เก็บ reference ปุ่มแลกของรางวัลแต่ละระดับ

        self.rfid_queue = queue.Queue()
        self.detection_queue = queue.Queue()

        self.build_idle_screen()

        threading.Thread(
            target=rfid_listener_thread, args=(self.rfid_queue,), daemon=True
        ).start()

        threading.Thread(
            target=camera_detection_thread, args=(self,), daemon=True
        ).start()

        self.root.after(200, self.poll_queues)

    # -------------------- หน้าจอ: รอแตะบัตร --------------------
    def build_idle_screen(self):
        self.clear_screen()
        self.current_uid = None
        self.current_student = None
        self.active_uid = None  # ปิดโหมดตรวจจับของกล้องไว้ก่อน

        tk.Label(
            self.root, text="แตะบัตรนักเรียนเพื่อเริ่มต้น",
            font=self.big_font, fg=COLOR_PRIMARY_DARK, bg=COLOR_BG,
        ).pack(expand=True)

        tk.Label(
            self.root, text="รอการแตะบัตร RFID...",
            font=self.mid_font, fg=COLOR_SUBTEXT, bg=COLOR_BG,
        ).pack()

    # -------------------- หน้าจอ: ฟอร์มลงทะเบียน (บัตรใหม่) --------------------
    def build_registration_screen(self, uid):
        self.clear_screen()
        self.current_uid = uid

        tk.Label(
            self.root, text="ยังไม่เคยลงทะเบียนบัตรใบนี้",
            font=self.mid_font, fg=COLOR_PRIMARY_DARK, bg=COLOR_BG,
        ).pack(pady=10)

        form_frame = tk.Frame(self.root, bg=COLOR_BG)
        form_frame.pack(pady=10)

        self.entry_student_code = self._add_form_row(form_frame, "รหัสนักเรียน", 0)
        self.entry_first_name = self._add_form_row(form_frame, "ชื่อ", 1)
        self.entry_last_name = self._add_form_row(form_frame, "นามสกุล", 2)
        self.entry_nickname = self._add_form_row(form_frame, "ชื่อเล่น", 3)

        tk.Button(
            self.root, text="ลงทะเบียน", font=self.mid_font,
            bg=COLOR_PRIMARY, fg="white", activebackground=COLOR_PRIMARY_DARK,
            command=self.submit_registration,
        ).pack(pady=15, ipadx=20, ipady=8)

        tk.Button(
            self.root, text="ยกเลิก", font=self.small_font,
            bg=COLOR_CARD, fg=COLOR_TEXT, command=self.build_idle_screen,
        ).pack()

        self.reg_message_label = tk.Label(
            self.root, text="", font=self.small_font, fg=COLOR_ERROR, bg=COLOR_BG,
        )
        self.reg_message_label.pack()

    def _add_form_row(self, parent, label_text, row):
        tk.Label(
            parent, text=label_text, font=self.small_font, fg=COLOR_TEXT, bg=COLOR_BG,
        ).grid(row=row, column=0, sticky="e", padx=5, pady=5)
        entry = tk.Entry(parent, font=self.small_font, width=20, bg=COLOR_CARD)
        entry.grid(row=row, column=1, padx=5, pady=5)
        return entry

    def submit_registration(self):
        student_code = self.entry_student_code.get().strip()
        first_name = self.entry_first_name.get().strip()
        last_name = self.entry_last_name.get().strip()
        nickname = self.entry_nickname.get().strip()

        if not all([student_code, first_name, last_name, nickname]):
            self.reg_message_label.config(text="กรุณากรอกข้อมูลให้ครบทุกช่อง")
            return

        new_student = register_student(
            self.current_uid, student_code, first_name, last_name, nickname
        )

        if new_student:
            self.build_student_screen(new_student)
        else:
            self.reg_message_label.config(text="เกิดข้อผิดพลาด กรุณาลองใหม่อีกครั้ง")

    # -------------------- หน้าจอ: แสดงข้อมูลนักเรียน + แลกของรางวัล --------------------
    def build_student_screen(self, student):
        self.clear_screen()
        self.current_student = student
        self.current_uid = student["rfid_uid"]
        self.active_uid = student["rfid_uid"]  # เปิดโหมดให้กล้องตัดสินใจให้แต้มได้

        tk.Label(
            self.root, text=f"สวัสดี {student['nickname']}!",
            font=self.big_font, fg=COLOR_PRIMARY_DARK, bg=COLOR_BG,
        ).pack(pady=(15, 5))

        self.points_label = tk.Label(
            self.root, text=f"แต้มสะสม: {student['points']} แต้ม",
            font=self.mid_font, fg=COLOR_TEXT, bg=COLOR_BG,
        )
        self.points_label.pack(pady=5)

        tk.Label(
            self.root, text="📷 นำขวดยื่นให้กล้องได้เลย ระบบจะตรวจสอบและให้แต้มอัตโนมัติ",
            font=self.small_font, fg=COLOR_SUBTEXT, bg=COLOR_BG,
        ).pack(pady=(0, 10))

        # ปุ่มแลกของรางวัล 4 ระดับ
        voucher_frame = tk.Frame(self.root, bg=COLOR_BG)
        voucher_frame.pack(pady=10)

        self.redeem_buttons = {}
        for i, tier in enumerate(VOUCHER_TIERS):
            btn = tk.Button(
                voucher_frame, text=f"แลก {tier} แต้ม",
                font=self.small_font, fg="white",
                command=lambda t=tier: self.redeem_voucher(t),
            )
            btn.grid(row=0, column=i, padx=6, ipadx=8, ipady=10)
            self.redeem_buttons[tier] = btn

        self.update_redeem_buttons_state()

        tk.Button(
            self.root, text="จบการทำงาน / กลับหน้าหลัก", font=self.small_font,
            bg=COLOR_CARD, fg=COLOR_TEXT, command=self.build_idle_screen,
        ).pack(pady=15)

        self.message_label = tk.Label(
            self.root, text="", font=self.small_font, fg=COLOR_TEXT, bg=COLOR_BG,
        )
        self.message_label.pack()

    def update_redeem_buttons_state(self):
        """เปิด/ปิดปุ่มแลกของรางวัลแต่ละระดับ ตามว่าแต้มพอไหม"""
        current_points = self.current_student["points"]
        for tier, btn in self.redeem_buttons.items():
            if current_points >= tier:
                btn.config(state="normal", bg=COLOR_PRIMARY)
            else:
                btn.config(state="disabled", bg=COLOR_DISABLED)

    def redeem_voucher(self, cost):
        """กดปุ่มแลกของรางวัลระดับใดระดับหนึ่ง หักแต้มตาม cost แลกบัตรกำนัล 1 ใบ"""
        if self.current_student["points"] < cost:
            self.show_message("แต้มไม่พอสำหรับแลกของรางวัลระดับนี้", error=True)
            return

        updated = update_points(self.current_uid, -cost)
        if updated:
            log_redemption(
                student_id=updated["id"], rfid_uid=self.current_uid,
                nickname=updated["nickname"], points_used=cost,
            )
            send_discord_notification(
                f"🎁 **{updated['nickname']}** แลกของรางวัลไป {cost} แต้ม "
                f"(เหลือ {updated['points']} แต้ม)"
            )
            self.current_student = updated
            self.points_label.config(text=f"แต้มสะสม: {updated['points']} แต้ม")
            self.update_redeem_buttons_state()
            self.show_message(f"แลกสำเร็จ! ใช้ {cost} แต้ม ได้รับบัตรกำนัล 1 ใบ")
        else:
            self.show_message("เกิดข้อผิดพลาดในการแลกของรางวัล", error=True)

    def show_message(self, text, error=False):
        color = COLOR_ERROR if error else COLOR_SUCCESS
        self.message_label.config(text=text, fg=color)

    # -------------------- เชื่อม background thread (RFID + กล้อง) กับ GUI --------------------
    def poll_queues(self):
        """เช็ค queue ทั้งสองทุก 200ms (ต้องอัปเดต widget จาก main thread เท่านั้น)"""
        try:
            while True:
                uid = self.rfid_queue.get_nowait()
                self.handle_card_scanned(uid)
        except queue.Empty:
            pass

        try:
            while True:
                event = self.detection_queue.get_nowait()
                self.handle_detection_event(event)
        except queue.Empty:
            pass

        self.root.after(200, self.poll_queues)

    def handle_card_scanned(self, uid):
        student = get_student_by_uid(uid)
        if student:
            self.build_student_screen(student)
        else:
            self.build_registration_screen(uid)

    def handle_detection_event(self, event):
        """ประมวลผลเหตุการณ์จาก camera_detection_thread แล้วอัปเดตหน้าจอ"""
        # ถ้าตอนนี้ไม่ได้อยู่หน้าจอนักเรียนแล้ว (กลับไปหน้า idle ไปแล้ว) ให้ข้ามไป
        if self.current_student is None:
            return

        kind = event[0]

        if kind == "accepted":
            updated = event[1]
            self.current_student = updated
            self.points_label.config(text=f"แต้มสะสม: {updated['points']} แต้ม")
            self.update_redeem_buttons_state()
            self.show_message(f"รับขวดสำเร็จ! +{POINTS_PER_BOTTLE} แต้ม")

        elif kind == "rejected":
            predicted_class, confidence = event[1], event[2]
            self.show_message(
                f"ขวดยังไม่ผ่าน ({predicted_class}, {confidence:.0%}) กรุณาถอดฉลากออกก่อน",
                error=True,
            )

        elif kind == "uncertain":
            confidence = event[1]
            self.show_message(f"ไม่มั่นใจ ({confidence:.0%}) กรุณาลองใหม่อีกครั้ง", error=True)

        elif kind == "error":
            self.show_message(event[1], error=True)

    def clear_screen(self):
        for widget in self.root.winfo_children():
            widget.destroy()


def main():
    root = tk.Tk()
    BottleBankApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
