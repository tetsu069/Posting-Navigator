const configured=(window.POSTING_NAVIGATOR_API||'').replace(/\/$/,'');
const API=configured || location.origin;
const $=id=>document.getElementById(id);
const state={uploadId:null,jobId:null,geojson:null,areaGeojson:null,summary:null,projectId:null,shareCode:null,workerId:1,watchId:null,current:null,completed:new Set(),segments:[],segmentLengths:[],layers:{},syncTimer:null,config:{gps_threshold_m:18,sync_interval_ms:5000},areaInfo:{},fieldGuideLeg:0,lastPosition:null,sessionToken:localStorage.getItem('pn_session')||'',user:null,navLegs:[],startPickMode:false,activeGuideLeg:0};
const map=L.map('map').setView([35.7005,139.6925],16);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:20,attribution:'© OpenStreetMap contributors'}).addTo(map);

function status(id,text,type=''){const el=$(id);el.textContent=text;el.className='status'+(type?' '+type:'')}
function authHeaders(extra={}){return state.sessionToken?{...extra,Authorization:`Bearer ${state.sessionToken}`} : extra}
async function api(path,opts={}){const r=await fetch(`${API}${path}`,{...opts,headers:authHeaders(opts.headers||{})});let j={};try{j=await r.json()}catch{}if(!r.ok)throw Error(j.error||`${r.status} ${r.statusText}`);return j}
function activateTab(name){document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x.dataset.tab===name));document.querySelectorAll('.tabpane').forEach(x=>x.classList.toggle('active',x.id===`tab-${name}`));setTimeout(()=>map.invalidateSize(),50)}
document.querySelectorAll('.tab').forEach(x=>x.onclick=()=>{activateTab(x.dataset.tab);if(x.dataset.tab==='field'&&state.geojson){const wid=+(($('fieldWorker')?.value)||state.workerId||1);loadWorker(wid)}});

async function loadConfig(){try{state.config=await api('/api/config');$('gpsThreshold').value=state.config.gps_threshold_m||18;$('gpsThresholdText').textContent=$('gpsThreshold').value;if(state.sessionToken){try{const me=await api('/api/auth/me');state.user=me.user}catch{state.sessionToken='';localStorage.removeItem('pn_session')}}setupGoogleLogin();if(state.user)loadMyProjects()}catch(e){status('status',`APIに接続できません。docs/config.js のRender URLを確認してください。\n${e.message}`,'error')}}

function setStartPoint(lat,lon,{center=false}={}){
  if(state.layers.start)state.layers.start.remove();
  $('lat').value=(+lat).toFixed(7);$('lon').value=(+lon).toFixed(7);
  state.layers.start=L.marker([lat,lon]).addTo(map).bindPopup('開始地点');
  if(center)map.setView([lat,lon],18);
}
function setStartPickMode(on){state.startPickMode=!!on;const b=$('pickStart');if(b){b.classList.toggle('active',state.startPickMode);b.textContent=state.startPickMode?'地図上の開始地点をクリックしてください':'地図で開始地点を指定'}map.getContainer().classList.toggle('start-pick-mode',state.startPickMode)}
map.on('click',e=>{
  // 最初の1クリックだけは、その地点を開始地点として自動設定する。
  // 2回目以降の通常クリックでは開始地点を動かさない。変更したい場合だけ明示設定モードを使う。
  const hasStartPoint=!!state.layers.start;
  if(hasStartPoint && !state.startPickMode)return;
  setStartPoint(e.latlng.lat,e.latlng.lng);
  if(state.startPickMode)setStartPickMode(false);
  status('status',hasStartPoint?'開始地点を変更しました。':'開始地点を設定しました。以後、通常クリックでは移動しません。','success');
});
$('pickStart').onclick=()=>setStartPickMode(!state.startPickMode);
$('useCurrent').onclick=()=>navigator.geolocation?.getCurrentPosition(p=>{const {latitude,longitude}=p.coords;setStartPoint(latitude,longitude,{center:true})},e=>status('status',`現在地を取得できません: ${e.message}`,'error'),{enableHighAccuracy:true});

$('kmz').onchange=async()=>{const f=$('kmz').files[0];if(!f)return;status('status','KMZを解析中…');$('build').disabled=true;const fd=new FormData();fd.append('kmz',f);try{const r=await fetch(`${API}/api/areas`,{method:'POST',body:fd,headers:authHeaders()});const j=await r.json();if(!r.ok)throw Error(j.error);state.uploadId=j.upload_id;state.areaGeojson=j.area_geojson||null;state.areaInfo=j.area_info||{};$('area').innerHTML=j.areas.map(x=>`<option>${escapeHtml(x)}</option>`).join('');$('area').disabled=false;$('build').disabled=false;renderAreaBoundaries(true);renderHouseholdInfo();status('status',`${j.areas.length}件の区画を読み込みました。地図に町丁目境界を表示しています。`,'success')}catch(e){status('status',e.message,'error')}};


