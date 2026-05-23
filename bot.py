import logging
import os
import sys

import telebot
from dotenv import load_dotenv

from gigachat_client import create_gigachat_client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LENGTH = 4096
MAX_HISTORY_MESSAGES = 12


class DialogMemory:
    """История диалога по user_id (последние N сообщений user/assistant)."""

    def __init__(self, max_messages: int = MAX_HISTORY_MESSAGES) -> None:
        self._max_messages = max_messages
        self._histories: dict[int, list[dict[str, str]]] = {}

    def get(self, user_id: int) -> list[dict[str, str]]:
        return list(self._histories.get(user_id, []))

    def append_exchange(
        self, user_id: int, user_text: str, assistant_text: str
    ) -> None:
        history = self._histories.setdefault(user_id, [])
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": assistant_text})
        if len(history) > self._max_messages:
            self._histories[user_id] = history[-self._max_messages :]

    def reset(self, user_id: int) -> None:
        if user_id in self._histories:
            del self._histories[user_id]
            logger.info("История диалога очищена для user_id=%s", user_id)


def split_message(text: str, max_len: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> list[str]:
    if len(text) <= max_len:
        return [text]

    parts: list[str] = []
    while text:
        parts.append(text[:max_len])
        text = text[max_len:]
    return parts


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN не задан в .env")
        sys.exit(1)

    gigachat = create_gigachat_client()
    bot = telebot.TeleBot(token)
    memory = DialogMemory()

    @bot.message_handler(commands=["reset"])
    def handle_reset(message: telebot.types.Message) -> None:
        user = message.from_user
        if not user:
            bot.reply_to(message, "Не удалось определить пользователя.")
            return

        memory.reset(user.id)
        bot.reply_to(message, "История диалога очищена. Можете начать заново.")

    @bot.message_handler(content_types=["text"])
    def handle_text(message: telebot.types.Message) -> None:
        user = message.from_user
        username = user.username if user else "unknown"
        user_id = user.id if user else None
        text = (message.text or "").strip()

        if not text:
            bot.reply_to(message, "Отправьте текстовое сообщение.")
            return

        if user_id is None:
            bot.reply_to(message, "Не удалось определить пользователя.")
            return

        logger.info("Сообщение от @%s (%s): %s", username, user_id, text[:200])

        history = memory.get(user_id)

        try:
            bot.send_chat_action(message.chat.id, "typing")
            answer = gigachat.chat(text, history=history)
            logger.info("Ответ GigaChat для %s: %s", user_id, answer[:200])

            memory.append_exchange(user_id, text, answer)

            for part in split_message(answer):
                bot.reply_to(message, part)
        except Exception:
            logger.exception("Ошибка при обработке сообщения от %s", user_id)
            bot.reply_to(
                message,
                "Не удалось получить ответ от GigaChat. Попробуйте позже.",
            )

    logger.info("Бот запущен, ожидание сообщений...")
    bot.infinity_polling()


if __name__ == "__main__":
    main()
