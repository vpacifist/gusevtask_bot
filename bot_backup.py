import json
import os
import atexit
import logging
import re
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackContext, filters
from telegram.error import BadRequest, Forbidden

logging.basicConfig(format='%(asctime)s - %(name)s - %(levellevelname)s - %(message)s', level=logging.INFO)

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
STATE_FILE = 'bot_state.json'

# Словарь для хранения задач для каждого чата
chat_tasks = {}
pinned_message_id = {}

# Статусы задач
STATUS_NOT_STARTED = 'Не начата'
STATUS_IN_PROGRESS = 'В процессе'
STATUS_REVIEW = 'На проверке'
STATUS_REWORK = 'Переделать'
STATUS_DONE = 'Завершена'

def save_state(chat_tasks, pinned_message_id):
    with open(STATE_FILE, 'w') as f:
        json.dump({'chat_tasks': chat_tasks, 'pinned_message_id': pinned_message_id}, f)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
            return state['chat_tasks'], state['pinned_message_id']
    return {}, {}

chat_tasks, pinned_message_id = load_state()

atexit.register(lambda: save_state(chat_tasks, pinned_message_id))

def save_state_handler(update: Update, context: CallbackContext) -> None:
    save_state(chat_tasks, pinned_message_id)
    update.message.reply_text('State saved successfully.')

async def start(update: Update, context: CallbackContext) -> None:
    logging.info(f"Received /start command from {update.effective_user.username}")
    await update.message.reply_text('Привет! Отправь мне свои задачи, и я помогу тебе их отслеживать.')

async def add_task(update: Update, context: CallbackContext) -> None:
    logging.info(f"Received /add command from {update.effective_user.username}")
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    tasks_text = update.message.text.split(' ', 1)[1] if ' ' in update.message.text else ''

    if tasks_text:
        if chat_id not in chat_tasks:
            chat_tasks[chat_id] = []

        new_tasks = re.split(r'\n|\r|\r\n', tasks_text)
        for task in new_tasks:
            if task.strip():
                chat_tasks[chat_id].append({'task': task.strip(), 'done': False, 'status': STATUS_NOT_STARTED})

        await update_pinned_message(chat_id, context.bot)
    else:
        await update.message.reply_text('Пожалуйста, укажи задачи.')

async def list_tasks(update: Update, context: CallbackContext) -> None:
    logging.info(f"Received /list command from {update.effective_user.username}")
    chat_id = update.effective_chat.id
    tasks = chat_tasks.get(chat_id, [])
    if tasks:
        message = '\n'.join(f"{i+1}. {'<s>'+task['task']+'</s>' if task['done'] else task['task']} [{task['status']}]" for i, task in enumerate(tasks))
        await update.message.reply_text(message, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text('Нет задач.')

async def update_pinned_message(chat_id, bot):
    tasks = chat_tasks.get(chat_id, [])
    if tasks:
        not_started_tasks = [f"{i+1}. {task['task']}" for i, task in enumerate(tasks) if task['status'] == STATUS_NOT_STARTED]
        in_progress_tasks = [f"{i+1}. {task['task']}" for i, task in enumerate(tasks) if task['status'] == STATUS_IN_PROGRESS]
        review_tasks = [f"{i+1}. {task['task']}" for i, task in enumerate(tasks) if task['status'] == STATUS_REVIEW]
        rework_tasks = [f"{i+1}. {task['task']}" for i, task in enumerate(tasks) if task['status'] == STATUS_REWORK]
        done_tasks = [f"{i+1}. <s>{task['task']}</s>" for i, task in enumerate(tasks) if task['status'] == STATUS_DONE]

        message = ''
        if not_started_tasks:
            message += "<b>Не начаты:</b>\n" + '\n'.join(not_started_tasks) + '\n\n'
        if in_progress_tasks:
            message += "<b>В процессе:</b>\n" + '\n'.join(in_progress_tasks) + '\n\n'
        if review_tasks:
            message += "<b>На проверке:</b>\n" + '\n'.join(review_tasks) + '\n\n'
        if rework_tasks:
            message += "<b>Переделать:</b>\n" + '\n'.join(rework_tasks) + '\n\n'
        if done_tasks:
            message += "<b>Завершены:</b>\n" + '\n'.join(done_tasks) + '\n\n'

        try:
            if chat_id in pinned_message_id:
                await bot.edit_message_text(chat_id=chat_id, message_id=pinned_message_id[chat_id], text=message.strip(), parse_mode=ParseMode.HTML)
            else:
                sent_message = await bot.send_message(chat_id=chat_id, text=message.strip(), parse_mode=ParseMode.HTML)
                pinned_message_id[chat_id] = sent_message.message_id
                await bot.pin_chat_message(chat_id=chat_id, message_id=sent_message.message_id)
        except (BadRequest, Forbidden) as e:
            logging.error(f"Failed to pin or edit pinned message: {e}")
            await bot.send_message(chat_id=chat_id, text="У меня нет прав администратора для закрепления сообщений. Пожалуйста, дайте мне эти права.")
    else:
        if chat_id in pinned_message_id:
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=pinned_message_id[chat_id], text="Нет задач.")
            except (BadRequest, Forbidden) as e:
                logging.error(f"Failed to edit pinned message: {e}")

async def delete_task(update: Update, context: CallbackContext) -> None:
    logging.info(f"Received /delete command from {update.effective_user.username}")
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        task_num = int(context.args[0]) - 1
        tasks = chat_tasks.get(chat_id, [])
        if 0 <= task_num < len(tasks):
            deleted_task = tasks.pop(task_num)
            await update_pinned_message(chat_id, context.bot)
        else:
            await update.message.reply_text('Неправильный номер задачи.')
    except (IndexError, ValueError):
        await update.message.reply_text('Пожалуйста, укажи номер задачи.')

