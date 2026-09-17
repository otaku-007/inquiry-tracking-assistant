(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  let items = [];
  for (let k = 0; k < 40; k++) {
    items = document.querySelectorAll('.im-message-flow .message-item-wrapper');
    if (items.length > 0) break;
    await sleep(300);
  }
  await sleep(800);
  items = document.querySelectorAll('.im-message-flow .message-item-wrapper');
  const out = [];
  const n = Math.min(items.length, 8);
  for (let i = 0; i < n; i++) {
    const it = items[i];
    const cls = it.className || '';
    let side = cls.includes('item-right') ? 'SELLER(我方)' : (cls.includes('item-left') ? 'BUYER(买家)' : 'UNKNOWN');
    const time = (it.querySelector('.item-base-info span:last-child') || {}).innerText || '';
    const name = (it.querySelector('.item-base-info .name') || {}).innerText || '';
    const uc = it.querySelector('.user-content') || it;
    let text = (uc.innerText || '').replace(/\s+/g, ' ').trim();
    const isCard = /-card/.test(cls);
    const avatar = (it.querySelector('.avatar, [class*="avatar"]') || {}).innerText || '';
    out.push({ i, side, time, name, avatar: avatar.slice(0,20), isCard, text: text.slice(0, 200) });
  }
  // header / owner
  const hdr = document.querySelector('.im-chat-container, .main-content-chat');
  const buyerHdr = document.querySelector('.ggs-massage-buyer-card, .chat-top-risk-notice');
  return JSON.stringify({
    url: location.href,
    buyerCard: buyerHdr ? buyerHdr.innerText.replace(/\s+/g,' ').slice(0,120) : '',
    count: items.length,
    msgs: out
  }, null, 1);
})()