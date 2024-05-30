import sqlite3
import logging
import atexit
import json
import os
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackContext
from telegram.error import BadRequest, Forbidden, TelegramError

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')


# Словарь для хранения задач для каждого чата - ОБЪЯВЛЕНИЕ ГЛОБАЛЬНЫХ ПЕРЕМЕННЫХ
chat_tasks = {}
pinned_message_id = {}
DB_PATH = '/root/mybot/mydatabase.db'


# Функция для создания таблиц в базе данных
def create_tables():
    with sqlite3.connect('mydatabase.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task TEXT NOT NULL,
                status TEXT NOT NULL,
                done INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                assignee TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pinned_messages (
                chat_id INTEGER PRIMARY KEY,
                message_id INTEGER
            )
        ''')


def create_users_table():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                user_id INTEGER
            )
        ''')


# Вызываем функцию для создания таблиц при запуске бота
create_tables()
create_users_table()


# Функция для загрузки состояния с обработкой ошибок
def load_state():
    global chat_tasks, pinned_message_id

    try:
        with sqlite3.connect(DB_PATH) as conn:
            logging.info("Connected to the database")
            cursor = conn.cursor()

            # Загрузка задач
            cursor.execute("SELECT * FROM tasks")
            logging.info("Executed SELECT query for tasks")
            rows = cursor.fetchall()
            logging.info(f"Fetched {len(rows)} tasks from database")
            for row in rows:
                task_id, task, status, done, chat_id, assignee_json = row
                assignee = json.loads(assignee_json) if assignee_json else None
                if chat_id not in chat_tasks:
                    chat_tasks[chat_id] = []
                chat_tasks[chat_id].append({'task': task, 'status': status, 'done': bool(done), 'assignee': assignee})

            # Загрузка ID закрепленных сообщений
            cursor.execute("SELECT * FROM pinned_messages")
            logging.info("Executed SELECT query for pinned messages")
            rows = cursor.fetchall()
            logging.info(f"Fetched {len(rows)} pinned messages from database")
            for row in rows:
                chat_id, message_id = row
                pinned_message_id[chat_id] = message_id

    except sqlite3.Error as e:
        logging.error(f"Error loading state from database: {e}")

    logging.info(f"Loaded chat_tasks: {chat_tasks}")
    logging.info(f"Loaded pinned_message_id: {pinned_message_id}")

    return chat_tasks, pinned_message_id


# Функция для сохранения состояния
def save_state(updated_chat_tasks, updated_pinned_message_id):
    global chat_tasks, pinned_message_id

    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            logging.info("Connected to the database for saving state")

            # Сохраняем задачи
            cursor.execute("DELETE FROM tasks")
            logging.info("Deleted all tasks from database")
            for chat_id, tasks in updated_chat_tasks.items():
                for task in tasks:
                    cursor.execute(
                        "INSERT INTO tasks (task, status, done, chat_id, assignee) VALUES (?, ?, ?, ?, ?)",
                        (task['task'], task['status'], int(task['done']), chat_id, json.dumps(task['assignee']))
                    )
            logging.info("Inserted updated tasks into database")

            # Сохраняем ID закрепленных сообщений
            cursor.execute("DELETE FROM pinned_messages")
            logging.info("Deleted all pinned messages from database")
            for chat_id, message_id in updated_pinned_message_id.items():
                cursor.execute(
                    "INSERT INTO pinned_messages (chat_id, message_id) VALUES (?, ?)",
                    (chat_id, message_id)
                )
            logging.info("Inserted updated pinned messages into database")

            conn.commit()
            logging.info("State saved successfully to database")

    except sqlite3.Error as e:
        logging.error(f"Error saving state to database: {e}")


# Загрузка состояния при запуске
load_state()
atexit.register(lambda: save_state(chat_tasks, pinned_message_id))


# --- Обработчики команд ---

