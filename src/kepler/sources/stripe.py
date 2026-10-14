"""Stripe: charges, read-only.

Env:
  STRIPE_API_KEY   a restricted key with read access to Charges (rk_...). Kepler only sends GETs.

Options in kepler.toml ([[sources]] with type = "stripe"):
  currency           only count charges in this currency, default "usd"
  min_sale_amount    a charge counts as a sale only at or above this amount (e.g. to skip
                     deposits or add-ons), default 0

Publishes per day: `sales` (successful, captured charges not fully refunded), `cash` (amount minus
refunds, in currency units) and `refunds`. If you bill in installments every installment is a
charge, so set `min_sale_amount` or count sales from your CRM instead.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

from ..http import Http, Transport
from .base import SourceData

API = "https://api.stripe.com/v1/charges"
MAX_PAGES = 100


class StripeSource:
    def __init__(self, name: str = "stripe", api_key: str | None = None, currency: str = "usd",
                 min_sale_amount: float = 0.0, transport: Transport | None = None):
        api_key = api_key or os.environ.get("STRIPE_API_KEY", "")
        if not api_key:
            raise RuntimeError("set STRIPE_API_KEY (a restricted, read-only key)")
        self.name = name
        self.currency = currency.lower()
        self.min_sale = float(min_sale_amount)
        self.http = Http({"Authorization": "Bearer " + api_key}, transport)

    def fetch(self, since: dt.date, until: dt.date) -> SourceData:
        start = int(dt.datetime.combine(since, dt.time()).astimezone().timestamp())
        params: dict[str, Any] = {"limit": 100, "created[gte]": start}
        data = SourceData(as_of=dt.datetime.now().astimezone())
        for _ in range(MAX_PAGES):
            body = self.http.get(API, params)
            charges = body.get("data", [])
            for c in charges:
                self._add(data, c, until)
            if not body.get("has_more") or not charges:
                break
            params["starting_after"] = charges[-1]["id"]
        for m in ("sales", "cash", "refunds"):
            data.daily.setdefault(m, {})
        return data

    def _add(self, data: SourceData, c: dict[str, Any], until: dt.date) -> None:
        if c.get("status") != "succeeded" or not c.get("paid") or not c.get("captured"):
            return
        if str(c.get("currency", "")).lower() != self.currency:
            return
        day = dt.datetime.fromtimestamp(int(c["created"])).astimezone().date()
        if day > until:
            return
        amount = int(c.get("amount") or 0) / 100
        refunded = int(c.get("amount_refunded") or 0) / 100
        d = day.isoformat()
        data.add("cash", d, round(amount - refunded, 2))
        data.add("refunds", d, refunded)
        if amount >= self.min_sale and not c.get("refunded"):
            data.add("sales", d, 1)
