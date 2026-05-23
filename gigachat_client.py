import logging
import os
import time
import uuid
from typing import Any

import requests
import urllib3

logger = logging.getLogger(__name__)

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
API_BASE_URL = "https://gigachat.devices.sberbank.ru/api"

DEFAULT_SYSTEM_PROMPT = (
    "Ты — AI-помощник транспортной компании ООО «АЯКС».\n\n"
    "Твоя специализация:\n"
    "- грузоперевозки;\n"
    "- рефрижераторные перевозки;\n"
    "- логистика;\n"
    "- поиск транспорта;\n"
    "- работа с перевозчиками;\n"
    "- маршруты и ставки.\n\n"
    "Если вопрос связан с логистикой и перевозками — отвечай как эксперт-помощник "
    "ООО «АЯКС»: профессионально, по делу, с учётом практики грузоперевозок и логистики.\n\n"
    "Если вопрос не связан с логистикой — отвечай как обычный вежливый AI-помощник, "
    "без навязывания темы перевозок.\n\n"
    "Работа с контекстом диалога (обязательно):\n"
    "- Всегда учитывай историю переписки: предыдущие сообщения пользователя и твои ответы.\n"
    "- Если пользователь пишет «этот маршрут», «по нему», «такая же ставка», «аналогично», "
    "«обратно», «в обратную сторону» или другие отсылки к уже обсуждавшемуся — восстанавливай "
    "параметры из предыдущих реплик: маршрут, тип транспорта, тоннаж, температурный режим, "
    "направление, ставку. Не проси повторить то, что уже было названо.\n"
    "- Если ранее обсуждали, например, ставку на рефрижераторную перевозку Москва — "
    "Санкт-Петербург, а пользователь спрашивает «а по маршруту Санкт-Петербург — Москва?» — "
    "понимай, что тема та же (ставки, тип перевозки, условия), меняется направление; "
    "сохраняй остальные параметры из контекста, если пользователь их не менял.\n"
    "- Если не хватает только части данных — не задавай заново весь список параметров; "
    "уточни только недостающий параметр одним коротким вопросом.\n"
    "- Если в истории нет нужных данных и восстановить параметры нельзя — честно скажи, "
    "чего именно не хватает, и спроси только это.\n\n"
    "Отвечай на русском языке, если пользователь не попросил другой язык."
)


class GigaChatClient:
    """Клиент GigaChat API: OAuth и chat completions."""

    def __init__(
        self,
        authorization_key: str,
        scope: str = "GIGACHAT_API_PERS",
        model: str = "GigaChat",
        verify_ssl: bool = False,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self._authorization_key = authorization_key
        self._scope = scope
        self._model = model
        self._verify_ssl = verify_ssl
        self._system_prompt = system_prompt.strip()
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> requests.Response:
        return requests.request(
            method,
            url,
            headers=headers,
            data=data,
            json=json,
            timeout=60,
            verify=self._verify_ssl,
        )

    def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 30:
            return self._access_token

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": f"Basic {self._authorization_key}",
        }
        payload = {"scope": self._scope}

        logger.info("Запрос access token GigaChat")
        response = self._request("POST", OAUTH_URL, headers=headers, data=payload)
        response.raise_for_status()

        data = response.json()
        token = data.get("access_token")
        if not token:
            raise ValueError("В ответе OAuth нет access_token")

        expires_at = data.get("expires_at")
        if expires_at:
            self._token_expires_at = float(expires_at) / 1000.0
        else:
            self._token_expires_at = time.time() + 1800

        self._access_token = token
        logger.info("Access token GigaChat получен")
        return token

    def chat(
        self,
        user_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        token = self._get_access_token()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        messages: list[dict[str, str]] = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        body = {
            "model": self._model,
            "messages": messages,
            "stream": False,
        }

        url = f"{API_BASE_URL}/v1/chat/completions"
        logger.info("Запрос к GigaChat chat/completions")
        response = self._request("POST", url, headers=headers, json=body)

        if not response.ok:
            logger.error("GigaChat error status: %s", response.status_code)
            logger.error("GigaChat error body: %s", response.text)

        response.raise_for_status()

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("Пустой ответ GigaChat: нет choices")

        message = choices[0].get("message") or {}
        content = message.get("content")
        if not content:
            raise ValueError("Пустой ответ GigaChat: нет content")

        return str(content).strip()


def create_gigachat_client() -> GigaChatClient:
    authorization_key = os.getenv("GIGACHAT_AUTHORIZATION_KEY", "").strip()
    if not authorization_key:
        raise ValueError("GIGACHAT_AUTHORIZATION_KEY не задан в .env")

    scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()
    model = os.getenv("GIGACHAT_MODEL", "GigaChat").strip()
    verify_ssl = os.getenv("GIGACHAT_VERIFY_SSL", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    system_prompt = os.getenv("GIGACHAT_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT).strip()

    return GigaChatClient(
        authorization_key=authorization_key,
        scope=scope,
        model=model,
        verify_ssl=verify_ssl,
        system_prompt=system_prompt,
    )
