"""Дневник (как WHOOP Journal): привычки «да/нет» за вчера и их влияние на восстановление.

Подключение — FEATURES в coach.py: route / extra / context_lines (см. там)."""


def route(app, uid, path, b, today):
    return None


def extra(app, uid, today):
    return {}


def context_lines(app, uid, today):
    return []
