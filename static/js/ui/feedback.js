/* Shared feedback form. Requests only run after the user submits. */
(() => {
  const wrapper = document.createElement('div');
  wrapper.innerHTML = `<button id="fb-fab" type="button" aria-haspopup="dialog" aria-controls="fb-overlay">Feedback</button>
  <div id="fb-overlay" hidden><section class="as-feedback-card" role="dialog" aria-modal="true" aria-labelledby="fb-heading">
  <button id="fb-close" type="button" aria-label="Close feedback">&times;</button>
  <p class="as-eyebrow">Help shape the club</p><h2 id="fb-heading">Send feedback</h2><p>Found a bug or have an idea? Tell us about it.</p>
  <div id="fb-msg" role="status" hidden></div><form id="fb-form">
  <label for="fb-title">Title <span>(required)</span></label><input id="fb-title" type="text" placeholder="A brief summary" required>
  <label for="fb-desc">Description</label><textarea id="fb-desc" rows="4" placeholder="Tell us more..."></textarea>
  <button id="fb-submit" type="submit" class="btn btn-primary btn-block">Send feedback</button></form></section></div>`;
  document.body.append(wrapper);
  const byId = id => document.getElementById(id);
  const fab=byId('fb-fab'), overlay=byId('fb-overlay'), close=byId('fb-close');
  const title=byId('fb-title'), desc=byId('fb-desc'), submit=byId('fb-submit'), msg=byId('fb-msg');
  let previousFocus, closeTimer, pendingRequest;
  let requestVersion = 0;
  fab.addEventListener('click', () => {previousFocus=document.activeElement;clearTimeout(closeTimer);overlay.hidden=false;msg.hidden=true;submit.disabled=false;submit.textContent='Send feedback';title.focus();});
  const closeModal=() => {overlay.hidden=true;clearTimeout(closeTimer);requestVersion++;pendingRequest?.abort();pendingRequest=null;title.value='';desc.value='';previousFocus?.focus();};
  close.addEventListener('click',closeModal);
  overlay.addEventListener('click',event=>{if(event.target===overlay) closeModal();});
  document.addEventListener('keydown',event=>{
    if(overlay.hidden) return;
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeModal();}
    if(event.key==='Tab'){
      const focusable=[...overlay.querySelectorAll('button,input,textarea')].filter(el=>!el.disabled);
      const first=focusable[0],last=focusable.at(-1);
      if(!focusable.includes(document.activeElement)){event.preventDefault();(event.shiftKey?last:first).focus();}
      else if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
    }
  },true);
  const showMsg=(text,ok)=>{msg.hidden=false;msg.textContent=text;msg.className=ok?'as-feedback-success':'as-feedback-error';};
  byId('fb-form').addEventListener('submit',async event=>{
    event.preventDefault();const summary=title.value.trim();if(!summary){showMsg('Please enter a title.',false);title.focus();return;}
    const submitFocused=document.activeElement===submit;
    submit.disabled=true;submit.textContent='Sending...';
    if(submitFocused) desc.focus();
    const version=++requestVersion;
    const controller=new AbortController();pendingRequest=controller;
    try{
      const response=await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:summary,description:desc.value.trim()}),signal:controller.signal});
      const data=await response.json();
      if(version!==requestVersion||overlay.hidden) return;
      if(response.ok&&data.ok){showMsg('Thanks for your feedback!',true);title.value='';desc.value='';closeTimer=setTimeout(closeModal,1400);}
      else{showMsg(data.error||'Could not send feedback. Try again.',false);submit.disabled=false;submit.textContent='Send feedback';}
    }catch{if(version!==requestVersion||overlay.hidden) return;showMsg('Connection failed. Try again.',false);submit.disabled=false;submit.textContent='Send feedback';}
    finally{if(pendingRequest===controller) pendingRequest=null;}
  });
})();
