import os
import time
from threading import Timer
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google import genai

app = Flask(__name__)

# ดึงค่าจาก Environment Variables ของ Render
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# สร้าง Client สำหรับ google-genai SDK
client = genai.Client(api_key=GEMINI_API_KEY)

user_buffers = {}
user_histories = {}

# ================= System Prompt (Updated) =================
SYSTEM_PROMPT = """
คุณคือ "ผู้ช่วยรับข้อมูล JOB INFORMATION ของบริษัท สมาร์ท แอนด์ อีซี่ จำกัด"
หน้าที่ของคุณคืออ่านข้อความ และช่วยรวบรวมข้อมูลตามหัวข้อต่อไปนี้:

[ข้อมูล 7 หัวข้อ]
1. company_name: ชื่อบริษัทลูกค้า
2. job_name: ชื่องาน
3. coordinator_name: ผู้รับงานฝั่งเรา
4. contact_info: ผู้ติดต่อฝั่งลูกค้า / เบอร์โทร (หากไม่ได้ระบุมาตั้งแต่แรก ห้ามถามหา ให้ลงค่าเป็น "- (ไม่ได้ระบุ)")
5. work_type: ประเภทงาน (ทำใหม่ หรือ ซ่อมแก้ไข)
6. items_list: รายละเอียดสเปกชิ้นงาน (ขนาด Ø, กว้างxยาวxลึก, ชนิดวัสดุ, จำนวน)
7. delivery_date: กำหนดส่งงาน (หากไม่ได้ระบุมาตั้งแต่แรก ห้ามถามหา ให้ลงค่าเป็น "- (ไม่ได้ระบุ)")

[กฎการทำงาน]
1. อ่านข้อความที่ผู้ใช้พิมพ์มา
2. ห้ามถามหาข้อมูลข้อ 4 (ผู้ติดต่อ/เบอร์โทร) และข้อ 7 (วันที่กำหนดส่งของ) เด็ดขาด หากผู้ใช้ไม่ได้พิมพ์บอกมาตั้งแต่แรก ให้ถือว่าไม่ต้องถามเพิ่ม
3. หากรายละเอียดสเปกชิ้นงาน (ข้อ 6) ยังขาดขนาดหรือวัสดุ ให้ถามเจาะจงสั้นๆ ทันที ไม่ต้องพูดเยิ่นเย้อ เช่น:
   - "ขอลายละเอียดงานหน่อยครับว่า ขนาดความกว้าง x ความลึกของร่องลิ่ม (มม.), ขนาดเพลา, วัสดุอะไร และกี่ชิ้นครับ?"
4. หากข้อมูลสเปกครบถ้วนแล้ว ให้แสดงสรุปในรูปแบบสะอาด คลีน ไม่ใช้เครื่องหมาย * ดังนี้:

📋 สรุปข้อมูล JOB INFORMATION

- บริษัทลูกค้า: [company_name หรือ - (ไม่ได้ระบุ)]
- ชื่องาน: [job_name หรือ - (ไม่ได้ระบุ)]
- ผู้รับงาน (Co-ordinator): [coordinator_name หรือ - (ไม่ได้ระบุ)]
- ผู้ติดต่อ / เบอร์โทร: [contact_info หรือ - (ไม่ได้ระบุ)]
- ประเภทงาน: [work_type หรือ - (ไม่ได้ระบุ)]
- รายการและจำนวน: [items_list หรือ - (ไม่ได้ระบุ)]
- วันที่กำหนดส่งของ: [delivery_date หรือ - (ไม่ได้ระบุ)]

สรุปข้อมูลเท่าที่มีเรียบร้อยแล้วครับ หากต้องการเพิ่มข้อมูลส่วนไหนแจ้งได้เลยครับ
"""

def call_gemini_with_retry(messages, retries=3):
    for i in range(retries):
        try:
            # เรียกใช้โมเดลผ่าน SDK ตัวใหม่
            response = client.models.generate_content(
                model="gemini-2.5-flash",  # หรือปรับชื่อรุ่นโมเดลตามที่ใช้งานจริง
                contents=messages
            )
            return response.text
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                time.sleep((2 ** i) + 1)
            else:
                raise e
    return "ขออภัยครับ ขณะนี้ระบบประมวลผลมีผู้ใช้งานจำนวนมาก กรุณาลองใหม่อีกครั้งในอีกสักครู่นะครับ"

def process_buffered_messages(user_id, reply_token):
    if user_id not in user_buffers:
        return

    combined_message = "\n".join(user_buffers[user_id]['messages'])
    del user_buffers[user_id]

    if user_id not in user_histories:
        user_histories[user_id] = []

    user_histories[user_id].append(f"User: {combined_message}")

    context_payload = SYSTEM_PROMPT + "\n\n[ประวัติการคุยที่ผ่านมา]\n" + "\n".join(user_histories[user_id][-10:])
    ai_reply = call_gemini_with_retry(context_payload)
    user_histories[user_id].append(f"AI: {ai_reply}")

    try:
        line_bot_api.reply_message(reply_token, TextSendMessage(text=ai_reply))
    except Exception as e:
        print(f"Failed to reply LINE message: {e}")

@app.route("/webhook", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text_sent = event.message.text
    reply_token = event.reply_token

    if user_id in user_buffers and 'timer' in user_buffers[user_id]:
        user_buffers[user_id]['timer'].cancel()

    if user_id not in user_buffers:
        user_buffers[user_id] = {'messages': [], 'timer': None}

    user_buffers[user_id]['messages'].append(text_sent)
    timer = Timer(2.5, process_buffered_messages, args=[user_id, reply_token])
    user_buffers[user_id]['timer'] = timer
    timer.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)