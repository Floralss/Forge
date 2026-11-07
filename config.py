import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "9172117528:AAGjJCcM6uJzIrzHer1twIt4UFPrkreUjV8")
ADMIN_TG_IDS = [
    int(x) for x in os.environ.get("ADMIN_TG_IDS", "5198310704").split(",") if x.strip().isdigit()
]
CHECKS_CHANNEL = os.environ.get("CHECKS_CHANNEL", "-1004399120906")  # channel ID
UPDATE_EXE = os.environ.get("UPDATE_EXE", "Forge-Setup.exe")
UPDATE_APK = os.environ.get("UPDATE_APK", "Forge.apk")
# serviceAccount.json рядом с bot.py
# Ссылка на PC-установщик (если файл > 50 МБ — бот шлёт ссылку, не файл)
UPDATE_PC_URL = "https://www.dropbox.com/scl/fi/mvbr04vzn9ejepn9i8zo9/Forge.Setup.1.8.15.exe?rlkey=nigp80gtbb5m0dcfqupxnm3pm&dl=1"
UPDATE_APK_URL = ""  # позже можно вставить ссылку на APK
