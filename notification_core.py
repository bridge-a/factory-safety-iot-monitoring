"""持久化告警、状态去重与独立邮件队列，不依赖网页访问。"""
import configparser
import json
import logging
import smtplib
import sqlite3
import ssl
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, parseaddr
from pathlib import Path

LOGGER = logging.getLogger("factory-notifications")
SEVERITY = {"gray": -1, "green": 0, "yellow": 1, "red": 2}
LEVEL_NAMES = {"green": "恢复正常", "yellow": "黄色预警", "red": "红色报警"}


@dataclass(frozen=True)
class MailSettings:
    enabled: bool = False
    host: str = "smtp.qq.com"
    port: int = 465
    username: str = ""
    password: str = field(default="", repr=False)
    recipient: str = ""
    timeout: int = 10
    max_attempts: int = 5
    allow_demo: bool = False

    @classmethod
    def load(cls, path):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(path, encoding="utf-8-sig")
        if not parser.has_section("smtp"):
            return cls()
        section = parser["smtp"]
        result = cls(
            enabled=section.getboolean("enabled", False),
            host=section.get("host", "smtp.qq.com").strip(),
            port=section.getint("port", 465),
            username=section.get("username", "").strip(),
            password=section.get("authorization_code", "").strip(),
            recipient=section.get("recipient", "").strip(),
            timeout=section.getint("timeout_seconds", 10),
            max_attempts=section.getint("max_attempts", 5),
            allow_demo=section.getboolean("allow_demo", False),
        )
        if result.enabled:
            for address in (result.username, result.recipient):
                if any(c in address for c in "\r\n,;") or parseaddr(address)[1] != address or "@" not in address:
                    raise ValueError("发件和收件地址均须填写一个完整邮箱地址")
            if not result.password or result.password == "YOUR_QQ_SMTP_AUTHORIZATION_CODE":
                raise ValueError("尚未填写 QQ 邮箱 SMTP 授权码")
            if not result.host or not 1 <= result.port <= 65535:
                raise ValueError("SMTP 服务器或端口配置错误")
            if not 1 <= result.timeout <= 60 or not 1 <= result.max_attempts <= 10:
                raise ValueError("超时应为 1～60 秒，重试次数应为 1～10")
        return result


