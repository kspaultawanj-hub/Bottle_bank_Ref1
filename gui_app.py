"""
GUI แอปพลิเคชันสำหรับเครื่องรับขวดพลาสติกอัจฉริยะ (Bottle Bank)
แสดงผลบนจอสัมผัสของ Raspberry Pi แทนการรันผ่าน Terminal ล้วนๆ

ฟีเจอร์:
- รอนักเรียนแตะบัตร RFID (อ่านผ่าน evdev ใน background thread)
- บัตรใหม่: แสดงฟอร์มกรอกข้อมูลลงทะเบียนบนหน้าจอ
- บัตรที่ลงทะเบียนแล้ว: แสดงชื่อเล่น + แต้มสะสม พร้อม 2 ปุ่ม
    - "+5 แต้ม (จำลองใส่ขวด)"  -> เพิ่มแต้ม (ใช้แทนกล้อง/พิมพ์ OK ไปก่อน)
    - "แลกของรางวัล (100 แต้ม)" -> กดได้เฉพาะตอนแต้ม >= 100 เท่านั้น หักแต้มแล้วแจ้งว่าได้รับ voucher

ติดตั้งก่อนใช้งาน:
    sudo apt install -y python3-tk python3-evdev
    pip3 install --break-system-packages supabase python-dotenv evdev

*** สำคัญเรื่องสิทธิ์การรัน ***
evdev ต้องการสิทธิ์เข้าถึง /dev/input/ ปกติต้องใช้ sudo แต่การรัน GUI ด้วย sudo
มักเจอปัญหาต่อจอไม่ได้ (couldn't connect to display) เพราะ sudo เปลี่ยน user
วิธีแก้ที่แนะนำ: เพิ่ม user เข้า group 'input' แทนการใช้ sudo ทุกครั้ง
    sudo usermod -aG input $USER
แล้ว logout/reboot 1 ครั้ง จากนั้นรันได้ตรงๆ โดยไม่ต้อง sudo:
    python3 gui_app.py
"""

import queue
import threading
import tkinter as tk
from tkinter import font as tkfont

import evdev
from evdev import InputDevice, categorize, ecodes, list_devices

from supabase_client import get_student_by_uid, register_student, update_points, log_redemption
from discord_notify import send_discord_notification

# ========================= CONFIG (แก้ค่าตรงนี้ได้ตามต้องการ) =========================

POINTS_PER_BOTTLE = 5           # แต้มที่ได้ต่อขวด 1 ใบ (ปุ่มจำลองใส่ขวด)
VOUCHER_COST_POINTS = 100       # แต้มที่ต้องใช้แลก voucher 1 ใบ
WINDOW_TITLE = "Bottle Bank - ระบบสะสมแต้ม"
WINDOW_SIZE = "800x480"         # ปรับตามขนาดจอจริงของคุณ

# =======================================================================================


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
    ที่บังเอิญมีคำว่า "keyboard" หรือ "hid" ในชื่อ (เช่น จอสัมผัส หรือคีย์บอร์ดจำลองอื่นๆ)
    ถ้าหาด้วยคำเจาะจงไม่เจอ ค่อย fallback ไปหาด้วยคำกว้างๆ เป็นทางเลือกสุดท้าย
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
            print(f"[RFID] คำเตือน: ใช้อุปกรณ์จากการค้นหาแบบกว้าง ({device.name}) "
                  f"อาจไม่ใช่เครื่องอ่าน RFID จริง", flush=True)
            return device

    return None


