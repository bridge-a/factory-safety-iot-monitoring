"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const page = document.body.dataset.page;
  const set = (id, value) => {if ($(id)) $(id).textContent = value;};
  const levels = {green:"正常",yellow:"黄色预警",red:"红色报警",gray:"数据待接入"};
  const mails = {disabled:"未启用",demo_blocked:"模拟邮件关闭",pending:"待发送",sending:"发送中",sent:"已提交",failed:"发送失败",not_required:"无需发送"};
  const modes = {hybrid:"真实环境＋模拟烟雾",demo:"全模拟演示",live:"全部真实采集"};
  const sources = {simulation:"模拟数据",mqtt:"实际采集",missing:"待接入"};
  const types = {environment:"环境异常",smoke:"烟雾异常",vision:"明火异常"};
  const scenarios = {normal:"正常采样",rising:"逐渐升高",warning:"黄色预警",alarm:"红色报警",recovery:"恢复测试",disconnected:"烟雾断线"};
  const token = document.querySelector('meta[name="control-token"]').content;
  let status = null, thresholds = {}, currentMode = null, eventCursor = null, eventStream = null;
  let minutes = 10, selectedHistoryMode = null, customRange = null, historyWindow = null, historyPoints = [], stats = null;
  let chart = null, historyVersion = 0, lastHistoryAt = 0, lastPageEventsAt = 0;
  let loadedHistoryQuery = null, historyState = "loading", historyExportBusy = false;
  let controlBusy = false, controlVersion = 0, notificationsOnline = true, eventPage = 1, eventPages = 1, queryVersion = 0;
  let appliedAlertParams = new URLSearchParams();
  let deviceQueueVersion = 0, deviceCopyBusy = false, deviceCopyTimer = null;
  try {const saved=JSON.parse(sessionStorage.getItem("factory-event-cursor")); if(saved && Number.isInteger(saved.cursor)){eventCursor=saved.cursor;eventStream=saved.stream;}} catch (_) {}
  const timeText = (ts, full=false) => ts ? new Date(ts*1000).toLocaleString("zh-CN",{
    ...(full?{year:"numeric",month:"2-digit",day:"2-digit"}:{}),hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false
  }) : "--:--:--";
  const numberText = (value, key) => value == null ? "--" : Number(value).toFixed(["temp_c","rh"].includes(key)?1:0);
  const node = (tag, text, cls) => {const el=document.createElement(tag);el.textContent=text;if(cls)el.className=cls;return el;};
  function saveCursor(){try{sessionStorage.setItem("factory-event-cursor",JSON.stringify({cursor:eventCursor,stream:eventStream}));}catch(_){}}
  async function api(path, options={}) {
    const response=await fetch(path,{...options,signal:AbortSignal.timeout(9000)});
    if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.error || "请求失败，请重试");}
    return response.json();
  }
  function connection(text, state) {
    if (!$("connection")) return;
    $("connection").className="connection "+state;
    $("connection").replaceChildren(node("i","","dot "+state),document.createTextNode(text));
  }
  function renderRiskIndicator(level, label, description){
    const indicator=$("riskIndicator");if(!indicator)return;
    indicator.dataset.level=level;
    if($("riskIndicatorText").textContent!==label)set("riskIndicatorText",label);
    indicator.title=description;
  }
  function renderVisionIndicator(vision){
    const indicator=$("visionIndicator");if(!indicator)return;
    const pending=vision?.state==="pending",label=pending?"明火检测未启用":"明火检测状态未知";
    indicator.dataset.state=pending?"pending":"unknown";
    if($("visionIndicatorText").textContent!==label)set("visionIndicatorText",label);
    indicator.title=pending?"尚未运行视觉检测，点击查看检测状态":"暂时无法确认视觉检测状态，点击查看";
  }
  function renderOverviewSound(sound){
    const ready=sound?.state==="ready",level=ready?sound.effective_level:"gray";
    const label=ready?({green:"待命",yellow:"预警模式",red:"报警模式",gray:"采样待恢复"}[level]||"状态未知"):
      sound?.state==="offline"?"设备离线":sound?.state==="pending"?"待联调":"状态未知";
    for(const id of ["overviewBuzzerState","overviewLedState"]){
      const el=$(id);if(!el)continue;
      el.textContent=label;el.classList.toggle("muted",!ready||level==="gray");
      el.style.color=({green:"var(--teal)",yellow:"var(--yellow)",red:"var(--red)"})[level]||"var(--muted)";
      el.title=ready?"设备上报的控制模式；实际声光动作需现场确认":"等待有效设备状态";
    }
  }
  function markOffline(message="后台连接中断") {
    connection(message,"red");
    renderOverviewSound(null);set("linkageNote","后台连接中断，声光状态未知。");
    renderVisionIndicator(null);
    if(page==="vision"){renderVisionStatus(null);renderVisionEvents(null);}
    setHistoryAuto("error", "连接中断");
    renderRiskIndicator("gray","状态未知",message+"，无法确认当前环境风险");
    ["espStatus","mqttStatus","databaseStatus","mailStatus"].forEach(id=>set(id,"未知"));
    set("riskLabel","状态未知");if($("riskLabel"))$("riskLabel").style.color="var(--muted)";
    set("riskCardLevel","状态未知");set("riskReason","后台采样不可用，请检查连接");
    set("riskAdvice","处置提示：恢复数据连接后重新判断");set("riskTime","--:--:--");
    if($("riskCard"))$("riskCard").className="current-risk gray";
    set("popupState","连接异常");set("mailLinkState","状态未知");set("simValue","--");set("simState","等待后台恢复");
    set("simSamplingStatus","连接中断");
    if($("simIndicator"))$("simIndicator").style.width="0%";
    if($("simReadingPanel"))$("simReadingPanel").dataset.level="gray";
    if(page==="simulation"){
      $("simulationWorkspace").dataset.state="error";$("simulationWorkspace").setAttribute("aria-busy","false");
      set("simFeedback",message+"，恢复连接后可继续联调。");set("simUpdated","连接中断");$("simUpdated").dataset.state="error";
      set("scenarioLabel","状态未知");set("simModeHint","暂时无法确认运行模式");$("modeSelect").value="";
      set("simMailNote","连接恢复后重新确认通知状态");set("simSoundState","状态未知");
      set("controlHint","后台连接中断，测试场景暂不可用。");
      ["popupState","mailLinkState"].forEach(id=>$(id).dataset.state="error");
      document.querySelectorAll("[data-scenario]").forEach(button=>{button.classList.remove("selected");button.setAttribute("aria-pressed","false");});
      updateSimulationControls();
    }
    document.querySelectorAll("[data-metric]").forEach(card=>{
      card.dataset.level="gray";card.querySelector('[data-role="value"]').textContent="--";
      card.querySelector('[data-role="state"]').textContent="状态未知";
      card.querySelector('[data-role="state"]').className="metric-state gray";
      card.querySelector('[data-role="source"]').textContent="连接中断";
      card.querySelector('[data-role="source"]').className="source-tag";
      card.querySelector(".gauge-progress").style.strokeDasharray="0 100";
      card.querySelector('[data-role="time"]').textContent="--:--:--";
    });
    if(page==="devices"){
      $("devicesWorkspace").dataset.state="error";$("devicesWorkspace").setAttribute("aria-busy","false");
      setDeviceState("deviceEsp","状态未知");setDeviceState("deviceMqttState","未知");setDeviceState("deviceMail","未知");
      ["deviceName","deviceReceived","deviceDemoMail"].forEach(id=>set(id,"—"));setDeviceTopic(null);
      set("deviceMqtt","连接恢复后更新通信状态");set("devicesFeedback",message+"，请检查后台连接后刷新。");
      set("deviceUpdated","连接中断");$("deviceUpdated").dataset.state="error";
      document.querySelectorAll("[data-device-reading]").forEach(el=>{el.dataset.state="unknown";el.querySelector("[data-device-value]").textContent="—";});
      document.querySelectorAll("[data-device-source]").forEach(el=>{el.dataset.state="unknown";el.textContent="状态未知";});
      setDeviceState("deviceVisionState","状态未知");setDeviceState("deviceSoundState","状态未知");
      set("deviceDataNote","设备配置保留；连接恢复后重新读取状态和数值。");
      deviceQueueVersion++;renderDeviceQueue(null);
    }
  }
  function renderStatus(data) {
    status=data;thresholds=data.thresholds || thresholds;
    if(currentMode!==data.mode) {
      currentMode=data.mode;lastPageEventsAt=0;
      if(page==="overview" || !selectedHistoryMode){selectedHistoryMode=data.mode;historyPoints=[];stats=null;historyVersion++;lastHistoryAt=0;}
      if($("historyMode") && !$("historyMode").dataset.initialized){$("historyMode").value=selectedHistoryMode||data.mode;$("historyMode").dataset.initialized="1";}
    }
    set("modeLabel",modes[data.mode]);
    set("modeDescription",data.mode==="demo"?"所有数值均为模拟，仅用于功能演示":data.mode==="hybrid"?"烟雾模拟；其余指标以实际上报为准":"仅显示有效的实际传感器读数");
    if($("modeSelect"))$("modeSelect").value=data.mode;
    set("scenarioLabel",scenarios[data.scenario]);
    document.querySelectorAll("[data-scenario]").forEach(button=>{
      button.classList.toggle("selected",data.mode!=="live"&&button.dataset.scenario===data.scenario);
      button.setAttribute("aria-pressed",String(data.mode!=="live"&&button.dataset.scenario===data.scenario));
      button.disabled=data.mode==="live"||controlBusy;
    });
    set("controlHint",data.mode==="live"?"真实采集模式下，测试场景不可用；切换模式后可继续模拟。":"点击场景立即应用；关闭网页后，后台仍持续采样。");
    if(!data.sample || !data.runtime_online){markOffline(data.error || "等待后台采样");return;}
    renderVisionIndicator(data.vision);
    if(page==="vision")renderVisionStatus(data.vision);
    const sample=data.sample;
    renderRiskIndicator(sample.level,sample.level==="gray"?"数据不完整":levels[sample.level],
      "当前监测状态，依据"+(modes[data.mode]||data.mode)+"数据。"+
      (sample.missing?.length?"缺少有效数据："+sample.missing.join("、"):"各环境指标均有有效读数"));
    connection(notificationsOnline?"后台运行中":"告警读取异常",notificationsOnline?"green":"red");
    set("espStatus",sample.esp32_online?"在线":"未连接");set("mqttStatus",data.mqtt.connected?"已连接":"未连接");
    set("databaseStatus","正常");
    const mailText=data.mail.config_error?"配置有误":data.mail.enabled?"已启用":"未启用";
    set("mailStatus",mailText);set("mailLinkState",mailText);
    set("popupState",notificationsOnline?"监听中":"读取异常");
    renderOverviewSound(data.sound_light);
    set("linkageNote",data.mail.enabled&&!data.mail.allow_demo?"模拟事件的邮件通知已关闭。":data.sound_light?.state==="ready"?"声光状态由设备上报；后台持续监测。":"后台持续监测；等待声光固件上报。");
    set("riskLabel",levels[sample.level]);if($("riskLabel"))$("riskLabel").style.color=({red:"var(--red)",yellow:"var(--yellow)",green:"var(--teal)",gray:"var(--muted)"})[sample.level];
    set("envDevice",data.mode==="demo"?"采集节点：全模拟演示":sample.hardware_device || "采集节点：待接入");
    document.querySelectorAll("[data-metric]").forEach(card=>{
      const key=card.dataset.metric,value=sample[key],source=sample.sources[key],level=sample.channels[key].level;
      card.dataset.level=level;card.querySelector('[data-role="value"]').textContent=numberText(value,key);
      const tag=card.querySelector('[data-role="source"]');tag.textContent=sources[source];tag.className="source-tag "+source;
      const state=card.querySelector('[data-role="state"]');state.textContent=value==null?"等待有效数据":level==="green"?"正常":level==="red"?"报警":"预警";state.className="metric-state "+level;
      card.querySelector('[data-role="time"]').textContent=value==null?"--:--:--":timeText(sample.ts);
      const ratio=value==null?0:Math.max(0,Math.min(100,(value-Number(card.dataset.min))/(Number(card.dataset.max)-Number(card.dataset.min))*100));
      card.querySelector(".gauge-progress").style.strokeDasharray=ratio+" 100";
    });
    if($("riskCard")){
      $("riskCard").className="current-risk "+sample.level;
      set("riskCardLevel",levels[sample.level]);set("riskTime",timeText(sample.ts));
      const abnormal=Object.entries(sample.channels).filter(([,v])=>["red","yellow"].includes(v.level));
      set("riskSource",abnormal.length?abnormal.map(([key])=>thresholds[key].label).join("、"):sample.missing.length?"数据有效性":"环境监测");
      set("riskReason",abnormal.length?abnormal.map(([,v])=>v.reason).join("；"):sample.missing.length?sample.missing.join("、")+"暂无有效数据":"当前有效数据在演示阈值范围内");
      set("riskAdvice",abnormal.length?"处置提示：核查异常来源与现场情况。":sample.missing.length?"处置提示：检查传感器与数据连接。":"处置提示：继续监测环境变化。");
    }
    set("simValue",numberText(sample.smoke_raw,"smoke_raw"));
    set("simState",sample.smoke_raw==null?"烟雾数据无效":(sources[sample.sources.smoke_raw]||"")+" · "+levels[sample.channels.smoke_raw.level]);
    if(page==="simulation"){
      renderSimulationStatus(data);
      set("simSamplingStatus",sample.smoke_raw==null?"数据待接入":"后台采样");
      set("simWarning",thresholds.smoke_raw?.yellow ?? "--");set("simAlarm",thresholds.smoke_raw?.red ?? "--");
      $("simReadingPanel").dataset.level=sample.channels.smoke_raw.level;
      $("simIndicator").style.width=(sample.smoke_raw==null?0:Math.max(0,Math.min(100,sample.smoke_raw/4095*100)))+"%";
    }
    if(page==="devices")renderDevices(data);
  }
  function updateSimulationControls(){
    if(page!=="simulation")return;
    const unavailable=controlBusy||$("simulationWorkspace").dataset.state==="error";
    $("modeSelect").disabled=unavailable;
    document.querySelectorAll("[data-scenario]").forEach(button=>button.disabled=unavailable||currentMode==="live");
  }
  function renderSimulationStatus(data){
    $("simulationWorkspace").dataset.state="ready";$("simulationWorkspace").setAttribute("aria-busy","false");
    set("simFeedback","");set("simUpdated","自动更新 · "+timeText(data.sample.ts));$("simUpdated").dataset.state="ready";
    set("scenarioLabel",data.mode==="live"?"真实采集中":"当前 · "+(scenarios[data.scenario]||"未知"));
    set("simModeHint",{demo:"全部传感器使用模拟读数",hybrid:"烟雾模拟，其他指标由设备上报",live:"仅显示有效的实际采集读数"}[data.mode]||"数据来源未知");
    $("popupState").dataset.state=notificationsOnline?"ready":"error";
    $("mailLinkState").dataset.state=data.mail.config_error?"error":data.mail.enabled?"ready":"unknown";
    set("simMailNote",data.mail.config_error?"邮件配置有误，请在主机上检查。":!data.mail.enabled?"邮件未启用；网页弹窗可独立验证。":data.mode!=="live"&&!data.mail.allow_demo?"当前不发送模拟事件邮件。":"邮件发送结果请在告警详情中查看。");
    set("simSoundState",data.sound_light?.state==="ready"?data.sound_light.detail:data.sound_light?.state==="offline"?"设备离线":data.sound_light?.state==="pending"?"待联调":"状态未知");
    $("simSoundState").dataset.state=data.sound_light?.state==="ready"?"ready":"unknown";
    updateSimulationControls();
  }
  function renderVisionStatus(vision) {
    const pending=vision?.state==="pending";
    $("visionStatus").dataset.state=pending?"pending":"unknown";
    $("cameraScreen").dataset.state=pending?"pending":"unknown";
    set("visionStateLabel",pending?"检测未启用":"检测状态未知");
    set("visionStateDescription",pending?"尚未运行视觉检测，当前无法判断明火状态。":"暂时无法确认检测状态，请刷新重试。");
    set("visionCameraState",pending?"待接入":"未知");set("visionModelState",pending?"待部署":"未知");
    set("cameraAvailability",pending?"摄像头待接入":"状态未确认");
    set("cameraEmptyDescription",pending?"USB 摄像头尚未接入":"暂未获取现场画面");
    set("visionUpdated",vision?"更新于 "+timeText(Date.now()/1000):"状态读取失败");
    $("visionUpdated").dataset.error=String(!vision);
  }
  function renderVisionEvents(data) {
    const events=data?.events?.slice(0,4)||[],state=!data?"error":events.length?"ready":"empty";
    $("visionEvents").dataset.state=state;$("visionEvents").setAttribute("aria-busy","false");
    $("visionEventsState").hidden=state==="ready";$("visionEventList").hidden=state!=="ready";
    $("visionEvidenceNote").hidden=state!=="ready";
    set("visionEventSummary",data?"全部模式 · "+(data.total?"最近 "+events.length+" 条 / 共 "+data.total+" 条":"0 条记录"):"记录读取失败");
    set("visionEventsStateTitle",state==="error"?"明火事件读取失败":"暂无明火事件");
    set("visionEventsStateDescription",state==="error"?"请检查后台连接，点击右上角刷新重试。":"检测启用后，触发的明火事件将在这里显示。");
    $("visionEventList").replaceChildren(...events.map(event=>{
      const item=node("li",""),when=node("time",timeText(event.ts)),copy=node("div","","vision-event-copy");
      when.dateTime=new Date(event.ts*1000).toISOString();
      when.append(node("small",new Date(event.ts*1000).toLocaleDateString("zh-CN",{year:"numeric",month:"2-digit",day:"2-digit"})));
      copy.append(node("p",event.reason),node("span","#"+event.id+" · "+(event.is_demo?"模拟数据":"实际采集")+" · "+(modes[event.mode]||event.mode)));
      item.append(when,copy,node("span",event.level==="green"?"恢复记录":levels[event.level]||"状态未知","vision-event-level "+event.level));
      return item;
    }));
  }
  function setDeviceState(id,text,state="unknown"){
    const el=$(id);if(!el)return;el.dataset.state=state;
    if(el.textContent!==text)el.textContent=text;
  }
  function setDeviceTopic(topic){
    const value=typeof topic==="string"?topic.trim():"",el=$("deviceMqttTopic");
    if(el.textContent!==(value||"—")){
      el.textContent=value||"—";clearTimeout(deviceCopyTimer);set("deviceCopyFeedback","");
    }
    $("copyDeviceTopic").disabled=!value||deviceCopyBusy;
  }
  async function copyDeviceTopic(){
    const button=$("copyDeviceTopic"),el=$("deviceMqttTopic"),topic=el.textContent;
    if(button.disabled)return;
    deviceCopyBusy=true;button.disabled=true;clearTimeout(deviceCopyTimer);set("deviceCopyFeedback","");
    let copied=false;
    try{
      if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(topic);copied=true;}
    }catch(_){}
    if(!copied && el.textContent===topic){
      // Some local HTTP deployments only support copying the current selection.
      const selection=window.getSelection(),range=document.createRange();
      range.selectNodeContents(el);selection.removeAllRanges();selection.addRange(range);
      try{copied=document.execCommand("copy");}catch(_){}
      if(copied)selection.removeAllRanges();
    }
    deviceCopyBusy=false;button.disabled=el.textContent==="—";
    if(el.textContent!==topic)return;
    set("deviceCopyFeedback",copied?"已复制":"自动复制失败，请手动复制上方主题。");
    $("deviceCopyFeedback").dataset.state=copied?"success":"error";
    if(copied)deviceCopyTimer=setTimeout(()=>set("deviceCopyFeedback",""),2500);
  }
  function renderDevices(data) {
    const sample=data.sample;
    $("devicesWorkspace").dataset.state="ready";$("devicesWorkspace").setAttribute("aria-busy","false");
    set("devicesFeedback","");set("deviceUpdated","自动更新 · "+timeText(sample.ts));$("deviceUpdated").dataset.state="ready";
    setDeviceState("deviceEsp",sample.esp32_online?"在线":sample.received_at?"离线":"未接入",sample.esp32_online?"ready":"unknown");
    set("deviceName",sample.hardware_device||"等待设备上报");
    set("deviceReceived",sample.received_at?timeText(sample.received_at,true):"尚无上报");
    setDeviceState("deviceMqttState",data.mqtt.connected?"已连接":"未连接",data.mqtt.connected?"ready":"pending");
    set("deviceMqtt",data.mqtt.detail);setDeviceTopic(data.mqtt.topic);
    setDeviceState("deviceMail",data.mail.config_error?"配置有误":data.mail.enabled?"已启用":"未启用",data.mail.config_error?"error":data.mail.enabled?"ready":"unknown");
    set("deviceDemoMail",data.mail.allow_demo?"允许发送":"不发送");
    document.querySelectorAll("[data-device-reading]").forEach(el=>{
      const key=el.dataset.deviceReading,value=sample[key];
      const source=key==="lux"?(data.mode==="demo"?"simulation":value==null?"missing":"mqtt"):sample.sources[key];
      el.dataset.state=value==null?"missing":source;
      el.querySelector("[data-device-value]").textContent=value==null?"—":key==="lux"?Number(value).toFixed(1):numberText(value,key);
      const sourceEl=document.querySelector('[data-device-source="'+key+'"]');
      sourceEl.dataset.state=value==null?"missing":source;
      sourceEl.textContent=value==null?(source==="simulation"?"模拟 · 无数据":"无有效数据"):sources[source]||"来源未知";
    });
    setDeviceState("deviceVisionState",data.vision?.state==="pending"?"待接入":"状态未知",data.vision?.state==="pending"?"pending":"unknown");
    setDeviceState("deviceSoundState",data.sound_light?.state==="ready"?data.sound_light.detail:data.sound_light?.state==="offline"?"设备离线":data.sound_light?.state==="pending"?"待联调":"状态未知",data.sound_light?.state==="ready"?"mqtt":data.sound_light?.state==="pending"?"pending":"unknown");
    set("deviceDataNote",data.mode==="demo"?"模拟读数仅用于功能演示与联调，不代表硬件已连接。":data.mode==="hybrid"?"烟雾使用模拟读数，其余指标以有效的实际上报为准。":"当前显示实际采集读数；缺失或过期值保留为空。");
  }
  function renderDeviceQueue(data){
    const counts=data?.mail_counts;
    for(const [id,key] of [["queuePending","pending"],["queueSending","sending"],["queueSent","sent"],["queueFailed","failed"]])set(id,counts?(counts[key]||0):"—");
    $("queueFailed").dataset.failed=String(Boolean(counts?.failed));
    set("deviceQueueMessage",counts?"":"邮件队列读取失败，请刷新重试。");
  }
  function historyParams(){
    const params=new URLSearchParams({mode:selectedHistoryMode||currentMode||"hybrid"});
    if(customRange){params.set("from",customRange.from);params.set("to",customRange.to);}else params.set("minutes",minutes);
    return params;
  }
  function selectedMetric(){return $("chartMetric")?.value || "smoke_raw";}
  function setHistoryAuto(state, text){
    if(!$("historyAutoState"))return;
    $("historyAutoState").dataset.state=state;set("historyAutoState",text);
  }
  function historyFeedback(text, error=false){
    set("historyMessage",text);if($("historyMessage"))$("historyMessage").dataset.error=String(error);
  }
  function setHistoryState(state){
    if(page!=="history")return;
    historyState=state;$("historyPanel").dataset.state=state;
    $("historyPanel").setAttribute("aria-busy",String(state==="loading"));
    $("historyChartState").hidden=state==="ready";
    $("retryHistory").hidden=state!=="error";
    const messages={loading:["正在读取历史数据","查询结果将在这里显示"],empty:["所选时段暂无采样","可以切换数据模式，或选择其他时间范围。"],
      missing:["该指标暂无有效读数","缺失读数保留为空，其他指标仍可切换查看。"],error:["历史数据读取失败","请检查后台连接，然后重新查询。"],
      chartMissing:["图表组件未加载","刷新页面重试，采样明细和 CSV 导出仍可使用。"]};
    if(messages[state]){set("historyStateTitle",messages[state][0]);set("historyStateDescription",messages[state][1]);}
    $("exportHistory").disabled=historyExportBusy || !stats?.total || ["loading","error"].includes(state);
  }
  function updateHistoryStats(){
    if(!$("statTotal"))return;
    const key=selectedMetric(),rule=thresholds[key];if(!rule)return;
    document.querySelectorAll(".statUnit").forEach(el=>el.textContent=rule.unit);
    set("historyMetricTitle",rule.label);set("historyWarning",rule.yellow);set("historyAlarm",rule.red);
    document.querySelectorAll("[data-history-column]").forEach(el=>el.classList.toggle("is-selected",el.dataset.historyColumn===key));
    if(!stats){["statTotal","statMin","statMax","statAvg"].forEach(id=>set(id,"--"));set("statValid","等待查询");return;}
    const item=stats.metrics[key];
    set("statTotal",stats.total);set("statValid","有效 "+item.valid+" · 缺失 "+(stats.total-item.valid));
    set("statMin",numberText(item.min,key));set("statMax",numberText(item.max,key));set("statAvg",item.avg==null?"--":item.avg.toFixed(1));
  }
  function drawHistoryChart(){
    updateHistoryStats();
    const key=selectedMetric(),rule=thresholds[key];if(!rule)return;
    if(!stats){setHistoryState(historyState);return;}
    if(!window.echarts){setHistoryState("chartMissing");return;}
    if(!chart)chart=echarts.init($("historyChart"));
    const mobile=window.innerWidth<=720;
    chart.setOption({animation:false,textStyle:{fontFamily:'"Microsoft YaHei",sans-serif'},
      grid:{left:mobile?43:53,right:mobile?19:25,top:24,bottom:32},
      tooltip:{trigger:"axis",confine:true,backgroundColor:"#fff",borderColor:"#dfe7e1",textStyle:{color:"#405749",fontSize:12},
        axisPointer:{type:"line",lineStyle:{color:"#a5b8ab",type:"dashed"}},
        valueFormatter:value=>value==null?"无有效数据":numberText(value,key)+" "+rule.unit},
      xAxis:{type:"time",min:historyWindow?.since*1000,max:historyWindow?.until*1000,splitNumber:mobile?3:7,
        axisLine:{lineStyle:{color:"#e4eae4"}},axisTick:{show:false},axisLabel:{color:"#829084",fontSize:11,hideOverlap:true,
          formatter:value=>new Date(value).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit",hour12:false})}},
      yAxis:{type:"value",min:range=>Math.min(0,Math.floor(range.min||0)),max:range=>{
          const step=key==="temp_c"?5:key==="rh"?10:500;return Math.ceil(Math.max(range.max||0,rule.red)*1.1/step)*step;
        },splitNumber:4,
        axisLabel:{color:"#829084",fontSize:10},splitLine:{lineStyle:{color:"#edf0eb",type:"dashed"}}},
      series:[{name:rule.label,type:"line",showSymbol:historyPoints.length<12,symbol:"circle",symbolSize:5,connectNulls:false,
        data:historyPoints.map(row=>[row.ts*1000,row[key]]),lineStyle:{width:2,color:"#32765c"},itemStyle:{color:"#32765c"},
        areaStyle:{color:"#6fa488",opacity:.055},markLine:{silent:true,symbol:"none",label:{show:false},
          data:[{yAxis:rule.yellow,lineStyle:{color:"#bda165",width:1,type:"dashed"}},{yAxis:rule.red,lineStyle:{color:"#cd9a91",width:1,type:"dashed"}}]}}]},true);
    $("historyChart").setAttribute("aria-label",rule.label+"历史折线图，单位 "+rule.unit+"，预警阈值 "+rule.yellow+"，报警阈值 "+rule.red);
    setHistoryState(!stats.total?"empty":stats.metrics[key].valid?"ready":"missing");
  }
  function drawChart(){
    if(!$("historyChart"))return;
    if(page==="history"){drawHistoryChart();return;}
    if(!window.echarts){$("historyChart").textContent="图表组件未加载，请刷新页面";return;}
    if(!chart)chart=echarts.init($("historyChart"));
    const key=selectedMetric(),rule=thresholds[key];if(!rule)return;
    chart.setOption({animation:false,textStyle:{fontFamily:'"Microsoft YaHei",sans-serif'},
      grid:{left:55,right:32,top:25,bottom:28},
      tooltip:{trigger:"axis",valueFormatter:value=>value==null?"无有效数据":Number(value).toFixed(1)+" "+rule.unit},
      xAxis:{type:"time",min:historyWindow?historyWindow.since*1000:undefined,max:historyWindow?historyWindow.until*1000:undefined,axisLine:{lineStyle:{color:"#dce5d4"}},axisTick:{show:false},axisLabel:{color:"#899b7c",fontSize:10,hideOverlap:true,formatter:value=>new Date(value).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit",hour12:false})}},
      yAxis:{type:"value",min:range=>Math.min(0,Math.floor(range.min||0)),max:range=>Math.ceil(Math.max(range.max||0,rule.red)*1.12),name:rule.unit,nameTextStyle:{color:"#99a38f",fontSize:9},axisLabel:{color:"#94a087",fontSize:9},splitLine:{lineStyle:{color:"#e5ebdf",type:"dashed"}}},
      series:[{name:rule.label,type:"line",showSymbol:historyPoints.length<12,symbolSize:4,connectNulls:false,
        data:historyPoints.map(row=>[row.ts*1000,row[key]]),lineStyle:{width:2,color:"#497f61"},itemStyle:{color:"#497f61"},areaStyle:{color:"#a1bd8b",opacity:.10},
        markLine:{silent:true,symbol:"none",label:{fontSize:9,position:"insideEndTop",formatter:params=>params.name},
          data:[{name:"预警 "+rule.yellow,yAxis:rule.yellow,lineStyle:{color:"#c4a56b",width:1,type:"dashed"}},{name:"报警 "+rule.red,yAxis:rule.red,lineStyle:{color:"#d19b8f",width:1,type:"dashed"}}]}}]},true);
    updateHistoryStats();
  }
  async function refreshHistory(){
    if(!$("historyChart") || !currentMode)return;
    const version=++historyVersion,params=historyParams(),query=params.toString();
    lastHistoryAt=Date.now();
    if(page==="history" && query!==loadedHistoryQuery){
      historyPoints=[];stats=null;setHistoryState("loading");updateHistoryStats();
      set("historyRangeText","正在查询…");set("historyCount","正在读取采样…");set("historyUpdated","");
      $("sampleRows").replaceChildren();const tr=node("tr",""),td=node("td","正在读取采样…","empty");td.colSpan=6;tr.append(td);$("sampleRows").append(tr);
    }
    try {
      const data=await api("/api/history?"+params);
      if(version!==historyVersion)return;
      historyPoints=data.points;stats=data.statistics;historyWindow={since:data.since,until:data.until};
      loadedHistoryQuery=query;
      set("historyCount",stats.total+" 条采样 · "+historyPoints.length+" 个展示点");
      set("historyNote",page==="history"?modes[data.mode]+" · 统计与导出使用全部采样，缺失读数不计入平均值。":modes[data.mode]+" · 虚线为演示阈值 · 最多展示 600 个抽样点，统计与导出使用全部采样");
      if(page==="history"){
        set("historyRangeText",timeText(data.since,true)+" — "+timeText(data.until,true)+"（本地时间）");
        set("historyUpdated","更新于 "+timeText(Date.now()/1000));
        set("sampleTableNote","最近 "+Math.min(20,historyPoints.length)+" / "+historyPoints.length+" 个展示点");
        setHistoryAuto("ready",customRange?"固定查询时段":"每 10 秒更新");
        if($("historyMessage").dataset.error==="true")historyFeedback("");
      }
      if($("sampleRows")){
        const rows=historyPoints.slice(-20).reverse().map(sample=>{
          const tr=node("tr",""),date=node("td",""),time=node("span",timeText(sample.ts),"sample-time");
          time.append(node("span",new Date(sample.ts*1000).toLocaleDateString("zh-CN",{year:"numeric",month:"2-digit",day:"2-digit"}),"sample-date"));date.append(time);tr.append(date);
          for(const key of ["temp_c","rh","co2_ppm","smoke_raw"]){
            const td=node("td",sample[key]==null?"—":numberText(sample[key],key));td.dataset.historyColumn=key;td.classList.toggle("is-missing",sample[key]==null);tr.append(td);
          }
          const mode=node("td","");mode.append(node("span",modes[sample.mode],"sample-mode "+sample.mode));tr.append(mode);return tr;
        });
        if(!rows.length){const tr=node("tr",""),td=node("td","所选时段暂无采样数据","empty");td.colSpan=6;tr.append(td);rows.push(tr);}
        $("sampleRows").replaceChildren(...rows);
      }
      drawChart();lastHistoryAt=Date.now();
    }catch(error){
      if(version!==historyVersion)return;
      historyPoints=[];stats=null;drawChart();
      ["statTotal","statMin","statMax","statAvg"].forEach(id=>set(id,"--"));
      set("statValid","读取失败");set("historyCount","历史读取失败");set("historyMessage",error.message);
      set("historyNote","本次查询未成功，请检查连接后重试。");
      if(page==="history"){
        setHistoryState("error");setHistoryAuto("error","查询失败");historyFeedback(error.message,true);
        set("historyRangeText","查询范围未加载");set("historyUpdated","");set("sampleTableNote","未加载");
      }
      if($("sampleRows")){$("sampleRows").replaceChildren();const tr=node("tr",""),td=node("td","本次查询未成功","empty");td.colSpan=6;tr.append(td);$("sampleRows").append(tr);}
    }
  }
  function showDetails(event){
    const body=$("eventDetails");body.replaceChildren();
    if(page==="alerts"){
      const summary=node("section","","detail-summary"),heading=node("div","");
      heading.append(node("span",event.level==="green"?"恢复正常":levels[event.level],"event-level "+event.level),node("span","事件 #"+event.id,"event-id"));
      summary.append(heading,node("h3",event.reason),node("time",timeText(event.ts,true)));body.append(summary);
      const metadata=node("section","","detail-section"),list=node("dl","","event-metadata");
      [["事件类别",types[event.source]||event.source],["运行模式",modes[event.mode]||event.mode],["数据来源",event.is_demo?"模拟演示":"实际采集"]].forEach(([label,value])=>{
        const row=node("div","");row.append(node("dt",label),node("dd",value));list.append(row);
      });metadata.append(node("h3","事件信息"),list);body.append(metadata);
      const snapshot=node("section","","detail-section"),grid=node("div","","snapshot-grid");
      ["temp_c","rh","co2_ppm","smoke_raw"].forEach(key=>{
        const rule=thresholds[key];if(!rule)return;
        const item=node("div","","snapshot-item"),value=node("div","");
        item.dataset.snapshotMetric=key;
        const label=node("span",rule.label,"snapshot-label");
        if(event.target===key && ["red","yellow","green"].includes(event.level)){
          item.classList.add("is-target");item.dataset.eventLevel=event.level;
          label.append(node("span",event.level==="green"?"恢复指标":"触发指标","metric-marker"));
        }
        value.append(node("strong",numberText(event.snapshot[key],key)),node("span",rule.unit,"snapshot-unit"));
        item.append(label,value,node("small",sources[event.snapshot.sources?.[key]]||"未标记来源"));grid.append(item);
      });snapshot.append(node("h3","触发时的采样快照"),grid);body.append(snapshot);
      const delivery=node("section","","detail-section"),deliveryList=node("dl","","event-metadata"),mailRow=node("div","");
      mailRow.append(node("dt","邮件通知"),node("dd",mails[event.mail_status]||event.mail_status));deliveryList.append(mailRow);
      if(event.attempts){const attempt=node("div","");attempt.append(node("dt","投递尝试"),node("dd",event.attempts+" 次"));deliveryList.append(attempt);}
      delivery.append(node("h3","通知结果"),deliveryList);
      if(event.last_error)delivery.append(node("p",event.last_error,"delivery-error"));body.insertBefore(delivery,snapshot);
      $("eventDialog").showModal();return;
    }
    const entries=[["事件编号","#"+event.id],["发生时间",timeText(event.ts,true)],["事件等级",event.level==="green"?"恢复正常":levels[event.level]],
      ["事件内容",event.reason],["运行模式",modes[event.mode]||event.mode],["事件来源",event.is_demo?"模拟演示":"实际采集"],["邮件状态",mails[event.mail_status]||event.mail_status]];
    for(const [key,rule] of Object.entries(thresholds)){
      entries.push([rule.label,numberText(event.snapshot[key],key)+" "+rule.unit+" · "+(sources[event.snapshot.sources?.[key]]||"未标记")]);
    }
    if(event.attempts)entries.push(["邮件尝试次数",event.attempts]);
    if(event.last_error)entries.push(["最近投递错误",event.last_error]);
    entries.forEach(([label,value])=>{const row=node("div","","event-detail");row.append(node("span",label),node("strong",value));body.append(row);});
    $("eventDialog").showModal();
  }
  function toast(event){
    [...$("toasts").children].filter(item=>item.dataset.device===event.device).forEach(item=>item.remove());
    const box=node("div","","toast "+event.level);box.dataset.device=event.device;box.setAttribute("role","alert");
    const head=node("div",""),close=node("button","×");close.setAttribute("aria-label","关闭告警弹窗");close.addEventListener("click",()=>box.remove());
    head.append(node("strong",(event.is_demo?"模拟 · ":"")+(event.level==="green"?"恢复正常":levels[event.level])),close);
    box.append(head,node("p",event.reason),node("small",timeText(event.ts)+" · "+mails[event.mail_status]));
    $("toasts").prepend(box);while($("toasts").children.length>3)$("toasts").lastElementChild.remove();
    if(event.level!=="red")setTimeout(()=>box.remove(),15000);
  }
  async function refreshNotifications(){
    try{
      const newest=await api("/api/notification_events");
      if(eventStream!==newest.stream_id || eventCursor===null || eventCursor>newest.latest_id){
        eventStream=newest.stream_id;eventCursor=newest.latest_id;saveCursor();
      }else{
        let pages=0;
        while(eventCursor<newest.latest_id && pages++<3){
          const data=await api("/api/notification_events?after="+eventCursor);
          if(data.stream_id!==eventStream){eventCursor=null;break;}if(!data.events.length)break;
          data.events.forEach(event=>{toast(event);eventCursor=Math.max(eventCursor,event.id);});saveCursor();
        }
      }
      notificationsOnline=true;
    }catch(_){notificationsOnline=false;connection("告警读取异常","red");set("popupState","读取异常");}
    if(page==="simulation" && $("simulationWorkspace").dataset.state==="ready"){
      set("popupState",notificationsOnline?"监听中":"读取异常");$("popupState").dataset.state=notificationsOnline?"ready":"error";
    }
  }
  function renderRecent(data){
    if(!$("recentList"))return;
    $("recentList").replaceChildren();
    if(!data.events.length){$("recentList").append(node("div","当前模式暂无告警记录","empty"));return;}
    data.events.slice(0,4).forEach(event=>{
      const item=node("button","","recent-event");item.type="button";
      const head=node("div","");head.append(node("i","","dot "+event.level),node("span",event.level==="green"?"恢复正常":types[event.source]||"告警"),node("span",event.is_demo?"模拟":"实际"),node("time",timeText(event.ts)));
      item.append(head,node("p",event.reason));item.addEventListener("click",()=>showDetails(event));$("recentList").append(item);
    });
  }
  function renderEventTable(data){
    eventPage=data.page;eventPages=data.pages;
    set("eventTotal",data.total);set("eventPageSize",data.page_size);set("pageNumber",data.page+" / "+data.pages);
    const counts=data.level_counts || {};
    [["","alertCountAll"],["red","alertCountRed"],["yellow","alertCountYellow"],["green","alertCountGreen"]].forEach(([level,id])=>{
      const count=data.level_counts?(level?(counts[level]||0):data.scope_total):null;
      set(id,count==null?"--":count);
      document.querySelector('[data-level-filter="'+level+'"]').classList.toggle("is-empty",count===0);
    });
    set("alertUpdated","更新于 "+timeText(Date.now()/1000));
    $("prevPage").disabled=data.page<=1;$("nextPage").disabled=data.page>=data.pages;
    const rows=data.events.map(event=>{
      const tr=node("tr","","event-row"),when=node("td","");
      const timestamp=node("time",timeText(event.ts),"event-time");timestamp.dateTime=new Date(event.ts*1000).toISOString();timestamp.title=timeText(event.ts,true);
      when.append(timestamp,node("small",new Date(event.ts*1000).toLocaleDateString("zh-CN",{year:"numeric",month:"2-digit",day:"2-digit"})));
      const level=node("td","");level.append(node("span",event.level==="green"?"恢复正常":levels[event.level],"event-level "+event.level));
      const reason=node("td","","event-reason");reason.append(node("p",event.reason),node("small",(types[event.source]||event.source)+" · #"+event.id));
      const action=node("td",""),button=node("button","详情","detail-button");button.setAttribute("aria-label","查看事件 "+event.id);
      button.addEventListener("click",()=>showDetails(event));action.append(button);
      const source=node("td",""),mail=node("td","");
      source.append(node("span",event.is_demo?"模拟数据":"实际采集","source-label "+(event.is_demo?"simulation":"mqtt")),node("small",modes[event.mode]||event.mode));
      mail.append(node("span",mails[event.mail_status]||event.mail_status,"mail-state "+(["sent","failed"].includes(event.mail_status)?event.mail_status:"neutral")));
      tr.append(when,level,reason,source,mail,action);
      return tr;
    });
    if(!rows.length){
      const tr=node("tr",""),td=node("td",""),empty=node("div","","empty-state");td.colSpan=6;
      if($("emptyEventIcon"))empty.append($("emptyEventIcon").content.cloneNode(true));
      empty.append(node("strong",appliedAlertParams.size?"没有符合条件的记录":"暂无告警记录"),node("span",appliedAlertParams.size?"调整筛选条件后重新查询。":"异常与恢复事件会在这里保存。"));
      td.append(empty);tr.append(td);rows.push(tr);
    }
    $("eventRows").replaceChildren(...rows);
  }
  async function refreshPageEvents(){
    if(!["overview","alerts","vision"].includes(page))return;
    const version=++queryVersion;
    const params=page==="overview"?new URLSearchParams({mode:currentMode||"hybrid"}):new URLSearchParams(appliedAlertParams);
    params.set("page",page==="alerts"?eventPage:1);
    if(page==="vision")params.set("source","vision");
    try{
      const data=await api("/api/alerts?"+params);if(version!==queryVersion)return;
      if(page==="overview")renderRecent(data);else if(page==="vision")renderVisionEvents(data);else{renderEventTable(data);set("alertMessage","");}
      lastPageEventsAt=Date.now();
    }catch(error){if(version!==queryVersion)return;if(page==="vision")renderVisionEvents(null);set("alertMessage",error.message);if($("recentList"))$("recentList").replaceChildren(node("div","告警记录读取失败","empty"));}
  }
  async function refreshQueue(){
    if(page!=="devices" || $("devicesWorkspace").dataset.state!=="ready")return;
    const version=++deviceQueueVersion;
    try{const data=await api("/api/notification_status");if(version===deviceQueueVersion)renderDeviceQueue(data);}
    catch(_){if(version===deviceQueueVersion)renderDeviceQueue(null);}
  }
  async function control(body){
    if(controlBusy)return;controlBusy=true;controlVersion++;updateSimulationControls();
    const activeRow=body.scenario?document.querySelector('[data-scenario="'+body.scenario+'"]'):null;
    const clearApplying=()=>{if(activeRow){delete activeRow.dataset.applying;activeRow.removeAttribute("aria-busy");}};
    if(activeRow){activeRow.dataset.applying="true";activeRow.setAttribute("aria-busy","true");}
    $("simulationWorkspace").setAttribute("aria-busy","true");$("refreshSimulation").disabled=true;
    set("controlMessage",body.scenario?"正在应用："+scenarios[body.scenario]+"…":"正在切换运行模式…");$("controlMessage").dataset.state="pending";
    try{
      const data=await api("/api/simulation",{method:"POST",headers:{"Content-Type":"application/json","X-Control-Token":token},body:JSON.stringify(body)});
      renderStatus(data);clearApplying();set("controlMessage",body.scenario?"已切换到："+scenarios[data.scenario]:"运行模式已切换为："+modes[data.mode]);$("controlMessage").dataset.state="success";await refreshNotifications();
    }catch(error){
      clearApplying();
      set("controlMessage",error.message);$("controlMessage").dataset.state="error";
      try{renderStatus(await api("/api/status"));}catch(_){markOffline();}
    }
    finally{clearApplying();controlBusy=false;controlVersion++;$("simulationWorkspace").setAttribute("aria-busy","false");$("refreshSimulation").disabled=false;updateSimulationControls();}
  }
  async function poll(){
    const version=controlVersion;
    try{
      const data=await api("/api/status");if(!controlBusy&&version===controlVersion)renderStatus(data);
      await refreshNotifications();
      if(Date.now()-lastPageEventsAt>=4000)await refreshPageEvents();
      if(Date.now()-lastHistoryAt>=10000)await refreshHistory();
      await refreshQueue();
    }catch(_){if(!controlBusy&&version===controlVersion)markOffline();}
    setTimeout(poll,2000);
  }
  function resetHistoryView(){
    minutes=10;customRange=null;historyFeedback("");
    $("historyFrom").value="";$("historyTo").value="";
    updateHistoryRangeControls();
    historyVersion++;refreshHistory();
  }
  function updateHistoryRangeControls(){
    document.querySelectorAll("[data-minutes]").forEach(button=>{
      const active=!customRange && Number(button.dataset.minutes)===minutes;
      button.classList.toggle("selected",active);button.setAttribute("aria-pressed",String(active));
    });
    if($("historyDate")){
      $("historyDate").open=false;$("historyDate").classList.toggle("has-range",Boolean(customRange));
      set("historyDateLabel",customRange?"已选时段":"自选时段");set("historyRangeError","");
      $("historyFrom").removeAttribute("aria-invalid");$("historyTo").removeAttribute("aria-invalid");
    }
  }
  function localInputDate(value){return new Date(value).getTime()/1000;}
  $("modeSelect")?.addEventListener("change",event=>control({mode:event.target.value}));
  document.querySelectorAll("[data-scenario]").forEach(button=>button.addEventListener("click",()=>control({scenario:button.dataset.scenario})));
  document.querySelectorAll("[data-minutes]").forEach(button=>button.addEventListener("click",()=>{
    minutes=Number(button.dataset.minutes);customRange=null;
    if($("historyFrom")){$("historyFrom").value="";$("historyTo").value="";}
    updateHistoryRangeControls();
    historyFeedback("最近 "+minutes+" 分钟，自动更新");refreshHistory();
  }));
  $("chartMetric")?.addEventListener("change",drawChart);
  document.querySelectorAll("[data-history-metric]").forEach(button=>button.addEventListener("click",()=>{
    $("chartMetric").value=button.dataset.historyMetric;
    document.querySelectorAll("[data-history-metric]").forEach(item=>{item.classList.toggle("active",item===button);item.setAttribute("aria-pressed",String(item===button));});
    drawChart();
  }));
  $("historyMode")?.addEventListener("change",event=>{selectedHistoryMode=event.target.value;historyFeedback("");refreshHistory();});
  $("historyRangeForm")?.addEventListener("submit",event=>{
    event.preventDefault();
    const from=localInputDate($("historyFrom").value),to=localInputDate($("historyTo").value);
    if(!Number.isFinite(from)||!Number.isFinite(to)||to<=from||to-from>86400){
      set("historyRangeError","请选择有效起止时间，单次最多 24 小时");
      $("historyFrom").setAttribute("aria-invalid","true");$("historyTo").setAttribute("aria-invalid","true");return;
    }
    customRange={from,to};updateHistoryRangeControls();
    historyFeedback("");refreshHistory();
  });
  $("resetHistory")?.addEventListener("click",resetHistoryView);
  $("refreshHistory")?.addEventListener("click",()=>{historyFeedback("");refreshHistory();});
  $("retryHistory")?.addEventListener("click",()=>{historyFeedback("");refreshHistory();});
  if(page==="history"){
    const picker=$("historyDate"),summary=picker.querySelector("summary");
    picker.addEventListener("toggle",()=>{
      if(!picker.open || $("historyFrom").value || !historyWindow)return;
      const localDate=ts=>{const date=new Date(ts*1000);return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);};
      $("historyFrom").value=localDate(historyWindow.since);$("historyTo").value=localDate(historyWindow.until);
    });
    document.addEventListener("pointerdown",event=>{if(!picker.contains(event.target))picker.open=false;});
    document.addEventListener("keydown",event=>{if(event.key==="Escape" && picker.open){picker.open=false;summary.focus();}});
  }
  $("exportHistory")?.addEventListener("click",async()=>{
    const button=$("exportHistory");historyExportBusy=true;button.disabled=true;
    try{
      const params=historyParams();
      if(page==="history" && historyWindow && loadedHistoryQuery){
        params.delete("minutes");params.set("from",historyWindow.since);params.set("to",historyWindow.until);
        params.set("mode",new URLSearchParams(loadedHistoryQuery).get("mode"));
      }
      const response=await fetch("/api/history/export?"+params,{signal:AbortSignal.timeout(30000)});
      if(!response.ok)throw new Error("导出失败，请检查查询条件");
      const url=URL.createObjectURL(await response.blob()),anchor=document.createElement("a");anchor.href=url;anchor.download="factory-history.csv";anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      historyFeedback("已导出当前查询范围的完整采样");
    }catch(error){historyFeedback(error.message,true);}finally{historyExportBusy=false;if(page==="history")setHistoryState(historyState);else button.disabled=false;}
  });
  function readAlertFilters(){
    const params=new URLSearchParams();
    for(const [id,key] of [["alertMode","mode"],["alertLevel","level"],["alertSource","source"],["alertQuery","q"]]){
      if($(id).value.trim())params.set(key,$(id).value.trim());
    }
    const from=$("alertFrom").value,to=$("alertTo").value;
    if(from)params.set("from",localInputDate(from));if(to)params.set("to",localInputDate(to));
    return params;
  }
  if(page==="alerts"){
    const rules=$("alertRules"),rulesButton=rules.querySelector("summary");
    document.addEventListener("pointerdown",event=>{if(!rules.contains(event.target))rules.open=false;});
    document.addEventListener("keydown",event=>{if(event.key==="Escape" && rules.open){rules.open=false;rulesButton.focus();}});
    function applyAlertFilters(){
      appliedAlertParams=readAlertFilters();eventPage=1;
      document.querySelectorAll("[data-level-filter]").forEach(button=>{
        const active=button.dataset.levelFilter===$("alertLevel").value;
        button.classList.toggle("active",active);button.setAttribute("aria-pressed",String(active));
      });
      const ranged=appliedAlertParams.has("from")||appliedAlertParams.has("to");
      document.querySelector(".date-disclosure").classList.toggle("has-range",ranged);
      set("dateFilterLabel",ranged?"已选时间":"时间范围");
      refreshPageEvents();
    }
    const source=new URLSearchParams(location.search).get("source");
    if(Object.hasOwn(types,source)){$("alertSource").value=source;appliedAlertParams.set("source",source);}
    $("alertFilters").addEventListener("submit",event=>{event.preventDefault();applyAlertFilters();});
    $("resetAlerts").addEventListener("click",()=>{$("alertFilters").reset();$("alertLevel").value="";applyAlertFilters();});
    document.querySelectorAll("[data-level-filter]").forEach(button=>button.addEventListener("click",()=>{$("alertLevel").value=button.dataset.levelFilter;applyAlertFilters();}));
    $("refreshAlerts")?.addEventListener("click",refreshPageEvents);
    $("prevPage").addEventListener("click",()=>{if(eventPage>1){eventPage--;refreshPageEvents();}});
    $("nextPage").addEventListener("click",()=>{if(eventPage<eventPages){eventPage++;refreshPageEvents();}});
  }
  $("refreshVision")?.addEventListener("click",async()=>{
    const button=$("refreshVision");button.disabled=true;set("visionUpdated","正在刷新");
    try{renderStatus(await api("/api/status"));}catch(_){markOffline();}
    await refreshPageEvents();button.disabled=false;
  });
  $("copyDeviceTopic")?.addEventListener("click",copyDeviceTopic);
  $("refreshSimulation")?.addEventListener("click",async()=>{
    const button=$("refreshSimulation"),version=controlVersion;button.disabled=true;
    try{const data=await api("/api/status");if(!controlBusy&&version===controlVersion)renderStatus(data);await refreshNotifications();}
    catch(_){if(!controlBusy&&version===controlVersion)markOffline();}
    finally{button.disabled=controlBusy;}
  });
  $("refreshDevices")?.addEventListener("click",async()=>{
    const button=$("refreshDevices");button.disabled=true;
    try{renderStatus(await api("/api/status"));await refreshQueue();}catch(_){markOffline();}
    finally{button.disabled=false;}
  });
  $("closeDialog").addEventListener("click",()=>$("eventDialog").close());
  window.addEventListener("resize",()=>chart?.resize());
  const clock=()=>{const now=new Date();set("date",now.toLocaleDateString("zh-CN",{year:"numeric",month:"2-digit",day:"2-digit",weekday:"short"}));set("clock",now.toLocaleTimeString("zh-CN",{hour12:false}));};
  clock();setInterval(clock,1000);poll();
})();
