"""Клиент модели для триажа — ЛЮБОЙ OpenAI-совместимый провайдер.

Провайдер-агностичен: провайдер, ключ и модель берутся из env, поэтому
подставляется что угодно — DeepSeek, OpenAI, OpenRouter, локальные Ollama /
LM Studio / vLLM — без правки кода. По умолчанию нацелен на DeepSeek
(https://api.deepseek.com); пресеты для остальных — в .env.example.

Разделение труда: этот модуль отвечает за *общение с моделью* и за то,
чтобы ответ был валидным `Verdict`. Смысловые решения (категория, причина,
улики) принимает модель; гарантию формата даёт наша Pydantic-валидация, а не API.

Особенности JSON-режима, которые здесь учтены:
- `response_format={"type": "json_object"}` гарантирует синтаксис JSON,
  но не нашу схему → обязательна `Verdict.model_validate_json`;
- промпт обязан содержать слово «JSON» и пример структуры (см. prompt.md);
- контент иногда приходит пустым → считаем это ошибкой и перегенерируем;
- при `finish_reason == "length"` JSON обрезан → повторяем с большим лимитом.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import requests
from pydantic import ValidationError

from schema import Verdict

# Планка confidence, ниже которой типовая модель считается неуверенной
# и (при --escalate) вопрос переадресуется «умной» модели.
ESCALATE_CONFIDENCE = 0.6


class LLMError(RuntimeError):
    """Ошибка общения с моделью: сеть, неверная модель, пустой/битый ответ."""


@dataclass
class Usage:
    """Токены и причина остановки одного вызова модели."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class TriageResult:
    """Вердикт + метаданные вызова (какой моделью, сколько токенов, эскалация)."""

    verdict: Verdict
    usages: list[Usage]
    escalated: bool = False

    @property
    def total_tokens(self) -> int:
        return sum(u.total_tokens for u in self.usages)


class DeepSeekClient:
    """Тонкая обёртка над chat/completions с валидацией и ретраями."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        model_hard: str | None,
        prompt: str,
        max_tokens: int = 1800,
        timeout: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.model_hard = model_hard or model
        self.prompt = prompt
        self.max_tokens = max_tokens
        self.timeout = timeout

    @classmethod
    def from_env(cls, prompt: str) -> "DeepSeekClient":
        """Собрать клиента из переменных окружения (.env). Ключ обязателен."""
        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY")
        if not api_key:
            raise LLMError(
                "Не задан ключ модели. Впишите DEEPSEEK_API_KEY или LLM_API_KEY в .env "
                "(см. .env.example — там пресеты DeepSeek/OpenAI/OpenRouter/Ollama/"
                "LM Studio). Для локальной модели ключ — любая заглушка."
            )
        try:
            max_tokens = int(os.getenv("LLM_MAX_TOKENS", "1800"))
        except ValueError:
            raise LLMError("LLM_MAX_TOKENS в .env должно быть целым числом") from None
        return cls(
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
            api_key=api_key,
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            model_hard=os.getenv("LLM_MODEL_HARD", "deepseek-v4-pro"),
            prompt=prompt,
            max_tokens=max_tokens,
        )

    # --------------------------------------------------------------------- #
    # Низкий уровень: один HTTP-вызов chat/completions                      #
    # --------------------------------------------------------------------- #
    def _chat(self, model: str, context: str, max_tokens: int) -> tuple[str, Usage]:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": self.prompt},
                {"role": "user", "content": context},
            ],
            "temperature": 0,  # триаж, не творчество
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},  # гарантирует синтаксис JSON
        }
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as err:
            raise LLMError(
                f"Сеть недоступна при обращении к {self.base_url}: {err}"
            ) from err

        if resp.status_code >= 400:
            # Частый случай — неверный id модели: подсказываем, где чинить.
            hint = ""
            if resp.status_code in (400, 404) and "model" in resp.text.lower():
                hint = (
                    f"\nПохоже, модель '{model}' недоступна на {self.base_url}. "
                    "Проверьте LLM_MODEL/LLM_MODEL_HARD в .env — актуальные id "
                    "смотрите в документации провайдера."
                )
            raise LLMError(f"{resp.status_code} от модели: {resp.text[:300]}{hint}")

        try:
            body = resp.json()
            choice = body["choices"][0]
        except (ValueError, KeyError, IndexError) as err:
            # 200 OK, но тело — не ожидаемый chat-ответ (ошибка провайдера, HTML шлюза…)
            raise LLMError(
                f"Неожиданный ответ модели (нет choices): {resp.text[:300]}"
            ) from err
        content = choice.get("message", {}).get("content") or ""
        raw_usage = body.get("usage", {})
        usage = Usage(
            model=model,
            prompt_tokens=raw_usage.get("prompt_tokens", 0),
            completion_tokens=raw_usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", "stop"),
        )
        return content, usage

    def _generate_json(self, model: str, context: str) -> tuple[str, Usage]:
        """Получить непустой, необрезанный JSON. Ретраит пустоту и обрезку."""
        max_tokens = self.max_tokens
        for attempt in range(3):
            content, usage = self._chat(model, context, max_tokens)

            if not content.strip():
                # Известная особенность JSON-режима DeepSeek: пустой content.
                context += "\n\nОтвет пришёл пустым. Верни непустой JSON по схеме."
                continue

            if usage.finish_reason == "length":
                # JSON обрезан по лимиту — перегенерируем с большим окном.
                max_tokens = min(max_tokens * 2, 8000)
                continue

            return content, usage

        raise LLMError(
            f"Модель {model} не вернула цельный JSON за {attempt + 1} попытки "
            f"(последняя причина остановки: {usage.finish_reason})."
        )

    @staticmethod
    def _parse(content: str) -> Verdict:
        """Снять возможную markdown-обёртку и провалидировать по схеме."""
        cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
        return Verdict.model_validate_json(cleaned)

    # --------------------------------------------------------------------- #
    # Высокий уровень: вердикт с валидацией, ретраем и эскалацией            #
    # --------------------------------------------------------------------- #
    def _ask(self, model: str, context: str) -> tuple[Verdict, list[Usage]]:
        """Один диагноз выбранной моделью. Одна повторная попытка на брак схемы."""
        content, usage = self._generate_json(model, context)
        try:
            return self._parse(content), [usage]
        except (ValidationError, json.JSONDecodeError, ValueError) as err:
            # Показываем модели её ошибку и просим строгий JSON — вторая попытка.
            retry_context = (
                f"{context}\n\nПрошлый ответ не прошёл валидацию схемы:\n{err}\n"
                "Верни СТРОГО валидный JSON ровно по схеме, без markdown и комментариев."
            )
            content2, usage2 = self._generate_json(model, retry_context)
            return self._parse(content2), [usage, usage2]

    def triage(self, context: str, *, escalate: bool = False) -> TriageResult:
        """Поставить диагноз падению.

        При ``escalate=True`` спорный случай (needs_human или низкая confidence)
        переспрашивается «умной» моделью — двухмодельная схема из ТЗ.
        """
        verdict, usages = self._ask(self.model, context)

        disputable = verdict.needs_human or verdict.confidence < ESCALATE_CONFIDENCE
        if escalate and disputable and self.model_hard != self.model:
            hard_verdict, hard_usages = self._ask(self.model_hard, context)
            return TriageResult(hard_verdict, usages + hard_usages, escalated=True)

        return TriageResult(verdict, usages)
