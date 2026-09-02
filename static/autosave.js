// autosave.js — background auto-save to localStorage + history, like Chrome
// Saves game state on each move, restores on history navigation or reload.
// No server required for restore; server remains source of truth when online.
(function(){
  const KEY = 'agent_soccer_autosave';
  const MAX_HISTORY = 50;

  function save(state){
    try{
      if(!state || !state.ball) return;
      const entry = {
        ts: Date.now(),
        kick: state.kick_count||0,
        scoreA: state.score_a||0,
        scoreB: state.score_b||0,
        ball: state.ball,
        playersA: state.players_a,
        playersB: state.players_b,
        isA: state.is_player_a,
        gameOver: !!state.game_over,
        winner: state.winner||null
      };
      // localStorage
      let hist = [];
      try{ hist = JSON.parse(localStorage.getItem(KEY)||'[]'); }catch(e){}
      hist.push(entry);
      if(hist.length>MAX_HISTORY) hist = hist.slice(-MAX_HISTORY);
      localStorage.setItem(KEY, JSON.stringify(hist));
      // history API
      try{
        const url = location.pathname + '#k' + entry.kick;
        history.pushState({kick: entry.kick, ts: entry.ts}, '', url);
      }catch(e){}
      // also try to persist to IndexedDB for larger data (optional)
      if(window.indexedDB){
        try{
          const req = indexedDB.open('agent_soccer', 1);
          req.onupgradeneeded = function(e){
            const db=e.target.result;
            if(!db.objectStoreNames.contains('saves')){
              db.createObjectStore('saves', {autoIncrement:true});
            }
          };
          req.onsuccess = function(e){
            const db=e.target.result;
            const tx=db.transaction('saves','readwrite');
            tx.objectStore('saves').add(entry);
          };
        }catch(e){}
      }
    }catch(e){}
  }

  function loadLatest(){
    try{
      const hist = JSON.parse(localStorage.getItem(KEY)||'[]');
      return hist.length ? hist[hist.length-1] : null;
    }catch(e){ return null; }
  }

  function loadHistory(){
    try{ return JSON.parse(localStorage.getItem(KEY)||'[]'); }catch(e){ return []; }
  }

  function clear(){
    try{ localStorage.removeItem(KEY); }catch(e){}
    try{
      if(window.indexedDB){
        const req=indexedDB.open('agent_soccer',1);
        req.onsuccess=function(e){
          const db=e.target.result;
          const tx=db.transaction('saves','readwrite');
          tx.objectStore('saves').clear();
        };
      }
    }catch(e){}
  }

  // Handle back/forward
  window.addEventListener('popstate', function(e){
    const st = e.state;
    if(st && typeof st.kick !== 'undefined'){
      // dispatch event for game to restore
      window.dispatchEvent(new CustomEvent('autosave:restore', {detail: st}));
    }
  });

  // Expose
  window.Autosave = { save, loadLatest, loadHistory, clear, KEY };

  // Background worker: periodically save if gameState exists
  let lastKick = -1;
  setInterval(function(){
    try{
      const gs = window.gameState || window.lastState || null;
      if(gs && gs.kick_count !== lastKick){
        save(gs);
        lastKick = gs.kick_count;
      }
    }catch(e){}
  }, 1500);

  // Also hook into fetch for /move and /ai_move to capture state
  const origFetch = window.fetch;
  window.fetch = function(input, init){
    return origFetch(input, init).then(resp=>{
      const url = typeof input==='string' ? input : input.url||'';
      if(url.includes('/move') || url.includes('/ai_move') || url.includes('/reset')){
        resp.clone().json().then(data=>{
          if(data && data.ball) save(data);
          else if(data && data.game) save(data.game);
        }).catch(()=>{});
      }
      return resp;
    });
  };

  console.log('Autosave ready — history + localStorage');
})();
