"""
Парсер RSS-лент о виноделии и публикация свежих материалов
в Telegram-канал "Записки винодела".

Берёт самые свежие посты из настроенных лент в feeds.json,
форматирует и отправляет в канал. Уже опубликованное не дублирует.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

ROOT = Path(__file__).parent.parent
FEEDS_FILE = ROOT / "feeds.json"
PUBLISHED_FILE = ROOT / "published_rss.json"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def strip_html(text):
    """Убирает HTML-теги, оставляя чистый текст."""
    if not text:
        return ""
    # Заменяем <br>, <p> на переносы
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    # Убираем все теги
    text = re.sub(r"<[^>]+>", "", text)
    # Декодируем HTML-сущности
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    text = text.replace("&#39;", "'").replace("&hellip;", "...")
    # Сжимаем пробелы и переносы
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def escape_markdown(text):
    """Экранирует Markdown-спецсимволы для Telegram."""
    if not text:
        return ""
    chars = ["_", "*", "[", "]", "`"]
    for c in chars:
        text = text.replace(c, "\\" + c)
    return text


def parse_feed(url):
    """Парсит RSS/Atom-ленту, возвращает список items."""
    try:
        resp = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (ZapiskiVinodelaBot)"}
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"  ❌ Ошибка загрузки: {e}")
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        print(f"  ❌ Ошибка парсинга XML: {e}")
        return []

    items = []

    # RSS 2.0
    for item in root.iter("item"):
        items.append(extract_rss_item(item))

    # Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
        items.append(extract_atom_item(entry, ns))

    return [i for i in items if i.get("link")]


def extract_rss_item(item):
    def text(tag):
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    pub_date = text("pubDate")
    try:
        dt = parsedate_to_datetime(pub_date) if pub_date else None
    except Exception:
        dt = None

    return {
        "title": text("title"),
        "link": text("link"),
        "description": text("description"),
        "pub_date": dt,
    }


def extract_atom_item(entry, ns):
    def find_text(tag):
        el = entry.find(f"{{http://www.w3.org/2005/Atom}}{tag}")
        return el.text.strip() if el is not None and el.text else ""

    link_el = entry.find("{http://www.w3.org/2005/Atom}link")
    link = link_el.get("href") if link_el is not None else ""

    pub_date = find_text("published") or find_text("updated")
    try:
        dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00")) if pub_date else None
    except Exception:
        dt = None

    return {
        "title": find_text("title"),
        "link": link,
        "description": find_text("summary") or find_text("content"),
        "pub_date": dt,
    }


def format_post(feed_name, item):
    """Форматирует RSS-item в пост для Telegram."""
    title = strip_html(item["title"])
    description = strip_html(item.get("description", ""))
    link = item["link"]

    # Обрезаем описание до 600 символов
    if len(description) > 600:
        description = description[:600].rsplit(" ", 1)[0] + "..."

    text = f"📰 *{escape_markdown(title)}*\n\n"

    if description:
        text += f"{escape_markdown(description)}\n\n"

    text += f"🔗 [Читать полностью]({link})\n"
    text += f"_Источник: {escape_markdown(feed_name)}_\n\n"
    text += "#дайджест #виноделие"

    return text


def send_to_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text[:4096],
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    })
    if not resp.ok:
        print(f"❌ Telegram API ответил: {resp.status_code} {resp.text}")
    resp.raise_for_status()


def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID должны быть установлены")
        sys.exit(1)

    config = load_json(FEEDS_FILE, {"feeds": [], "max_items_per_run": 1})
    published = load_json(PUBLISHED_FILE, [])
    published_set = set(published)

    max_items = config.get("max_items_per_run", 1)
    min_age = timedelta(hours=config.get("min_age_hours", 0))
    max_age = timedelta(days=config.get("max_age_days", 30))
    now = datetime.now(timezone.utc)

    # Собираем все свежие items со всех лент
    candidates = []
    for feed in config.get("feeds", []):
        if not feed.get("enabled", True):
            continue
        print(f"📡 Загружаю: {feed['name']}")
        items = parse_feed(feed["url"])
        print(f"   получено {len(items)} элементов")

        for item in items:
            if item["link"] in published_set:
                continue
            # Фильтр по дате
            if item.get("pub_date"):
                age = now - item["pub_date"]
                if age < min_age or age > max_age:
                    continue
            candidates.append((feed["name"], item))

    if not candidates:
        print("Нет новых материалов для публикации")
        return

    # Сортируем: самое свежее — первым
    candidates.sort(
        key=lambda x: x[1].get("pub_date") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True
    )

    # Публикуем максимум max_items
    to_publish = candidates[:max_items]
    for feed_name, item in to_publish:
        print(f"📤 Публикую: {item['title'][:60]}...")
        text = format_post(feed_name, item)
        send_to_telegram(text)
        published.append(item["link"])

    # Сохраняем (храним последние 500 ссылок)
    save_json(PUBLISHED_FILE, published[-500:])
    print(f"✅ Опубликовано: {len(to_publish)}")


if __name__ == "__main__":
    main()