$('area').onchange=()=>{renderAreaBoundaries(true);renderHouseholdInfo()};

function renderAreaBoundaries(fitSelected=false){
  if(state.layers.areas){state.layers.areas.remove();state.layers.areas=null}
  if(!state.areaGeojson)return;
  const selected=$('area').value;
  let selectedLayer=null;
  state.layers.areas=L.geoJSON(state.areaGeojson,{
    style:f=>{
      const active=f.properties?.name===selected;
      return active
        ? {color:'#2563eb',weight:4,opacity:.95,fillColor:'#3b82f6',fillOpacity:.14}
        : {color:'#64748b',weight:1.5,opacity:.65,fillColor:'#94a3b8',fillOpacity:.035};
    },
    onEachFeature:(f,layer)=>{
      const name=f.properties?.name||'';
      layer.bindTooltip(name,{sticky:true,direction:'center',className:'area-tooltip'});
      if(name===selected)selectedLayer=layer;
    }
  }).addTo(map);
  if(fitSelected&&selectedLayer){
    const b=selectedLayer.getBounds();
    if(b.isValid())map.fitBounds(b,{padding:[35,35]});
    selectedLayer.bringToFront?.();
  }
}

function currentHouseholds(){const v=state.areaInfo?.[$('area').value]?.households;return Number.isFinite(+v)?+v:null}
function renderHouseholdInfo(){const n=currentHouseholds(),box=$('householdBox');if(!box)return;if(n==null){box.classList.add('hidden');return}box.classList.remove('hidden');$('householdTotal').textContent=n.toLocaleString('ja-JP')+'世帯'}
function updateHouseholdProgress(pct){const total=currentHouseholds(),box=$('householdProgress');if(!box||total==null){box?.classList.add('hidden');return}const done=Math.max(0,Math.min(total,Math.round(total*(pct||0)/100)));box.classList.remove('hidden');$('householdDone').textContent=done.toLocaleString('ja-JP')+'世帯';$('householdRemain').textContent=(total-done).toLocaleString('ja-JP')+'世帯'}

$('build').onclick=async()=>{status('status','道路取得・巡回計算・KML生成を実行中…');$('build').disabled=true;$('downloads').classList.add('hidden');try{const j=await api('/api/build',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({upload_id:state.uploadId,area:$('area').value,workers:+$('workers').value,start_lat:$('lat').value,start_lon:$('lon').value,offline_fallback:$('fallback').checked})});state.jobId=j.job_id;state.geojson=j.geojson;state.summary=j.summary;renderGeneratedMap();renderMetrics();renderDownloads();status('status',`生成完了（道路データ: ${j.summary.data_mode}）`,'success');await createSharedProject();prepareWorkerUI();activateTab('build')}catch(e){status('status',e.message,'error')}finally{$('build').disabled=false}};