async def start(update: Update, context: CallbackContext) -> None:
    logging.info(f"Received /start command from {update.effective_user.username} in chat {update.effective_chat.id}")
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    bot_username = context.bot.username

    # Сохраняем chat_id и user_id в базу данных
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO users (chat_id, user_id) VALUES (?, ?)",
                (chat_id, user_id)
            )  # Разбиваем строку на две
            conn.commit()
    except sqlite3.Error as e:
        logging.error(f"Error saving user data: {e}")

    await update.message.reply_text(
        f'Я бот <b>{bot_username}</b>.\nОтправь мне задачи, и я помогу их отслеживать.\nСписок команд — /help',
        parse_mode=ParseMode.HTML  # Указываем режим разбора HTML
    )


# Обработчик команды /add
async def add_task(update: Update, context: CallbackContext) -> None:
    logging.info(f"Received /add command from {update.effective_user.username} in chat {update.effective_chat.id}")
    chat_id = update.effective_chat.id

    command_parts = update.message.text.split()

    if len(command_parts) < 2:
        await update.message.reply_text(
            'Пожалуйста, укажите задачу в формате: /add <задача> или /add @<исполнитель> <задача>'
        )
        logging.warning("No task text provided")
        return

    assignee_id = None
    assignee_name = None
    tasks_text = []

    for part in command_parts[1:]:
        if part.startswith("@"):
            assignee_mention = part
            try:
                user_id = int(assignee_mention.split()[0][3:-1])
                assignee_user = await context.bot.get_chat_member(chat_id, user_id)
                assignee_id = assignee_user.user.id
                assignee_name = assignee_user.user.full_name
                logging.info(f"Assignee found: {assignee_name} (ID: {assignee_id})")
            except (BadRequest, ValueError):
                await update.message.reply_text("Пользователь не найден.")
                logging.error("Assignee not found")
                return
        else:
            tasks_text.append(part)

    tasks_text = " ".join(tasks_text)

    if tasks_text.strip():
        if chat_id not in chat_tasks:
            chat_tasks[chat_id] = []

        new_tasks = tasks_text.split('\n')
        for task in new_tasks:
            if task.strip():
                chat_tasks[chat_id].append({
                    'task': task.strip(),
                    'done': False,
                    'status': 'Не начата',
                    'chat_id': chat_id,
                    'assignee': {'id': assignee_id, 'name': assignee_name}
                })
                logging.info(f"Task added: {task.strip()}")

        await update_pinned_message(chat_id, context.bot)
        save_state(chat_tasks, pinned_message_id)
    else:
        await update.message.reply_text('Пожалуйста, укажи задачу.')
        logging.warning("Empty task text provided")


async def get_assignee_name(bot, chat_id, assignee_data):
    if assignee_data is not None and 'id' in assignee_data:
        try:
            assignee_user = await bot.get_chat_member(chat_id, assignee_data['id'])
            return assignee_user.user.full_name
        except BadRequest:
            logging.error(f"Ошибка при получении информации о пользователе: {assignee_data}")
            return "Неизвестный пользователь"
    else:
        return "Не назначен"


async def get_assignee_name_with_context(context, task):
    assignee_data = task.get('assignee', None)
    return await get_assignee_name(context.bot, context.effective_chat.id, assignee_data)


