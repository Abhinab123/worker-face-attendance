from __future__ import annotations

import math
from dataclasses import dataclass

import sqlalchemy as sa
from flask import request

from ..extensions import db


@dataclass
class Page:
    items: list
    page: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, math.ceil(self.total / self.per_page))

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


def paginate(stmt, per_page: int = 50, scalars: bool = True) -> Page:
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    total = db.session.scalar(sa.select(sa.func.count()).select_from(stmt.order_by(None).subquery())) or 0
    result = db.session.execute(stmt.limit(per_page).offset((page - 1) * per_page))
    items = list(result.scalars()) if scalars else list(result.all())
    return Page(items, page, per_page, total)


def clean_args(**kwargs) -> dict:
    return {k: v for k, v in kwargs.items() if v not in (None, "", False)}
