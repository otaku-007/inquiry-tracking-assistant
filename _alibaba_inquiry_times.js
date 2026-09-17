(() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const rows = [...document.querySelectorAll('a.inquiry-item')];
  const out = rows.map(r => {
    const header = r.querySelector('.aui2-grid-header');
    const t = clean(header ? header.innerText : '');
    const m = t.match(/询价单号[：:]\s*(\d+)[\s\S]*?更新时间[：:]\s*([0-9:\-]+)[\s\S]*?创建时间[：:]\s*([0-9:\-]+)/);
    return { id: r.getAttribute('data-trade-id'), raw: t.slice(0, 90), update: m ? m[2] : null, create: m ? m[3] : null };
  });
  const pagWrap = document.querySelector('.ui2-pagination-pages');
  const pagEls = pagWrap ? [...pagWrap.querySelectorAll('a,span,button,li')].map(e => ({ tag: e.tagName, txt: clean(e.innerText), cls: (e.className || '').toString().slice(0, 50) })) : [];
  const nextBtn = [...document.querySelectorAll('a,button')].filter(e => /下一页/.test(e.innerText || '')).map(e => ({ txt: clean(e.innerText), cls: (e.className || '').toString(), disabled: e.hasAttribute('disabled') || /disabled/.test((e.className || '').toString()), href: e.getAttribute('href') }));
  const activeTab = [...document.querySelectorAll('.ui2-tab-nav > *')].filter(e => /active/.test((e.className || '').toString())).map(e => clean(e.innerText));
  const dateFilterActive = [...document.querySelectorAll('.aui-toolbar *, .main-content *')].filter(e => /active|selected/.test((e.className || '').toString()) && clean(e.innerText).length < 12).map(e => clean(e.innerText) + '|' + (e.className || '').toString().slice(0, 40)).slice(0, 12);
  return { __result: { url: location.href, title: document.title, loginRedirect: /login\.alibaba\.com/.test(location.href), rowCount: rows.length, activeTab, dateFilterActive, pagination: pagWrap ? clean(pagWrap.innerText) : null, pagEls, nextBtn, times: out } };
})()
