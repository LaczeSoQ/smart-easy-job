import os
import traceback
from flask import Flask, abort, request
from google import genai
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)

# ตั้งค่า LINE และ Gemini SDK จาก Environment Variables
configuration = Configuration(
    access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
)
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# System Prompt
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
3. หากรายละเอียดสเปกชิ้นงาน (ข้อ 6) ยังขาดขนาดหรือวัสดุ ให้ถามเจาะจงสั้นๆ ทันที เช่น:
   - "ขอลายละเอียดงานหน่อยครับว่า ขนาดความกว้าง x ความลึกของร่องลิ่ม (มม.), ขนาดเพลา, วัสดุอะไร และกี่ชิ้นครับ?"
4. หากข้อมูลสเปกครบถ้วนแล้ว ให้แสดงสรุปในรูปแบบสะอาด คลีน ไม่ใส่เครื่องหมาย * ดังนี้:

📋 สรุปข้อมูล JOB INFORMATION

• บริษัทลูกค้า: [company_name หรือ - (ไม่ได้ระบุ)]
• ชื่องาน: [job_name หรือ - (ไม่ได้ระบุ)]
• ผู้รับงาน (Co-ordinator): [coordinator_name หรือ - (ไม่ได้ระบุ)]
• ผู้ติดต่อ / เบอร์โทร: [contact_info หรือ - (ไม่ได้ระบุ)]
• ประเภทงาน: [work_type หรือ - (ไม่ได้ระบุ)]
• รายการและจำนวน: [items_list หรือ - (ไม่ได้ระบุ)]
• วันที่กำหนดส่งของ: [delivery_date หรือ - (ไม่ได้ระบุ)]

ตรวจสอบข้อมูลเรียบร้อยแล้ว หากถูกต้องกรุณากดยืนยันได้เลยครับ
"""


@app.route("/callback", methods=["POST"])
def callback():
  signature = request.headers.get("X-Line-Signature", "")
  body = request.get_data(as_text=True)
  app.logger.info("Request body: " + body)
  try:
    handler.handle(body, signature)
  except InvalidSignatureError:
    app.logger.error("Invalid signature check error.")
    abort(400)
  except Exception as e:
    app.logger.error(f"Error handling webhook: {traceback.format_exc()}")
  return "OK"


# จัดการเฉพาะข้อความตัวอักษร
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
  user_message = event.message.text
  ai_reply_text = ""

  try:
    # 1. เรียกใช้งาน Gemini AI
    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=f"{SYSTEM_PROMPT}\n\nข้อความจากลูกค้า: {user_message}",
    )

    if response and response.text:
      ai_reply_text = response.text
    else:
      ai_reply_text = "ไม่สามารถประมวลผลข้อความได้ในขณะนี้ครับ"

  except Exception as e:
    # พิมพ์รายละเอียด Error ฉบับเต็มลงใน Log บน Render
    app.logger.error(f"Error calling Gemini API: {traceback.format_exc()}")
    ai_reply_text = "ระบบ AI ขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้งครับ"

  # 2. ส่งข้อความตอบกลับไปยัง LINE
  try:
    with ApiClient(configuration) as api_client:
      line_bot_api = MessagingApi(api_client)
      line_bot_api.reply_message(
          ReplyMessageRequest(
              reply_token=event.reply_token,
              messages=[TextMessage(text=ai_reply_text)],
          )
      )
  except Exception as e:
    # พิมพ์รายละเอียด Error ฝั่ง LINE API ลง Log
    app.logger.error(f"Error sending LINE Reply: {traceback.format_exc()}")


if __name__ == "__main__":
  app.run(port=5000)