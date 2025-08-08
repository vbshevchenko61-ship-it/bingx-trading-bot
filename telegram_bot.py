import time
from datetime import datetime
from typing import Set, List, Dict
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException, NoSuchElementException
from chromedriver_py import binary_path
import telebot
import logging
import threading
import sys
import traceback

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot_debug.log"),
        logging.StreamHandler()
    ]
)

# Конфигурация
TOKEN = "7881481886:AAF3YEZgJMZU6oRFhPSCrtpvVwKuBaTKaGg"
BINGX_URL = "https://bingx.com/ru-ru/CopyTrading/1399302708580769794?accountEnum=BINGX_SWAP_FUTURES&apiIdentity=1409770858837684224&from=4&list_id=all&rankStatisticDays=30"
CHECK_INTERVAL = 60
ADMIN_CHAT_ID = 5115771083
MAX_RETRIES = 3
HISTORY_TAB_SELECTOR = "#__nuxt > div > div > div.trader-home > div.trading-data-container > div.tab-bar > ul > li:nth-child(3) > div.mtb-base-p-item"

# Инициализация бота Telegram
bot = telebot.TeleBot(TOKEN, threaded=False)

# Глобальные переменные
subscribers: Set[int] = set()
processed_trades = []  # Список сохраненных сделок для показа
processed_hashes = set()  # Множество хешей для проверки дубликатов
shutdown_flag = False
driver_instance = None
driver_lock = threading.Lock()

# Обработчики команд
@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        chat_id = message.chat.id
        if chat_id not in subscribers:
            subscribers.add(chat_id)
            bot.send_message(
                chat_id,
                "✅ Вы подписаны на уведомления! Бот будет присылать новые сделки, ожидайте.\n\n"
                "Доступные команды:\n"
                "/trades - Отобразить 10 последних сделок"
            )
            logging.info(f"Чат {chat_id} подписан на уведомления")
        else:
            bot.send_message(
                chat_id,
                "ℹ️ Вы уже подписаны на уведомления.\n\n"
                "Доступные команды:\n"
                "/trades - Отобразить 10 последних сделок"
            )
    except Exception as e:
        logging.error(f"Ошибка при обработке команды start: {str(e)}")

@bot.message_handler(commands=['trades'])
def show_recent_trades(message):
    """Показать последние 10 сделок"""
    try:
        chat_id = message.chat.id
        if not processed_trades:
            bot.send_message(chat_id, "📊 Сделки еще не найдены. Ожидайте...")
            return
        
        # Берем последние 10 сделок
        recent_trades = processed_trades[-10:] if len(processed_trades) >= 10 else processed_trades
        
        if not recent_trades:
            bot.send_message(chat_id, "📊 Нет сделок для отображения")
            return
        
        message_text = "📊 Последние сделки:\n\n"
        for i, trade_data in enumerate(recent_trades, 1):
            message_text += f"{i}. {trade_data['date']} | {trade_data['pair']} | Тип: {trade_data['close_type']} | Объем: {trade_data['volume']} | Цена: {trade_data['price']} | Прибыль: {trade_data['profit']}\n"
        
        bot.send_message(chat_id, message_text)
        logging.info(f"Отправлены последние сделки в чат {chat_id}")
    except Exception as e:
        logging.error(f"Ошибка при показе сделок: {str(e)}")
        bot.send_message(message.chat.id, "❌ Ошибка при получении сделок")

# Обработчик кнопки убран

def bot_polling():
    """Запуск бота с обработкой исключений"""
    while not shutdown_flag:
        try:
            bot.polling(none_stop=True, interval=1, timeout=20)
        except Exception as e:
            logging.error(f"Ошибка в polling: {str(e)}")
            time.sleep(5)
    logging.warning("Поток бота Telegram остановлен")

