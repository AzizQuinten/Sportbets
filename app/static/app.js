const pct=x=>((Number(x)||0)*100).toFixed(2)+'%';
const eur=x=>'€'+Number(x||0).toFixed(2);
const num=x=>Number(x||0).toLocaleString('nl-NL');
const dec=x=>x==null?'—':Number(x).toFixed(4);
const esc=s=>String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const dt=x=>x?new Date(x).toLocaleString('nl-NL',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';
const reasonNames={
  too_few_books:'Too few bookmakers',edge_below_threshold:'Edge below gate',ev_below_threshold:'EV below gate',
  market_vig_too_high:'Market vig too high',market_quality_too_low:'Market quality too low',confidence_too_low:'Confidence too low',
  already_started:'Already started',model_not_mature_enough:'Model reliability too low',model_market_disagreement:'Model/market disagreement',
  ev_too_extreme:'EV too extreme',independent_model_unavailable:'Independent model unavailable',adjusted_ev_not_positive:'Adjusted EV not positive',
  odds_outside_core_range:'Odds outside CORE range'
};

function toast(t,ms=3000){const e=document.getElementById('toast');e.textContent=t;e.style.display='block';clearTimeout(window.__toast);window.__toast=setTimeout(()=>e.style.display='none',ms)}
async function getJSON(url,opts){const r=await fetch(url,opts);let j={};try{j=await r.json()}catch{}if(!r.ok){const d=j.detail;throw new Error(typeof d==='object'?(d.message||JSON.stringify(d)):(d||j.error||`HTTP ${r.status}`))}return j}
function reasons(s){return(s||'').split(',').filter(Boolean).map(r=>`<span class="reason">${esc(reasonNames[r]||r)}</span>`).join('')||'<span class="muted">—</span>'}
function healthClass(s){return s==='STABLE'?'ok':s==='DEGRADED'?'warn':s==='WATCH'?'warn':'learn'}
function matchName(x){const h=x?.home_team||x?.meta?.home_team||'',a=x?.away_team||x?.meta?.away_team||'';return h&&a?`${h} — ${a}`:(x?.sport||'Unknown match')}

function renderDiagnostics(d){
  const x=d.latest||{};const badge=document.getElementById('diagBadge');const state=d.scanner_running?'RUNNING':(x.status||'NO SCAN');
  badge.textContent=state;badge.className='miniBadge '+(state==='SUCCESS'?'good':state==='FAILED'?'bad':'');
  document.getElementById('diagStats').innerHTML=[
    ['Version',d.version||'—'],['Latest scan',dt(x.finished_at||x.started_at)],['Status',state],['Sports',num(x.sports||0)],
    ['Prices',num(x.snapshots||0)],['Signals',num(x.signals||0)],['Quota',x.quota_remaining==null?'—':num(x.quota_remaining)],['Tracked leagues',num((d.tracked_sports||[]).length)]
  ].map(v=>`<div class="engineItem"><span>${v[0]}</span><b>${v[1]}</b></div>`).join('');
  const er=document.getElementById('diagError');er.textContent=x.error||(state==='RUNNING'?'Scan is running in the background.':'No scanner error recorded.');er.className='diagError '+((x.error&&(x.status==='FAILED'||x.status==='PARTIAL'))?'hasError':'');
}

function renderValidation(v){
  const c=v.core||{},x=v.exploration||{},s=v.scout||{},r=v.recent_core_20||{},h=v.health||{};
  const b=document.getElementById('validationBadge');b.textContent=`${h.status||'UNKNOWN'} · CORE RISK x${Number(h.risk_multiplier??1).toFixed(2)}`;b.className='miniBadge '+(h.status==='STABLE'?'good':h.status==='DEGRADED'?'bad':'');
  document.getElementById('validationStats').innerHTML=[
    ['CORE settled',num(c.n||0)],['CORE ROI',pct(c.roi||0)],['CORE avg CLV',c.avg_clv==null?'—':pct(c.avg_clv)],['Positive CLV',c.positive_clv_rate==null?'—':pct(c.positive_clv_rate)],
    ['Profit factor',c.profit_factor==null?'—':Number(c.profit_factor).toFixed(2)],['Recent 20 ROI',pct(r.roi||0)],['Explore settled',num(x.n||0)],['Scout settled',num(s.n||0)]
  ].map(v=>`<div class="engineItem"><span>${v[0]}</span><b>${v[1]}</b></div>`).join('');
  document.getElementById('leagueValidation').innerHTML=(v.by_sport||[]).slice(0,14).map(x=>`<tr><td>${esc(x.key)}</td><td>${num(x.n)}</td><td>${pct(x.roi)}</td><td>${pct(x.winrate)}</td><td>${x.avg_clv==null?'—':pct(x.avg_clv)}</td><td>${dec(x.model_brier)}</td><td>${dec(x.market_brier)}</td><td>${eur(x.max_drawdown)}</td></tr>`).join('')||'<tr><td colspan="8">Waiting for settled paper bets.</td></tr>';
  const rs=(h.reasons||[]).join(' · ');document.getElementById('validationNote').textContent=`CORE alone controls stake risk. ${rs?`Current note: ${rs}. `:''}${v.methodology?.note||''}`;
}

function renderFunnel(a){
  const f=a.funnel||{};const entries=[
    ['Signals',f.signals||0,'all priced outcomes'],['Model covered',f.model_covered||0,'independent team evidence'],['High quality',f.high_market_quality||0,'clean enough market'],
    ['Adj EV > 0',f.positive_adjusted_ev||0,'uncertainty-adjusted'],['SCOUT prices',f.scout_price_candidates||0,'leave-one-out market edge'],['CORE accepted',f.core_accepted||0,'full stack passed']
  ];
  const max=Math.max(1,...entries.map(x=>Number(x[1]||0)));
  document.getElementById('funnel').innerHTML=entries.map((x,i)=>`<div class="funnelStep"><div class="funnelTop"><b>${esc(x[0])}</b><strong>${num(x[1])}</strong></div><div class="funnelBar"><i style="width:${Math.max(2,(Number(x[1]||0)/max)*100)}%"></i></div><small>${esc(x[2])}</small></div>`).join('');
  const mh=a.market_health||{};document.getElementById('funnelBadge').textContent=`MODEL COVERAGE ${pct(mh.model_coverage||0)}`;
  document.getElementById('marketSummary').textContent=`Avg market quality ${pct(mh.avg_market_quality||0)} · avg books ${Number(mh.avg_books||0).toFixed(1)} · avg vig ${pct(mh.avg_vig||0)} · avg price premium ${pct(mh.avg_price_premium||0)} · avg opportunity ${pct(mh.avg_opportunity_score||0)}.`;
}

function renderBetCards(bets){
  const root=document.getElementById('betCards');
  root.innerHTML=bets.map(x=>`<article class="betCard">
    <div class="betCardHead"><div><span class="pill tier-${String(x.tier||'LEGACY').toLowerCase()}">${esc(x.tier||'LEGACY')}</span><b>${esc(matchName(x))}</b></div><span class="statusText ${String(x.status||'').toLowerCase()}">${esc(x.status)}</span></div>
    <div class="betPick">${esc(x.outcome)} <span>@ ${Number(x.odds).toFixed(2)}</span></div>
    <div class="betGrid"><div><span>Stake</span><b>${eur(x.stake)}</b></div><div><span>Quality</span><b>${x.market_quality==null?'—':pct(x.market_quality)}</b></div><div><span>Research EV</span><b>${x.adjusted_ev==null?'—':pct(x.adjusted_ev)}</b></div><div><span>CLV</span><b>${x.clv==null?'—':pct(x.clv)}</b></div><div><span>Kickoff</span><b>${dt(x.commence_time)}</b></div><div><span>P/L</span><b>${eur(x.pnl)}</b></div></div>
    <div class="betFoot">${esc(x.bookmaker||'')} · ${esc(x.sport||'')}</div>
  </article>`).join('')||'<div class="muted">No paper bets yet.</div>';
}

async function load(){
  try{
    const[k,a,e,b,h,m,sh,d,v]=await Promise.all([
      getJSON('/api/kpis'),getJSON('/api/analytics'),getJSON('/api/engine-status'),getJSON('/api/paper-bets'),getJSON('/api/scan-history?limit=15'),getJSON('/api/model-status'),getJSON('/api/shadow?limit=30'),getJSON('/api/scan-diagnostics'),getJSON('/api/validation')
    ]);
    renderDiagnostics(d);renderValidation(v);renderFunnel(a);renderBetCards(b);
    const q=k.quota_remaining==null?'—':num(k.quota_remaining),clv=k.avg_clv==null?'CLV collecting':`avg CLV ${pct(k.avg_clv)}`;
    document.getElementById('kpis').innerHTML=[
      ['Equity',eur(k.equity),'paper bankroll'],['P/L',eur(k.pnl),`${k.settled} settled · ${clv}`],['ROI',pct(k.roi),'settled stakes'],['Bets',num(k.bets),`${num(k.open_bets||0)} open · ${num(k.core_bets||0)} core · ${num(k.exploration_bets||0)} explore · ${num(k.scout_bets||0)} scout`],
      ['Winrate',pct(k.winrate),'settled only'],['Exposure',eur(k.open_exposure),'currently open'],['Last scan',num(k.last_scan_prices),'prices ingested'],['API quota',q,'remaining credits']
    ].map(x=>`<div class="card"><div class="label">${x[0]}</div><div class="value">${x[1]}</div><div class="subvalue">${x[2]}</div></div>`).join('');

    const last=e.latest_successful_scan,badge=document.getElementById('engineBadge');badge.textContent=e.scanner_running?'SCANNER RUNNING':(e.auto_scan_enabled?'AUTO ENGINE ON':'AUTO ENGINE OFF');badge.className='engineBadge '+(e.auto_scan_enabled?'ok':'warn');
    const mb=document.getElementById('modelBadge');mb.textContent=`MODEL ${m.status}`;mb.className='engineBadge '+(m.status==='ACTIVE'?'ok':'learn');
    const hb=document.getElementById('healthBadge');hb.textContent=`RISK ${v.health?.status||'LEARNING'}`;hb.className='engineBadge '+healthClass(v.health?.status);
    document.getElementById('engineStats').innerHTML=[['Last scan',last?dt(last.finished_at||last.started_at):'—'],['Next due',dt(e.next_scan_due_at)],['Smart interval',`${e.recommended_scan_interval_minutes} min`],['Latest status',e.latest_scan?.status||'NO DATA'],['Signals',last?num(last.signals):'0'],['CORE accepted',last?num(last.accepted):'0'],['Top edge',last&&last.top_edge!=null?pct(last.top_edge):'—'],['Top EV',last&&last.top_ev!=null?pct(last.top_ev):'—']].map(x=>`<div class="engineItem"><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');
    const s=e.strategy;document.getElementById('strategyLine').textContent=`CORE base: ${s.min_bookmakers}+ books · edge ≥ ${pct(s.min_edge)} · EV ≥ ${pct(s.min_ev)} · quality ≥ ${pct(s.min_market_quality)} · confidence ≥ ${pct(s.min_confidence)}. Gates adapt only when market quality + model reliability justify it. League daily cap ${pct(s.max_league_daily_exposure_pct)}. SCOUT uses an independent leave-one-out market reference.`;

    document.getElementById('modelStats').innerHTML=[['Results learned',num(m.results)],['Teams rated',num(m.ratings)],['Leagues learned',num(m.leagues_learned||0)],['Mature teams',num(m.mature_teams)],['Avg games',Number(m.avg_games_per_team||0).toFixed(1)],['Max model weight',pct(m.max_model_weight)],['Last result',dt(m.last_result_at)],['Status',m.status]].map(x=>`<div class="engineItem"><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');
    document.getElementById('modelLine').textContent=`${m.engine||'Independent football model'}. Market stays the anchor and model shifts remain capped. Historical bootstrap: ${m.bootstrap?.seasons||'—'} seasons.`;

    const rc=a.reject_counts||{},total=Object.values(rc).reduce((p,c)=>p+c,0)||1,entries=Object.entries(rc).sort((x,y)=>y[1]-x[1]);
    document.getElementById('rejects').innerHTML=entries.length?entries.map(([r,c])=>`<div class="rejectRow"><div class="rejectName">${esc(reasonNames[r]||r)}</div><div class="bar"><i style="width:${Math.max(3,c/total*100)}%"></i></div><div class="rejectCount">${c}</div></div>`).join(''):'<div class="muted">No rejection data yet.</div>';
    document.getElementById('shadowStats').innerHTML=[['Tracked',num(sh.total)],['Open',num(sh.open)],['Settled',num(sh.settled)],['Winrate',pct(sh.winrate)],['P/L units',Number(sh.pnl_units||0).toFixed(2)],['ROI / pick',pct(sh.roi_units)],['Avg CLV',sh.avg_clv==null?'—':pct(sh.avg_clv)],['Risk','€0.00']].map(x=>`<div class="engineItem"><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');

    const mh=a.market_health||{};document.getElementById('marketHealth').textContent=`Q ${pct(mh.avg_market_quality||0)} · model ${pct(mh.model_coverage||0)} · ${num(mh.positive_market_ev||0)} market +EV`;
    document.getElementById('nearMisses').innerHTML=(a.near_misses||[]).map(x=>{
      const meta=x.meta||{},line=meta.line||{},mm=meta.model||{},match=`${esc(meta.home_team||'')} — ${esc(meta.away_team||'')}`;
      return `<article class="pickCard"><div class="pickTop"><div><div class="pickMatch">${match}</div><div class="pickSub">${esc(x.sport)} · ${esc(x.book)} · ${x.books} books · vig ${pct(x.vig)}</div></div><span class="pill">${esc(x.outcome)}</span></div><div class="pickMetrics"><div><span>Odds</span><b>${Number(x.odds).toFixed(2)}</b></div><div><span>Score</span><b>${pct(meta.opportunity_score||0)}</b></div><div><span>Market EV</span><b>${pct(meta.market_ev||0)}</b></div><div><span>Adj EV</span><b>${pct(meta.adjusted_ev??x.ev)}</b></div><div><span>Quality</span><b>${pct(meta.market_quality||0)}</b></div><div><span>Model rel.</span><b>${pct(mm.reliability||0)}</b></div><div><span>Price premium</span><b>${pct(meta.price_premium||0)}</b></div><div><span>Line move</span><b>${pct(line.odds_move||0)}</b></div></div><div class="pickBottom"><div>${reasons(x.reject_reason)}</div></div></article>`
    }).join('')||'<div class="muted">No watchlist candidates yet.</div>';

    const ac=a.accepted||[];document.getElementById('acceptedCount').textContent=`${ac.length} CORE ACCEPTED`;
    document.getElementById('signals').innerHTML=ac.map(x=>`<tr><td>${esc(x.meta?.home_team||'')} — ${esc(x.meta?.away_team||'')}</td><td>${esc(x.outcome)}</td><td>${Number(x.odds).toFixed(2)}</td><td>${pct(x.edge)}</td><td>${pct(x.ev)}</td><td>${pct(x.meta?.adjusted_ev??x.ev)}</td><td>${pct(x.meta?.market_quality||0)}</td><td>${pct(x.confidence)}</td><td>${pct(x.meta?.opportunity_score||0)}</td><td>${esc(x.book)}</td></tr>`).join('')||'<tr><td colspan="10">No CORE signal cleared every gate this scan.</td></tr>';

    document.getElementById('bets').innerHTML=b.map(x=>`<tr><td><span class="pill">${esc(x.tier||'LEGACY')}</span></td><td>${esc(matchName(x))}</td><td>${esc(x.outcome)}</td><td>${Number(x.odds).toFixed(2)}</td><td>${eur(x.stake)}</td><td>${x.market_quality==null?'—':pct(x.market_quality)}</td><td>${x.adjusted_ev==null?'—':pct(x.adjusted_ev)}</td><td>${esc(x.status)}</td><td>${x.clv==null?'—':pct(x.clv)}</td><td>${eur(x.pnl)}</td></tr>`).join('')||'<tr><td colspan="10">No paper bets yet.</td></tr>';
    document.getElementById('scanHistory').innerHTML=h.map(x=>`<tr><td>${dt(x.started_at)}</td><td>${esc(x.trigger)}</td><td><span class="statusText ${String(x.status).toLowerCase()}">${esc(x.status)}</span></td><td>${num(x.snapshots)}</td><td>${num(x.signals)}</td><td>${num(x.accepted)}</td><td>${num(x.paper_bets)}</td><td>${x.top_ev==null?'—':pct(x.top_ev)}</td><td>${x.quota_remaining==null?'—':num(x.quota_remaining)}</td></tr>`).join('');
    return e;
  }catch(err){toast(`Dashboard refresh failed: ${err.message}`,7000)}
}

async function waitForScan(){const btn=document.getElementById('scanBtn');for(let i=0;i<72;i++){await new Promise(r=>setTimeout(r,2500));const e=await load();if(!e?.scanner_running){btn.disabled=false;btn.textContent='Run scan';toast(`Scan ${e?.latest_scan?.status||'DONE'}. Dashboard updated.`,5000);return}}btn.disabled=false;btn.textContent='Run scan';toast('Scan is still running; watchdog will stop it if it exceeds the runtime limit.',7000)}
async function runCycle(){const btn=document.getElementById('scanBtn');btn.disabled=true;btn.textContent='Starting…';try{const j=await getJSON('/api/run-cycle',{method:'POST'});toast(j.message||`Scan ${j.status}`,4000);btn.textContent='Running…';waitForScan()}catch(err){btn.disabled=false;btn.textContent='Run scan';toast(`Scan request failed: ${err.message}`,12000)}}
async function bootstrapModel(){const btn=document.getElementById('bootstrapBtn');btn.disabled=true;btn.textContent='Loading history…';try{const j=await getJSON('/api/bootstrap-model',{method:'POST'});toast(`Model bootstrap: ${num(j.historical_fetched||0)} historical · ${num(j.new_results||0)} learned · ${num(j.learned_leagues||0)} leagues`,8000)}catch(err){toast(`Bootstrap failed: ${err.message}`,6000)}finally{btn.disabled=false;btn.textContent='Bootstrap model';await load()}}
async function settle(){const btn=document.getElementById('settleBtn');btn.disabled=true;btn.textContent='Settling…';try{const j=await getJSON('/api/settle',{method:'POST'});toast(`Settled ${num(j.settled)} bets · learned ${num(j.new_model_results||0)} results · checked ${num((j.sports_checked||[]).length)} leagues`,6000)}catch(err){toast(`Settle failed: ${err.message}`,6000)}finally{btn.disabled=false;btn.textContent='Settle results';await load()}}
load();setInterval(load,30000);
