"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const set = (id, text) => { if ($(id)) $(id).textContent = text; };
  const stage = document.querySelector("[data-camera-stage]");
  const image = stage?.querySelector("[data-camera-image]");
  let camera = null, timer = null, controller = null, generation = 0, objectUrl = null, busy = false;
  const textFor = state => ({ready:"已接入",disabled:"未启用",starting:"连接中",reconnecting:"重连中",error:"采集异常",stopped:"已停止"}[state] || "状态未知");

  function display(ready, message, detail = camera?.detail || "暂未获取摄像头状态") {
    if (!stage) return;
    stage.dataset.cameraLive = String(ready);
    stage.setAttribute("aria-label", ready ? "摄像头实时画面" : message);
    stage.querySelectorAll("[data-camera-placeholder]").forEach(el => { el.hidden = ready; });
    image.hidden = !ready;
    stage.querySelectorAll("[data-camera-label]").forEach(el => { el.textContent = ready ? "实时画面" : message; });
    stage.querySelectorAll("[data-camera-detail]").forEach(el => { el.textContent = detail; });
    const caption = document.querySelector("[data-camera-caption]");
    if (caption) caption.textContent = ready ? camera.resolution + " · 自动更新" : "画面未接入";
  }

  function clearFrame() {
    image?.removeAttribute("src");
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
  }

  function schedule(delay = 100) {
    clearTimeout(timer);
    if (image && camera?.state === "ready" && !document.hidden) timer = setTimeout(loadFrame, delay);
  }

  async function loadFrame() {
    if (busy || !image || document.hidden || camera?.state !== "ready") return;
    busy = true;
    const version = generation;
    controller = new AbortController();
    const timeout = setTimeout(() => controller?.abort(), 2500);
    let nextUrl = null, retry = 100;
    try {
      const response = await fetch("/api/camera/frame", {cache:"no-store",signal:controller.signal});
      if (!response.ok || !response.headers.get("content-type")?.startsWith("image/jpeg")) throw new Error("frame unavailable");
      nextUrl = URL.createObjectURL(await response.blob());
      const decoded = new Image();
      decoded.src = nextUrl;
      await decoded.decode();
      if (version !== generation || document.hidden || camera?.state !== "ready") return;
      const oldUrl = objectUrl;
      image.src = nextUrl; objectUrl = nextUrl; nextUrl = null;
      if (oldUrl) URL.revokeObjectURL(oldUrl);
      display(true, "");
    } catch (_) {
      if (version === generation) {
        display(false, "画面中断，正在重连", "暂时未收到有效画面，正在自动重试");
        clearFrame();
      }
      retry = 1000;
    } finally {
      if (nextUrl) URL.revokeObjectURL(nextUrl);
      clearTimeout(timeout); controller = null; busy = false;
      schedule(retry);
    }
  }

  function update(value) {
    camera = value;
    const ready = camera?.state === "ready", label = textFor(camera?.state);
    set("cameraStatus", label); set("visionCameraState", label); set("deviceVisionState", label);
    if ($("cameraStatus")) $("cameraStatus").classList.toggle("muted", !ready);
    if ($("deviceVisionState")) $("deviceVisionState").dataset.state = ready ? "mqtt" : "unknown";
    if (!ready) {
      generation++; clearTimeout(timer); controller?.abort();
      display(false, label); clearFrame();
    } else if (image && !busy) {
      if (stage.dataset.cameraLive !== "true") display(false, "正在加载画面", "正在读取摄像头画面");
      schedule(0);
    }
  }
  window.FactoryCamera = {update};
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      generation++; clearTimeout(timer); controller?.abort(); display(false,"画面已暂停"); clearFrame();
    } else { update(camera); }
  });
  window.addEventListener("pagehide", () => {
    camera = null; generation++; clearTimeout(timer); controller?.abort(); clearFrame();
  });
})();
