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
import os
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.errors import HttpError
from time import sleep

# Загрузка токена
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')

# Настройки Google Sheets
SPREADSHEET_ID = 'YOUR_SPREADSHEET_ID'
CREDS_FILE = 'credentials.json'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния диалога
CALENDAR_VIEW, CONFIRMATION = range(2)

class CalendarBot:
    def __init__(self):
        self.month_names = [
            "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
            "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
        ]
        self.day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    
    def generate_august_calendar(self, selected_dates=None):
        """Генерация календаря для августа 2025 с подсветкой нежелательных дат"""
        if selected_dates is None:
            selected_dates = []
        
        year = 2025
        month = 8
        cal = calendar.monthcalendar(year, month)
        keyboard = []
        
        # Заголовок с месяцем и годом
        keyboard.append([
            InlineKeyboardButton(f"Август 2025", callback_data="ignore")
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
                    # Подсветка нежелательных дат (1-18 августа)
                    if 1 <= day <= 18:
                        emoji = "🔴"  # Красный кружок
                        prefix = "❌"  # Добавляем крестик
                    else:
                        emoji = "⚪"   # Белый кружок
                        prefix = ""
                    
                    # Если дата выбрана - заменяем на галочку
                    if date_str in selected_dates:
                        emoji = "✅"
                        prefix = ""
                        
                    row.append(
                        InlineKeyboardButton(
                            f"{prefix}{emoji}{day}",
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
            "📅 Выберите даты в АВГУСТЕ 2025, когда вы можете присутствовать:\n"
            "🔴 Даты с 1 по 18 августа не рекомендуются к выбору",
            reply_markup=self.generate_august_calendar()
        )
        return CALENDAR_VIEW

    async def handle_calendar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        data = query.data
        user_data = context.user_data
        
        if data.startswith('date_'):
            _, year_str, month_str, day_str = data.split('_')
            day = int(day_str)
            date_str = f"{day:02d}.{month_str}.{year_str}"
            
            # Проверяем, не пытаются ли выбрать нежелательную дату
            if 1 <= day <= 18:
                await query.answer("Эта дата не рекомендуется для выбора!", show_alert=True)
                return CALENDAR_VIEW
            
            if date_str in user_data['selected_dates']:
                user_data['selected_dates'].remove(date_str)
            else:
                user_data['selected_dates'].append(date_str)
            
            await query.edit_message_reply_markup(
                reply_markup=self.generate_august_calendar(user_data['selected_dates'])
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
                reply_markup=self.generate_august_calendar(context.user_data['selected_dates'])
            )
            return CALENDAR_VIEW

    async def save_results(self, user_data):
        """Сохранение результатов в Google Таблицу"""
        try:
            # Авторизация
            creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
            client = gspread.authorize(creds)
            
            # Открытие таблицы
            try:
                sheet = client.open_by_key(SPREADSHEET_ID).sheet1
            except gspread.SpreadsheetNotFound:
                logger.error("Таблица не найдена! Проверьте SPREADSHEET_ID")
                return
            
            # Подготовка данных
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            row = [
                str(user_data['user_id']),
                user_data['username'],
                ", ".join(user_data['selected_dates']),
                timestamp
            ]
            
            # Создание заголовков при первом запуске
            if sheet.row_count == 0:
                sheet.append_row(["User ID", "Username", "Selected Dates", "Timestamp"])
            
            # Добавление данных
            sheet.append_row(row)
            logger.info(f"Данные сохранены в Google Таблицу: {row}")
            
        except HttpError as e:
            logger.error(f"Ошибка Google API: {e}")
            # Fallback: сохраняем в локальный файл
            with open("backup_results.csv", "a", encoding="utf-8") as f:
                f.write(f"{','.join(map(str, row))}\n")
                
        except gspread.exceptions.APIError as e:
            logger.error(f"Ошибка gspread: {e}")
            sleep(10)
            await self.save_results(user_data)  # Повторная попытка
            
        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}")


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