async def done_task(update: Update, context: CallbackContext) -> None:
    logging.info(f"Received /done command from {update.effective_user.username}")
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        task_num = int(context.args[0]) - 1
        tasks = chat_tasks.get(chat_id, [])
        if 0 <= task_num < len(tasks):
            tasks[task_num]['done'] = True
            tasks[task_num]['status'] = STATUS_DONE
            await update_pinned_message(chat_id, context.bot)
        else:
            await update.message.reply_text('Неправильный номер задачи.')
    except (IndexError, ValueError):
        await update.message.reply_text('Пожалуйста, укажи номер задачи.')

async def edit_task(update: Update, context: CallbackContext) -> None:
    logging.info(f"Received /edit command from {update.effective_user.username}")
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        task_num = int(context.args[0]) - 1
        new_task_text = ' '.join(context.args[1:])
        tasks = chat_tasks.get(chat_id, [])
        if 0 <= task_num < len(tasks):
            tasks[task_num]['task'] = new_task_text
            await update_pinned_message(chat_id, context.bot)
        else:
            await update.message.reply_text('Неправильный номер задачи.')
    except (IndexError, ValueError):
        await update.message.reply_text('Пожалуйста, укажи номер задачи и новый текст задачи.')

async def set_task_status(update: Update, context: CallbackContext, status: str) -> None:
    logging.info(f"Received status change command from {update.effective_user.username}")
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        task_num = int(context.args[0]) - 1
        tasks = chat_tasks.get(chat_id, [])
        if 0 <= task_num < len(tasks):
            tasks[task_num]['status'] = status
            if status == STATUS_NOT_STARTED:
                tasks[task_num]['done'] = False
            await update_pinned_message(chat_id, context.bot)
        else:
            await update.message.reply_text('Неправильный номер задачи.')
    except (IndexError, ValueError):
        await update.message.reply_text('Пожалуйста, укажи номер задачи.')

async def help_command(update: Update, context: CallbackContext) -> None:
    logging.info(f"Received /help command from {update.effective_user.username}")
    help_text = (
        "/start - Начать работу с ботом\n"
        "/add <задача> - Добавить новую задачу или несколько задач, разделённых новой строкой\n"
        "/list - Показать список задач\n"
        "/done <номер задачи> - Отметить задачу как выполненную\n"
        "/delete <номер задачи> - Удалить задачу\n"
        "/edit <номер задачи> <новый текст> - Редактировать текст задачи\n"
        "/notstarted <номер задачи> - Установить статус задачи 'Не начата'\n"
        "/inprogress <номер задачи> - Установить статус задачи 'В процессе'\n"
        "/review <номер задачи> - Установить статус задачи 'На проверке'\n"
        "/rework <номер задачи> - Установить статус задачи 'Переделать'\n"
        "/help - Показать это сообщение помощи"
    )
    await update.message.reply_text(help_text)

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("save_state", save_state_handler))
app.add_handler(CommandHandler("start", start, filters.ChatType.GROUPS))
app.add_handler(CommandHandler("add", add_task, filters.ChatType.GROUPS))
app.add_handler(CommandHandler("list", list_tasks, filters.ChatType.GROUPS))
app.add_handler(CommandHandler("done", done_task, filters.ChatType.GROUPS))
app.add_handler(CommandHandler("delete", delete_task, filters.ChatType.GROUPS))
app.add_handler(CommandHandler("edit", edit_task, filters.ChatType.GROUPS))
app.add_handler(CommandHandler("help", help_command, filters.ChatType.GROUPS))
app.add_handler(CommandHandler("notstarted", lambda update, context: set_task_status(update, context, STATUS_NOT_STARTED), filters.ChatType.GROUPS))
app.add_handler(CommandHandler("inprogress", lambda update, context: set_task_status(update, context, STATUS_IN_PROGRESS), filters.ChatType.GROUPS))
app.add_handler(CommandHandler("review", lambda update, context: set_task_status(update, context, STATUS_REVIEW), filters.ChatType.GROUPS))
app.add_handler(CommandHandler("rework", lambda update, context: set_task_status(update, context, STATUS_REWORK), filters.ChatType.GROUPS))

app.add_handler(CommandHandler("start", start, filters.ChatType.PRIVATE))
app.add_handler(CommandHandler("add", add_task, filters.ChatType.PRIVATE))
app.add_handler(CommandHandler("list", list_tasks, filters.ChatType.PRIVATE))
app.add_handler(CommandHandler("done", done_task, filters.ChatType.PRIVATE))
app.add_handler(CommandHandler("delete", delete_task, filters.ChatType.PRIVATE))
app.add_handler(CommandHandler("edit", edit_task, filters.ChatType.PRIVATE))
app.add_handler(CommandHandler("help", help_command, filters.ChatType.PRIVATE))
app.add_handler(CommandHandler("notstarted", lambda update, context: set_task_status(update, context, STATUS_NOT_STARTED), filters.ChatType.PRIVATE))
app.add_handler(CommandHandler("inprogress", lambda update, context: set_task_status(update, context, STATUS_IN_PROGRESS), filters.ChatType.PRIVATE))
app.add_handler(CommandHandler("review", lambda update, context: set_task_status(update, context, STATUS_REVIEW), filters.ChatType.PRIVATE))
app.add_handler(CommandHandler("rework", lambda update, context: set_task_status(update, context, STATUS_REWORK), filters.ChatType.PRIVATE))

logging.info("Starting bot")
app.run_polling()