# Обработчик команды /list
# Обработчик команды /list
async def list_tasks(update: Update, context: CallbackContext) -> None:
    logging.info(f"Received /list command from {update.effective_user.username} in chat {update.effective_chat.id}")

    chat_id = update.effective_chat.id
    tasks = chat_tasks.get(chat_id, [])
    if tasks:
        message = "<b>Список задач:</b>\n\n"

        async def format_task_with_assignee(task, index):
            assignee_name = "Не назначен"
            if task.get('assignee'):
                assignee_name = await get_assignee_name(context.bot, chat_id, task['assignee'].get('id'))
            if task['status'] == 'Завершена':
                return f"{index + 1}. <s>{task['task']} ({assignee_name})</s>"
            else:
                return f"{index + 1}. {task['task']} ({assignee_name})"

        not_started_tasks = [
            await format_task_with_assignee(task, i)
            for i, task in enumerate(tasks)
            if task['status'] == 'Не начата'
        ]
        in_progress_tasks = [
            await format_task_with_assignee(task, i)
            for i, task in enumerate(tasks)
            if task['status'] == 'В процессе'
        ]
        review_tasks = [
            await format_task_with_assignee(task, i)
            for i, task in enumerate(tasks)
            if task['status'] == 'На проверке'
        ]
        rework_tasks = [
            await format_task_with_assignee(task, i)
            for i, task in enumerate(tasks)
            if task['status'] == 'Переделать'
        ]
        done_tasks = [
            await format_task_with_assignee(task, i)
            for i, task in enumerate(tasks)
            if task['status'] == 'Завершена'
        ]

        if not_started_tasks:
            message += "<b>Не начаты:</b>\n" + "\n".join(not_started_tasks) + "\n\n"
        if in_progress_tasks:
            message += "<b>В процессе:</b>\n" + "\n".join(in_progress_tasks) + "\n\n"
        if review_tasks:
            message += "<b>На проверке:</b>\n" + "\n".join(review_tasks) + "\n\n"
        if rework_tasks:
            message += "<b>Переделать:</b>\n" + "\n".join(rework_tasks) + "\n\n"
        if done_tasks:
            message += "<b>Завершены:</b>\n" + "\n".join(done_tasks) + "\n\n"

        await update.message.reply_text(message, parse_mode=ParseMode.HTML)
        logging.info(f"Sent task list to chat {chat_id}")
    else:
        await update.message.reply_text("Нет задач.", parse_mode=ParseMode.HTML)
        logging.info(f"No tasks found for chat {chat_id}")


