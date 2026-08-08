"""
โมดูลเชื่อมต่อกับ Supabase สำหรับจัดการข้อมูลนักเรียนและแต้มสะสม

ติดตั้งก่อนใช้งาน:
    pip3 install --break-system-packages supabase python-dotenv

ต้องสร้างไฟล์ .env ในโฟลเดอร์เดียวกับสคริปต์นี้ (ดูตัวอย่างใน env_example.txt)
ใส่ค่าจริงจากหน้า Settings > API ของโปรเจกต์ Supabase:
    SUPABASE_URL=https://xxxxx.supabase.co
    SUPABASE_KEY=your-anon-key-here

*** ห้าม commit ไฟล์ .env ขึ้น GitHub เด็ดขาด เพราะมีกุญแจฐานข้อมูลอยู่ในนั้น ***
"""

import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "ไม่พบ SUPABASE_URL หรือ SUPABASE_KEY กรุณาสร้างไฟล์ .env "
        "(ดูตัวอย่างใน env_example.txt) แล้วใส่ค่าจริงจากหน้า Settings > API"
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_student_by_uid(uid: str):
    """
    ค้นหานักเรียนจาก RFID UID ในตาราง students

    return: dict ข้อมูลนักเรียนถ้าเจอ (เช่น {'nickname': 'เอ', 'points': 12, ...})
            หรือ None ถ้ายังไม่เคยลงทะเบียน
    """
    response = (
        supabase.table("students")
        .select("*")
        .eq("rfid_uid", uid)
        .execute()
    )
    if response.data:
        return response.data[0]
    return None


def register_student(uid: str, student_code: str, first_name: str, last_name: str, nickname: str):
    """
    ลงทะเบียนนักเรียนใหม่ ผูกข้อมูลกับ RFID UID
    แต้มเริ่มต้นจะเป็น 0 โดยอัตโนมัติ (ตั้งไว้ที่ default ของตาราง)

    return: dict ข้อมูลนักเรียนที่เพิ่งสร้าง หรือ None ถ้าเกิดข้อผิดพลาด
    """
    response = (
        supabase.table("students")
        .insert(
            {
                "rfid_uid": uid,
                "student_code": student_code,
                "first_name": first_name,
                "last_name": last_name,
                "nickname": nickname,
            }
        )
        .execute()
    )
    return response.data[0] if response.data else None


def update_points(uid: str, delta: int):
    """
    ปรับแต้มของนักเรียน (บวกหรือลบ) ตาม RFID UID
    delta เป็นบวก = เพิ่มแต้ม, เป็นลบ = ลดแต้ม (เช่นตอนแลกรางวัล)

    return: dict ข้อมูลนักเรียนหลังอัปเดตแต้มแล้ว หรือ None ถ้าไม่เจอนักเรียน/เกิดข้อผิดพลาด
    """
    student = get_student_by_uid(uid)
    if student is None:
        return None

    new_points = student["points"] + delta
    if new_points < 0:
        new_points = 0  # กันแต้มติดลบ

    response = (
        supabase.table("students")
        .update({"points": new_points})
        .eq("rfid_uid", uid)
        .execute()
    )
    return response.data[0] if response.data else None
