import os
import logging
from datetime import datetime, timedelta
import calendar
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.errors import HttpError
from time import sleep

# Получаем токен из переменных окружения
TOKEN = os.environ['TOKEN']

# Настройки Google Sheets
SPREADSHEET_ID = os.environ['SPREADSHEET_ID']
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Автоматическое создание credentials.json
if 'GOOGLE_CREDS_JSON' in os.environ:
    with open('credentials.json', 'w') as f:
        f.write(os.environ['GOOGLE_CREDS_JSON'])
    CREDS_FILE = 'credentials.json'
else:
    CREDS_FILE = None
    logging.warning("GOOGLE_CREDS_JSON не задан!")

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния диалога
CALENDAR_VIEW, CONFIRMATION = range(2)


# Состояния диалога
CALENDAR_VIEW, CONFIRMATION = range(2)


class CalendarBot:
    def __init__(self):
        self.month_names = [
            "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
            "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
        ]
        self.day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    def generate_july_calendar(self, selected_dates=None):
        """Генерация календаря только для июля 2025 года"""
        if selected_dates is None:
            selected_dates = []

        year = 2025
        month = 7
        cal = calendar.monthcalendar(year, month)
        keyboard = []

        # Заголовок с месяцем и годом
        keyboard.append([
            InlineKeyboardButton(f"Июль 2025", callback_data="ignore")
        ])

        # Дни недели
        keyboard.append([
            InlineKeyboardButton(day, callback_data="ignore") for day in self.day_names
        ])

        # Даты
        for week in cal:
            row = []
            for day in week:
                if day == 0:
                    row.append(InlineKeyboardButton(" ", callback_data="ignore"))
                else:
                    date_str = f"{day:02d}.{month:02d}.{year}"
                    emoji = "✅" if date_str in selected_dates else "⚪"
                    row.append(
                        InlineKeyboardButton(
                            f"{emoji}{day}",
                            callback_data=f"date_{year}_{month}_{day}"
                        )
                    )
            keyboard.append(row)

        # Кнопки действий
        keyboard.append([
            InlineKeyboardButton("🚫 Отменить", callback_data="cancel"),
            InlineKeyboardButton("✅ Готово", callback_data="done")
        ])

        return InlineKeyboardMarkup(keyboard)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        user = update.message.from_user

        context.user_data.update({
            'user_id': user.id,
            'username': user.username or user.full_name,
            'selected_dates': [],
        })

        await update.message.reply_text(
            "📅 Выберите даты в ИЮЛЕ 2025, когда вы можете присутствовать:",
            reply_markup=self.generate_july_calendar()
        )
        return CALENDAR_VIEW

    async def handle_calendar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        data = query.data
        user_data = context.user_data

        if data.startswith('date_'):
            _, year_str, month_str, day_str = data.split('_')
            date_str = f"{int(day_str):02d}.{int(month_str):02d}.{int(year_str)}"

            if date_str in user_data['selected_dates']:
                user_data['selected_dates'].remove(date_str)
            else:
                user_data['selected_dates'].append(date_str)

            await query.edit_message_reply_markup(
                reply_markup=self.generate_july_calendar(user_data['selected_dates'])
            )
            return CALENDAR_VIEW

        elif data == 'done':
            if not user_data['selected_dates']:
                await query.answer("Выберите хотя бы одну дату!", show_alert=True)
                return CALENDAR_VIEW

            dates_list = "\n".join([f"• {d}" for d in sorted(user_data['selected_dates'])])
            await query.edit_message_text(
                f"📋 Ваш выбор:\n{dates_list}\n\nПодтвердить?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Да", callback_data="confirm")],
                    [InlineKeyboardButton("✏️ Изменить", callback_data="edit")]
                ])
            )
            return CONFIRMATION

        elif data == 'cancel':
            await query.edit_message_text("❌ Опрос отменен")
            return ConversationHandler.END

        return CALENDAR_VIEW

    async def handle_confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()

        if query.data == 'confirm':
            await self.save_results(context.user_data)
            await query.edit_message_text("✅ Ваши даты сохранены! Спасибо!")
            return ConversationHandler.END

        elif query.data == 'edit':
            await query.edit_message_text(
                "📅 Выберите даты:",
                reply_markup=self.generate_july_calendar(context.user_data['selected_dates'])
            )
            return CALENDAR_VIEW

    async def save_results(self, user_data):
        """Сохранение результатов в Google Таблицу с правильным форматом столбцов"""
        try:
            # Авторизация
            creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
            client = gspread.authorize(creds)

            # Открытие таблицы
            try:
                spreadsheet = client.open_by_key(SPREADSHEET_ID)
                sheet = spreadsheet.sheet1
            except gspread.SpreadsheetNotFound:
                logger.error("Таблица не найдена! Проверьте SPREADSHEET_ID")
                return

            # Получаем все даты июля 2025
            july_dates = [f"{day:02d}.07.2025" for day in range(1, 32)]

            # Подготовка данных
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            user_id = str(user_data['user_id'])
            username = user_data['username']

            # Получаем текущие заголовки
            if sheet.row_count > 0:
                headers = sheet.row_values(1)
            else:
                headers = []

            # Создаем правильные заголовки, если их нет
            if not headers:
                headers = ["Timestamp", "User ID", "Username"] + july_dates
                sheet.append_row(headers)
                sleep(2)  # Пауза для API
                logger.info("Созданы заголовки столбцов")

            # Формируем строку для записи
            row_data = [timestamp, user_id, username]

            # Добавляем данные для каждой даты (1 если выбрана, иначе 0)
            for date in july_dates:
                row_data.append("1" if date in user_data['selected_dates'] else "0")

            # Находим строку пользователя (если уже отвечал)
            user_row_index = None
            existing_data = sheet.get_all_records()

            for i, record in enumerate(existing_data, start=2):  # Строки начинаются с 2
                if str(record.get("User ID", "")) == user_id:
                    user_row_index = i
                    break

            # Обновляем или добавляем строку
            if user_row_index:
                # Обновляем существующую запись
                for col_index, value in enumerate(row_data, start=1):
                    sheet.update_cell(user_row_index, col_index, value)
            else:
                # Добавляем новую строку
                sheet.append_row(row_data)

            logger.info(f"Данные сохранены в Google Таблицу для {username}")

            # Применяем форматирование
            self.format_spreadsheet(sheet, headers, july_dates)

        except HttpError as e:
            logger.error(f"Ошибка Google API: {e}")
            # Резервное сохранение в CSV
            with open("backup_results.csv", "a", encoding="utf-8") as f:
                f.write(f"{timestamp},{user_id},{username},")
                f.write(",".join(["1" if d in user_data['selected_dates'] else "0" for d in july_dates]))
                f.write("\n")
            logger.info("Данные сохранены в резервный файл backup_results.csv")

        except gspread.exceptions.APIError as e:
            logger.error(f"Ошибка gspread: {e}")
            if "RESOURCE_EXHAUSTED" in str(e):
                logger.warning("Превышены квоты API. Повтор через 10 сек...")
                sleep(10)
                await self.save_results(user_data)  # Повторная попытка

        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}")
            logger.exception(e)

    def format_spreadsheet(self, sheet, headers, july_dates):
        """Применяем форматирование к таблице"""
        try:
            # Форматирование заголовков
            sheet.format("A1:Z1", {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
                "horizontalAlignment": "CENTER"
            })

            # Авто-ширина столбцов
            body = {
                "requests": [{
                    "autoResizeDimensions": {
                        "dimensions": {
                            "sheetId": sheet.id,
                            "dimension": "COLUMNS",
                            "startIndex": 0,
                            "endIndex": len(headers)
                        }
                    }
                }]
            }
            sheet.spreadsheet.batch_update(body)

            # Условное форматирование для выбранных дат
            for i, date in enumerate(july_dates, start=4):  # Начиная с колонки D
                col_letter = chr(64 + i)  # A=65, B=66, D=68 и т.д.
                sheet.conditional_format(
                    f"{col_letter}2:{col_letter}{sheet.row_count}",
                    {
                        "type": "TEXT_EQ",
                        "values": ["1"],
                        "format": {
                            "backgroundColor": {"red": 0.85, "green": 0.92, "blue": 0.83}
                        }
                    }
                )

            logger.info("Форматирование таблицы применено")

        except Exception as e:
            logger.warning(f"Ошибка при форматировании таблицы: {e}")


def main() -> None:
    bot = CalendarBot()

    # Создаем Application
    application = Application.builder().token(TOKEN).build()

    # Настраиваем обработчики
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', bot.start)],
        states={
            CALENDAR_VIEW: [CallbackQueryHandler(bot.handle_calendar)],
            CONFIRMATION: [CallbackQueryHandler(bot.handle_confirmation)]
        },
        fallbacks=[]
    )

    application.add_handler(conv_handler)

    # Запускаем бота
    application.run_polling()
    logger.info("Бот запущен и ожидает команд...")


if __name__ == '__main__':
    main()


