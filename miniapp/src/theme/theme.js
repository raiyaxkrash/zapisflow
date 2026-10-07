const valid = value => ['system','light','dark'].includes(value) ? value : 'system';
const hex = value => /^#[a-f0-9]{6}$/i.test(value||'') ? value : '';
const luminance = hex => { const c=hex.slice(1).match(/../g).map(v=>parseInt(v,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return c[0]*.2126+c[1]*.7152+c[2]*.0722; };
export function applyTheme(preference, tg, root=document.documentElement, win=root.ownerDocument?.defaultView) {
  const background=hex(tg?.themeParams?.bg_color);
  const systemDark=tg ? (tg.colorScheme ? tg.colorScheme==='dark' : background ? parseInt(background.slice(1,3),16)*0.299+parseInt(background.slice(3,5),16)*0.587+parseInt(background.slice(5,7),16)*0.114<128 : false) : !!win?.matchMedia?.('(prefers-color-scheme: dark)').matches;
  root.dataset.theme=preference==='dark'||(preference==='system'&&systemDark)?'dark':'light';
  root.style.colorScheme=root.dataset.theme;
  const header=hex(tg?.themeParams?.header_bg_color),text=hex(tg?.themeParams?.text_color);
  const pair=header&&text ? (Math.max(luminance(header),luminance(text))+.05)/(Math.min(luminance(header),luminance(text))+.05)>=4.5 : false;
  for(const [name,key] of [['bg','header_bg_color'],['text','text_color']]) {
    const value=preference==='system'&&pair?hex(tg?.themeParams?.[key]):'';
    if(value)root.style.setProperty('--zf-native-header-'+name,value);else root.style.removeProperty('--zf-native-header-'+name);
  }
}
export function setupTheme(tg,win=window) {
  let preference='system',explicit=false;
  try {const saved=win.localStorage.getItem('zapisflow-theme');explicit=['system','light','dark'].includes(saved);preference=valid(saved);}catch{}
  const media=win.matchMedia?.('(prefers-color-scheme: dark)');
  const render=()=>{applyTheme(preference,tg,win.document.documentElement,win);win.dispatchEvent?.(new win.Event('zapisflow-theme-change'));};
  render();tg?.onEvent?.('themeChanged',render);media?.addEventListener?.('change',render);
  return {get:()=>preference,useBrand(value){if(!explicit){preference=valid(value);render();}},set(value){preference=valid(value);explicit=true;try{win.localStorage.setItem('zapisflow-theme',preference);}catch{}render();},dispose(){tg?.offEvent?.('themeChanged',render);media?.removeEventListener?.('change',render);}};
}
