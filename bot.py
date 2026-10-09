import os
import time
import logging
import schedule
import requests
from bs4 import BeautifulSoup
from datetime import datetime, time as dt_time
from telegram import Bot
from telegram.error import TelegramError, NetworkError, TimedOut
from dotenv import load_dotenv

# ================== CONFIG ==================
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or "YOUR_BOT_TOKEN_HERE"
CHAT_ID = os.getenv("CHAT_ID") or "YOUR_CHAT_ID_HERE"

MIN_OI_CHANGE = 7.0          # Minimum OI % change
MAX_GAP = 1.5                # Max gap % allowed
SCAN_INTERVAL = 3            # minutes

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot_errors.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)

# ================== SAFE REQUEST ==================
def safe_request(url, headers=None, timeout=12, retries=3):
    if headers is None:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response
        except Exception as e:
            logger.warning(f"Request failed ({attempt+1}/{retries}): {e}")
            time.sleep(2 ** attempt)
    return None

# ================== NEWS ==================
def get_moneycontrol_news(symbol):
    try:
        url = f"https://www.moneycontrol.com/news/tags/{symbol.lower()}.html"
        res = safe_request(url)
        if not res:
            return "Moneycontrol news not available"
        soup = BeautifulSoup(res.text, "lxml")
        headlines = soup.select("h2 a, h3 a")[:3]
        news = [h.get_text(strip=True) for h in headlines if h.get_text(strip=True)]
        return " | ".join(news) if news else "No recent news"
    except Exception as e:
        logger.error(f"Moneycontrol error: {e}")
        return "News fetch failed"

def get_economic_times_news(symbol):
    try:
        url = f"https://economictimes.indiatimes.com/topic/{symbol}"
        res = safe_request(url)
        if not res:
            return "ET news not available"
        soup = BeautifulSoup(res.text, "lxml")
        headlines = soup.select("h2 a, h3 a, .story-title a")[:2]
        news = [h.get_text(strip=True) for h in headlines if h.get_text(strip=True)]
        return " | ".join(news) if news else "No recent news"
    except Exception as e:
        logger.error(f"ET error: {e}")
        return "News fetch failed"

# ================== DATA FETCH (NiftyTrader) ==================
def get_fno_data():
    url = "https://www.niftytrader.in/oi-spurts"
    res = safe_request(url)
    if not res:
        logger.error("Failed to fetch NiftyTrader data")
        return []

    soup = BeautifulSoup(res.text, "lxml")
    rows = soup.select("table tbody tr")
    data = []

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 10:
            continue
        try:
            symbol = cols[0].get_text(strip=True)
            ltp = float(cols[2].get_text(strip=True).replace(",", ""))
            price_chg = float(cols[3].get_text(strip=True).replace("%", "").split()[0])
            oi_chg = float(cols[6].get_text(strip=True).replace("%", "").replace(",", ""))
            volume = cols[7].get_text(strip=True)
            signal = cols[8].get_text(strip=True)
            spurt = float(cols[9].get_text(strip=True))

            data.append({
                "symbol": symbol,
                "ltp": ltp,
                "price_chg": price_chg,
                "oi_chg": oi_chg,
                "volume": volume,
                "signal": signal,
                "spurt": spurt
            })
        except Exception as e:
            continue
    return data

# ================== FILTER ==================
def is_high_probability(stock):
    if stock["oi_chg"] < MIN_OI_CHANGE:
        return False
    if abs(stock["price_chg"]) > 8:   # too extended
        return False
    if stock["signal"] not in ["Long Buildup", "Short Buildup"]:
        return False
    return True

# ================== ALERT ==================
def send_alert(stock):
    bias = "🟢 BUY CALL" if stock["signal"] == "Long Buildup" else "🔴 BUY PUT"
    
    mc_news = get_moneycontrol_news(stock["symbol"])
    et_news = get_economic_times_news(stock["symbol"])

    message = f"""
🔥 *100% Match Alert*

*{stock['symbol']}*
Price: ₹{stock['ltp']} ({stock['price_chg']:+.2f}%)
OI Change: *{stock['oi_chg']:+.2f}%*
Signal: {stock['signal']}
Volume: {stock['volume']}
Spurt Score: {stock['spurt']}

Bias → {bias}

📰 Moneycontrol: {mc_news}
📰 Economic Times: {et_news}

Time: {datetime.now().strftime('%H:%M:%S')}
"""
    try:
        bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="Markdown")
        logger.info(f"Alert sent: {stock['symbol']}")
    except (TelegramError, NetworkError, TimedOut) as e:
        logger.error(f"Telegram send failed: {e}")

# ================== MAIN SCAN ==================
def scan_and_alert():
    now = datetime.now().time()
    # Market hours only (9:20 to 15:25)
    if not (dt_time(9, 20) <= now <= dt_time(15, 25)):
        logger.info("Outside market hours")
        return

    logger.info("Scanning started...")
    stocks = get_fno_data()
    if not stocks:
        logger.warning("No data received")
        return

    high_prob = [s for s in stocks if is_high_probability(s)]
    high_prob = sorted(high_prob, key=lambda x: x["oi_chg"], reverse=True)[:5]

    if not high_prob:
        logger.info("No high probability stock found")
        return

    for stock in high_prob:
        send_alert(stock)
        time.sleep(1)

# ================== SCHEDULER ==================
def start_bot():
    logger.info("Bot started successfully")
    bot.send_message(chat_id=CHAT_ID, text="✅ F&O Alert Bot is LIVE now!")

    schedule.every(SCAN_INTERVAL).minutes.do(scan_and_alert)

    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    start_bot()