function routeStepFeatures(){return (state.geojson?.features||[]).filter(f=>f.properties?.kind==='route_step').sort((a,b)=>(a.properties.seq||0)-(b.properties.seq||0))}
function navigationLegFeatures(){return (state.geojson?.features||[]).filter(f=>f.properties?.kind==='navigation_leg').sort((a,b)=>(a.properties.leg||0)-(b.properties.leg||0))}
function renderGeneratedMap(){
  clearRouteLayers();
  const workers=workerFeatures(), workerAreas=workerAreaFeatures();
  const multi=workers.length>1;
  const steps=routeStepFeatures(), legs=navigationLegFeatures();
  state.navLegs=multi?[]:legs;state.activeGuideLeg=0;
  const hasSteps=steps.length>0;
  state.layers.generated=L.geoJSON(state.geojson,{
    filter:f=>{const k=f.properties?.kind;
      if(k==='navigation_leg'||k==='worker_navigation_leg'||k==='worker_route_step')return false;
      if(multi && (k==='route'||k==='route_step'||k==='start'))return false;
      if(!multi && (k==='worker_area'||k==='worker_route'))return false;
      return !(hasSteps&&!multi&&k==='route');
    },
    style:f=>{
      const k=f.properties?.kind;
      if(k==='area')return state.areaGeojson?{weight:0,opacity:0,fillOpacity:0}:{weight:4,color:'#2563eb',opacity:.95,fillColor:'#3b82f6',fillOpacity:.10};
      if(k==='worker_area'){const c=WORKER_COLORS[(+f.properties.worker_id-1)%WORKER_COLORS.length];return{weight:3,color:c,opacity:.9,fillColor:c,fillOpacity:.10}}
      if(k==='worker_route'){const c=WORKER_COLORS[(+f.properties.worker_id-1)%WORKER_COLORS.length];return{weight:6,color:c,opacity:.92,lineCap:'round',lineJoin:'round'}}
      if(k==='road')return{weight:1.0,color:'#94a3b8',opacity:.18};
      if(k==='route_step'){if(f.properties.transfer)return{weight:3,color:'#64748b',opacity:.45,dashArray:'8 8'};if(f.properties.duplicated)return{weight:4,color:'#f59e0b',opacity:.58};return{weight:4,color:'#ef4444',opacity:.50}}
      return{weight:2,color:'#64748b'}
    },
    onEachFeature:(f,l)=>{if(f.properties?.kind==='worker_area'||f.properties?.kind==='worker_route'){const p=f.properties;l.bindTooltip(`${p.name||'担当'}${p.estimated_households?` ・ 推定${p.estimated_households}世帯`:''}${p.length_m?` ・ ${(p.length_m/1000).toFixed(2)}km`:''}`)}}
  }).addTo(map);
  if(multi){renderWorkerSummary(workers);$('routeGuide')?.classList.add('hidden')}else{renderRouteGuide(legs);drawRouteEndpoints(legs.length?legs:steps);if(legs.length)focusGuideLeg(0,false)}
  const b=state.layers.generated.getBounds();if(b.isValid())map.fitBounds(b,{padding:[15,15]})
}
function renderWorkerSummary(workers){
  const box=$('routeGuide');if(!box)return;
  box.innerHTML=`<h3>担当別エリア・独立巡回ルート</h3><div class="hint">町丁目を地理的に分割し、各担当が自分のエリア内だけを巡回します。担当を押すと地図で強調します。</div><div class="guide-list">${workers.map((f,i)=>{const p=f.properties,c=WORKER_COLORS[i%WORKER_COLORS.length];return `<button class="guide-item worker-preview" data-worker="${p.worker_id}"><div class="guide-num" style="background:${c}">${p.worker_id}</div><div><b>${escapeHtml(p.name||`担当${p.worker_id}`)} ・ ${(p.length_m/1000).toFixed(2)}km</b><span>${p.estimated_households?`推定 ${p.estimated_households}世帯 ・ `:''}約${p.estimated_minutes||'-'}分</span></div></button>`}).join('')}</div>`;
  box.classList.remove('hidden');
  box.querySelectorAll('.worker-preview').forEach(el=>el.onclick=()=>focusWorkerPreview(+el.dataset.worker));
}
function focusWorkerPreview(workerId){
  if(state.layers.focus)state.layers.focus.remove();
  const f=workerFeatures().find(x=>+x.properties.worker_id===workerId);if(!f)return;
  const c=WORKER_COLORS[(workerId-1)%WORKER_COLORS.length];state.layers.focus=L.geoJSON(f,{style:{color:c,weight:10,opacity:1}}).addTo(map);
  const b=state.layers.focus.getBounds();if(b.isValid())map.fitBounds(b,{padding:[60,60],maxZoom:18});
}
function clearRouteLayers(){['generated','todo','done','gps','directions','sequence','focus','nextPreview'].forEach(k=>{if(state.layers[k]){state.layers[k].remove();state.layers[k]=null}})}
function bearingDeg(a,b){const p=Math.PI/180,y=Math.sin((b[0]-a[0])*p)*Math.cos(b[1]*p),x=Math.cos(a[1]*p)*Math.sin(b[1]*p)-Math.sin(a[1]*p)*Math.cos(b[1]*p)*Math.cos((b[0]-a[0])*p);return(Math.atan2(y,x)/p+360)%360}
function interpolateCoord(a,b,t){return[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t]}
function arrowSvg(){return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 1.5 4.5 10H9v12h6V10h4.5z"/></svg>'}
function lineLengths(coords){const lens=[];let total=0;for(let i=0;i<coords.length-1;i++){const d=haversine(coords[i][1],coords[i][0],coords[i+1][1],coords[i+1][0]);lens.push(d);total+=d}return{lens,total}}
function coordAtDistance(coords,d){const {lens,total}=lineLengths(coords);if(!coords.length)return null;if(total<=0)return coords[0];d=Math.max(0,Math.min(total,d));let acc=0;for(let i=0;i<lens.length;i++){if(acc+lens[i]>=d){const t=lens[i]?((d-acc)/lens[i]):0;return interpolateCoord(coords[i],coords[i+1],t)}acc+=lens[i]}return coords.at(-1)}
function tangentAtDistance(coords,d){const {total}=lineLengths(coords);const delta=Math.min(12,Math.max(3,total*.12));const a=coordAtDistance(coords,Math.max(0,d-delta)),b=coordAtDistance(coords,Math.min(total,d+delta));return{pt:coordAtDistance(coords,d),bearing:bearingDeg(a,b)}}
function drawRouteEndpoints(features){
  if(state.layers.sequence)state.layers.sequence.remove();const g=L.layerGroup();if(!features.length){state.layers.sequence=g.addTo(map);return}
  const first=features[0].geometry.coordinates[0],lastF=features.at(-1),last=lastF.geometry.coordinates.at(-1);
  L.marker([first[1],first[0]],{interactive:false,icon:L.divIcon({className:'route-end-wrap',html:'<div class="route-end start">START</div>',iconSize:[52,24],iconAnchor:[26,12]})}).addTo(g);
  L.marker([last[1],last[0]],{interactive:false,icon:L.divIcon({className:'route-end-wrap',html:'<div class="route-end goal">GOAL</div>',iconSize:[48,24],iconAnchor:[24,12]})}).addTo(g);
  state.layers.sequence=g.addTo(map);
}
function arrowMarkersForLeg(feature){
  const g=L.layerGroup(),c=feature.geometry?.coordinates||[];if(c.length<2||$('showArrows')?.checked===false)return g;
  const {total}=lineLengths(c);const ds=total<35?[total*.5]:total<90?[total*.35,total*.72]:[total*.22,total*.50,total*.78];
  ds.forEach(d=>{const m=tangentAtDistance(c,d);if(!m?.pt)return;L.marker([m.pt[1],m.pt[0]],{interactive:false,icon:L.divIcon({className:'route-arrow-wrap',html:`<div class="route-arrow selected" style="transform:rotate(${m.bearing}deg)">${arrowSvg()}</div>`,iconSize:[30,30],iconAnchor:[15,15]})}).addTo(g)});return g;
}
function focusGuideLeg(index,fit=true){
  if(!state.navLegs?.length)return;index=Math.max(0,Math.min(state.navLegs.length-1,index));state.activeGuideLeg=index;
  if(state.layers.focus)state.layers.focus.remove();if(state.layers.directions)state.layers.directions.remove();if(state.layers.nextPreview)state.layers.nextPreview.remove();
  const f=state.navLegs[index],c=f.geometry.coordinates,p=f.properties;
  state.layers.focus=L.geoJSON(f,{style:{color:'#2563eb',weight:9,opacity:1,lineCap:'round',lineJoin:'round'}}).addTo(map);
  if(index+1<state.navLegs.length){state.layers.nextPreview=L.geoJSON(state.navLegs[index+1],{style:{color:'#60a5fa',weight:6,opacity:.48,dashArray:'5 7'}}).addTo(map)}
  state.layers.directions=arrowMarkersForLeg(f).addTo(map);
  if($('showNumbers')?.checked!==false){const pt=c[0];L.marker([pt[1],pt[0]],{interactive:false,icon:L.divIcon({className:'route-seq-wrap focus-seq',html:`<div class="route-seq">${p.leg}</div>`,iconSize:[32,32],iconAnchor:[16,16]})}).addTo(state.layers.directions)}
  document.querySelectorAll('.guide-item').forEach((el,i)=>el.classList.toggle('active',i===index));
  const counter=$('guideCounter');if(counter)counter.textContent=`${index+1} / ${state.navLegs.length}`;
  const title=$('guideCurrent');if(title)title.innerHTML=`<b>${p.leg}. ${escapeHtml(p.turn||'進む')}</b><span>${escapeHtml(p.name||'道路に沿って進む')} ・ 約${Math.round(p.length_m||0)}m</span>`;
  $('guidePrev')&&( $('guidePrev').disabled=index===0 );$('guideNext')&&( $('guideNext').disabled=index===state.navLegs.length-1 );
  const item=document.querySelector(`.guide-item[data-index="${index}"]`);item?.scrollIntoView({block:'nearest'});
  if(fit){const b=state.layers.focus.getBounds();if(b.isValid())map.fitBounds(b,{padding:[80,80],maxZoom:18})}
}
function renderRouteGuide(legs){
  const box=$('routeGuide');if(!box)return;if(!legs?.length){box.classList.add('hidden');return}
  box.innerHTML=`<h3>開始地点からの最適巡回順</h3><div class="hint">上から1→2→3…の順に進みます。項目をクリックすると、その区間だけ地図上で青く強調します。</div><div class="guide-controller"><button id="guidePrev" class="secondary">← 前へ</button><div><strong id="guideCounter">1 / ${legs.length}</strong><div id="guideCurrent" class="guide-current"></div></div><button id="guideNext" class="secondary">次へ →</button></div><div class="guide-list">${legs.map((f,i)=>`<button class="guide-item" data-index="${i}"><div class="guide-num">${f.properties.leg}</div><div><b>${escapeHtml(f.properties.turn||'進む')} ・ ${Math.round(f.properties.length_m||0)}m</b><span>${escapeHtml(f.properties.name||f.properties.instruction||'道路に沿って進む')}</span></div></button>`).join('')}</div>`;
  box.classList.remove('hidden');
  box.querySelectorAll('.guide-item').forEach(el=>el.onclick=()=>focusGuideLeg(+el.dataset.index,true));
  $('guidePrev').onclick=()=>focusGuideLeg(state.activeGuideLeg-1,true);$('guideNext').onclick=()=>focusGuideLeg(state.activeGuideLeg+1,true);
  focusGuideLeg(0,false);
}
function distanceToFeatureMeters(lat,lon,f){let best=Infinity,c=f?.geometry?.coordinates||[];for(let j=0;j<c.length-1;j++)best=Math.min(best,pointSegmentMeters(lat,lon,c[j][1],c[j][0],c[j+1][1],c[j+1][0]));return best}
function advanceFieldGuide(){if(!state.current||!state.navLegs?.length)return;const cur=Math.max(0,Math.min(state.navLegs.length-1,state.fieldGuideLeg||0));let bestIdx=cur,bestD=distanceToFeatureMeters(state.current.lat,state.current.lon,state.navLegs[cur]);for(let i=cur+1;i<=Math.min(cur+3,state.navLegs.length-1);i++){const d=distanceToFeatureMeters(state.current.lat,state.current.lon,state.navLegs[i]);if(d+4<bestD){bestD=d;bestIdx=i}}const threshold=+$('gpsThreshold').value;if(bestIdx>cur&&bestD<=Math.max(threshold,22))state.fieldGuideLeg=bestIdx;else{const c=state.navLegs[cur].geometry.coordinates,end=c[c.length-1],dEnd=haversine(state.current.lat,state.current.lon,end[1],end[0]);if(dEnd<=Math.max(10,threshold*.7)&&cur+1<state.navLegs.length)state.fieldGuideLeg=cur+1}}
function nextRouteInstruction(){const box=$('nextGuide');if(!box||!state.navLegs?.length)return;advanceFieldGuide();const idx=Math.max(0,Math.min(state.navLegs.length-1,state.fieldGuideLeg||0)),f=state.navLegs[idx],p=f.properties;box.innerHTML=`<b>現在の案内 ${idx+1} / ${state.navLegs.length}</b><div class="turn">${p.leg}. ${escapeHtml(p.turn||'進む')}</div><div>${escapeHtml(p.name||'道路に沿って進む')} ・ 約${Math.round(p.length_m||0)}m</div>`;box.classList.remove('hidden');if(state.watchId!==null){state.activeGuideLeg=idx;focusGuideLeg(idx,false)}}
function renderMetrics(){const s=state.summary;$('metrics').innerHTML=`<div class="metric">全体距離<b>${(s.route_length_m/1000).toFixed(2)} km</b></div><div class="metric">対象道路<b>${s.source_edges||0}本</b></div><div class="metric">非連結成分<b>${s.component_count||1}</b></div><div class="metric">重複倍率<b>${(s.route_ratio||s.duplication_ratio||0).toFixed(2)}</b></div><div class="metric">移動区間<b>${((s.transfer_length_m||0)/1000).toFixed(2)} km</b></div><div class="metric">担当人数<b>${s.worker_count}人</b></div>`;$('metrics').classList.remove('hidden')}
function renderDownloads(){const base=`${API}/download/${state.jobId}`;$('downloads').innerHTML=`<label>成果物</label><a href="${base}/posting_navigator_results.zip">一式ZIP</a><a href="${base}/posting_navigator.kmz">統合KMZ</a><a href="${base}/posting_navigator.kml">統合KML</a><a href="${base}/assignments.csv">担当CSV</a><a href="${base}/summary.json">集計JSON</a>`;$('downloads').classList.remove('hidden')}

async function createSharedProject(){try{const j=await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:state.jobId})});state.projectId=j.project_id;state.shareCode=j.share_code;localStorage.setItem('pn_last_project',JSON.stringify({projectId:state.projectId,shareCode:state.shareCode}));renderProjectBox();startTeamSync()}catch(e){status('teamStatus',`共有プロジェクトを作成できません: ${e.message}`,'error')}}
function renderProjectBox(){if(!state.projectId)return;$('projectBox').classList.remove('hidden');$('shareCode').textContent=state.shareCode;status('teamStatus','共有中。各担当の進捗は約5秒ごとに同期されます。','success')}
$('copyCode').onclick=async()=>{await navigator.clipboard?.writeText(state.shareCode||'');$('copyCode').textContent='コピー済み';setTimeout(()=>$('copyCode').textContent='コピー',1200)};
$('joinProject').onclick=async()=>{const code=$('joinCode').value.trim().toUpperCase();if(!code)return;try{const j=await api(`/api/projects/join/${encodeURIComponent(code)}`);state.projectId=j.project_id;state.shareCode=j.share_code;state.geojson=j.geojson;state.summary=j.summary;renderGeneratedMap();renderProjectBox();prepareWorkerUI();startTeamSync();activateTab('field');loadWorker(+(($('fieldWorker').value)||1));status('teamStatus',`${j.area} の共有プロジェクトに参加しました。`,'success')}catch(e){status('teamStatus',e.message,'error')}};