def rfid_listener_thread(uid_queue):
    """
    ทำงานเบื้องหลังตลอดเวลา อ่านค่าจากเครื่องอ่าน RFID
    พอได้ UID ครบ (เจอ ENTER) จะส่งเข้า queue ให้ฝั่ง GUI ไปประมวลผลต่อ

    เหตุผลที่แยก thread: read_loop() เป็นคำสั่งบล็อก (รอรับสัญญาณค้างไว้)
    ถ้ารันในเธรดเดียวกับ Tkinter mainloop จะทำให้หน้าจอค้างไม่ตอบสนอง

    หมายเหตุ: ใส่ print(..., flush=True) และ try/except ครอบทั้งฟังก์ชัน
    เพื่อ debug ปัญหา thread ทำงานเงียบๆ โดยไม่มี error โผล่ให้เห็น
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
                if data.keystate == 1:  # keydown เท่านั้น
                    key = data.keycode
                    print(f"[RFID] รับคีย์: {key}", flush=True)
                    if key in KEYCODE_MAP:
                        if KEYCODE_MAP[key] == "ENTER":
                            if buffer:
                                print(f"[RFID] UID ครบแล้ว: {buffer} -> ส่งเข้า queue", flush=True)
                                uid_queue.put(buffer)
                                buffer = ""
                        else:
                            buffer += KEYCODE_MAP[key]

    except Exception:
        # พิมพ์ traceback แบบเต็มออกมา เผื่อ thread ล้มเงียบๆ โดยไม่มีอะไรขึ้นเลย
        import traceback
        print("[RFID] เกิดข้อผิดพลาดใน thread:", flush=True)
        traceback.print_exc()


# ---------------------------------------- ส่วน GUI ----------------------------------------


class BottleBankApp:
    def __init__(self, root):
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.configure(bg="#1e293b")

        self.big_font = tkfont.Font(size=28, weight="bold")
        self.mid_font = tkfont.Font(size=18)
        self.small_font = tkfont.Font(size=14)

        self.current_uid = None       # UID ของบัตรที่กำลังใช้งานอยู่ตอนนี้
        self.current_student = None   # ข้อมูลนักเรียนที่กำลังแสดงอยู่บนหน้าจอ

        self.uid_queue = queue.Queue()  # ช่องทางส่งข้อมูลจาก RFID thread มาหา GUI thread

        self.build_idle_screen()

        # เริ่ม background thread สำหรับอ่าน RFID
        listener = threading.Thread(
            target=rfid_listener_thread, args=(self.uid_queue,), daemon=True
        )
        listener.start()

        # เช็ค queue ทุก 200ms ว่ามีบัตรใหม่ถูกแตะเข้ามาไหม
        self.root.after(200, self.poll_rfid_queue)

    # -------------------- หน้าจอ: รอแตะบัตร --------------------
    def build_idle_screen(self):
        self.clear_screen()
        self.current_uid = None
        self.current_student = None

        tk.Label(
            self.root, text="แตะบัตรนักเรียนเพื่อเริ่มต้น",
            font=self.big_font, fg="white", bg="#1e293b",
        ).pack(expand=True)

        tk.Label(
            self.root, text="รอการแตะบัตร RFID...",
            font=self.mid_font, fg="#94a3b8", bg="#1e293b",
        ).pack()

    # -------------------- หน้าจอ: ฟอร์มลงทะเบียน (บัตรใหม่) --------------------
    def build_registration_screen(self, uid):
        self.clear_screen()
        self.current_uid = uid

        tk.Label(
            self.root, text="ยังไม่เคยลงทะเบียนบัตรใบนี้",
            font=self.mid_font, fg="white", bg="#1e293b",
        ).pack(pady=10)

        form_frame = tk.Frame(self.root, bg="#1e293b")
        form_frame.pack(pady=10)

        self.entry_student_code = self._add_form_row(form_frame, "รหัสนักเรียน", 0)
        self.entry_first_name = self._add_form_row(form_frame, "ชื่อ", 1)
        self.entry_last_name = self._add_form_row(form_frame, "นามสกุล", 2)
        self.entry_nickname = self._add_form_row(form_frame, "ชื่อเล่น", 3)

        tk.Button(
            self.root, text="ลงทะเบียน", font=self.mid_font,
            bg="#22c55e", fg="white", command=self.submit_registration,
        ).pack(pady=15, ipadx=20, ipady=8)

        tk.Button(
            self.root, text="ยกเลิก", font=self.small_font,
            bg="#64748b", fg="white", command=self.build_idle_screen,
        ).pack()

        self.reg_message_label = tk.Label(
            self.root, text="", font=self.small_font, fg="#ef4444", bg="#1e293b",
        )
        self.reg_message_label.pack()

    def _add_form_row(self, parent, label_text, row):
        """สร้างแถว label + ช่องกรอกข้อความ 1 แถว คืนค่า widget ช่องกรอกกลับไปให้เก็บ reference"""
        tk.Label(
            parent, text=label_text, font=self.small_font, fg="white", bg="#1e293b",
        ).grid(row=row, column=0, sticky="e", padx=5, pady=5)
        entry = tk.Entry(parent, font=self.small_font, width=20)
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

    # -------------------- หน้าจอ: แสดงข้อมูลนักเรียน + ปุ่มใช้งานแต้ม --------------------
    def build_student_screen(self, student):
        self.clear_screen()
        self.current_student = student
        self.current_uid = student["rfid_uid"]

        tk.Label(
            self.root, text=f"สวัสดี {student['nickname']}!",
            font=self.big_font, fg="white", bg="#1e293b",
        ).pack(pady=(20, 5))

        self.points_label = tk.Label(
            self.root, text=f"แต้มสะสม: {student['points']} แต้ม",
            font=self.mid_font, fg="#facc15", bg="#1e293b",
        )
        self.points_label.pack(pady=5)

        button_frame = tk.Frame(self.root, bg="#1e293b")
        button_frame.pack(pady=20)

        tk.Button(
            button_frame, text=f"+{POINTS_PER_BOTTLE} แต้ม\n(จำลองใส่ขวด)",
            font=self.mid_font, bg="#3b82f6", fg="white",
            command=self.add_bottle_points,
        ).grid(row=0, column=0, padx=10, ipadx=15, ipady=15)

        self.redeem_button = tk.Button(
            button_frame, text=f"แลกของรางวัล\n({VOUCHER_COST_POINTS} แต้ม)",
            font=self.mid_font, fg="white",
            command=self.redeem_voucher,
        )
        self.redeem_button.grid(row=0, column=1, padx=10, ipadx=15, ipady=15)

        self.update_redeem_button_state()

        tk.Button(
            self.root, text="จบการทำงาน / กลับหน้าหลัก", font=self.small_font,
            bg="#64748b", fg="white", command=self.build_idle_screen,
        ).pack(pady=10)

        self.message_label = tk.Label(
            self.root, text="", font=self.small_font, fg="white", bg="#1e293b",
        )
        self.message_label.pack()

    def update_redeem_button_state(self):
        """เปิด/ปิดปุ่มแลกของรางวัลตามว่าแต้มพอ 100 แต้มหรือยัง"""
        if self.current_student["points"] >= VOUCHER_COST_POINTS:
            self.redeem_button.config(state="normal", bg="#22c55e")
        else:
            self.redeem_button.config(state="disabled", bg="#94a3b8")

    def add_bottle_points(self):
        """กดปุ่ม +5 แต้ม (จำลองว่าใส่ขวด 1 ใบผ่านการตรวจสอบแล้ว)"""
        updated = update_points(self.current_uid, POINTS_PER_BOTTLE)
        if updated:
            self.current_student = updated
            self.points_label.config(text=f"แต้มสะสม: {updated['points']} แต้ม")
            self.update_redeem_button_state()
            self.show_message(f"รับขวดสำเร็จ! +{POINTS_PER_BOTTLE} แต้ม")
        else:
            self.show_message("เกิดข้อผิดพลาดในการบันทึกแต้ม", error=True)

    def redeem_voucher(self):
        """กดปุ่มแลกของรางวัล หักแต้ม 100 แต้ม แลกบัตรกำนัล 1 ใบ"""
        if self.current_student["points"] < VOUCHER_COST_POINTS:
            self.show_message("แต้มไม่พอสำหรับแลกของรางวัล", error=True)
            return

        updated = update_points(self.current_uid, -VOUCHER_COST_POINTS)
        if updated:
            log_redemption(
                student_id=updated["id"],
                rfid_uid=self.current_uid,
                nickname=updated["nickname"],
                points_used=VOUCHER_COST_POINTS,
            )
            send_discord_notification(
                f"🎁 **{updated['nickname']}** แลกของรางวัลไป {VOUCHER_COST_POINTS} แต้ม "
                f"(เหลือ {updated['points']} แต้ม)"
            )
            self.current_student = updated
            self.points_label.config(text=f"แต้มสะสม: {updated['points']} แต้ม")
            self.update_redeem_button_state()
            self.show_message("แลกสำเร็จ! ได้รับบัตรกำนัล 1 ใบ กรุณาติดต่อรับที่ครู")
        else:
            self.show_message("เกิดข้อผิดพลาดในการแลกของรางวัล", error=True)

    def show_message(self, text, error=False):
        color = "#ef4444" if error else "#4ade80"
        self.message_label.config(text=text, fg=color)

    # -------------------- ส่วนเชื่อม RFID (background thread) กับ GUI --------------------
    def poll_rfid_queue(self):
        """
        เช็ค queue ทุก 200ms ว่ามี UID ใหม่จาก background thread ส่งเข้ามาไหม
        ต้องทำแบบนี้เพราะ Tkinter อัปเดตหน้าจอได้จาก main thread เท่านั้น
        ห้ามอัปเดต widget ตรงๆ จาก thread อื่น จะทำให้โปรแกรม crash แบบสุ่ม
        """
        try:
            while True:
                uid = self.uid_queue.get_nowait()
                self.handle_card_scanned(uid)
        except queue.Empty:
            pass
        self.root.after(200, self.poll_rfid_queue)

    def handle_card_scanned(self, uid):
        student = get_student_by_uid(uid)
        if student:
            self.build_student_screen(student)
        else:
            self.build_registration_screen(uid)

    def clear_screen(self):
        """ลบ widget ทั้งหมดบนหน้าจอก่อนวาดหน้าใหม่ (ใช้แทนการสลับหน้าต่าง)"""
        for widget in self.root.winfo_children():
            widget.destroy()


def main():
    root = tk.Tk()
    BottleBankApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