class AlertStore:
    def __init__(self, path, recovery_hold_seconds=15):
        self.path = str(path)
        self.recovery_hold_seconds = recovery_hold_seconds
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS device_state (
                    device TEXT PRIMARY KEY, level TEXT NOT NULL, signature TEXT NOT NULL,
                    candidate TEXT, candidate_since REAL
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT UNIQUE NOT NULL, ts REAL NOT NULL, device TEXT NOT NULL,
                    level TEXT NOT NULL, payload TEXT NOT NULL,
                    mail_status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '', sent_at REAL
                );
                CREATE INDEX IF NOT EXISTS alerts_pending ON alerts(mail_status,next_attempt);
            """)
            db.execute("INSERT OR IGNORE INTO metadata VALUES('stream_id',?)", (uuid.uuid4().hex,))
            columns = {row[1] for row in db.execute("PRAGMA table_info(device_state)")}
            if "last_seen" not in columns:
                db.execute("ALTER TABLE device_state ADD COLUMN last_seen REAL")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def observe(self, data, evaluation, mail_enabled=False, now=None, allow_demo=False):
        """首次异常及升级立即记录；重复值去重；降级/恢复须持续 15 秒。"""
        now = time.time() if now is None else now
        level = evaluation["level"]
        if level not in SEVERITY or level == "gray":
            # 数据缺失不能作为恢复正常的证据，也不能计入连续恢复时间。
            with closing(self.connect()) as db, db:
                db.execute("UPDATE device_state SET candidate=NULL,candidate_since=NULL WHERE device=?",
                           (str(data.get("device") or "unknown"),))
            return None
        device = str(data.get("device") or "unknown")[:128]
        signature = json.dumps([level, sorted((x[0], x[2]) for x in evaluation["abnormal"])])
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT * FROM device_state WHERE device=?", (device,)).fetchone()
            previous_level = state["level"] if state else None
            if state and state["signature"] == signature:
                db.execute("UPDATE device_state SET candidate=NULL,candidate_since=NULL,last_seen=? WHERE device=?", (now, device))
                return None
            if state and SEVERITY[level] < SEVERITY[previous_level]:
                gap = now - state["last_seen"] if state["last_seen"] is not None else float("inf")
                if state["candidate"] != signature or not 0 <= gap <= 6:
                    db.execute("UPDATE device_state SET candidate=?,candidate_since=?,last_seen=? WHERE device=?",
                               (signature, now, now, device))
                    return None
                if now - state["candidate_since"] < self.recovery_hold_seconds:
                    db.execute("UPDATE device_state SET last_seen=? WHERE device=?", (now, device))
                    return None
            db.execute("INSERT OR REPLACE INTO device_state(device,level,signature,candidate,candidate_since,last_seen) VALUES(?,?,?,NULL,NULL,?)",
                       (device, level, signature, now))
            if previous_level is None and level == "green":
                return None
            abnormal = sorted(evaluation["abnormal"], key=lambda x: SEVERITY[x[2]], reverse=True)
            primary = abnormal[0] if abnormal else (None, None, None)
            snapshot = {key: data.get(key) for key in ("temp_c", "rh", "lux", "co2_ppm", "co2_valid", "smoke_raw", "smoke_valid", "sources")}
            event = {"ts": now, "device": device, "level": level, "prev_level": previous_level,
                     "source": data.get("event_source", "environment"), "reason": evaluation["reason"],
                     "target": primary[0] or data.get("metric"), "value": primary[1], "snapshot": snapshot,
                     "mode": data.get("mode", "live"), "is_demo": bool(data.get("is_demo", False))}
            event.update(snapshot)  # 兼容原看板的最新告警字段。
            notify = level in {"yellow", "red"}
            status = ("pending" if mail_enabled else "disabled") if notify else "not_required"
            if notify and mail_enabled and event["is_demo"] and not allow_demo:
                status = "demo_blocked"
            cursor = db.execute("INSERT INTO alerts(event_key,ts,device,level,payload,mail_status,next_attempt) VALUES(?,?,?,?,?,?,?)",
                                (uuid.uuid4().hex, now, device, level, json.dumps(event, ensure_ascii=False), status, now))
            event["id"] = cursor.lastrowid
            event["mail_status"] = status
            return event

    @staticmethod
    def decode(row):
        event = json.loads(row["payload"])
        event.update(id=row["id"], mail_status=row["mail_status"], attempts=row["attempts"],
                     last_error=row["last_error"], sent_at=row["sent_at"])
        return event

    def list_events(self, after=None, limit=50):
        limit = max(1, min(int(limit), 100))
        with closing(self.connect()) as db:
            if after is None:
                rows = db.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM alerts WHERE id>? ORDER BY id LIMIT ?", (after, limit)).fetchall()
            stream_id = db.execute("SELECT value FROM metadata WHERE key='stream_id'").fetchone()[0]
            latest_id = db.execute("SELECT COALESCE(MAX(id),0) FROM alerts").fetchone()[0]
        return {"events": [self.decode(row) for row in rows], "stream_id": stream_id, "latest_id": latest_id}

    def query_events(self, page=1, page_size=20, mode="", level="", source="", query="", since=None, until=None):
        clauses, values = [], []
        for column, value in (("json_extract(payload,'$.mode')", mode),
                              ("json_extract(payload,'$.source')", source)):
            if value:
                clauses.append(column + "=?")
                values.append(value)
        if query:
            clauses.append("instr(json_extract(payload,'$.reason'),?)>0")
            values.append(query)
        if since is not None:
            clauses.append("ts>=?")
            values.append(since)
        if until is not None:
            clauses.append("ts<=?")
            values.append(until)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with closing(self.connect()) as db:
            # Keep tab counts independent of the selected level. Both counts and rows
            # use one read snapshot, even when the sampling thread inserts an event.
            db.execute("BEGIN")
            level_counts = dict(db.execute("SELECT level,COUNT(*) FROM alerts" + where + " GROUP BY level", values).fetchall())
            scope_total = sum(level_counts.values())
            total = level_counts.get(level, 0) if level else scope_total
            counts = ({level: total} if total else {}) if level else level_counts
            if level:
                where += (" AND " if clauses else " WHERE ") + "level=?"
                values.append(level)
            pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, pages)
            rows = db.execute("SELECT * FROM alerts" + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
                              [*values, page_size, (page - 1) * page_size]).fetchall()
        return {"events": [self.decode(row) for row in rows], "total": total, "counts": counts,
                "level_counts": level_counts, "scope_total": scope_total,
                "page": page, "pages": pages, "page_size": page_size}

    def heartbeat(self, smtp_enabled, mqtt_connected):
        state = json.dumps({"heartbeat": time.time(), "smtp_enabled": smtp_enabled,
                            "mqtt_connected": mqtt_connected})
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES('service',?)", (state,))

    def status(self):
        with closing(self.connect()) as db:
            row = db.execute("SELECT value FROM metadata WHERE key='service'").fetchone()
            counts = dict(db.execute("SELECT mail_status,COUNT(*) FROM alerts GROUP BY mail_status").fetchall())
        state = json.loads(row[0]) if row else {}
        state["service_online"] = time.time() - state.get("heartbeat", 0) < 20
        state["mail_counts"] = counts
        return state

    def claim_mail(self, now=None):
        now = time.time() if now is None else now
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT * FROM alerts WHERE
                (mail_status='pending' AND next_attempt<=?) OR
                (mail_status='sending' AND lease_until<?) ORDER BY id LIMIT 1""", (now, now)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE alerts SET mail_status='sending', attempts=attempts+1,lease_until=? WHERE id=?", (now + 300, row["id"]))
            result = dict(row)
            result["attempts"] += 1
            return result

    def finish_mail(self, item, success, error="", max_attempts=5, now=None):
        now = time.time() if now is None else now
        status = "sent" if success else ("failed" if item["attempts"] >= max_attempts else "pending")
        retry_at = now + min(30 * 2 ** (item["attempts"] - 1), 600)
        with closing(self.connect()) as db, db:
            db.execute("UPDATE alerts SET mail_status=?,last_error=?,sent_at=?,next_attempt=?,lease_until=0 WHERE id=?",
                       (status, error, now if success else None, retry_at, item["id"]))


