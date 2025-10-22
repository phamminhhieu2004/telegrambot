import os
import re
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from docx import Document

# === CẤU HÌNH BOT ===
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Ẩn token bằng biến môi trường

if not BOT_TOKEN:
    raise ValueError("⚠️ Chưa thiết lập biến BOT_TOKEN trong Render hoặc .env!")

# === LOGGING ===
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# === DỮ LIỆU BỘ NHỚ ===
questions_data = {}
user_answers = {}

# === PHÂN TÍCH FILE WORD ===
def parse_docx(file_path):
    doc = Document(file_path)
    questions = []
    i = 0

    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue

        # Tìm câu hỏi
        if re.match(r"^Câu\s*\d+", text):
            i += 1
            q_type = "multiple_choice"
            options = []
            correct = []
            question_text = text

            # Kiểm tra xem có loại câu hỏi không
            if "[Câu hỏi tự luận]" in text:
                q_type = "essay"
            elif "[Câu hỏi điền từ]" in text:
                q_type = "fill_in"
            elif "[Câu hỏi kéo thả]" in text:
                q_type = "match"

            # Thu thập các đoạn tiếp theo (A., B., đáp án…)
            for nxt in doc.paragraphs[doc.paragraphs.index(p)+1:]:
                line = nxt.text.strip()
                if not line:
                    continue
                if line.startswith("Câu "):
                    break
                if re.match(r"^[A-D]\.", line):
                    options.append(line)
                elif "Đáp án đúng" in line:
                    correct = re.findall(r"[A-D]+|\d+|[A-Za-zÀ-ỹ\s-]+", line.split(":")[-1].strip())
                elif "[IMG]" in line:
                    pass
                question_text += "\n" + line

            # Lưu câu hỏi
            questions.append({
                "id": i,
                "type": q_type,
                "text": question_text,
                "options": options,
                "correct": correct,
                "images": []  # nơi lưu ảnh (nếu có)
            })

    # Lưu ảnh (nếu có)
    for rel in doc.part.rels.values():
        if "image" in rel.target_ref:
            image_data = rel.target_part.blob
            img_path = f"/tmp/image_{len(questions)}.jpg"
            with open(img_path, "wb") as f:
                f.write(image_data)
            if questions:
                questions[-1]["images"].append(img_path)

    return questions


# === LỆNH /START ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Xin chào bạn! Gửi file để Hiếu làm cho nhé.\n\n"
        "• Trắc nghiệm\n• Tự luận\n• Điền từ\n• Kéo thả\n\n"
        "Bot sẽ tự nhận dạng và chấm điểm cho bạn sau khi làm xong!"
    )

# === NHẬN FILE WORD ===
async def handle_docx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    doc = update.message.document
    if not doc.file_name.endswith(".docx"):
        await update.message.reply_text("⚠️ Gửi file .docx hợp lệ!")
        return

    file = await doc.get_file()
    file_path = f"/tmp/{user_id}.docx"
    await file.download_to_drive(file_path)

    questions = parse_docx(file_path)
    if not questions:
        await update.message.reply_text("❌ Không tìm thấy câu hỏi nào.")
        return

    questions_data[user_id] = questions
    user_answers[user_id] = {"index": 0, "answers": []}

    await update.message.reply_text(f"✅ Đã tải {len(questions)} câu hỏi!\nGõ /startquiz để bắt đầu.")

# === BẮT ĐẦU LÀM BÀI ===
async def startquiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in questions_data:
        await update.message.reply_text("📄 Gửi file đề trước nhé.")
        return

    user_answers[user_id] = {"index": 0, "answers": []}
    await send_question(update.message, user_id)

# === GỬI CÂU HỎI ===
async def send_question(message, user_id):
    qdata = user_answers[user_id]
    qlist = questions_data[user_id]

    if qdata["index"] >= len(qlist):
        await message.reply_text("🎯 Đã hoàn tất bài thi!")
        return

    q = qlist[qdata["index"]]
    text = f"📝 {q['text']}"

    if q["type"] == "multiple_choice":
        keyboard = [
            [InlineKeyboardButton("A", callback_data="A"),
             InlineKeyboardButton("B", callback_data="B")],
            [InlineKeyboardButton("C", callback_data="C"),
             InlineKeyboardButton("D", callback_data="D")],
            [InlineKeyboardButton("✅ Xác nhận", callback_data="confirm")]
        ]
        if q["images"]:
            with open(q["images"][0], "rb") as img:
                await message.reply_photo(img, caption=text,
                    reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    elif q["type"] == "essay":
        await message.reply_text(f"✍️ Câu hỏi tự luận:\n\n{text}\n\n👉 Gõ câu trả lời của bạn:")
    elif q["type"] == "fill_in":
        await message.reply_text(f"🧩 Điền từ/số vào chỗ trống:\n{text}")
    elif q["type"] == "match":
        await message.reply_text(f"🔗 Kéo thả / ghép nối:\n{text}")
    else:
        await message.reply_text(text)

# === CHẠY BOT ===
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("startquiz", startquiz))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docx))
    app.run_polling()

if __name__ == "__main__":
    main()