def init_driver():
    """Настройка и запуск ChromeDriver с улучшенными параметрами"""
    global driver_instance
    
    if driver_instance:
        return driver_instance
        
    chrome_options = Options()
    
    # Режим без графического интерфейса
    chrome_options.add_argument("--headless=new")
    
    # Основные параметры  
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # Установим правильный путь к Chromium
    chrome_options.binary_location = "/nix/store/qa9cnw4v5xkxyip6mb9kxqfq1z4x2dx1-chromium-138.0.7204.100/bin/chromium-browser"
    
    # Параметры для обхода защиты
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")
    chrome_options.add_argument('--ignore-certificate-errors')
    chrome_options.add_argument('--ignore-ssl-errors')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('--allow-running-insecure-content')
    chrome_options.add_argument('--disable-web-security')
    chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    # Дополнительные параметры для стабильности
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--proxy-server='direct://'")
    chrome_options.add_argument("--proxy-bypass-list=*")
    chrome_options.add_argument("--disable-application-cache")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-logging")
    chrome_options.add_argument("--log-level=3")
    
    try:
        service = Service(
            executable_path="/nix/store/8zj50jw4w0hby47167kqqsaqw4mm5bkd-chromedriver-unwrapped-138.0.7204.100/bin/chromedriver",
            service_args=['--verbose', '--log-path=chromedriver.log']
        )
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Настройка таймаутов
        driver.set_page_load_timeout(90)
        driver.set_script_timeout(30)
        
        driver_instance = driver
        logging.info("Драйвер успешно инициализирован")
        return driver
    except Exception as e:
        logging.error(f"Ошибка при инициализации драйвера: {str(e)}")
        return None

def close_driver():
    """Безопасное закрытие драйвера"""
    global driver_instance
    if driver_instance:
        try:
            driver_instance.quit()
            logging.info("Драйвер успешно закрыт")
        except Exception as e:
            logging.error(f"Ошибка при закрытии драйвера: {str(e)}")
        finally:
            driver_instance = None

def get_trades_data() -> List[Dict[str, str]]:
    """Получение данных о сделках с использованием WebDriver"""
    for attempt in range(MAX_RETRIES):
        if shutdown_flag:
            return []
            
        with driver_lock:
            driver = init_driver()
            if not driver:
                logging.error("Драйвер не инициализирован")
                return []
                
            try:
                logging.info(f"Попытка {attempt+1}/{MAX_RETRIES}: Открытие страницы")
                driver.get(BINGX_URL)
                
                # Ожидаем загрузки основного контента с увеличенным таймаутом
                WebDriverWait(driver, 45).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.tab-bar"))
                )
                logging.info("Основной контент загружен")
                
                # Используем селектор для вкладки "История сделок"
                history_tab = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, HISTORY_TAB_SELECTOR))
                )
                history_tab.click()
                logging.info("Переключение на вкладку 'История сделок'")
                
                # Ожидаем загрузки таблицы
                WebDriverWait(driver, 30).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, "div.table-wrap"))
                )
                logging.info("Таблица истории сделок загружена")
                
                # Даем дополнительное время для загрузки данных
                time.sleep(3)
                
                return parse_trade_history(driver)
            except (TimeoutException, WebDriverException, NoSuchElementException) as e:
                logging.error(f"Ошибка при загрузке страницы (попытка {attempt+1}): {str(e)}")
                if attempt < MAX_RETRIES - 1:
                    # Пересоздаем драйвер перед следующей попыткой
                    close_driver()
                    retry_delay = 15 * (attempt + 1)
                    logging.info(f"Повторная попытка через {retry_delay} секунд...")
                    time.sleep(retry_delay)
                    continue
                else:
                    return []
            except Exception as e:
                logging.error(f"Неожиданная ошибка в get_trades_data: {str(e)}")
                return []
            finally:
                # Не закрываем драйвер, чтобы сохранить сессию
                pass
    
    return []

def parse_trade_history(driver) -> List[Dict[str, str]]:
    """Парсинг данных из таблицы истории сделок"""
    trades = []
    try:
        # Используем селектор для таблицы
        table = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.table-wrap table"))
        )
        rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")
        
        for row in rows:
            try:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) < 7:
                    continue
                
                # Очистка текста
                clean_text = lambda text: " ".join(text.strip().split())
                
                # Извлекаем данные из всех столбцов
                date_text = clean_text(cells[0].text)
                # Убираем AM/PM из времени, оставляем только дату и время
                date_text = date_text.replace(" AM", "").replace(" PM", "")
                
                trade_data = {
                    "date": date_text,                         # Дата
                    "pair": clean_text(cells[1].text),         # Пара
                    "close_type": clean_text(cells[2].text),   # Тип закрытия
                    "volume": clean_text(cells[3].text),       # Объем
                    "price": clean_text(cells[4].text),        # Цена
                    "profit": clean_text(cells[5].text),       # Прибыль
                }
                
                trade_hash = f"{trade_data['pair']}_{trade_data['price']}_{trade_data['date']}"
                
                if trade_hash not in processed_hashes:
                    trades.append(trade_data)
                    processed_trades.append(trade_data)  # Сохраняем полные данные сделки
                    processed_hashes.add(trade_hash)
                    # Ограничиваем размер списка до 100 последних сделок
                    if len(processed_trades) > 100:
                        removed_trade = processed_trades.pop(0)
                        removed_hash = f"{removed_trade['pair']}_{removed_trade['price']}_{removed_trade['date']}"
                        processed_hashes.discard(removed_hash)
            except Exception as e:
                logging.warning(f"Ошибка парсинга строки: {str(e)}")
                continue
                
        return trades
    except Exception as e:
        logging.error(f"Ошибка парсинга таблицы: {str(e)}")
        return []

