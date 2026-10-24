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
        for page in range(MAX_PAGES):
            body = self.http.get(API, params)
            if not isinstance(body, dict) or not isinstance(body.get("data"), list) or \
                    not isinstance(body.get("has_more"), bool):
                raise RuntimeError("Stripe returned an invalid charges page")
            charges = body["data"]
            if any(not isinstance(c, dict) for c in charges):
                raise RuntimeError("Stripe returned an invalid charge row")
            for c in charges:
                self._add(data, c, until)
            if not body["has_more"]:
                break
            if not charges or page == MAX_PAGES - 1:
                raise RuntimeError("Stripe read incomplete: pagination did not finish")
            params["starting_after"] = charges[-1]["id"]
        else:
            raise RuntimeError("Stripe read incomplete: pagination limit reached")
        for m in ("sales", "cash", "refunds"):
            data.daily.setdefault(m, {})
        data.validate()
        return data

    def _add(self, data: SourceData, c: dict[str, Any], until: dt.date) -> None:
        if not isinstance(c.get("id"), str) or not c["id"] or c.get("status") not in ("succeeded", "failed", "pending"):
            raise RuntimeError("Stripe returned an invalid charge")
        if c["status"] != "succeeded":
            return
        if not all(isinstance(c.get(k), bool) for k in ("paid", "captured")) or \
                not all(isinstance(c.get(k), int) and not isinstance(c[k], bool) and c[k] >= 0
                        for k in ("created", "amount")) or not isinstance(c.get("currency"), str):
            raise RuntimeError("Stripe returned invalid charge fields")
        refund = c.get("amount_refunded")
        if isinstance(refund, bool) or not isinstance(refund, int) or not 0 <= refund <= c["amount"] or \
                not isinstance(c.get("refunded"), bool):
            raise RuntimeError("Stripe returned invalid refund fields")
        if c["refunded"] != (c["amount"] > 0 and refund == c["amount"]):
            raise RuntimeError("Stripe returned inconsistent refund fields")
        if not c["paid"] or not c["captured"]:
            return
        if str(c.get("currency", "")).lower() != self.currency:
            return
        day = dt.datetime.fromtimestamp(int(c["created"])).astimezone().date()
        if day > until:
            return
        amount = c["amount"] / 100
        refunded = refund / 100
        d = day.isoformat()
        data.add("cash", d, round(amount - refunded, 2))
        data.add("refunds", d, refunded)
        if amount >= self.min_sale and not c["refunded"]:
            data.add("sales", d, 1)