def compose_mail(item, settings):
    event = json.loads(item["payload"])
    event_time = datetime.fromtimestamp(event["ts"], timezone(timedelta(hours=8)))
    message = EmailMessage()
    prefix = "【模拟演示】" if event.get("is_demo") else ""
    message["Subject"] = f"{prefix}工厂监测 {LEVEL_NAMES[event['level']]} · {event['device']}"
    message["From"] = settings.username
    message["To"] = settings.recipient
    message["Date"] = format_datetime(event_time)
    message["Message-ID"] = f"<factory-{item['event_key']}@{settings.username.split('@')[-1]}>"
    message.set_content("\n".join([
        prefix + "工厂环境监测告警", f"事件编号：{item['id']}",
        f"发生时间：{event_time:%Y-%m-%d %H:%M:%S}（北京时间）", f"设备：{event['device']}",
        f"等级：{LEVEL_NAMES[event['level']]}", f"原因：{event['reason']}",
        f"触发指标：{event['target']}，数值：{event['value']}",
        "", "这是事件发生时的记录。请查看监控页面确认当前状态。",
        "本系统当前用于课程设计演示，阈值为演示配置。",
    ]))
    return message


def send_email(item, settings):
    """QQ 邮箱使用 SMTP_SSL；授权码不进入日志和前端。"""
    context = ssl.create_default_context()
    smtp = smtplib.SMTP_SSL(settings.host, settings.port, timeout=settings.timeout, context=context)
    try:
        smtp.login(settings.username, settings.password)
        refused = smtp.send_message(compose_mail(item, settings))
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    finally:
        # DATA 成功后 QUIT 失败不应造成整封邮件重发。
        try:
            smtp.quit()
        except (OSError, smtplib.SMTPException):
            smtp.close()


def deliver_one(store, settings, sender=send_email, now=None):
    if not settings.enabled:
        return False
    item = store.claim_mail(now)
    if not item:
        return False
    if json.loads(item["payload"]).get("is_demo") and not settings.allow_demo:
        with closing(store.connect()) as db, db:
            db.execute("UPDATE alerts SET mail_status='demo_blocked',lease_until=0 WHERE id=?", (item["id"],))
        return True
    try:
        sender(item, settings)
    except Exception as exc:
        # 不记录 SMTP 的原始响应正文，避免把地址、授权码等带入日志/API。
        error = type(exc).__name__
        if isinstance(getattr(exc, "smtp_code", None), int):
            error += f" ({exc.smtp_code})"
        store.finish_mail(item, False, error, settings.max_attempts, now)
        LOGGER.warning("事件 %s 邮件发送失败：%s", item["id"], error)
    else:
        store.finish_mail(item, True, now=now)
        LOGGER.info("事件 %s 已提交到邮件服务器", item["id"])
    return True