function prepareWorkerUI(){const features=workerFeatures();const options=features.map(f=>`<option value="${f.properties.worker_id}">${escapeHtml(f.properties.name||`担当${f.properties.worker_id}`)}</option>`).join('');$('fieldWorker').innerHTML=options;if(options)state.workerId=+($('fieldWorker').value||1)}
$('fieldWorker').onchange=()=>loadWorker(+$('fieldWorker').value);
function workerFeatures(){return (state.geojson?.features||[]).filter(f=>f.properties?.kind==='worker_route').sort((a,b)=>a.properties.worker_id-b.properties.worker_id)}
function workerAreaFeatures(){return (state.geojson?.features||[]).filter(f=>f.properties?.kind==='worker_area').sort((a,b)=>a.properties.worker_id-b.properties.worker_id)}
function workerNavigationLegFeatures(workerId){return (state.geojson?.features||[]).filter(f=>f.properties?.kind==='worker_navigation_leg'&&+f.properties.worker_id===+workerId).sort((a,b)=>(a.properties.leg||0)-(b.properties.leg||0))}
const WORKER_COLORS=['#2563eb','#f97316','#16a34a','#a855f7','#e11d48','#0891b2','#ca8a04','#4f46e5'];
function loadWorker(workerId){state.workerId=workerId;const feat=workerFeatures().find(f=>+f.properties.worker_id===workerId);if(!feat)return;state.navLegs=workerNavigationLegFeatures(workerId);const coords=feat.geometry.coordinates;state.segments=[];state.segmentLengths=[];for(let i=0;i<coords.length-1;i++){state.segments.push([coords[i],coords[i+1]]);state.segmentLengths.push(haversine(coords[i][1],coords[i][0],coords[i+1][1],coords[i+1][0]))}const key=progressKey();let saved=[];try{saved=JSON.parse(localStorage.getItem(key)||'[]')}catch{}state.completed=new Set(saved);state.fieldGuideLeg=0;state.lastPosition=null;$('fieldWorkerName').textContent=`${feat.properties.name||`担当${workerId}`}${feat.properties.estimated_households?` ・ 推定${feat.properties.estimated_households}世帯`:''}`;drawFieldRoute();updateFieldProgress();nextRouteInstruction();pullProgress()}
function progressKey(){return `pn_progress_${state.projectId||'local'}_${state.workerId}`}
function drawFieldRoute(){if(!state.areaGeojson&&state.geojson){const areaOnly={type:'FeatureCollection',features:(state.geojson.features||[]).filter(f=>f.properties?.kind==='area')};if(areaOnly.features.length){state.areaGeojson=areaOnly;renderAreaBoundaries(false)}}if(state.layers.generated){state.layers.generated.remove();state.layers.generated=null}if(state.layers.todo)state.layers.todo.remove();if(state.layers.done)state.layers.done.remove();if(state.layers.directions)state.layers.directions.remove();if(state.layers.sequence)state.layers.sequence.remove();const todo=[],done=[];state.segments.forEach((s,i)=>(state.completed.has(i)?done:todo).push({type:'Feature',properties:{segment:i},geometry:{type:'LineString',coordinates:s}}));state.layers.todo=L.geoJSON({type:'FeatureCollection',features:todo},{style:{color:'#ef4444',weight:7,opacity:.70}}).addTo(map);state.layers.done=L.geoJSON({type:'FeatureCollection',features:done},{style:{color:'#22c55e',weight:8,opacity:.95}}).addTo(map);drawNextWorkerArrow();const both=L.featureGroup([state.layers.todo,state.layers.done]);const b=both.getBounds();if(b.isValid())map.fitBounds(b,{padding:[20,20]})}
function drawNextWorkerArrow(){if(state.layers.directions)state.layers.directions.remove();const g=L.layerGroup();let idx=state.segments.findIndex((_,i)=>!state.completed.has(i));if(idx<0){state.layers.directions=g.addTo(map);return}const s=state.segments[idx],a=s[0],b=s[1],pt=interpolateCoord(a,b,.5),deg=bearingDeg(a,b);L.geoJSON({type:'Feature',properties:{},geometry:{type:'LineString',coordinates:s}},{style:{color:'#2563eb',weight:10,opacity:1}}).addTo(g);L.marker([pt[1],pt[0]],{interactive:false,icon:L.divIcon({className:'route-arrow-wrap',html:`<div class="route-arrow field selected" style="transform:rotate(${deg}deg)">${arrowSvg()}</div>`,iconSize:[32,32],iconAnchor:[16,16]})}).addTo(g);state.layers.directions=g.addTo(map)}
function updateFieldProgress(){const done=[...state.completed].reduce((s,i)=>s+(state.segmentLengths[i]||0),0), total=state.segmentLengths.reduce((a,b)=>a+b,0),pct=total?done/total*100:0;$('fieldPercent').textContent=`${pct.toFixed(1)}%`;$('fieldDistance').textContent=`${(done/1000).toFixed(2)} / ${(total/1000).toFixed(2)} km`;localStorage.setItem(progressKey(),JSON.stringify([...state.completed]));updateHouseholdProgress(pct);return{done,total,pct}}
$('gpsThreshold').oninput=()=>$('gpsThresholdText').textContent=$('gpsThreshold').value;

