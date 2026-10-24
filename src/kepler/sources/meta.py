"""Meta Marketing API: ad insights (read) and one write, pausing an ad.

Env:
  META_ACCESS_TOKEN    a system-user token with ads_read (add ads_management only if you turn
                       auto-cut on in live mode)
  META_AD_ACCOUNT_ID   the ad account number, with or without the "act_" prefix

Options in kepler.toml ([[sources]] with type = "meta"):
  api_version          Graph API version, default "v23.0"
  lead_action_types    action types counted as leads, default ["lead", "offsite_conversion.fb_pixel_lead"]
  campaign_ids         optional allowlist; auto-cut refuses to pause an ad outside it

Reads: GET /act_<id>/insights (level=ad, one row per ad per day) and GET /act_<id>/ads (status).
Write: POST /<ad_id> with exactly status=PAUSED. `_post` refuses anything else before it is sent.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import urllib.parse
from collections.abc import Callable
from typing import Any

from ..http import Http, Transport
from .base import AdRow, SourceData

GRAPH = "https://graph.facebook.com"
DEFAULT_LEAD_TYPES = ("lead", "offsite_conversion.fb_pixel_lead")
MAX_PAGES = 50


class MetaSource:
    def __init__(self, name: str = "meta", token: str | None = None, account_id: str | None = None,
                 api_version: str = "v23.0", lead_action_types: list[str] | None = None,
                 campaign_ids: list[str] | None = None, transport: Transport | None = None):
        token = token or os.environ.get("META_ACCESS_TOKEN", "")
        account_id = account_id or os.environ.get("META_AD_ACCOUNT_ID", "")
        if not token or not account_id:
            raise RuntimeError("set META_ACCESS_TOKEN and META_AD_ACCOUNT_ID")
        self.name = name
        self.account = account_id if account_id.startswith("act_") else "act_" + account_id
        self.base = "%s/%s" % (GRAPH, api_version)
        self.lead_types = set(lead_action_types or DEFAULT_LEAD_TYPES)
        self.campaign_ids = set(campaign_ids or [])
        self.http = Http({"Authorization": "Bearer " + token}, transport)

    def _pages(self, url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(MAX_PAGES):
            body = self.http.get(url, params)
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise RuntimeError("Meta returned an invalid data page")
            if any(not isinstance(row, dict) for row in body["data"]):
                raise RuntimeError("Meta returned an invalid data row")
            rows.extend(body["data"])
            paging = body.get("paging", {})
            if not isinstance(paging, dict):
                raise RuntimeError("Meta returned invalid pagination")
            nxt = paging.get("next")
            if nxt is None:
                return rows
            if not isinstance(nxt, str) or not nxt.startswith(GRAPH + "/") or \
                    urllib.parse.urlsplit(nxt).netloc != urllib.parse.urlsplit(GRAPH).netloc:
                raise RuntimeError("Meta returned an invalid continuation URL")
            if page == MAX_PAGES - 1:
                raise RuntimeError("Meta read incomplete: pagination limit reached")
            url, params = nxt, {}
        raise RuntimeError("Meta read incomplete: no pages allowed")

    def _leads(self, actions: list[dict[str, Any]] | None) -> int:
        if actions is None:
            return 0
        if not isinstance(actions, list) or any(not isinstance(a, dict) for a in actions):
            raise ValueError("invalid Meta actions")
        leads = 0
        for action in actions:
            kind = action.get("action_type")
            if not isinstance(kind, str) or not kind or kind != kind.strip():
                raise ValueError("invalid Meta action type")
            value = _numeric(action.get("value"), whole=kind in self.lead_types)
            if kind in self.lead_types:
                leads += int(value)
        return leads

    def fetch(self, since: dt.date, until: dt.date) -> SourceData:
        rng = json.dumps({"since": since.isoformat(), "until": until.isoformat()})
        insights = self._pages("%s/%s/insights" % (self.base, self.account), {
            "level": "ad", "time_increment": 1, "time_range": rng, "limit": 500,
            "fields": "ad_id,ad_name,campaign_id,spend,impressions,inline_link_clicks,actions"})
        ads = self._pages("%s/%s/ads" % (self.base, self.account),
                          {"fields": "id,name,status,campaign_id", "limit": 500})
        status = {a["id"]: a.get("status", "") for a in ads}
        data = SourceData(as_of=dt.datetime.now().astimezone(),
                          daily={m: {} for m in ("spend", "impressions", "clicks", "meta_leads")})
        for r in insights:
            day = r.get("date_start", "")
            row = AdRow(date=day, ad_id=str(r["ad_id"]), ad_name=r.get("ad_name", ""),
                        campaign_id=str(r.get("campaign_id", "")), status=status.get(str(r["ad_id"]), ""),
                        spend=_numeric(r.get("spend")), impressions=int(_numeric(r.get("impressions"), whole=True)),
                        clicks=int(_numeric(r.get("inline_link_clicks", 0), whole=True)),
                        leads=self._leads(r.get("actions")))
            data.ads.append(row)
            data.add("spend", day, row.spend)
            data.add("impressions", day, row.impressions)
            data.add("clicks", day, row.clicks)
            data.add("meta_leads", day, row.leads)
        data.validate()
        return data

    def reread_ad(self, ad_id: str, since: dt.date, until: dt.date) -> tuple[float, int]:
        """One ad's total for the window, no daily or placement breakdown."""
        _check_id(ad_id)
        rows = self._pages("%s/%s/insights" % (self.base, ad_id), {
            "fields": "spend,impressions",
            "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()})})
        return (round(sum(_numeric(r.get("spend")) for r in rows), 2),
                sum(int(_numeric(r.get("impressions"), whole=True)) for r in rows))

    def preflight_ad(self, ad_id: str) -> dict[str, str]:
        _check_id(ad_id)
        before = self.http.get("%s/%s" % (self.base, ad_id), {"fields": "status,campaign_id"})
        if not isinstance(before, dict) or not isinstance(before.get("status"), str) or not before["status"]:
            raise RuntimeError("Meta returned an invalid ad status")
        if self.campaign_ids and str(before.get("campaign_id")) not in self.campaign_ids:
            raise RuntimeError("ad %s is outside the configured campaign_ids; not pausing" % ad_id)
        return before

    def pause_ad(self, ad_id: str, on_ack: Callable[[], None] | None = None,
                 before_send: Callable[[], None] | None = None) -> dict[str, str]:
        before = self.preflight_ad(ad_id)
        if before["status"] != "ACTIVE":
            return {"before": before["status"], "after": before["status"]}
        if before_send is not None:
            before_send()
        r = self._post("%s/%s" % (self.base, ad_id), {"status": "PAUSED"})
        if not isinstance(r, dict) or r.get("success") is not True:
            raise RuntimeError("Meta did not confirm the pause of ad %s" % ad_id)
        if on_ack is not None:
            on_ack()
        after = self.http.get("%s/%s" % (self.base, ad_id), {"fields": "status,campaign_id"})
        if not isinstance(after, dict) or after.get("status") != "PAUSED":
            raise RuntimeError("pause verification failed for ad %s" % ad_id)
        return {"before": "ACTIVE", "after": "PAUSED"}

    def _post(self, url: str, form: dict[str, str]) -> Any:
        """The only write path. Allows one shape: POST /<version>/<numeric ad id> status=PAUSED."""
        if not re.fullmatch(re.escape(self.base) + r"/\d+", url) or form != {"status": "PAUSED"}:
            raise PermissionError("refused write: only pausing a single ad is allowed")
        return self.http.post_form(url, form)


def _check_id(ad_id: str) -> None:
    if not re.fullmatch(r"\d+", ad_id):
        raise ValueError("Meta ad IDs are numeric; got %r" % ad_id)


def _numeric(value: Any, whole: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid Meta numeric field") from None
    if isinstance(value, bool) or not math.isfinite(number) or number < 0 or (whole and not number.is_integer()):
        raise ValueError("invalid Meta numeric field")
    return number