# Обработчик команды /delete
async def delete_task(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    if context.args:
        task_number = int(context.args[0]) - 1
    else:
        await update.message.reply_text("Пожалуйста, укажите номер задачи.")
        return  # Выход из функции, если аргументы не указаны

    if chat_id in chat_tasks and 0 <= task_number < len(chat_tasks[chat_id]):
        del chat_tasks[chat_id][task_number]
        await update_pinned_message(chat_id, context.bot)
        save_state(chat_tasks, pinned_message_id)  # Сохранение состояния после удаления задачи
        await update.message.reply_text(f"Задача {task_number + 1} удалена.")
    else:
        await update.message.reply_text("Некорректный номер задачи.")


# Обработчики команд для изменения статуса задач
async def set_task_status(update: Update, context: CallbackContext, status: str) -> None:
    chat_id = update.effective_chat.id
    if context.args:
        try:
            task_number = int(context.args[0]) - 1
        except ValueError:
            await update.message.reply_text("Некорректный номер задачи.")
            return
    else:
        await update.message.reply_text("Пожалуйста, укажите номер задачи.")
        return

    if chat_id in chat_tasks and 0 <= task_number < len(chat_tasks[chat_id]):
        chat_tasks[chat_id][task_number]['status'] = status
        await update_pinned_message(chat_id, context.bot)
        save_state(chat_tasks, pinned_message_id)
        await update.message.reply_text(f"Статус задачи {task_number + 1} изменен на '{status}'.")
    else:
        await update.message.reply_text("Некорректный номер задачи.")


# Обработчик команды /edit
async def edit_task(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id

    if context.args:  # Проверка наличия аргументов
        try:
            task_number = int(context.args[0]) - 1
            new_text = ' '.join(context.args[1:])

            if 0 <= task_number < len(chat_tasks.get(chat_id, [])) and new_text.strip():
                chat_tasks[chat_id][task_number]['task'] = new_text
                await update_pinned_message(chat_id, context.bot)
                save_state(chat_tasks, pinned_message_id)
                await update.message.reply_text(f"Задача {task_number + 1} изменена.")
            else:
                if not 0 <= task_number < len(chat_tasks.get(chat_id, [])):
                    await update.message.reply_text("Некорректный номер задачи.")
                else:
                    await update.message.reply_text("Новый текст задачи не может быть пустым.")
        except ValueError:
            await update.message.reply_text("Некорректный номер задачи.")
    else:
        await update.message.reply_text("Пожалуйста, укажите номер задачи и новый текст.")


# Обработчик команды /my_id
async def chat_id_command(update: Update):
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"ID этого чата: {chat_id}")


async def done_task(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    if context.args:
        try:
            task_number = int(context.args[0]) - 1
        except ValueError:
            await update.message.reply_text("Некорректный номер задачи.")
            return
    else:
        await update.message.reply_text("Пожалуйста, укажите номер задачи.")
        return

    if chat_id in chat_tasks and 0 <= task_number < len(chat_tasks[chat_id]):
        chat_tasks[chat_id][task_number]['done'] = True
        chat_tasks[chat_id][task_number]['status'] = 'Завершена'
        await update_pinned_message(chat_id, context.bot)
        save_state(chat_tasks, pinned_message_id)
        assignee_name = get_assignee_name(
            context.bot, chat_id, chat_tasks[chat_id][task_number].get('assignee', None)
        )
        await update.message.reply_text(f"Задача {task_number + 1} выполнена ({assignee_name}).")
    else:
        await update.message.reply_text("Некорректный номер задачи.")


# Функция для обновления закрепленного сообщения
async def update_pinned_message(chat_id: int, bot) -> None:
    logging.info(f"Updating pinned message for chat {chat_id}")
    tasks = chat_tasks.get(chat_id, [])
    if tasks:
        not_started_tasks = []
        in_progress_tasks = []
        review_tasks = []
        rework_tasks = []
        done_tasks = []

        for i, task in enumerate(tasks):
            assignee_name = await get_assignee_name(bot, chat_id, task.get('assignee', {}).get('id'))

            if task['status'] == 'Не начата':
                not_started_tasks.append(f"{i + 1}. {task['task']} ({assignee_name})")
            elif task['status'] == 'В процессе':
                in_progress_tasks.append(f"{i + 1}. {task['task']} ({assignee_name})")
            elif task['status'] == 'На проверке':
                review_tasks.append(f"{i + 1}. {task['task']} ({assignee_name})")
            elif task['status'] == 'Переделать':
                rework_tasks.append(f"{i + 1}. {task['task']} ({assignee_name})")
            elif task['status'] == 'Завершена':
                done_tasks.append(f"{i + 1}. <s>{task['task']} ({assignee_name})</s>")

        message = "<b>Список задач:</b>\n\n"

        if not_started_tasks:
            message += "<b>Не начаты:</b>\n" + "\n".join(not_started_tasks) + "\n\n"
        if in_progress_tasks:
            message += "<b>В процессе:</b>\n" + "\n".join(in_progress_tasks) + "\n\n"
        if review_tasks:
            message += "<b>На проверке:</b>\n" + "\n".join(review_tasks) + "\n\n"
        if rework_tasks:
            message += "<b>Переделать:</b>\n" + "\n".join(rework_tasks) + "\n\n"
        if done_tasks:
            message += "<b>Завершены:</b>\n" + "\n".join(done_tasks) + "\n\n"

        message = message.strip()

        try:
            if chat_id in pinned_message_id:
                logging.info(f"Editing existing pinned message {pinned_message_id[chat_id]}")
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=pinned_message_id[chat_id],
                    text=message,
                    parse_mode=ParseMode.HTML
                )
            else:
                logging.info("Sending new pinned message")
                sent_message = await bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML
                )
                pinned_message_id[chat_id] = sent_message.message_id
                await bot.pin_chat_message(
                    chat_id=chat_id,
                    message_id=sent_message.message_id
                )
        except TelegramError as e:
            logging.error(f"Error updating pinned message: {e}")
            if isinstance(e, (BadRequest, Forbidden)):
                await bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ У меня недостаточно прав для закрепления сообщений. "
                         "Пожалуйста, сделайте меня администратором и дайте право закреплять сообщения. 🙏"
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text="Произошла ошибка при обновлении списка задач. Пожалуйста, попробуйте позже."
                )

            # Обработка случая, когда нет задач
        if not tasks and chat_id in pinned_message_id:  # Добавлен этот блок
            try:
                logging.info(f"Editing existing pinned message {pinned_message_id[chat_id]} (no tasks)")
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=pinned_message_id[chat_id],
                    text="Нет задач."
                )
            except (BadRequest, Forbidden) as e:
                logging.error(f"Failed to edit pinned message: {e}")


