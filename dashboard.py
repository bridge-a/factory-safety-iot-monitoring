"""工厂安全生产监控：单进程 Flask 页面与独立后台工作线程。"""
import atexit
import logging
import os
import secrets
import time
import math
import csv
import io
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, render_template, request, Response, stream_with_context
from werkzeug.serving import WSGIRequestHandler
from alert_rule import METRICS
from runtime import MonitorRuntime, MODES, SCENARIOS
import settings


class PreviewRequestHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        # Avoid ten successful JPEG access-log lines per viewer per second.
        # Failed frame requests and all other routes keep their usual logging.
        if str(code) == "200" and self.path.split("?", 1)[0] == "/api/camera/frame":
            return
        super().log_request(code, size)


def create_app(runtime=None, start_runtime=False):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 4096
    runtime = runtime or MonitorRuntime()
    app.extensions["monitor"] = runtime
    token = secrets.token_urlsafe(32)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/")
    def index():
        return page_view("overview")

    def page_view(page):
        titles = {"overview": "监控总览", "history": "历史趋势", "alerts": "告警记录",
                  "vision": "明火检测", "devices": "设备管理", "simulation": "模拟与联调"}
        return render_template("dashboard.html", metrics=METRICS, modes=MODES, scenarios=SCENARIOS,
                               control_token=token, page=page, page_title=titles[page], titles=titles)

    for page in ("history", "alerts", "vision", "devices", "simulation"):
        app.add_url_rule("/" + page, "page_" + page, lambda page=page: page_view(page))

    @app.get("/api/status")
    def status():
        return jsonify(dict(runtime.snapshot(), thresholds=METRICS))

    @app.get("/api/camera/frame")
    def camera_frame():
        frame = runtime.camera.frame()
        if frame is None:
            return jsonify(error=runtime.camera.snapshot()["detail"]), 503
        jpeg, sequence = frame
        return Response(jpeg, mimetype="image/jpeg", headers={"X-Camera-Sequence": str(sequence)})

    def history_range():
        mode = request.args.get("mode", runtime.mode)
        if mode not in MODES:
            raise ValueError("未知数据模式")
        if request.args.get("from") or request.args.get("to"):
            since = float(request.args["from"])
            until = float(request.args["to"])
        else:
            minutes = int(request.args.get("minutes", "10"))
            if minutes not in {10, 30, 60, 360, 1440}:
                raise ValueError("不支持的时间范围")
            until = time.time()
            since = until - minutes * 60
        if not all(math.isfinite(value) for value in (since, until)) or not 0 < until - since <= 86400:
            raise ValueError("查询时间须有效，单次最多 24 小时")
        return since, until, mode

    @app.get("/api/history")
    def history():
        try:
            since, until, mode = history_range()
        except (ValueError, KeyError):
            return jsonify(error="查询时间须有效，单次最多 24 小时"), 400
        return jsonify(points=runtime.env.history(since, mode, until=until), mode=mode,
                       statistics=runtime.env.statistics(since, until, mode), since=since, until=until)

    @app.get("/api/history/export")
    def export_history():
        try:
            since, until, mode = history_range()
        except (ValueError, KeyError):
            return jsonify(error="查询时间须有效，单次最多 24 小时"), 400
        def generate():
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["时间（北京时间）", "模式", "温度°C", "湿度%RH", "CO2 ppm", "烟雾ADC",
                             "温度来源", "湿度来源", "CO2来源", "烟雾来源"])
            yield "\ufeff" + output.getvalue()
            for sample in runtime.env.rows(since, until, mode):
                output.seek(0)
                output.truncate()
                values = [datetime.fromtimestamp(sample["ts"], timezone(timedelta(hours=8))).isoformat(),
                          sample["mode"], *[sample.get(key) for key in METRICS],
                          *[sample.get("sources", {}).get(key, "") for key in METRICS]]
                # Prevent spreadsheet formula execution for any future user supplied field.
                values = ["'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value
                          for value in values]
                writer.writerow(values)
                yield output.getvalue()
        return Response(stream_with_context(generate()), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="factory-history.csv"'})

    @app.get("/api/alerts")
    def query_alerts():
        try:
            page = int(request.args.get("page", "1"))
            mode = request.args.get("mode", "")
            level = request.args.get("level", "")
            source = request.args.get("source", "")
            query = request.args.get("q", "").strip()
            since = float(request.args["from"]) if request.args.get("from") else None
            until = float(request.args["to"]) if request.args.get("to") else None
            if page < 1 or mode not in {"", *MODES} or level not in {"", "red", "yellow", "green"}:
                raise ValueError()
            if source not in {"", "environment", "smoke", "vision"} or len(query) > 80:
                raise ValueError()
            if any(value is not None and not math.isfinite(value) for value in (since, until)):
                raise ValueError()
            if since is not None and until is not None and since >= until:
                raise ValueError()
        except (ValueError, KeyError):
            return jsonify(error="请检查告警筛选条件"), 400
        return jsonify(runtime.alerts.query_events(page=page, mode=mode, level=level, source=source,
                                                  query=query, since=since, until=until))

    @app.get("/api/notification_events")
    def events():
        raw = request.args.get("after")
        try:
            after = int(raw) if raw is not None else None
            if after is not None and after < 0:
                raise ValueError()
        except ValueError:
            return jsonify(error="无效事件游标"), 400
        return jsonify(runtime.alerts.list_events(after=after))

    @app.get("/api/notification_status")
    def notification_status():
        return jsonify(runtime.alerts.status())

    @app.post("/api/simulation")
    def simulation():
        if not secrets.compare_digest(request.headers.get("X-Control-Token", ""), token):
            return jsonify(error="页面已过期，请刷新后再操作"), 403
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not data or set(data) - {"mode", "scenario"}:
            return jsonify(error="请提交运行模式或烟雾场景"), 400
        if any(not isinstance(value, str) for value in data.values()):
            return jsonify(error="模式和场景必须是文本"), 400
        try:
            runtime.configure(**data)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(runtime.snapshot())

    if start_runtime:
        runtime.start()
        atexit.register(runtime.stop)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = create_app(start_runtime=os.environ.get("FACTORY_DISABLE_RUNTIME") != "1")
    app.run(host=settings.WEB_HOST, port=settings.WEB_PORT, threaded=True, use_reloader=False,
            request_handler=PreviewRequestHandler)
