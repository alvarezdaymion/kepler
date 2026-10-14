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
import os
import re
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
        body = self.http.get(url, params)
        for _ in range(MAX_PAGES):
            rows.extend(body.get("data", []))
            nxt = (body.get("paging") or {}).get("next")
            if not nxt or not nxt.startswith(GRAPH + "/"):
                break
            body = self.http.get(nxt)
        return rows

    def _leads(self, actions: list[dict[str, Any]] | None) -> int:
        return int(sum(float(a.get("value") or 0) for a in actions or [] if a.get("action_type") in self.lead_types))

    def fetch(self, since: dt.date, until: dt.date) -> SourceData:
        rng = json.dumps({"since": since.isoformat(), "until": until.isoformat()})
        insights = self._pages("%s/%s/insights" % (self.base, self.account), {
            "level": "ad", "time_increment": 1, "time_range": rng, "limit": 500,
            "fields": "ad_id,ad_name,campaign_id,spend,impressions,inline_link_clicks,actions"})
        ads = self._pages("%s/%s/ads" % (self.base, self.account),
                          {"fields": "id,name,status,campaign_id", "limit": 500})
        status = {a["id"]: a.get("status", "") for a in ads}
        data = SourceData(as_of=dt.datetime.now().astimezone())
        for r in insights:
            day = r.get("date_start", "")
            row = AdRow(date=day, ad_id=str(r["ad_id"]), ad_name=r.get("ad_name", ""),
                        campaign_id=str(r.get("campaign_id", "")), status=status.get(str(r["ad_id"]), ""),
                        spend=float(r.get("spend") or 0), impressions=int(r.get("impressions") or 0),
                        clicks=int(r.get("inline_link_clicks") or 0), leads=self._leads(r.get("actions")))
            data.ads.append(row)
            data.add("spend", day, row.spend)
            data.add("impressions", day, row.impressions)
            data.add("clicks", day, row.clicks)
            data.add("meta_leads", day, row.leads)
        return data

    def reread_ad(self, ad_id: str, since: dt.date, until: dt.date) -> tuple[float, int]:
        """One ad's total for the window, no daily or placement breakdown."""
        _check_id(ad_id)
        rows = self._pages("%s/%s/insights" % (self.base, ad_id), {
            "fields": "spend,impressions",
            "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()})})
        return (round(sum(float(r.get("spend") or 0) for r in rows), 2),
                sum(int(r.get("impressions") or 0) for r in rows))

    def pause_ad(self, ad_id: str) -> dict[str, str]:
        _check_id(ad_id)
        fields = {"fields": "status,campaign_id"}
        before = self.http.get("%s/%s" % (self.base, ad_id), fields)
        if self.campaign_ids and str(before.get("campaign_id")) not in self.campaign_ids:
            raise RuntimeError("ad %s is outside the configured campaign_ids; not pausing" % ad_id)
        if before.get("status") != "ACTIVE":
            return {"before": before.get("status", ""), "after": before.get("status", "")}
        r = self._post("%s/%s" % (self.base, ad_id), {"status": "PAUSED"})
        if not r.get("success"):
            raise RuntimeError("Meta did not confirm the pause of ad %s" % ad_id)
        after = self.http.get("%s/%s" % (self.base, ad_id), fields)
        return {"before": "ACTIVE", "after": after.get("status", "")}

    def _post(self, url: str, form: dict[str, str]) -> Any:
        """The only write path. Allows one shape: POST /<version>/<numeric ad id> status=PAUSED."""
        if not re.fullmatch(re.escape(self.base) + r"/\d+", url) or form != {"status": "PAUSED"}:
            raise PermissionError("refused write: only pausing a single ad is allowed")
        return self.http.post_form(url, form)


def _check_id(ad_id: str) -> None:
    if not re.fullmatch(r"\d+", ad_id):
        raise ValueError("Meta ad IDs are numeric; got %r" % ad_id)
