"""
ส่งข้อความแจ้งเตือนไปยัง Discord ผ่าน Webhook
ใช้แจ้งเตือนหลังบ้านเมื่อมีเหตุการณ์สำคัญเกิดขึ้น เช่น มีคนแลกของรางวัล

ติดตั้งก่อนใช้งาน:
    pip3 install --break-system-packages requests python-dotenv

ต้องมี DISCORD_WEBHOOK_URL อยู่ในไฟล์ .env (ดูวิธีสร้าง webhook ได้จากคู่มือที่แนบมา)
ถ้าไม่ได้ตั้งค่า DISCORD_WEBHOOK_URL ไว้ ฟังก์ชันนี้จะข้ามการส่งเงียบๆ ไม่ error
(เผื่อใครยังไม่อยากตั้งค่า Discord ตอนนี้ ระบบหลักส่วนอื่นจะยังทำงานได้ปกติ)
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")


def send_discord_notification(message: str):
    """
    ส่งข้อความไปยัง Discord channel ที่ตั้งค่า webhook ไว้
    ถ้าส่งไม่สำเร็จ (เช่น ไม่มีอินเทอร์เน็ตชั่วคราว) จะ print แจ้งเตือนใน terminal
    แต่ไม่ทำให้โปรแกรมหลัก (GUI) ล่ม เพราะการแจ้งเตือนไม่ควรบล็อกฟังก์ชันการทำงานหลัก
    """
    if not DISCORD_WEBHOOK_URL:
        print("[Discord] ยังไม่ได้ตั้งค่า DISCORD_WEBHOOK_URL ข้ามการแจ้งเตือน")
        return

    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=5,  # ไม่รอนานเกินไป กันโปรแกรมค้างถ้าอินเทอร์เน็ตช้า
        )
        if response.status_code not in (200, 204):
            print(f"[Discord] ส่งแจ้งเตือนไม่สำเร็จ (status {response.status_code})")
    except requests.exceptions.RequestException as e:
        print(f"[Discord] เกิดข้อผิดพลาดตอนส่งแจ้งเตือน: {e}")
