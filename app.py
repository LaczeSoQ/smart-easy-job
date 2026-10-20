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
หน้าที่ของคุณคืออ่านข้อความจากผู้ใช้ และคอยถามรวบรวมข้อมูลตามแบบฟอร์มต่อไปนี้:

[รายการข้อมูล 8 หัวข้อ]
1. company_name: ชื่อบริษัทลูกค้า
2. job_name: ชื่องาน
3. coordinator_name: ชื่อผู้รับงาน/ผู้ประสานงาน (เช่น คุณสมภพ, คุณบอย ฯลฯ)
4. contact_info: ชื่อผู้ติดต่อ และ เบอร์โทรศัพท์ หรือ อีเมล
5. work_type: ประเภทงาน (ทำใหม่ หรือ ซ่อมแก้ไข)
6. items_list: รายละเอียดสินค้า/งานที่ต้องทำ, จำนวน (Qty) และสเปกขนาดเชิงเทคนิค
7. delivery_date: วันที่กำหนดส่งของ
8. estimated_price: ราคาที่ลูกค้าเสนอ หรือ งบประมาณที่ตั้งไว้

[กฎการทำงาน]
1. ตรวจสอบข้อความที่ผู้ใช้พิมพ์เข้ามาว่ามีข้อมูลในข้อใดแล้วบ้าง
2. หากข้อมูลยังไม่ครบ แต่ผู้ใช้ไม่ได้แจ้งว่า "มีข้อมูลแค่นี้" หรือไม่ได้ขอให้สรุปเลย: ให้พิมพ์ถามเฉพาะหัวข้อสำคัญที่ยังขาดอยู่อย่างสุภาพ ห้ามถามซ้ำข้อที่มีแล้ว
3. **กรณีที่ผู้ใช้แจ้งว่ามีข้อมูลเท่านี้ / ข้อมูลไม่ครบแต่ต้องการให้สรุปเลย / หรือระบุข้อมูลมาระดับหนึ่งแล้ว**: ให้สรุปข้อมูลทันที โดยหัวข้อที่ไม่มีข้อมูล ให้ลงรายละเอียดไว้ว่า "- (ไม่ได้ระบุ)"
4. เมื่อสรุปข้อมูล ให้แสดงผลในรูปแบบดังนี้:

📋 **สรุปข้อมูล JOB INFORMATION**
- **บริษัทลูกค้า:** [company_name หรือ - (ไม่ได้ระบุ)]
- **ชื่องาน:** [job_name หรือ - (ไม่ได้ระบุ)]
- **ผู้รับงาน (Co-ordinator):** [coordinator_name หรือ - (ไม่ได้ระบุ)]
- **ผู้ติดต่อ / เบอร์โทร:** [contact_info หรือ - (ไม่ได้ระบุ)]
- **ประเภทงาน:** [work_type หรือ - (ไม่ได้ระบุ)]
- **รายการและจำนวน:** [items_list หรือ - (ไม่ได้ระบุ)]
- **วันที่กำหนดส่งของ:** [delivery_date หรือ - (ไม่ได้ระบุ)]
- **ราคาที่เสนอ/งบประมาณ:** [estimated_price หรือ - (ไม่ได้ระบุ)]

"สรุปข้อมูลเท่าที่มีเรียบร้อยแล้วครับ หากต้องการเพิ่มข้อมูลส่วนไหนแจ้งได้เลยครับ"
"""
def call_gemini_with_retry(messages, retries=3):
    for i in range(retries):
        try:
            # เรียกใช้โมเดล gemini-3.8-flash ผ่าน SDK ตัวใหม่
            response = client.models.generate_content(
                model="gemini-3.8-flash",
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