$('gpsStart').onclick=()=>{if(!navigator.geolocation){status('gpsStatus','この端末ではGPSを利用できません。','error');return}if(state.watchId!==null)return;state.watchId=navigator.geolocation.watchPosition(onPosition,e=>status('gpsStatus',`GPSエラー: ${e.message}`,'error'),{enableHighAccuracy:true,maximumAge:1500,timeout:15000});$('gpsStart').disabled=true;$('gpsStop').disabled=false;status('gpsStatus','GPS追跡中。ルート上を歩くと近い区間が自動的に緑になります。','success')};
$('gpsStop').onclick=()=>{if(state.watchId!==null)navigator.geolocation.clearWatch(state.watchId);state.watchId=null;$('gpsStart').disabled=false;$('gpsStop').disabled=true;status('gpsStatus','GPS追跡を停止しました。')};
function angleDiff(a,b){return Math.abs(((a-b+540)%360)-180)}
function onPosition(p){const lat=p.coords.latitude,lon=p.coords.longitude,heading=Number.isFinite(p.coords.heading)?p.coords.heading:null;state.current={lat,lon,accuracy:p.coords.accuracy,heading};if(state.layers.gps)state.layers.gps.remove();state.layers.gps=L.marker([lat,lon],{icon:L.divIcon({className:'',html:'<div class="gps-marker"></div>',iconSize:[18,18],iconAnchor:[9,9]})}).addTo(map);if($('autoFollow').checked)map.panTo([lat,lon]);const threshold=+$('gpsThreshold').value;let next=state.segments.findIndex((_,i)=>!state.completed.has(i));let best=-1,bestD=Infinity;const from=Math.max(0,next<0?0:next-1),to=Math.min(state.segments.length-1,(next<0?0:next)+12);for(let i=from;i<=to;i++){if(state.completed.has(i))continue;const seg=state.segments[i],d=pointSegmentMeters(lat,lon,seg[0][1],seg[0][0],seg[1][1],seg[1][0]);let ok=true;if(heading!==null&&(p.coords.speed||0)>.5){const rb=bearingDeg(seg[0],seg[1]);ok=Math.min(angleDiff(heading,rb),angleDiff(heading,(rb+180)%360))<=85}if(ok&&d<bestD){bestD=d;best=i}}if(best>=0&&bestD<=threshold){if(next>=0&&best>next+3)best=next;for(let i=Math.max(0,next);i<=best;i++)state.completed.add(i);drawFieldRoute();updateFieldProgress();pushProgress()}advanceFieldGuide();nextRouteInstruction();state.lastPosition={lat,lon};status('gpsStatus',`GPS追跡中\n精度: ±${Math.round(p.coords.accuracy)}m / 次の巡回区間まで: ${isFinite(bestD)?bestD.toFixed(1):'-'}m / 案内: ${Math.min((state.fieldGuideLeg||0)+1,state.navLegs.length)}番`,'success')}
$('resetProgress').onclick=()=>{if(!confirm('この担当の進捗を0%に戻しますか？'))return;state.completed.clear();drawFieldRoute();updateFieldProgress();pushProgress()};