def format_trade_message(trade: Dict[str, str]) -> str:
    """Форматирование сообщения для Telegram в одну строку"""
    return (
        f"{trade['date']} | "
        f"{trade['pair']} | "
        f"Тип: {trade['close_type']} | "
        f"Объем: {trade['volume']} | "
        f"Цена: {trade['price']} | "
        f"Прибыль: {trade['profit']}"
    )

def send_trade_message(chat_id: int, trade: Dict[str, str]):
    """Безопасная отправка сообщения о сделке"""
    try:
        message = format_trade_message(trade)
        bot.send_message(chat_id, message)
        logging.info(f"Сообщение отправлено в чат {chat_id}")
        return True
    except Exception as e:
        if "bot was blocked" in str(e):
            logging.error(f"Чат {chat_id} заблокировал бота. Удаляю из подписчиков.")
            subscribers.discard(chat_id)
        elif "Too Many Requests" in str(e):
            logging.warning(f"Слишком много запросов для чат {chat_id}")
            time.sleep(5)
        return False

def check_new_trades():
    """Основная функция проверки новых сделок"""
    if shutdown_flag:
        return
        
    start_time = time.time()
    try:
        new_trades = get_trades_data()
        
        if new_trades:
            logging.info(f"Найдено новых сделок: {len(new_trades)}")
            if subscribers:
                for trade in new_trades:
                    if shutdown_flag:
                        return
                    for chat_id in list(subscribers):
                        send_trade_message(chat_id, trade)
                        time.sleep(1)
            else:
                logging.info("Нет активных подписчиков")
        else:
            logging.info("Новых сделок не обнаружено")
    except Exception as e:
        logging.error(f"Ошибка при проверке сделок: {str(e)}")
    finally:
        duration = time.time() - start_time
        logging.info(f"Время выполнения: {duration:.2f} сек")

def main():
    """Основной цикл работы бота"""
    global shutdown_flag
    
    logging.info("=" * 60)
    logging.info(f"БОТ ЗАПУЩЕН | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info("=" * 60)
    logging.info(f"Интервал проверки: {CHECK_INTERVAL} секунд")
    logging.info(f"Селектор вкладки: {HISTORY_TAB_SELECTOR}")
    
    # Уведомление администратору о запуске
    try:
        bot.send_message(
            ADMIN_CHAT_ID,
            "Бот запущен и готов к работе, подпишитесь на уведомления командой /start"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление о запуске: {str(e)}")

    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=bot_polling, daemon=True)
    bot_thread.start()
    
    # Основной цикл проверки сделок
    while not shutdown_flag:
        try:
            check_new_trades()
            
            # Пауза с проверкой флага остановки
            for _ in range(CHECK_INTERVAL):
                if shutdown_flag:
                    break
                time.sleep(1)
                
        except KeyboardInterrupt:
            logging.info("Работа остановлена пользователем")
            shutdown_flag = True
            break
        except Exception as e:
            logging.error(f"Критическая ошибка в основном цикле: {str(e)}")
            time.sleep(30)
    
    # Завершение работы
    close_driver()
    logging.warning("=" * 60)
    logging.warning("ЗАВЕРШЕНИЕ РАБОТЫ БОТА")
    logging.warning("=" * 60)
    
    # Отправка финального уведомления
    try:
        bot.send_message(
            ADMIN_CHAT_ID,
            "🛑 Работа бота прекращена!\n"
            "Для повторного запуска необходимо перезапустить скрипт вручную."
        )
    except Exception as e:
        logging.error(f"Не удалось отправить финальное уведомление: {str(e)}")
    
    # Даем время на отправку сообщения
    time.sleep(3)
    sys.exit(0)

if __name__ == "__main__":
    main()