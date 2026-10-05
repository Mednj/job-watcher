import html

import httpx

from app.config import Settings


class DeliveryError(Exception):
    def __init__(self, message, retry_after=None, permanent=False):
        super().__init__(message)
        self.retry_after = retry_after
        self.permanent = permanent


def format_message(job):
    def escape(value):
        return html.escape(str(value)[:400])

    return (
        f"<b>New job · {escape(job['source'].title())}</b>\n\n"
        f"<b>{escape(job['title'])}</b>\n"
        f"{escape(job['company'])}\n"
        f"📍 {escape(job['location'])}\n"
        f"📄 {escape(job['contract'])}\n"
        f"Published: {escape(job.get('published_label') or 'Not provided')}\n\n"
        f'<a href="{html.escape(job["url"], quote=True)}">View &amp; apply</a>'
    )


class Telegram:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings, self.client = settings, client

    async def send(self, message):
        if not self.settings.telegram_configured:
            raise DeliveryError("Telegram is not configured", permanent=True)
        try:
            response = await self.client.post(
                f"https://api.telegram.org/bot{self.settings.telegram_token}/sendMessage",
                json={
                    "chat_id": self.settings.telegram_chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "link_preview_options": {"is_disabled": True},
                },
            )
        except httpx.HTTPError:
            # Never expose request URLs: the Telegram token is part of the URL.
            raise DeliveryError("Telegram request failed or timed out") from None
        try:
            body = response.json()
        except ValueError:
            raise DeliveryError("Telegram returned an invalid response") from None
        if not isinstance(body, dict):
            raise DeliveryError("Telegram returned an invalid response")
        if response.status_code == 429 or body.get("error_code") == 429:
            raise DeliveryError(
                "Telegram rate limit reached", body.get("parameters", {}).get("retry_after", 30)
            )
        if response.status_code != 200 or not body.get("ok"):
            code = body.get("error_code", response.status_code)
            # Do not copy arbitrary upstream descriptions into logs or API responses.
            raise DeliveryError(
                f"Telegram rejected delivery (HTTP {code})", permanent=code in (400, 401, 403, 404)
            )
        return body.get("result", {}).get("message_id")
