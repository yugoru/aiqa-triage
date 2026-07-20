"""Схема вердикта триажа.

Structured output: модель обязана вернуть JSON ровно этой формы.
Enum не даст ей изобрести категорию, Pydantic отбракует всё остальное.

Это — граница ответственности: DeepSeek возвращает синтаксически валидный JSON
(response_format=json_object), а *схему* гарантирует именно эта модель, а не API.
"""

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Category(str, Enum):
    """Таксономия причин падения. Ровно пять значений — модель выбирает из них."""

    PRODUCT = "продукт"  # баг в тестируемом API
    TEST_CODE = "код_автотеста"  # устаревший assert, кривые тест-данные
    INFRASTRUCTURE = "инфраструктура"  # сеть, окружение, недоступность сервиса
    TOOLING = "инструментарий"  # обвязка: фикстуры, conftest, раннер, плагины
    FLAKY = "flaky"  # нестабильное поведение, гонки, случайность


class Verdict(BaseModel):
    """Диагноз по одному падению. Заполняется моделью, проверяется кодом."""

    test: str = Field(description="Полное имя теста из отчёта")
    category: Category
    confidence: float = Field(ge=0, le=1, description="Уверенность в вердикте, 0..1")
    root_cause: str = Field(description="Гипотеза первопричины, 1-2 предложения")
    evidence: str = Field(
        description="Улики из трейсбека/кода/контракта. Вердикт без улик не принимается."
    )
    suggested_action: str = Field(
        description="Что делать: чинить продукт/тест/окружение, карантин и т.п."
    )
    needs_human: bool = Field(
        description="true, если уверенности мало или улики противоречивы"
    )
    draft_bug_title: str | None = Field(
        default=None,
        description="Заголовок черновика бага, только для category=продукт",
    )

    @field_validator("evidence", "root_cause", "suggested_action")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """Вердикт без улик и без объяснения — не вердикт. Отклоняем пустышки."""
        if not value or not value.strip():
            raise ValueError("поле обязательно и не может быть пустым")
        return value.strip()
