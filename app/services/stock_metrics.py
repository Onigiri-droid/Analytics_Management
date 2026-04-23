from __future__ import annotations


def compute_stock_status(stock_qty: float, sales_qty: float) -> str:
    """
    Единая логика статуса остатка:
      - critical: остатка меньше недельных продаж
      - low: остаток меньше 2 недельных продаж
      - empty: остатков нет
      - ok: в остальных случаях
    """
    if sales_qty > 0 and stock_qty > 0:
        ratio = stock_qty / sales_qty
        if ratio < 1:
            return "critical"
        if ratio < 2:
            return "low"
        return "ok"
    if stock_qty == 0:
        return "empty"
    return "ok"
