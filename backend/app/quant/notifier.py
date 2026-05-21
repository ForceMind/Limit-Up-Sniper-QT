from __future__ import annotations

import json
import os
import smtplib
import ssl
import threading
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from app.quant.engine import DATA_DIR, read_json, safe_float, write_json


CONFIG_FILE = DATA_DIR / "config.json"
NOTIFICATION_STATE_FILE = DATA_DIR / "trade_notification_state.json"
NOTIFICATION_LOG_FILE = DATA_DIR / "trade_notification_logs.jsonl"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def _first_env(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


class TradeNotifier:
    def __init__(self) -> None:
        self.state_file = NOTIFICATION_STATE_FILE
        self.log_file = NOTIFICATION_LOG_FILE
        self._lock = threading.RLock()

    def config(self) -> Dict[str, Any]:
        payload = read_json(CONFIG_FILE, {})
        email_cfg = payload.get("email_config") if isinstance(payload, dict) and isinstance(payload.get("email_config"), dict) else {}
        smtp_server = _first_env("SMTP_SERVER", "EMAIL_SMTP_SERVER") or str(email_cfg.get("smtp_server") or "").strip()
        smtp_user = _first_env("SMTP_USER", "EMAIL_SMTP_USER") or str(email_cfg.get("smtp_user") or "").strip()
        smtp_password = _first_env("SMTP_PASSWORD", "EMAIL_SMTP_PASSWORD") or str(email_cfg.get("smtp_password") or "").strip()
        recipient = _first_env("EMAIL_TO", "RECIPIENT_EMAIL") or str(email_cfg.get("recipient_email") or "").strip()
        sender = _first_env("EMAIL_FROM") or smtp_user
        try:
            smtp_port = int(_first_env("SMTP_PORT", "EMAIL_SMTP_PORT") or email_cfg.get("smtp_port") or 465)
        except Exception:
            smtp_port = 465
        enabled = _env_bool("EMAIL_ENABLED", bool(email_cfg.get("enabled")))
        use_ssl = _env_bool("SMTP_USE_SSL", bool(email_cfg.get("smtp_use_ssl", smtp_port == 465)))
        return {
            "enabled": bool(enabled and smtp_server and smtp_user and smtp_password and recipient),
            "configured": bool(smtp_server and smtp_user and smtp_password and recipient),
            "smtp_server": smtp_server,
            "smtp_port": smtp_port,
            "smtp_user": smtp_user,
            "smtp_password": smtp_password,
            "sender": sender,
            "recipient": recipient,
            "use_ssl": use_ssl,
        }

    def status(self) -> Dict[str, Any]:
        cfg = self.config()
        state = read_json(self.state_file, {})
        notified = state.get("notified_trade_ids") if isinstance(state, dict) and isinstance(state.get("notified_trade_ids"), list) else []
        return {
            "status": "ok",
            "enabled": cfg["enabled"],
            "configured": cfg["configured"],
            "smtp_server": cfg["smtp_server"],
            "smtp_port": cfg["smtp_port"],
            "recipient": self._mask_email(cfg["recipient"]),
            "notified_trades": len(notified),
            "last_notification": state.get("last_notification", {}) if isinstance(state, dict) else {},
        }

    def send_test(self) -> Dict[str, Any]:
        return self.send_email(
            subject="涨停狙击手邮件通知测试",
            plain_text=f"邮件通知配置正常。\n发送时间：{datetime.now().isoformat(timespec='seconds')}",
            html_text=f"<p>邮件通知配置正常。</p><p>发送时间：{datetime.now().isoformat(timespec='seconds')}</p>",
            event_type="test",
        )

    def notify_trade_events(self, trades: List[Dict[str, Any]], as_of: str, source: str = "paper_trading") -> Dict[str, Any]:
        cfg = self.config()
        if not cfg["enabled"]:
            return {"status": "disabled", "sent": 0, "configured": cfg["configured"]}
        with self._lock:
            state = read_json(self.state_file, {})
            if not isinstance(state, dict):
                state = {}
            notified = set(str(item) for item in state.get("notified_trade_ids", []) if str(item).strip())
            candidates = []
            for trade in trades:
                if not isinstance(trade, dict):
                    continue
                side = str(trade.get("side") or "").upper()
                if side not in {"BUY", "SELL"}:
                    continue
                if str(trade.get("date") or "") != as_of:
                    continue
                trade_id = self._trade_id(trade, source)
                if trade_id in notified:
                    continue
                candidates.append((trade_id, trade))

            sent = 0
            errors = []
            for trade_id, trade in candidates:
                payload = self._trade_email(trade, as_of=as_of, source=source)
                result = self.send_email(
                    subject=payload["subject"],
                    plain_text=payload["plain_text"],
                    html_text=payload["html_text"],
                    event_type="trade",
                    meta={"trade_id": trade_id, "side": trade.get("side"), "code": trade.get("code"), "date": trade.get("date")},
                )
                if result.get("status") == "ok":
                    notified.add(trade_id)
                    sent += 1
                else:
                    errors.append(result.get("message", "send_failed"))

            state["notified_trade_ids"] = sorted(notified)[-5000:]
            state["last_notification"] = {
                "as_of": as_of,
                "source": source,
                "candidate_count": len(candidates),
                "sent": sent,
                "errors": errors[:5],
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            write_json(self.state_file, state)
            return {"status": "ok", **state["last_notification"]}

    def send_email(
        self,
        subject: str,
        plain_text: str,
        html_text: str = "",
        event_type: str = "notification",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        cfg = self.config()
        if not cfg["enabled"]:
            return {"status": "disabled", "message": "email notification is not enabled or config is incomplete"}

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = cfg["sender"]
        message["To"] = cfg["recipient"]
        message.attach(MIMEText(plain_text, "plain", "utf-8"))
        if html_text:
            message.attach(MIMEText(html_text, "html", "utf-8"))

        try:
            if cfg["use_ssl"] or int(cfg["smtp_port"]) == 465:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(cfg["smtp_server"], int(cfg["smtp_port"]), context=context, timeout=20) as server:
                    server.login(cfg["smtp_user"], cfg["smtp_password"])
                    server.sendmail(cfg["sender"], [cfg["recipient"]], message.as_string())
            else:
                with smtplib.SMTP(cfg["smtp_server"], int(cfg["smtp_port"]), timeout=20) as server:
                    server.starttls(context=ssl.create_default_context())
                    server.login(cfg["smtp_user"], cfg["smtp_password"])
                    server.sendmail(cfg["sender"], [cfg["recipient"]], message.as_string())
            self._append_log({"status": "ok", "event_type": event_type, "subject": subject, "meta": meta or {}})
            return {"status": "ok", "message": "sent"}
        except Exception as exc:
            message_text = str(exc)[:200]
            self._append_log({"status": "failed", "event_type": event_type, "subject": subject, "message": message_text, "meta": meta or {}})
            return {"status": "failed", "message": message_text}

    def _trade_email(self, trade: Dict[str, Any], as_of: str, source: str) -> Dict[str, str]:
        side = str(trade.get("side") or "").upper()
        side_text = "买入" if side == "BUY" else "卖出"
        code = str(trade.get("code") or "")
        name = str(trade.get("name") or "")
        qty = safe_float(trade.get("qty"), 0)
        price = safe_float(trade.get("price"), 0)
        amount = qty * price
        score = safe_float(trade.get("score"), 0)
        pnl_pct = safe_float(trade.get("pnl_pct"), 0)
        reason = str(trade.get("reason") or "")[:500]
        subject = f"涨停狙击手{side_text}触发：{code} {name}"
        plain = (
            f"{side_text}触发\n"
            f"日期：{as_of}\n"
            f"股票：{code} {name}\n"
            f"数量：{qty:.0f}\n"
            f"价格：{price:.3f}\n"
            f"金额：{amount:.2f}\n"
            f"评分：{score:.2f}\n"
            f"收益率：{pnl_pct:.2f}%\n"
            f"原因：{reason}\n"
            f"来源：{source}\n"
        )
        html = (
            f"<h3>涨停狙击手{side_text}触发</h3>"
            f"<p><b>日期：</b>{as_of}</p>"
            f"<p><b>股票：</b>{code} {name}</p>"
            f"<p><b>数量：</b>{qty:.0f}</p>"
            f"<p><b>价格：</b>{price:.3f}</p>"
            f"<p><b>金额：</b>{amount:.2f}</p>"
            f"<p><b>评分：</b>{score:.2f}</p>"
            f"<p><b>收益率：</b>{pnl_pct:.2f}%</p>"
            f"<p><b>原因：</b>{reason}</p>"
            f"<p><b>来源：</b>{source}</p>"
        )
        return {"subject": subject, "plain_text": plain, "html_text": html}

    def _trade_id(self, trade: Dict[str, Any], source: str) -> str:
        parts = [
            source,
            str(trade.get("date") or ""),
            str(trade.get("side") or "").upper(),
            str(trade.get("code") or ""),
            str(trade.get("qty") or ""),
            str(trade.get("price") or ""),
            str(trade.get("reason") or "")[:80],
        ]
        return "|".join(parts)

    def _append_log(self, row: Dict[str, Any]) -> None:
        payload = dict(row)
        payload["created_at"] = datetime.now().isoformat(timespec="seconds")
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _mask_email(self, value: str) -> str:
        text = str(value or "").strip()
        if "@" not in text:
            return ""
        name, domain = text.split("@", 1)
        if len(name) <= 2:
            masked = name[:1] + "*"
        else:
            masked = name[:2] + "***" + name[-1:]
        return f"{masked}@{domain}"


trade_notifier = TradeNotifier()
