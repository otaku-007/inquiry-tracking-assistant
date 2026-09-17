(() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const rows = [...document.querySelectorAll('a.inquiry-item')];
  const out = rows.map(r => {
    const q = s => r.querySelector(s);
    const header = r.querySelector('.aui2-grid-header');
    const src = q('.buyer-product-name-content');
    const msg = q('.buyer-chat-msg');
    const nameEl = q('.aui2-grid-name');
    const cat = q('.aui-grid-category');
    const owner = q('.aui-grid-owner-name');
    const status = q('.aui2-grid-quo-status-col');
    const lvlImg = q('img.level-tag-icon');
    const flag = q('.ui2-flag');
    const hi = [...r.querySelectorAll('div,span,i,em,b')].find(e => clean(e.textContent) === '高意向');
    const parts = clean(header ? header.innerText : '').match(/询价单号：\s*(\d+)\s*更新时间：\s*([0-9\-]+)\s*创建时间：\s*([0-9\-]+)/);
    return {
      id: r.getAttribute('data-trade-id'),
      updateTime: parts ? parts[2] : null,
      createTime: parts ? parts[3] : null,
      source: clean(src ? src.innerText : ''),
      lastMsg: clean(msg ? msg.innerText : '').slice(0, 90),
      buyer: nameEl ? nameEl.getAttribute('title') : null,
      category: clean(cat ? cat.innerText : ''),
      owner: owner ? owner.getAttribute('title') : null,
      status: clean(status ? status.innerText : ''),
      levelImg: lvlImg ? (lvlImg.outerHTML || '').slice(0, 220) : null,
      highIntent: !!hi,
      country: flag ? flag.getAttribute('title') : null,
      href: r.getAttribute('href')
    };
  });
  const pag = document.querySelector('.ui2-pagination-pages');
  const tabNav = [...document.querySelectorAll('.ui2-tab-nav a, .ui2-tab-nav li')].map(e => clean(e.innerText) + '[' + (e.className || '').toString().slice(0, 40) + ']');
  const tabsAll = [...document.querySelectorAll('.ui2-tab-nav > *')].map(e => clean(e.innerText) + '|cls=' + (e.className || '').toString() + '|aria=' + (e.getAttribute('aria-selected') || ''));
  const totalEl = [...document.querySelectorAll('*')].filter(e => /共\s*\d+\s*(条|页)/.test(e.innerText || '') && e.children.length < 4).slice(0, 5).map(e => clean(e.innerText).slice(0, 120));
  return { __result: { count: rows.length, pagination: pag ? clean(pag.innerText) : null, tabsAll, statusTabs: tabNav, totalHints: totalEl, rows: out } };
})()
