const pct = x => ((Number(x)||0)*100).toFixed(2)+'%';
const eur = x => '€'+Number(x||0).toFixed(2);
const num = x => Number(x||0).toLocaleString('nl-NL');
const esc = s => String(s ?? '').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const dt = x => x ? new Date(x).toLocaleString('nl-NL',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';
const reasonNames={too_few_books:'Too few bookmakers',edge_below_threshold:'Edge below gate',ev_below_threshold:'EV below gate',market_vig_too_high:'Market vig too high',already_started:'Already started'};

function toast(t,ms=3000){const e=document.getElementById('toast');e.textContent=t;e.style.display='block';clearTimeout(window.__toast);window.__toast=setTimeout(()=>e.style.display='none',ms)}
async function getJSON(url,opts){const r=await fetch(url,opts);let j={};try{j=await r.json()}catch{}if(!r.ok)throw new Error(j.detail||j.error||`HTTP ${r.status}`);return j}
function reasons(s){return (s||'').split(',').filter(Boolean).map(r=>`<span class="reason">${esc(reasonNames[r]||r)}</span>`).join('')||'<span class="muted">—</span>'}
function modelMeta(x){return x?.meta?.model||{}}

async function load(){
  try{
    const [k,a,e,b,h,m,sh]=await Promise.all([
      getJSON('/api/kpis'),getJSON('/api/analytics'),getJSON('/api/engine-status'),getJSON('/api/paper-bets'),getJSON('/api/scan-history?limit=15'),getJSON('/api/model-status'),getJSON('/api/shadow?limit=30')
    ]);

    const q=k.quota_remaining==null?'—':num(k.quota_remaining);
    const clv=k.avg_clv==null?'CLV collecting':`avg CLV ${pct(k.avg_clv)}`;
    const defs=[
      ['Equity',eur(k.equity),'paper bankroll'],['P/L',eur(k.pnl),`${k.settled} settled · ${clv}`],['ROI',pct(k.roi),'settled stakes'],['Bets',num(k.bets),'paper ledger'],
      ['Winrate',pct(k.winrate),'settled only'],['Exposure',eur(k.open_exposure),'currently open'],['Last scan',num(k.last_scan_prices),'prices ingested'],['API quota',q,'remaining credits']
    ];
    document.getElementById('kpis').innerHTML=defs.map(x=>`<div class="card"><div class="label">${x[0]}</div><div class="value">${x[1]}</div><div class="subvalue">${x[2]}</div></div>`).join('');

    const last=e.latest_successful_scan;
    const badge=document.getElementById('engineBadge');
    badge.textContent=e.auto_scan_enabled?'AUTO ENGINE ON':'AUTO ENGINE OFF';
    badge.className='engineBadge '+(e.auto_scan_enabled?'ok':'warn');
    const modelBadge=document.getElementById('modelBadge');
    modelBadge.textContent=`MODEL ${m.status}`;
    modelBadge.className='engineBadge '+(m.status==='ACTIVE'?'ok':'learn');

    document.getElementById('engineStats').innerHTML=[
      ['Last scan',last?dt(last.finished_at||last.started_at):'—'],['Next due',dt(e.next_scan_due_at)],['Smart interval',`${e.recommended_scan_interval_minutes} min`],['Latest status',last?last.status:'NO DATA'],
      ['Signals',last?num(last.signals):'0'],['Accepted',last?num(last.accepted):'0'],['Top edge',last&&last.top_edge!=null?pct(last.top_edge):'—'],['Top EV',last&&last.top_ev!=null?pct(last.top_ev):'—']
    ].map(x=>`<div class="engineItem"><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');
    const s=e.strategy;
    document.getElementById('strategyLine').textContent=`Hard gates: ${s.min_bookmakers}+ books · edge ≥ ${pct(s.min_edge)} · EV ≥ ${pct(s.min_ev)} · vig ≤ ${pct(s.max_vig)} · max stake ${pct(s.max_stake_pct)}.`;

    document.getElementById('modelStats').innerHTML=[
      ['Results learned',num(m.results)],['Teams rated',num(m.ratings)],['Mature teams',num(m.mature_teams)],['Avg games',Number(m.avg_games_per_team||0).toFixed(1)],
      ['Max model weight',pct(m.max_model_weight)],['Last result',dt(m.last_result_at)],['Status',m.status],['Outliers removed',num(a.market_health?.outlier_quotes_removed||0)]
    ].map(x=>`<div class="engineItem"><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');

    const bootstrap=m.bootstrap||{};
    if(m.status==='LEARNING'){
      document.getElementById('modelLine').textContent=`Cold-start protection active. Historical bootstrap: ${bootstrap.enabled?'ON':'OFF'} · ${bootstrap.seasons||0} seasons · public-domain source. Model influence grows only after real completed matches are loaded.`;
    }else{
      document.getElementById('modelLine').textContent='Independent Elo/Poisson probabilities have earned model weight; market consensus remains the anchor.';
    }

    const rc=a.reject_counts||{};const total=Object.values(rc).reduce((p,c)=>p+c,0)||1;const entries=Object.entries(rc).sort((x,y)=>y[1]-x[1]);
    document.getElementById('rejects').innerHTML=entries.length?entries.map(([r,c])=>`<div class="rejectRow"><div class="rejectName">${esc(reasonNames[r]||r)}</div><div class="bar"><i style="width:${Math.max(3,c/total*100)}%"></i></div><div class="rejectCount">${c}</div></div>`).join(''):'<div class="muted">No rejection data yet.</div>';

    document.getElementById('shadowStats').innerHTML=[
      ['Tracked',num(sh.total)],['Open',num(sh.open)],['Settled',num(sh.settled)],['Winrate',pct(sh.winrate)],
      ['P/L units',Number(sh.pnl_units||0).toFixed(2)],['ROI / pick',pct(sh.roi_units)],['Avg CLV',sh.avg_clv==null?'—':pct(sh.avg_clv)],['Risk','€0.00']
    ].map(x=>`<div class="engineItem"><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');

    const mh=a.market_health||{};
    document.getElementById('marketHealth').textContent=`${num(mh.positive_ev||0)} +EV · ${Number(mh.avg_books||0).toFixed(1)} books · vig ${pct(mh.avg_vig||0)} · model ${pct(mh.avg_model_reliability||0)}`;

    document.getElementById('nearMisses').innerHTML=(a.near_misses||[]).map(x=>{
      const mm=modelMeta(x);const match=`${esc(x.meta?.home_team||'')} — ${esc(x.meta?.away_team||'')}`;
      return `<article class="pickCard">
        <div class="pickTop"><div><div class="pickMatch">${match}</div><div class="pickSub">${esc(x.book)} · ${x.books} books · vig ${pct(x.vig)}</div></div><span class="pill">${esc(x.outcome)}</span></div>
        <div class="pickMetrics"><div><span>Odds</span><b>${Number(x.odds).toFixed(2)}</b></div><div><span>Edge</span><b class="${x.edge>0?'good':'bad'}">${pct(x.edge)}</b></div><div><span>EV</span><b class="${x.ev>0?'good':'bad'}">${pct(x.ev)}</b></div><div><span>Model</span><b>${pct(mm.reliability||0)}</b></div></div>
        <div class="pickBottom"><div>${reasons(x.reject_reason)}</div><span class="modelSource">${esc(mm.source||'market_only')}</span></div>
      </article>`
    }).join('')||'<div class="muted">No near-miss candidates yet.</div>';

    const accepted=a.accepted||[];
    document.getElementById('acceptedCount').textContent=`${accepted.length} ACCEPTED`;
    document.getElementById('acceptedCount').className='miniBadge '+(accepted.length?'good':'');
    document.getElementById('signals').innerHTML=accepted.map(x=>{const mm=modelMeta(x);return `<tr><td>${esc(x.meta?.home_team||'')} — ${esc(x.meta?.away_team||'')}</td><td><span class="pill">${esc(x.outcome)}</span></td><td>${Number(x.odds).toFixed(2)}</td><td class="good">${pct(x.edge)}</td><td class="good">${pct(x.ev)}</td><td>${pct(mm.reliability||0)}</td><td>${pct(x.confidence)}</td><td>${esc(x.book)}</td></tr>`}).join('')||'<tr><td colspan="8">No accepted signals yet.</td></tr>';

    document.getElementById('bets').innerHTML=b.map(x=>`<tr><td>${esc(x.sport)}</td><td>${esc(x.outcome)}</td><td>${esc(x.bookmaker||'')}</td><td>${Number(x.odds).toFixed(2)}</td><td>${eur(x.stake)}</td><td>${esc(x.status)}</td><td class="${(x.clv||0)>=0?'good':'bad'}">${x.clv==null?'—':pct(x.clv)}</td><td class="${x.pnl>=0?'good':'bad'}">${eur(x.pnl)}</td></tr>`).join('')||'<tr><td colspan="8">No paper bets yet.</td></tr>';

    document.getElementById('scanHistory').innerHTML=h.map(x=>`<tr><td>${dt(x.started_at)}</td><td>${esc(x.trigger)}</td><td><span class="statusText ${String(x.status).toLowerCase()}">${esc(x.status)}</span></td><td>${num(x.snapshots)}</td><td>${num(x.signals)}</td><td>${num(x.accepted)}</td><td>${num(x.paper_bets)}</td><td>${x.top_ev==null?'—':pct(x.top_ev)}</td><td>${x.quota_remaining==null?'—':num(x.quota_remaining)}</td></tr>`).join('')||'<tr><td colspan="9">No audited scans yet.</td></tr>';
  }catch(err){toast(`Dashboard refresh failed: ${err.message}`,5000)}
}

async function runCycle(){
  const btn=document.getElementById('scanBtn');btn.disabled=true;btn.textContent='Scanning…';
  try{const j=await getJSON('/api/run-cycle',{method:'POST'});toast(`Scan: ${num(j.snapshots)} prices · ${num(j.signals)} signals · ${num(j.accepted)} accepted · ${num(j.paper_bets)} bets · ${num(j.shadow_picks||0)} shadow`,5000)}
  catch(err){toast(`Scan failed: ${err.message}`,5000)}
  finally{btn.disabled=false;btn.textContent='Run scan';await load()}
}

async function bootstrapModel(){
  const btn=document.getElementById('bootstrapBtn');btn.disabled=true;btn.textContent='Loading history…';
  try{
    const j=await getJSON('/api/bootstrap-model',{method:'POST'});
    const detail=j.errors?.length?` · ${j.errors.length} source warning(s)`:'';
    toast(`Model bootstrap: ${num(j.historical_fetched||0)} historical fetched · ${num(j.new_results||0)} new results learned${detail}`,7000);
  }catch(err){toast(`Bootstrap failed: ${err.message}`,6000)}
  finally{btn.disabled=false;btn.textContent='Bootstrap model';await load()}
}

async function settle(){
  const btn=document.getElementById('settleBtn');btn.disabled=true;btn.textContent='Settling…';
  try{const j=await getJSON('/api/settle',{method:'POST'});toast(`Settled ${num(j.settled)} bets · learned ${num(j.new_model_results||0)} new results`,4000)}
  catch(err){toast(`Settle failed: ${err.message}`,5000)}
  finally{btn.disabled=false;btn.textContent='Settle results';await load()}
}

load();setInterval(load,30000);
