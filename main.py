import os
import re
import logging
import traceback
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from docx import Document

# === CẤU HÌNH BOT ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("⚠️ Chưa thiết lập biến BOT_TOKEN trong Render hoặc .env!")

# === LOGGING ===
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# === DỮ LIỆU BỘ NHỚ ===
questions_data = {}   # user_id -> list of questions
user_answers = {}     # user_id -> {"index": int, "answers": [], "selected": None}


# === HÀM CHUẨN HÓA CHUỖI (dùng cho so sánh đáp án) ===
def normalize_answer(ans: str) -> str:
    if ans is None:
        return ""
    # loại bỏ zero-width và non-breaking space, dấu câu phổ biến, và tất cả khoảng trắng, chuyển về lowercase
    s = ans
    s = s.replace("\u200b", "")
    s = s.replace("\u00a0", " ")
    s = s.replace(",", "")
    s = s.replace(".", "")
    s = s.replace(";", "")
    s = s.replace(":", "")
    s = re.sub(r"\s+", "", s)
    return s.strip().lower()


# === PHÂN TÍCH FILE WORD ===
def parse_docx(file_path):
    """
    Đọc file Word, tách câu, nhận dạng loại:
      - multiple_choice (có A./A) ...)
      - true_false (có 'đúng' và 'sai' trong nội dung)
      - fill (có 'điền' hoặc '...' hoặc mặc định)
      - sort (có 'sắp xếp')
    Lưu "Đáp án đúng" riêng (không hiển thị khi gửi câu).
    """
    doc = Document(file_path)
    paragraphs = [p.text.rstrip() for p in doc.paragraphs]
    questions = []
    i = 0

    # bắt đầu câu: Câu 1, Câu:1, Câu - 1 ...
    question_pattern = re.compile(r"^[Cc][\s\.:_-]*\d+")
    # nhận dạng option đa dạng: A. A) (A) a. A - A —
    option_pattern = re.compile(r'^\s*[\(\[]?[A-Ea-e][\)\]\.\-\—]?\s+.*')

    current_lines = []
    current_options = []
    current_correct = []

    def flush_current():
        nonlocal i, current_lines, current_options, current_correct
        if not current_lines:
            return
        i += 1

        visible_lines = []
        correct_ans = []

        for ln in current_lines:
            if re.search(r"đáp án đúng", ln, re.IGNORECASE):
                txt = ln.split(":", 1)[-1].strip()
                found = re.findall(r"[A-EĐđSsAaIi]+|\d+|[A-Za-zÀ-ỹ\s-]+", txt)
                correct_ans = [x.strip() for x in found if x.strip()]
            else:
                visible_lines.append(ln)

        question_text = "\n".join(visible_lines).strip()
        low = question_text.lower()

        # quyết định loại (ưu tiên multiple_choice nếu có options)
        if current_options:
            q_type = "multiple_choice"
        elif "đúng" in low and "sai" in low:
            q_type = "true_false"
        elif "sắp xếp" in low or re.search(r"sắp.?xếp", low):
            q_type = "sort"
        elif "điền" in low or "..." in question_text:
            q_type = "fill"
        else:
            q_type = "fill"

        questions.append({
            "id": i,
            "type": q_type,
            "text": question_text,
            "options": list(current_options),
            "correct": list(correct_ans),
        })

        logging.info(f"Parsed Q{i}: type={q_type}, options={len(current_options)}, correct={correct_ans}")

        current_lines = []
        current_options = []
        current_correct = []

        return

    for para in paragraphs:
        text = para.strip()
        if not text:
            continue

        # nếu bắt đầu câu mới
        if question_pattern.match(text):
            if current_lines:
                flush_current()
            current_lines = [text]
            current_options = []
            current_correct = []
            continue

        # nếu dòng là option
        if option_pattern.match(text):
            current_options.append(text)
            current_lines.append(text)
            continue

        # nếu là dòng đáp án
        if re.search(r"đáp án đúng", text, re.IGNORECASE):
            current_lines.append(text)
            continue

        # bình thường: thêm vào current_lines
        if current_lines:
            current_lines.append(text)
        else:
            # chưa bắt đầu câu nào, bỏ qua header
            continue

    # flush câu cuối
    if current_lines:
        flush_current()

    logging.info(f"Total parsed questions: {len(questions)}")
    return questions


# === /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Xin chào! Gửi file .docx chứa đề (Câu 1, Câu 2,...).\n"
        "Hỗ trợ: Trắc nghiệm (A–E), Đúng/Sai, Điền, Sắp xếp.\n"
        "Gõ /startquiz sau khi bot báo đã tải đề."
    )


# === NHẬN FILE WORD ===
async def handle_docx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".docx"):
        await update.message.reply_text("⚠️ Vui lòng gửi file .docx hợp lệ.")
        return

    file_path = f"/tmp/{user_id}.docx"
    try:
        file = await doc.get_file()
        await file.download_to_drive(file_path)
    except Exception as e:
        logging.exception("Lỗi tải file")
        await update.message.reply_text(f"⚠️ Lỗi khi tải file: {e}")
        return

    try:
        questions = parse_docx(file_path)
    except Exception as e:
        tb = traceback.format_exc()
        logging.error(tb)
        await update.message.reply_text(f"❌ Lỗi khi đọc file: {e}")
        return

    if not questions:
        await update.message.reply_text("❌ Không tìm thấy câu hỏi nào trong file Word.")
        return

    # Lưu vào bộ nhớ
    questions_data[user_id] = questions
    user_answers[user_id] = {"index": 0, "answers": [], "selected": None}

    await update.message.reply_text(f"✅ Đã tải {len(questions)} câu hỏi! Gõ /startquiz để bắt đầu.")


