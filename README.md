# Forge Bot (Python)

## Установка (Windows)
1. Установи Python 3.11+ с https://www.python.org (галочка **Add to PATH**)
2. Firebase → Service accounts → ключ → сохрани как `serviceAccount.json` **в эту папку**
3. В `files/` положи `Forge-Setup.exe` и `Forge.apk`
4. Открой **cmd** (не обязательно PowerShell):

```bat
cd %USERPROFILE%\Downloads\tg-bot-python
python -m pip install -r requirements.txt
python bot.py
```

Если `python` не находится: `py -m pip install -r requirements.txt` и `py bot.py`

## Команды админа
- `/check plus 1m`
- `/check nft COLLECTION_ID`
- `/admin` — тикеты

## Меню
Plus · NFT · Скачать обновление · Чеки · Донат · Поддержка
