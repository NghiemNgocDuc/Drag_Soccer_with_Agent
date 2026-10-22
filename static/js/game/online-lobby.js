// Native buttons with standard keyboard tab navigation; no polling or animation.
export function setupOnlineLobbyTabs() {
  const tabs=[...document.querySelectorAll('[data-online-tab]')];
  function select(name) {
    if (!tabs.some(tab=>tab.dataset.onlineTab===name)) return;
    for (const tab of tabs) {
      const active=tab.dataset.onlineTab===name;
      tab.setAttribute('aria-selected',String(active));
      tab.tabIndex=active?0:-1;
      document.getElementById(tab.getAttribute('aria-controls')).hidden=!active;
    }
  }
  for (const [index,tab] of tabs.entries()) {
    tab.addEventListener('click',()=>select(tab.dataset.onlineTab));
    tab.addEventListener('keydown',event=>{
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
      if(tabs[next].disabled)return;
      select(tabs[next].dataset.onlineTab);tabs[next].focus();
    });
  }
  window.setOnlineLobbyTab=select;
  document.getElementById('online-join-code').addEventListener('keydown',event=>{
    if(event.key==='Enter'){event.preventDefault();window.joinOnlineByCode();}
  });
  document.getElementById('online-room-password').addEventListener('keydown',event=>{
    if(event.key==='Enter'){event.preventDefault();window.joinOnlineByCode();}
  });
  return {select};
}