async def help_command(update: Update, _context: CallbackContext) -> None:
    """Send a message when the command /help is issued."""
    help_text = """
    <b>Я бот для управления задачами</b> 
При добавлении первой задачи я создам сообщение со списком задач.
Я закреплю это сообщение в чате и буду обновлять сам.

<b>Команды</b>

/add <i>текст задачи</i> <i>@исполнитель</i> - создать задачу (опционально указать исполнителя через @)
/delete <i>номер</i> - удалить задачу по номеру
/edit <i>номер</i> <i>новый текст</i> - изменить текст задачи

/assign <i>номер</i> <i>@исполнитель</i> - назначить исполнителя, если вы не сделали этого сразу при создании задачи.

Статусы задач:
/done <i>номер</i> - отметить задачу как выполненную
/notstarted <i>номер</i> - пометить задачу как 'Не начата'
/inprogress <i>номер</i> - пометить задачу как 'В процессе'
/review <i>номер</i> - пометить задачу как 'На проверке'
/rework <i>номер</i> - пометить задачу как 'Переделать'

Служебные команды:
/save_state - сохранить состояние бота
/my_id - узнать ID текущего чата
/list - показать список задач

/help - показать эту справку
    """
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)


async def save_state_handler(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id
    logging.info(f"User {user.first_name} ({user.id}) saved state in chat {chat_id}.")
    save_state(chat_tasks, pinned_message_id)
    await update.message.reply_text(f'@{user.username}, состояние сохранено!', reply_markup=context.bot.main_button)


# Обработчик команды /assign
async def assign_task(update: Update, context: CallbackContext) -> None:
    logging.info(f"Received /assign command from {update.effective_user.username} in chat {update.effective_chat.id}")
    chat_id = update.effective_chat.id
    try:
        args = context.args

        if len(args) != 2:
            raise ValueError("Неверное количество аргументов")

        # Пробуем определить, что является номером задачи, а что - исполнителем
        try:
            task_number = int(args[0]) - 1
            assignee_mention = args[1]
        except ValueError:
            task_number = int(args[1]) - 1
            assignee_mention = args[0]

        if not assignee_mention.startswith("@"):
            raise ValueError("Исполнитель должен быть указан через @")

        try:
            user_id = int(assignee_mention.split()[0][3:-1])
            assignee_user = await context.bot.get_chat_member(chat_id, user_id)
            assignee_id = assignee_user.user.id
            assignee_name = assignee_user.user.full_name

            logging.info(f"Assigning task {task_number + 1} to user {assignee_name} ({assignee_id})")  # Логирование

        except (BadRequest, ValueError):
            await update.message.reply_text("Пользователь не найден.")
            return

        if chat_id in chat_tasks and 0 <= task_number < len(chat_tasks[chat_id]):
            chat_tasks[chat_id][task_number]['assignee'] = {'id': assignee_id, 'name': assignee_name}
            await update_pinned_message(chat_id, context.bot)
            save_state(chat_tasks, pinned_message_id)
            await update.message.reply_text(f"Задача {task_number + 1} назначена на {assignee_name}.")
        else:
            await update.message.reply_text("Некорректный номер задачи или исполнитель.")
    except (IndexError, ValueError) as e:
        logging.error(f"Error in /assign command: {e}")
        await update.message.reply_text(f"Ошибка: {e}\nИспользуйте формат: /assign <номер задачи> @<исполнитель>")


# Добавление обработчиков команд
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("add", add_task))
app.add_handler(CommandHandler("list", list_tasks))
app.add_handler(CommandHandler("delete", delete_task))
app.add_handler(CommandHandler("done", done_task))
app.add_handler(CommandHandler("notstarted", lambda update, context: set_task_status(update, context, 'Не начата')))
app.add_handler(CommandHandler("inprogress", lambda update, context: set_task_status(update, context, 'В процессе')))
app.add_handler(CommandHandler("review", lambda update, context: set_task_status(update, context, 'На проверке')))
app.add_handler(CommandHandler("rework", lambda update, context: set_task_status(update, context, 'Переделать')))
app.add_handler(CommandHandler("edit", edit_task))
app.add_handler(CommandHandler("save_state", save_state_handler))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("my_id", lambda update, _: chat_id_command(update)))
app.add_handler(CommandHandler("assign", assign_task))


# Запуск бота
app.run_polling(allowed_updates=Update.ALL_TYPES)