# === /startquiz ===
async def startquiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in questions_data:
        await update.message.reply_text("📄 Gửi file đề trước nhé.")
        return
    user_answers[user_id] = {"index": 0, "answers": [], "selected": None}
    await send_question(update.message, user_id)


# === GỬI CÂU HỎI ===
async def send_question(message, user_id):
    qdata = user_answers.get(user_id)
    qlist = questions_data.get(user_id)
    if not qdata or not qlist:
        await message.reply_text("❌ Không thấy dữ liệu đề. Gửi lại file .docx.")
        return

    if qdata["index"] >= len(qlist):
        await show_result(message, user_id)
        return

    q = qlist[qdata["index"]]
    text = f"📝 {q['text']}"

    buttons = []
    if q["type"] == "multiple_choice":
        for opt in q.get("options", []):
            # match A., A) (A) etc.
            m = re.match(r'^\s*[\(\[]?([A-Ea-e])[\)\]\.\-\—]?\s*(.*)', opt)
            if m:
                key = m.group(1).upper()
                buttons.append(InlineKeyboardButton(key, callback_data=key))
    elif q["type"] == "true_false":
        buttons = [
            InlineKeyboardButton("✅ Đúng", callback_data="Đúng"),
            InlineKeyboardButton("❌ Sai", callback_data="Sai"),
        ]

    if buttons:
        # arrange buttons 2 per row if more than 1, else single column
        kb = []
        row = []
        for i, b in enumerate(buttons, start=1):
            row.append(b)
            if len(row) == 2:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        kb.append([InlineKeyboardButton("✅ Xác nhận", callback_data="confirm")])
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    elif q["type"] == "fill":
        await message.reply_text(f"✏️ {text}\n➡️ Gõ câu trả lời của bạn:")
    elif q["type"] == "sort":
        opts = "\n".join(q.get("options", []))
        await message.reply_text(f"🔢 {text}\n\n{opts}\n\n➡️ Gõ thứ tự bạn cho là đúng (vd: A,B,C,D,E):")


# === NHẬN TRẢ LỜI VĂN BẢN (điền / sắp xếp) ===
async def handle_text_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_answers or user_id not in questions_data:
        return

    state = user_answers[user_id]
    qlist = questions_data[user_id]
    idx = state["index"]
    if idx >= len(qlist):
        return

    q = qlist[idx]
    if q["type"] in ("fill", "sort"):
        ans = update.message.text.strip()
        state["answers"].append(ans)
        state["index"] += 1
        await update.message.reply_text(f"✅ Đã ghi nhận: {ans}")
        await send_question(update.message, user_id)


# === CALLBACK (trắc nghiệm / đúng-sai) ===
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in user_answers or user_id not in questions_data:
        await query.edit_message_text("⚠️ Không có bài đang làm.")
        return

    data = query.data
    state = user_answers[user_id]

    if data in ("A", "B", "C", "D", "E", "Đúng", "Sai"):
        state["selected"] = data
        # cập nhật message để hiển thị lựa chọn
        try:
            await query.edit_message_text((query.message.text or "") + f"\n\n✅ Đã chọn: {data}\nNhấn ✅ Xác nhận để lưu.")
        except Exception:
            # nếu edit fail (ví dụ message không cho edit), gửi 1 reply nhỏ
            await query.message.reply_text(f"✅ Đã chọn: {data}. Nhấn ✅ Xác nhận để lưu.")
        return

    if data == "confirm":
        sel = state.get("selected")
        if not sel:
            await query.message.reply_text("⚠️ Chưa chọn đáp án nào.")
            return
        state["answers"].append(sel)
        state["selected"] = None
        state["index"] += 1
        await query.message.reply_text(f"✅ Lưu đáp án: {sel}")
        await send_question(query.message, user_id)


# === CHẤM ĐIỂM VÀ HIỂN THỊ KẾT QUẢ ===
async def show_result(message, user_id):
    qlist = questions_data.get(user_id, [])
    answers = user_answers.get(user_id, {}).get("answers", [])

    total = len(qlist)
    correct_count = 0
    summary_lines = []

    for i, q in enumerate(qlist):
        correct_ans = ",".join(q.get("correct", [])) if q.get("correct") else ""
        user_ans = answers[i] if i < len(answers) else ""
        # chuẩn hóa trước khi so sánh
        if normalize_answer(user_ans) == normalize_answer(correct_ans):
            correct_count += 1
            status = "✅ Đúng"
        else:
            status = "❌ Sai"
        summary_lines.append(f"Câu {i+1}: {status} (Bạn: {user_ans or '?'} / Đúng: {correct_ans or '?'})")

    # Tính điểm: tỉ lệ đúng sang thang 10 (2 chữ số)
    score = round((correct_count / total) * 10, 2) if total else 0.0

    result_text = (
        f"🎯 Hoàn thành bài thi!\n"
        f"📊 Điểm: {score}/10\n"
        f"✅ Số câu đúng: {correct_count}/{total}\n\n"
        + "\n".join(summary_lines)
    )

    await message.reply_text(result_text)


# === MAIN ===
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("startquiz", startquiz))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docx))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_answer))

    app.run_polling()  # polling (delay = 0 behavior is that we don't intentionally sleep between sends)


if __name__ == "__main__":
    main()
    