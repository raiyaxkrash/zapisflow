import { escape as e } from './escape.js';
import { icon } from './icons.js';
const data = (action, id) => action ? ` data-action="${e(action)}" data-id="${e(id ?? '')}"` : '';
// Content slots contain application-generated escaped markup, never raw tenant text.
export function Button({label, action, id, variant='primary', disabled=false, type='button'} = {}) { return `<button type="${type==='submit'?'submit':'button'}" class="zf-button ${e(variant)}"${data(action,id)}${disabled?' disabled':''}>${e(label)}</button>`; }
export function IconButton({label, name='more', action, id}) { return `<button type="button" class="zf-icon-button" aria-label="${e(label)}"${data(action,id)}>${icon(name)}</button>`; }
export const Card = ({content='', raised=false}) => `<article class="zf-card${raised?' zf-card-raised':''}">${content}</article>`;
export const Section = ({title, content=''}) => `<section class="zf-section">${title?`<h3>${e(title)}</h3>`:''}${content}</section>`;
export const PageHeader = ({title, subtitle}) => `<header class="zf-page-header">${subtitle?`<p class="greeting">${e(subtitle)}</p>`:''}<h2>${e(title)}</h2></header>`;
export function TopBar({name='ZapisFlow',logo,theme='system'}) { return `<header class="app-top"><div class="wordmark">${logo?`<img class="brand-logo" src="${e(logo)}" alt="" width="40" height="40">`:'<span class="logo" aria-hidden="true">z</span>'}<span>${e(name)}</span></div><label class="theme-label">Тема<select id="theme" aria-label="Тема">${[['system','Система'],['light','Светлая'],['dark','Тёмная']].map(([id,label])=>`<option value="${id}"${id===theme?' selected':''}>${label}</option>`).join('')}</select></label></header>`; }
export function BottomNav({items, active}) { return `<nav class="app-nav" aria-label="Основная навигация">${items.map(([id,label,name])=>`<button type="button" class="${id===active?'nav-active':'nav-button'}"${data('nav',id)}${id===active?' aria-current="page"':''}>${icon(name)}<span>${e(label)}</span></button>`).join('')}</nav>`; }
export const Badge = ({label, tone='neutral'}) => `<span class="zf-badge" data-tone="${e(tone)}">${e(label)}</span>`;
export const StatusPill = Badge;
export function Input({label,name,value='',type='text',error='',required=false}) { const safeType=['text','tel','email','number','time','date','color'].includes(type)?type:'text';return `<label class="field">${e(label)}<input name="${e(name)}" type="${safeType}" value="${e(value)}"${required?' required':''}${error?` aria-invalid="true" aria-describedby="${e(name)}-error"`:''}></label>${error?`<p id="${e(name)}-error" class="zf-field-error" role="alert">${e(error)}</p>`:''}`; }
export const Textarea = ({label,name,value=''}) => `<label class="field">${e(label)}<textarea name="${e(name)}" rows="3">${e(value)}</textarea></label>`;
export const Switch = ({label,name,checked=false}) => `<label class="zf-switch"><input type="checkbox" role="switch" name="${e(name)}"${checked?' checked':''}><span>${e(label)}</span></label>`;
export const SegmentedControl = ({label,name,items,value}) => `<fieldset class="zf-segmented"><legend>${e(label)}</legend>${items.map(([id,text])=>`<label><input type="radio" name="${e(name)}" value="${e(id)}"${id===value?' checked':''}><span>${e(text)}</span></label>`).join('')}</fieldset>`;
export const Dialog = ({id,title,content='',sheet=false}) => `<dialog id="${e(id)}" class="zf-dialog${sheet?' zf-bottom-sheet':''}" aria-labelledby="${e(id)}-title"><h2 id="${e(id)}-title">${e(title)}</h2>${content}<form method="dialog"><button class="secondary" value="close">Закрыть</button></form></dialog>`;
export const BottomSheet = options => Dialog({...options,sheet:true});
export function openDialog(dialog, invoker) { if (!dialog?.showModal) return false;const restore=()=>{if(invoker?.isConnected)invoker.focus();};dialog.addEventListener('close',restore,{once:true});dialog.showModal();return true; }
export const Skeleton = ({label='Загружаем',rows=2}={}) => `<div role="status" aria-label="${e(label)}">${Array.from({length:Math.max(1,Math.min(5,rows))},()=>'<div class="skeleton" aria-hidden="true"></div>').join('')}</div>`;
export const EmptyState = ({title,description='',action}) => `<div class="empty"><h3>${e(title)}</h3>${description?`<p>${e(description)}</p>`:''}${action?Button(action):''}</div>`;
export const ErrorState = ({message='Не удалось загрузить данные',retry=true}={}) => `${PageHeader({title:'Попробуем ещё раз?',subtitle:'Не удалось выполнить действие'})}<div class="notice" role="alert">${e(message)}</div>${retry?Button({label:'Повторить',action:'retry'}):''}`;
