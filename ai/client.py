"""
ai/client.py — ЕДИНСТВЕННОЕ место, где вызывается модель во всём проекте
(контракт 5.3, раздел VI ТЗ). Все прочие модули просят у OpenRouter только
через ask() — прямых httpx-запросов на openrouter.ai быть больше нигде не
должно, иначе учёт расходов (ai_usage) и предохранители из части IX
перестанут работать.

Внутри ask() и нигде больше (VI.1 ТЗ): обрезка входа, проверка дневного
лимита в долларах, запись в ai_usage, повторы при сбоях, откат на запасную
модель. Дневной лимит — жёсткий стоп, а не предупреждение (IX.1).

ОТСТУПЛЕНИЕ ОТ КОНТРАКТА 5.3: добавлен необязательный параметр inn=None
(по умолчанию, не ломает вызовы позиционными аргументами как в контракте) —
без него нечем заполнить ai_usage.inn, а эта колонка в схеме 4.1 есть явно
и нужна, чтобы понимать, на кого именно потрачены деньги.

СТАТУС ПРОВЕРКИ: код написан по документированному, стабильному, OpenAI-
совместимому API OpenRouter (раздел VI.1 ТЗ) — это не угаданная вёрстка
сайта, а описанный контракт, и здесь я уверен в форме запроса/ответа
значительно больше, чем в разделах про fsa.gov.ru/2ГИС/ФССП. Не проверено
вживую только потому, что у меня нет вашего OPENROUTER_API_KEY, а тратить
чужие деньги на реальные вызовы без вас рядом я не должен. Впишите ключ в
.env и прогоните `python -m ai.classify --eval`, когда будете на месте.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import httpx
import json as _json
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_chain, wait_fixed

import config
import db

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Состояние процесса (сбрасывается при каждом новом запуске hunter.py —
# ровно то, что нужно для "лимита вызовов ЗА ПРОГОН").
_state = {"consecutive_primary_failures": 0, "calls_this_run": 0}


class AiBudgetExceeded(RuntimeError):
    """Дневной лимит $ или лимит вызовов за прогон исчерпан — жёсткий стоп (IX.1 ТЗ)."""


def _today_spend(conn) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM ai_usage WHERE date(ts) = date('now')"
    ).fetchone()
    return row["total"] or 0.0


def _extract_json(text: str) -> Optional[dict]:
    """Модель иногда заворачивает JSON в ```json или добавляет вступление
    (VI.1 ТЗ) — вырезаем блок между первой { и последней } и парсим.
    Не распарсилось — None, вызывающий код превращает это в verdict='unclear',
    не падает."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return _json.loads(text[start : end + 1])
    except _json.JSONDecodeError:
        return None


def _price_from_config(model: str, tokens_in: int, tokens_out: int) -> float:
    """Резервный расчёт цены, если OpenRouter не вернул usage.cost (VI.1
    ТЗ). config.PRICE_TABLE — заглушка до заполнения реальными ценами."""
    rate = config.PRICE_TABLE.get(model)
    if not rate:
        return 0.0
    return (tokens_in / 1000) * rate.get("in", 0) + (tokens_out / 1000) * rate.get("out", 0)


@retry(
    stop=stop_after_attempt(3),
    # раздел VI.1 ТЗ: паузы 2 -> 8 -> 20 секунд, ступенчато, не фиксированный интервал.
    wait=wait_chain(wait_fixed(2), wait_fixed(8), wait_fixed(20)),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
def _call_openrouter(model: str, system: str, user: str, max_tokens: int, json_mode: bool) -> dict:
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": config.USER_AGENT,
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    with httpx.Client(timeout=config.HTTP_TIMEOUT) as client:
        resp = client.post(OPENROUTER_URL, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()


def ask(
    task: str,
    system: str,
    user: str,
    model: str,
    max_tokens: int,
    json_mode: bool = True,
    inn: Optional[str] = None,
) -> dict:
    """Единственная точка вызова модели. Всегда пишет строку в ai_usage,
    даже при сбое (IX ТЗ, "без исключений"). Не распарсился JSON или сбой
    сети после всех попыток — не бросает исключение, возвращает
    {"verdict": "unclear", ...} и живёт дальше (VI.1 ТЗ)."""
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("ai.client.ask: OPENROUTER_API_KEY не задан в .env")

    conn = db.init_db()
    try:
        if _today_spend(conn) >= config.MAX_AI_SPEND_PER_DAY_USD:
            raise AiBudgetExceeded(
                f"дневной лимит ${config.MAX_AI_SPEND_PER_DAY_USD:.2f} исчерпан — "
                f"остановка, не предупреждение (IX.1 ТЗ)"
            )
        if _state["calls_this_run"] >= config.MAX_AI_CALLS_PER_RUN:
            raise AiBudgetExceeded(f"лимит вызовов за прогон ({config.MAX_AI_CALLS_PER_RUN}) исчерпан")

        max_input = config.MAX_AI_INPUT_CHARS.get(task)
        if max_input:
            user = user[:max_input]  # обрезка входа — жёстко, здесь и только здесь (XIII "Грабли")

        use_model = model
        if _state["consecutive_primary_failures"] >= 3 and config.MODEL_FALLBACK:
            use_model = config.MODEL_FALLBACK

        ok = True
        tokens_in = tokens_out = 0
        cost = 0.0
        parsed: dict = {}

        try:
            raw = _call_openrouter(use_model, system, user, max_tokens, json_mode)
            usage = raw.get("usage", {}) or {}
            tokens_in = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)
            cost = usage.get("cost") or _price_from_config(use_model, tokens_in, tokens_out)

            content = raw["choices"][0]["message"]["content"]
            parsed = _extract_json(content) if json_mode else {"text": content}
            if parsed is None:
                parsed = {"verdict": "unclear", "confidence": 0, "reason": "модель вернула не-JSON"}

            if use_model == model:
                _state["consecutive_primary_failures"] = 0
        except httpx.HTTPError as exc:
            ok = False
            parsed = {"verdict": "unclear", "confidence": 0, "reason": f"сбой модели: {exc}"}
            if use_model == model:
                _state["consecutive_primary_failures"] += 1
        finally:
            _state["calls_this_run"] += 1
            conn.execute(
                """
                INSERT INTO ai_usage (ts, model, task, inn, tokens_in, tokens_out, cost_usd, ok)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    use_model,
                    task,
                    inn,
                    tokens_in,
                    tokens_out,
                    cost,
                    1 if ok else 0,
                ),
            )
            conn.commit()

        return parsed
    finally:
        conn.close()
