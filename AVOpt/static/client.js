'use strict';
const $ = id => document.getElementById(id);
const keys = ['tilt','s','h','g'];
const names = {tilt:'倾角',s:'跨度',h:'安装高度',g:'横向间隙'};
const cropTemplates = window.__CROPS__ || {};
const economicFields={tariff:0.35,pv_capex_per_w:3,pv_life:25,pv_om_per_w_year:0.04,ag_cost_per_ha:6000,land_rent_per_ha:0};
const economicLabels={tariff:'电价 元/kWh',pv_capex_per_w:'光伏造价 元/W',pv_life:'寿命 年',pv_om_per_w_year:'运维 元/W/年',ag_cost_per_ha:'农业成本 元/hm²/年',land_rent_per_ha:'土地租金 元/hm²/年'};
const pvDefaults={modules_per_string:20,bypass_blocks:3,block_axis:'slope',row_offset:0};
let economicConfig = {}, cropPrices = {}, previousCrop = '冬小麦';
let locationRevision = 0, reverseTimer = null;
const f = (v,n=1) => v === undefined || v === null || !Number.isFinite(Number(v)) ? '—' : Number(v).toFixed(n);
const ptext = v => v == null || !Number.isFinite(Number(v)) ? '—' : Number(v) < 1e-6 ? '&lt;1e-6' : Number(v).toFixed(6);
const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let lastResult = null, locations = [], saved = [];
try { saved = JSON.parse(localStorage.getItem('avopt_scenarios_v11') || '[]'); if(!Array.isArray(saved)) saved=[]; } catch { saved=[]; }
const year = new Date().getFullYear();
for(let y=2020;y<=2026;y++){const o=new Option(String(y)+(y===year?'（已发布部分）':''),String(y));o.selected=y===Math.min(year,2026);$('years').add(o);}
function status(message){$('status').textContent=message;}
async function jsonRequest(url,body){
  const r=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await r.json();if(!r.ok || data.ok===false)throw new Error(data.error || '请求失败');return data;
}
async function runJob(body, started){
  const job=await jsonRequest('/api/jobs',body);
  while(true){
    const current=await jsonRequest('/api/jobs?id='+encodeURIComponent(job.id));
    if(current.state==='done')return current.result;
    status((current.state==='queued'?'等待前面的计算完成':'正在计算')+'，已用时 '+Math.round((Date.now()-started)/1000)+' 秒。请保持页面打开。');
    await new Promise(resolve=>setTimeout(resolve,4000));
  }
}
function invalidateLocation(){locationRevision++;clearTimeout(reverseTimer);locations=[];$('area').innerHTML='<option value="">地区已改变，请重新检索</option>';$('coordinates_confirmed').checked=false;$('bias_correct').checked=false;}
$('place').addEventListener('input',invalidateLocation);
function coordinatesChanged(){
  invalidateLocation();$('place').value='';$('location_note').textContent='正在按经纬度定位行政区…';
  $('radiation_status').textContent='坐标已改变，请核对辐射覆盖年份。';
  reverseTimer=setTimeout(()=>locateCoordinates(),650);
}
['lat','lon'].forEach(id=>$(id).addEventListener('input',coordinatesChanged));
async function locateCoordinates(){
  clearTimeout(reverseTimer);
  if(window.__STATIC__){status('离线报告不能获取未缓存地区，请打开本地系统。');return false;}
  const lat=$('lat').value,lon=$('lon').value,revision=++locationRevision;
  if(!lat.trim()||!lon.trim()||!Number.isFinite(Number(lat))||!Number.isFinite(Number(lon))){$('location_note').textContent='请填写有效的纬度与经度。';return false;}
  $('coordinates_confirmed').checked=false;$('location_note').textContent='正在匹配行政区边界…';
  try{
    const d=await jsonRequest('/api/reverse-place?lat='+encodeURIComponent(lat)+'&lon='+encodeURIComponent(lon));
    if(revision!==locationRevision||$('lat').value!==lat||$('lon').value!==lon)return false;
    const p=d.item;locations=[p];$('place').value=p.name;
    $('area').innerHTML=`<option value="${esc(p.areaCode)}">${esc(p.province+' / '+p.city+' / '+p.name)}</option>`;
    $('coordinates_confirmed').checked=true;
    $('location_note').textContent=p.province+' / '+p.city+' / '+p.name;refreshRadiation();return true;
  }catch(e){if(revision===locationRevision){$('location_note').textContent='定位失败：'+e.message;}return false;}
}
$('reverse_lookup').onclick=()=>locateCoordinates();
function applyLocation(){
  const p=locations.find(x=>String(x.areaCode)===$('area').value);if(!p)return;
  $('coordinates_confirmed').checked=false;
  if(p.lat!==undefined){$('lat').value=p.lat;$('lon').value=p.lon;}
  $('location_note').textContent=p.lat!==undefined?(p.coordinate_note+'；已填入，请确认。'):'本地尚无该地区坐标，请填写实际地块经纬度并勾选确认。';
  refreshRadiation();
}
$('area').onchange=applyLocation;
$('lookup').onclick=async()=>{
  if(window.__STATIC__)return status('离线报告不能检索地区，请打开运行中的系统。');
  invalidateLocation();const revision=locationRevision;$('lookup').disabled=true;
  try{const d=await jsonRequest('/api/place?q='+encodeURIComponent($('place').value.trim()));
    if(revision!==locationRevision)return;
    locations=d.items||[];
    $('area').innerHTML='<option value="">请选择具体气象行政区</option>'+locations.map(p=>`<option value="${esc(p.areaCode)}">${esc(p.province+' / '+p.city+' / '+p.name)}</option>`).join('');
    $('coordinates_confirmed').checked=false;
    if(locations.length===1){$('area').value=String(locations[0].areaCode);applyLocation();}
    if(!locations.length)status('没有找到地区，请输入更完整的市县名称。');
  }catch(e){status(e.message);}finally{$('lookup').disabled=false;}
};
function updateCrop(resetPrice=false){
  const name=$('crop').value, info=cropTemplates[name]||{};
  if(resetPrice){cropPrices[previousCrop]=$('crop_price').value;$('crop_price').value=cropPrices[name]??info.price??'';}
  previousCrop=name;
  $('crop_price_label').textContent='作物单价 / 元/kg'+(info.product?'（'+info.product+'）':'');
  $('crop_note').textContent=info.season?'生育期：'+info.season:'';
}
$('crop').onchange=()=>updateCrop(true);
function updateDesign(){const n=keys.filter(k=>!$(k+'_fix').checked&&Number($(k+'_lo').value)!==Number($(k+'_hi').value)).length;$('design_note').textContent='五水平 · '+n+' 个变量 · '+[1,5,25,80,155][n]+' 组试验';}
keys.forEach(k=>['fix','lo','hi'].forEach(s=>$(k+'_'+s).addEventListener('change',updateDesign)));
function collect(){
  const r={};
  for(const id of ['place','lat','lon','crop','width','mlen','canopy','levels','y_min','weather_source','target_tmean','target_precip','bias_reference','rh_override'])r[id]=$(id).value;
  keys.forEach(k=>{r[k+'_fix']=$(k+'_fix').checked;['lo','hi','val'].forEach(s=>r[k+'_'+s]=$(k+'_'+s).value);});
  r.w_energy=$('w_e').value;r.w_yield=$('w_y').value;r.w_cv=$('w_c').value;
  r.area_code=$('area').value;r.coordinates_confirmed=$('coordinates_confirmed').checked;r.bias_correct=$('bias_correct').checked;
  r.years=Array.from($('years').selectedOptions,o=>Number(o.value));r.economic={...economicConfig};r.crop_price=$('crop_price').value;r.levels=5;r.radiation_source=r.weather_source==='era5'?'era5':'era5_land';
  Object.keys(economicFields).forEach(k=>r.economic[k]=$(k).value);
  r.pv_options=Object.fromEntries(Object.keys(pvDefaults).map(k=>[k,$(k).value]));
  return r;
}
function fill(r){
  locationRevision++;clearTimeout(reverseTimer);
  for(const id of ['place','lat','lon','crop','width','mlen','canopy','levels','y_min','weather_source','target_tmean','target_precip','bias_reference','rh_override'])if(r[id]!==undefined)$(id).value=r[id];
  keys.forEach(k=>{if(r[k+'_fix']!==undefined)$(k+'_fix').checked=r[k+'_fix']===true;['lo','hi','val'].forEach(s=>{if(r[k+'_'+s]!==undefined)$(k+'_'+s).value=r[k+'_'+s];});});
  [['w_e','w_energy'],['w_y','w_yield'],['w_c','w_cv']].forEach(([a,b])=>{if(r[b]!==undefined)$(a).value=r[b];});
  economicConfig={...(r.economic||{})};$('levels').value=5;
  Object.entries(economicFields).forEach(([k,value])=>$(k).value=economicConfig[k]??value);
  Object.entries(pvDefaults).forEach(([k,value])=>$(k).value=r.pv_options?.[k]??value);
  cropPrices={};if(economicConfig.wheat_price!=null)cropPrices['冬小麦']=economicConfig.wheat_price;if(economicConfig.rape_price!=null)cropPrices['冬油菜']=economicConfig.rape_price;
  const legacyPrice=$('crop').value==='冬小麦'?economicConfig.wheat_price:$('crop').value==='冬油菜'?economicConfig.rape_price:undefined;
  $('crop_price').value=r.crop_price??legacyPrice??cropTemplates[$('crop').value]?.price??'';
  updateCrop();updateDesign();
  const restoredYears=r.years??(r.weather_source==='builtin'?[2020,2021,2022,2023,2024]:null);
  if(restoredYears)Array.from($('years').options).forEach(o=>o.selected=restoredYears.includes(Number(o.value)));
  $('bias_correct').checked=r.bias_correct===true;$('coordinates_confirmed').checked=r.coordinates_confirmed===true;
  $('area').innerHTML=r.area_code?`<option value="${esc(r.area_code)}">${esc(r.place)} · 已保存行政区 ${esc(r.area_code)}</option>`:'<option value="">请检索地区</option>';
  if(!window.__STATIC__)refreshRadiation();
}
function radiationQuery(){return 'lat='+encodeURIComponent($('lat').value)+'&lon='+encodeURIComponent($('lon').value)+'&years='+Array.from($('years').selectedOptions||[],o=>o.value).join(',')+'&weather_source='+encodeURIComponent($('weather_source').value);}
async function refreshRadiation(){
  if(window.__STATIC__)return;
  const query=radiationQuery();
  const online=$('weather_source').value==='era5';$('online_weather').classList[online?'remove':'add']('hide');$('local_radiation').classList[online?'add':'remove']('hide');$('radiation_heading').textContent='太阳辐射 · '+(online?'ERA5':'ERA5-Land（本地）');
  try{const d=await jsonRequest('/api/radiation/status?'+query);if(query!==radiationQuery())return;
    if(d.auto_download){
      const cached=Object.entries(d.cached_years||{}).map(([year,end])=>end.endsWith('-12-31')?year+' 年':year+' 年（至 '+end.slice(5)+'）');
      $('radiation_status').textContent=(d.ready?'所选年份气象已缓存在本机。':'所选年份有 '+d.missing_days+' 天需联网获取。')+
        (cached.length?' 当前坐标已缓存 '+cached.join('、')+'。':' 当前坐标暂无气象缓存。');
    }else{
      $('radiation_status').textContent=d.ready?'所选年份辐射已齐备 · 格点 '+d.grid.join(', ')+'°':'所选年份缺 '+d.missing_days+' 天辐射，请导入 CSV。'+(d.complete_years.length?' 已有完整年份：'+d.complete_years.join('、'):'');
    }
    return d;
  }catch(e){if(query===radiationQuery())$('radiation_status').textContent=e.message;}
}
$('years').addEventListener('change',refreshRadiation);
$('weather_source').onchange=()=>{if($('weather_source').value==='builtin')Array.from($('years').options).forEach(o=>{if(Number(o.value)>2024)o.selected=false;});refreshRadiation();};
$('weather_download').onclick=async()=>{const b=$('weather_download');b.disabled=true;status('正在获取所选年份气象…');try{const d=await jsonRequest('/api/weather/download',collect());status((d.cache_fallback?'联网更新失败，当前可用缓存为 ':'气象已缓存 ')+d.start+' 至 '+d.end+'，共 '+d.days+' 天。');}catch(e){status(e.message);}finally{await refreshRadiation();b.disabled=false;}};
$('weather_export').onclick=async()=>{try{const r=await fetch('/api/weather/export?'+radiationQuery());if(!r.ok)throw new Error((await r.json()).error||'导出失败');download(await r.blob(),filename('_ERA5逐日气象.csv'));}catch(e){status(e.message);}};
$('radiation_import').onclick=async()=>{
  if(window.__STATIC__)return status('请在运行中的系统导入辐射数据。');
  const file=$('radiation_file').files[0];if(!file)return status('请先选择 ERA5-Land 辐射 CSV。');
  const button=$('radiation_import');button.disabled=true;
  try{if(file.size>7000000)throw new Error('CSV 超过 7 MB，请按年分批导入');
    const d=await jsonRequest('/api/radiation/import',{lat:$('lat').value,lon:$('lon').value,format:$('radiation_format').value,filename:file.name,csv_text:await file.text()});
    status('已导入 '+d.imported_days+' 天辐射（'+d.start+' 至 '+d.end+'）。'+(d.dropped_edge_days.length?' 已排除首尾不完整日：'+d.dropped_edge_days.join('、'):'')+'覆盖所选年份后即可计算。');await refreshRadiation();
  }catch(e){status('辐射导入失败：'+e.message);}finally{button.disabled=false;}
};
$('radiation_request').onclick=async()=>{try{const d=await jsonRequest('/api/radiation/request?'+radiationQuery());download(new Blob([JSON.stringify(d,null,2)],{type:'application/json'}),'ERA5-Land_CDS下载请求.json');}catch(e){status(e.message);}};
async function run(body){
  if(window.__STATIC__)return status('本页为离线报告，请启动系统后重新计算。');
  if($('run').disabled)return;
  const t0=Date.now();$('run').disabled=true;$('scan').disabled=true;$('run').textContent='计算中…';$('spin').classList.add('on');
  try{const radiation=await jsonRequest('/api/radiation/status?'+radiationQuery());if(!radiation.ready&&!radiation.auto_download){await refreshRadiation();throw new Error('所选年份的 ERA5-Land 辐射尚未齐备，请先导入 CSV。');}if(body.weather_source!=='builtin'&&!body.coordinates_confirmed){if(!await locateCoordinates())throw new Error('未能按坐标确定行政区，请检查左侧定位提示。');body={...body,place:$('place').value,area_code:$('area').value,lat:$('lat').value,lon:$('lon').value,coordinates_confirmed:true};}const d=await runJob(body,t0);render(d);status('完成，用时 '+f((Date.now()-t0)/1000)+' 秒。');await refreshRadiation();}
  catch(e){status('计算失败：'+e.message);}
  finally{$('run').disabled=false;$('scan').disabled=false;$('run').textContent='按综合损失估算寻优';$('spin').classList.remove('on');}
}
$('run').onclick=()=>run(collect());
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));t.classList.add('on');
  ['p1','p2','p3','p4','p5'].forEach(p=>$(p).classList.add('hide'));$(t.dataset.t).classList.remove('hide');
});
function render(d){
  lastResult=d;const o=d.opt||{},m=d.meta||{};$('result').style.display='block';
  for(const [id,k,n] of [['v_t','tilt',1],['v_s','s',2],['v_h','h',2],['v_g','g',2],['v_under','under',3],['v_y','yield_pct',1]])$(id).textContent=f(o[k],n);
  $('v_gcr').textContent=o.gcr==null?'—':f(o.gcr*100,1);$('v_e').textContent=o.energy==null?'—':f(o.energy/1000,1);$('v_n').textContent=o.net==null?'—':f(o.net/10000,2);
  const warnings=[...(d.warnings||[])];if(o.feasible===false&&!warnings.some(x=>x.includes('未找到')))warnings.push('当前方案未满足设定的产量约束。');
  $('warn').style.display=warnings.length?'block':'none';$('warn').textContent=warnings.join(' ');
  const actualPeriod=m.calculation_basis==='actual_period';
  $('meta').textContent=(o.feasible===false?'未满足约束的参考方案':'当前搜索范围内的推荐方案')+' · '+(m.place||'')+' · '+(actualPeriod?m.date_start+' 至 '+m.date_end+'（'+m.days+' 天）':(m.years||[]).join('、'))+' · 均温 '+f(m.tmean)+'℃ · 累计降水 '+f(m.precip)+' mm';
  $('energy_metric_label').textContent=actualPeriod?'时段累计发电量':'发电量';$('net_metric_label').textContent=actualPeriod?'时段净收益估算':'净收益';$('yield_metric_label').textContent=m.crop_scope?.yield_basis==='observed_period_equivalent'?'相对时段产量当量（露地=100）':'相对产量（露地=100）';
  $('data_note').textContent='气象来源：'+(m.source||'未记录')+'；太阳几何/辐射坐标：'+f(m.lat,4)+'°N，'+f(m.lon,4)+'°E；湿度：'+(m.humidity_source||'未记录')+'。订正前：'+f(m.before?.tmean)+'℃ / '+f(m.before?.precip)+' mm；本次使用：'+f(m.tmean)+'℃ / '+f(m.precip)+' mm。'+(m.correction?'订正依据：'+m.correction.reference:'未进行均值订正。')+(m.quality?' 缺测天数 '+m.quality.missing_days+(m.quality.longest_zero_precip_days!=null?'，最长连续零降水 '+m.quality.longest_zero_precip_days+' 天。':'。'):'');
  if(m.radiation)$('data_note').textContent+=' 辐射：'+m.radiation.dataset+'；格点 '+(m.radiation.grid||[]).join(', ')+'°；日界 '+(m.radiation.day_timezone||'未记录')+'；单位 '+(m.radiation.unit||'未记录')+'。'+(m.meteorology_source||'');
  const losses=o.pv_losses;
  $('pv_shading_note').textContent=losses?(actualPeriod?'时段综合电量差 ':'综合年电量差 ')+f(o.pv_shading_loss*100,2)+'% · 排间直射 −'+f(losses.row_direct*100,2)+'% · 间隙恢复 +'+f(losses.gap_recovery*100,2)+'% · 散射/反射 −'+f(losses.diffuse*100,2)+'% · 电气失配 −'+f(losses.mismatch*100,2)+'%':'历史结果未记录完整损失，请重新计算。';
  const r=d.request||{};
  const info=d.crop_info||cropTemplates[r.crop]||{};
  $('report_params').textContent='计算时间 '+(d.created_at||'未记录')+' · 版本 '+(d.version||'未记录')+' · 作物 '+(r.crop||'冬小麦')+' · 作物单价 '+f(r.crop_price??(r.crop==='冬油菜'?r.economic?.rape_price:r.economic?.wheat_price),2)+' 元/kg'+(info.product?'（'+info.product+'）':'')+' · 生育期 '+(info.season||'见模板')+' · 试验水平 '+(r.levels||'未记录')+' · 坡面宽 '+(r.width||4.18)+' m · 组件长 '+(r.mlen||2.28)+' m · 测点高度 '+(r.canopy??1)+' m · 最低相对产量 '+(r.y_min??60)+'% · 权重（收益/产量/均匀）'+[r.w_energy??.4,r.w_yield??.4,r.w_cv??.2].join('/')+' · 范围 '+keys.map(k=>names[k]+':'+(r[k+'_fix']?r[k+'_val']:r[k+'_lo']+'～'+r[k+'_hi'])).join('；')+' · '+Object.keys(economicFields).filter(k=>r.economic?.[k]!=null).map(k=>economicLabels[k]+' '+r.economic[k]).join('；');
  if(r.pv_options)$('report_params').textContent+=' · 接线估算 '+r.pv_options.modules_per_string+' 单元/串，'+r.pv_options.bypass_blocks+' 分区/单元，'+(r.pv_options.block_axis==='east_west'?'东西向':'坡向')+'分区，排间错位 '+r.pv_options.row_offset+' m';
  for(let i=1;i<=3;i++)if(d.plots?.['r'+i])$('i'+i).src=d.plots['r'+i];
  $('tbl').innerHTML='<tr><th>#</th><th>β/°</th><th>S/m</th><th>H/m</th><th>G/m</th><th>覆盖率</th><th>板下采光</th><th>相对产量/%</th><th>发电 MWh/hm²</th><th>净收益 万元/hm²</th><th>LER</th></tr>'+(d.runs||[]).map((x,i)=>'<tr>'+[i+1,f(x.tilt),f(x.s,2),f(x.h,2),f(x.g,2),f(x.gcr,3),f(x.under,3),f(x.yield_pct),x.energy==null?'—':f(x.energy/1000),x.net==null?'—':f(x.net/10000,2),f(x.ler,2)].map(v=>'<td>'+v+'</td>').join('')+'</tr>').join('');
  $('r2').textContent='拟合 R²：净收益 '+f(d.r2?.net,3)+'，产量 '+f(d.r2?.yield,3)+'，均匀性 '+f(d.r2?.cv,3)+'。试验点 '+(d.runs||[]).length+'；活跃变量 '+(d.active?.join('、')||'无，单方案未拟合响应面')+'。';
  const sens=d.sensitivity||[], max=Math.max(1,...sens.map(x=>Math.abs(x.net_change)));
  $('sensitivity').innerHTML='<table><tr><th>因子</th><th>扰动值</th><th>净收益变化 元/hm²</th><th>产量变化 百分点</th><th>幅度</th></tr>'+[...sens].sort((a,b)=>Math.abs(b.net_change)-Math.abs(a.net_change)).map(x=>`<tr><td>${esc(names[x.factor]||x.factor)}</td><td>${f(x.value,3)}</td><td>${f(x.net_change)}</td><td>${f(x.yield_pct_change,2)}</td><td><span class="bar" style="width:${Math.abs(x.net_change)/max*100}px"></span></td></tr>`).join('')+'</table>';
  $('diagnostics').innerHTML=Object.entries(d.diagnostics||{}).map(([key,x])=>`<p>${esc({net:'净收益',yield:'产量',cv:'变异系数',under:'板下采光'}[key]||key)}：样本 ${f(x.n,0)} / 参数 ${f(x.parameters,0)} / 秩 ${f(x.rank,0)}；RMSE ${f(x.rmse,3)}；留一 RMSE ${f(x.loo_rmse,3)}；调整 R² ${f(x.adjusted_r2,4)}；推荐点内核值 ${f(x.core_at_best,3)}，拟合值 ${f(x.surrogate_at_best,3)}，拟合−内核 ${f(x.surrogate_error_at_best,3)}；推荐点的响应面 95% 近似预测区间 ${x.prediction_interval_95?x.prediction_interval_95.map(v=>f(v,3)).join('～'):'不可估计'}。</p><table><tr><th>来源</th><th>df</th><th>平方和</th><th>F</th><th>p</th></tr>${(x.anova||[]).map(a=>`<tr><td>${esc(a.term)}</td><td>${f(a.df,0)}</td><td>${f(a.ss,3)}</td><td>${f(a.f,3)}</td><td>${ptext(a.p)}</td></tr>`).join('')}</table><p>失拟 p：${ptext(x.lack_of_fit_p)}。${esc(x.lack_of_fit_note)} ${esc(x.note)}</p>`).join('')||'全部变量固定，本次没有响应面拟合。';
}
function filename(suffix){return '农光互补_'+(lastResult?.meta?.place||$('place').value).replace(/[\\/:*?"<>|]/g,'_')+'_'+new Date().toLocaleDateString('sv-SE')+suffix;}
function download(blob,name){const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
$('config_export').onclick=()=>download(new Blob([JSON.stringify({schema:'avopt-config-1.6',request:collect()},null,2)],{type:'application/json'}),filename('_参数.json'));
$('json_export').onclick=()=>{if(lastResult)download(new Blob([JSON.stringify(lastResult,null,2)],{type:'application/json'}),filename('_方案.json'));};
$('import_file').onchange=async e=>{const file=e.target.files[0];if(!file)return;try{if(file.size>8e6)throw new Error('文件超过 8 MB');const d=JSON.parse(await file.text());if(!d.request||typeof d.request!=='object')throw new Error('缺少方案参数 request');fill(d.request);if(d.opt)render(d);status('已导入；再次计算时服务端会重新校验地区和参数。');}catch(err){status('导入失败：'+err.message);}finally{e.target.value='';}};
$('excel_export').onclick=async()=>{if(!lastResult)return;if(window.__STATIC__)return status('Excel 导出需要运行中的本地服务；离线报告可导出 JSON 或打印 PDF。');try{const r=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(lastResult)});if(!r.ok)throw new Error((await r.json()).error||'导出失败');download(await r.blob(),filename('_明细.xlsx'));}catch(e){status(e.message);}};
$('print').onclick=()=>{if(lastResult){document.title=filename('_报告');window.print();}};
function showSaved(){
  $('saved_list').innerHTML=saved.map((x,i)=>`<div class="saved-row"><input type="checkbox" data-compare="${i}" aria-label="选择方案 ${i+1}"><span>${esc(x.label)}</span><button data-load="${i}">载入</button><button data-remove="${i}">删除</button></div>`).join('')||'<p class="note">尚无保存的方案。</p>';
  document.querySelectorAll('[data-load]').forEach(b=>b.onclick=()=>{const d=saved[Number(b.dataset.load)].data;fill(d.request);render(d);status('已载入保存结果；修改输入后请重新计算。');});
  document.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{const next=saved.filter((_,i)=>i!==Number(b.dataset.remove));try{localStorage.setItem('avopt_scenarios_v11',JSON.stringify(next));saved=next;showSaved();}catch(e){status(e.message);}});
}
$('save').onclick=()=>{if(!lastResult)return;if(saved.length>=6)return status('已保存 6 项，请先导出并删除不需要的方案。');const x={label:(lastResult.meta?.place||'方案')+' · '+(lastResult.created_at||'')+' · 产量 '+f(lastResult.opt.yield_pct)+'%',data:lastResult};try{localStorage.setItem('avopt_scenarios_v11',JSON.stringify([...saved,x]));saved.push(x);showSaved();status('方案已保存在当前浏览器。');}catch(e){status('本机保存空间不足，请导出 JSON。');}};
$('compare').onclick=()=>{const selected=Array.from(document.querySelectorAll('[data-compare]:checked'),x=>saved[Number(x.dataset.compare)]);if(selected.length<2||selected.length>3)return status('请选择 2～3 个已保存方案。');const fields=[['倾角 °','tilt'],['跨度 m','s'],['安装高度 m','h'],['间隙 m','g'],['相对产量 %','yield_pct'],['发电量 kWh/hm²','energy'],['净收益 元/hm²','net'],['LER','ler']];$('comparison').innerHTML='<table><tr><th>指标</th>'+selected.map(x=>'<th>'+esc(x.label)+'</th>').join('')+'</tr>'+fields.map(([label,k])=>'<tr><td>'+label+'</td>'+selected.map(x=>'<td>'+f(x.data.opt[k],2)+'</td>').join('')+'</tr>').join('')+'<tr><td>约束满足</td>'+selected.map(x=>'<td>'+(x.data.opt.feasible?'是':'否')+'</td>').join('')+'</tr></table><p class="note">请结合各方案的地区、年份、气象订正和价格参数解释差异。</p>';};
$('scan').onclick=()=>{if(!lastResult)return;const r=collect(),k=$('scan_factor').value;keys.forEach(x=>{r[x+'_fix']=x!==k;if(x!==k)r[x+'_val']=lastResult.opt[x];});fill(r);run(r);};
showSaved();updateCrop();updateDesign();if(!window.__STATIC__)refreshRadiation();
