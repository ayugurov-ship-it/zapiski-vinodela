"""
Скрипт публикации постов в Telegram-канал "Записки винодела".

Выбирает следующий неопубликованный пост и отправляет его в канал.
Отслеживает опубликованные посты в файле published.json.
"""

import json
import os
import re
import sys
from pathlib import Path

import requests

POSTS_DIR = Path(__file__).parent.parent / "posts"
PUBLISHED_FILE = Path(__file__).parent.parent / "published.json"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def load_published():
    if PUBLISHED_FILE.exists():
        return json.loads(PUBLISHED_FILE.read_text(encoding="utf-8"))
    return []


def save_published(published):
    PUBLISHED_FILE.write_text(
        json.dumps(published, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_post(filepath):
    """Парсит пост из Markdown-файла с frontmatter."""
    text = filepath.read_text(encoding="utf-8")

    # Извлекаем frontmatter
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if match:
        body = match.group(2).strip()
    else:
        body = text.strip()

    return body


def send_to_telegram(text):
    """Отправляет сообщение в Telegram-канал."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    # Telegram ограничивает сообщения до 4096 символов
    if len(text) > 4096:
        # Разбиваем на части
        parts = []
        while text:
            if len(text) <= 4096:
                parts.append(text)
                break
            # Ищем последний перенос строки до лимита
            split_pos = text.rfind("\n", 0, 4096)
            if split_pos == -1:
                split_pos = 4096
            parts.append(text[:split_pos])
            text = text[split_pos:].lstrip()

        for part in parts:
            resp = requests.post(url, json={
                "chat_id": CHAT_ID,
                "text": part,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            })
            resp.raise_for_status()
    else:
        resp = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        })
        resp.raise_for_status()

    return True


def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID должны быть установлены")
        sys.exit(1)

    # Получаем список всех постов
    posts = sorted(POSTS_DIR.glob("*.md"))
    if not posts:
        print("Нет постов для публикации")
        sys.exit(0)

    # Загружаем список опубликованных
    published = load_published()

    # Находим следующий неопубликованный пост
    next_post = None
    for post in posts:
        if post.name not in published:
            next_post = post
            break

    if next_post is None:
        print("Все посты уже опубликованы!")
        sys.exit(0)

    print(f"Публикуем: {next_post.name}")

    # Парсим и отправляем
    body = parse_post(next_post)
    send_to_telegram(body)

    # Отмечаем как опубликованный
    published.append(next_post.name)
    save_published(published)

    print(f"Опубликовано: {next_post.name}")


if __name__ == "__main__":
    main()