async function pushProgress(){if(!state.projectId)return;const m=updateFieldProgress();try{await api(`/api/projects/${state.projectId}/progress/${state.workerId}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({completed_segments:[...state.completed],completed_distance_m:m.done,lat:state.current?.lat,lon:state.current?.lon})})}catch(e){console.warn(e)}}
async function pullProgress(){if(!state.projectId)return;try{const j=await api(`/api/projects/${state.projectId}/progress`);const me=j.workers.find(w=>+w.worker_id===state.workerId);if(me){const remote=new Set(me.completed_segments||[]);if(remote.size>state.completed.size){state.completed=remote;drawFieldRoute();updateFieldProgress()}}renderTeam(j.workers)}catch(e){status('teamStatus',e.message,'error')}}
function startTeamSync(){if(state.syncTimer)clearInterval(state.syncTimer);pullProgress();state.syncTimer=setInterval(pullProgress,state.config.sync_interval_ms||5000)}
function renderTeam(workers){$('teamProgress').innerHTML=(workers||[]).map(w=>`<div class="worker-card"><div class="worker-head"><b>担当${String(w.worker_id).padStart(2,'0')}</b><span>${w.percent.toFixed(1)}%</span></div><div class="bar"><i style="width:${Math.min(100,w.percent)}%"></i></div><div class="hint">${(w.completed_distance_m/1000).toFixed(2)} / ${(w.total_distance_m/1000).toFixed(2)} km${w.updated_at?' ・ 更新 '+new Date(w.updated_at*1000).toLocaleTimeString():''}</div></div>`).join('')}


async function loadMyProjects(){if(!state.user)return;try{const j=await api('/api/projects');$('myProjectsBox').classList.remove('hidden');$('myProjects').innerHTML=(j.projects||[]).map(p=>`<div class="worker-card"><div class="worker-head"><b>${escapeHtml(p.area)}</b><button class="ghost open-project" data-code="${p.share_code}">開く</button></div><div class="hint">共有コード ${p.share_code} ・ ${p.worker_count}人 ・ ${new Date(p.created_at*1000).toLocaleString()}</div></div>`).join('')||'<div class="hint">保存プロジェクトはまだありません。</div>';document.querySelectorAll('.open-project').forEach(b=>b.onclick=async()=>{$('joinCode').value=b.dataset.code;await $('joinProject').onclick()})}catch(e){console.warn(e)}}

function setupGoogleLogin(){const cid=state.config.google_client_id;if(!cid){$('loginBox').innerHTML='<span class="hint">Googleログイン未設定</span>';return}const s=document.createElement('script');s.src='https://accounts.google.com/gsi/client';s.async=true;s.onload=()=>{google.accounts.id.initialize({client_id:cid,callback:async r=>{try{const j=await api('/api/auth/google',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({credential:r.credential})});state.sessionToken=j.token;state.user=j.user;localStorage.setItem('pn_session',j.token);renderLogin();loadMyProjects()}catch(e){alert(e.message)}}});renderLogin()};document.head.appendChild(s)}
function renderLogin(){if(state.user){$('loginBox').innerHTML=`<button id="logout" class="ghost">${escapeHtml(state.user.name||state.user.email)} ▾</button>`;$('logout').onclick=async()=>{try{await api('/api/auth/logout',{method:'POST'})}catch{}state.sessionToken='';state.user=null;localStorage.removeItem('pn_session');$('myProjectsBox').classList.add('hidden');renderLogin()}}else if(window.google){$('loginBox').innerHTML='<div id="googleBtn"></div>';google.accounts.id.renderButton($('googleBtn'),{theme:'outline',size:'medium',text:'signin_with',shape:'pill'})}}

let deferredPrompt=null;window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();deferredPrompt=e;$('installBtn').classList.remove('hidden')});$('installBtn').onclick=async()=>{if(!deferredPrompt)return;deferredPrompt.prompt();await deferredPrompt.userChoice;deferredPrompt=null;$('installBtn').classList.add('hidden')};if('serviceWorker'in navigator)window.addEventListener('load',()=>navigator.serviceWorker.register('service-worker.js').catch(console.warn));

function haversine(lat1,lon1,lat2,lon2){const R=6371000,p=Math.PI/180,dLat=(lat2-lat1)*p,dLon=(lon2-lon1)*p,a=Math.sin(dLat/2)**2+Math.cos(lat1*p)*Math.cos(lat2*p)*Math.sin(dLon/2)**2;return 2*R*Math.atan2(Math.sqrt(a),Math.sqrt(1-a))}
function pointSegmentMeters(lat,lon,lat1,lon1,lat2,lon2){const mLat=(lat+lat1+lat2)/3*Math.PI/180,x=(v)=>v*Math.PI/180*6371000*Math.cos(mLat),y=(v)=>v*Math.PI/180*6371000;const px=x(lon),py=y(lat),ax=x(lon1),ay=y(lat1),bx=x(lon2),by=y(lat2),dx=bx-ax,dy=by-ay;const t=Math.max(0,Math.min(1,((px-ax)*dx+(py-ay)*dy)/(dx*dx+dy*dy||1)));return Math.hypot(px-(ax+t*dx),py-(ay+t*dy))}
$('showArrows')?.addEventListener('change',()=>state.navLegs?.length&&focusGuideLeg(state.activeGuideLeg,false));
$('showNumbers')?.addEventListener('change',()=>state.navLegs?.length&&focusGuideLeg(state.activeGuideLeg,false));

function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]))}

loadConfig();
