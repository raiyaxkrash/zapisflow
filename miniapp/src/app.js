import './style.css';
import { Api } from './api/client.js';
import { bootstrap, botIdFromPath } from './telegram/bootstrap.js';
import { setupTheme } from './theme/theme.js';
import { escape as e, rub, button as b, field as f, header, bookingCard, canManage } from './ui.js';

const api = new Api();
const root = document.getElementById('app');
let tg, theme, ctx, services = [], team = [], chosen, selectedStaff = null, selectedSlot, hold, selectedDate,
  screen = 'home', mode = 'client', busy = false, lastRoute = ['home'], revision = 0, pendingAction = null;
const formValues = form => Object.fromEntries(new FormData(form));

function shell(content) {
  const caps = ctx?.capabilities;
  root.innerHTML = `<div class="app-shell"><header class="app-top"><div class="wordmark"><span class="logo">z</span><span>${e(ctx?.project.name || 'ZapisFlow')}</span></div>
    <label class="theme-label">Тема<select id="theme" aria-label="Тема"><option value="system">Telegram</option><option value="light">Светлая</option><option value="dark">Чёрная</option></select></label></header>
    ${caps && canManage(ctx) ? `<div class="mode-switch">${b('Клиент', 'mode', 'client', mode === 'client' ? 'primary' : 'secondary')}${b('Управление', 'mode', 'master', mode === 'master' ? 'primary' : 'secondary')}</div>` : ''}
    <main class="view app-view">${content}</main>${ctx ? `<nav class="app-nav">${(mode === 'client' ? [['home','Главная'],['services','Записаться'],['bookings','Мои записи'],['contact','Контакты']] : [['dashboard','Записи'],...(caps.can_edit_project ? [['clients','Клиенты'],['manual','+ Запись'],['settings','Управление']] : [])]).map(([p,l]) => b(l,'nav',p,screen===p?'nav-active':'nav-button')).join('')}</nav>` : ''}</div>`;
  if (theme) root.querySelector('#theme').value = theme.get();
  tg?.BackButton[screen === 'home' || screen === 'dashboard' ? 'hide' : 'show']();
}
function notice(text) { return `<div class="notice">${e(text)}</div>`; }
function empty(text) { return `<div class="empty">${e(text)}</div>`; }
function serviceRows(rows, action = 'choose-service') {
  return rows.length ? rows.map(s => `<button class="service" data-action="${action}" data-id="${s.id}"><span class="service-icon">✂</span><span class="detail"><strong>${e(s.title)}</strong><small>${s.duration_min} минут · ${s.buffer_min} мин буфер</small></span><span class="price">${rub(s.price)}<small>›</small></span></button>`).join('') : empty('Услуг пока нет');
}
async function route(page, id) {
  const serial = ++revision; screen = page; lastRoute = [page, id];
  shell('<p role="status">Загружаем…</p>');
  try {
    const content = await render(page, id);
    if (serial === revision) shell(content);
  } catch (error) { if (serial === revision) showError(error); }
}
function showError(error) {
  shell(header('Не удалось выполнить действие', 'Попробуем ещё раз?') + notice(error.message) +
    (error.status === 401 ? '<p>Закройте Mini App и откройте его снова из бота.</p>' : b('Повторить','retry')) +
    b('На главную','nav',mode === 'client' ? 'home' : 'dashboard','secondary'));
}
function contacts() {
  const labels = {studio_phone:'Телефон',whatsapp_phone:'WhatsApp',studio_address:'Адрес',working_hours_text:'Режим работы',telegram_username:'Telegram',vk_profile:'ВКонтакте'};
  return header(ctx.project.name,'Будем на связи') + `<p>${e(ctx.contacts.contacts_intro_text || '')}</p><div class="summary">${Object.entries(labels).filter(([k])=>ctx.contacts[k]).map(([k,l])=>`<div class="summary-row"><span>${l}</span><strong>${e(ctx.contacts[k])}</strong></div>`).join('')}</div><p>${e(ctx.project.about || '')}</p>`;
}
async function render(page, id) {
  if (page === 'home' || page === 'services') {
    services = await api.get('/client/services');
    return header(`Рады вас видеть, ${ctx.user.first_name}`, page === 'home' ? 'Время для себя' : 'Выберите услугу') +
      (page === 'home' ? `<div class="studio-banner"><h3>${e(ctx.project.name)}</h3><p>${e(ctx.contacts.studio_address || '')}</p><p>${e(ctx.contacts.working_hours_text || '')}</p></div>` : '') +
      (!ctx.can_book ? notice('Онлайн-запись сейчас недоступна. Свяжитесь со студией.') : '') + serviceRows(services);
  }
  if (page === 'staff') {
    team = await api.get(`/client/staff?service_id=${chosen.id}`);
    return header(chosen.title, 'К кому запишемся?') + (team.length ? `<div class="options">${b('Любой специалист','staff','any','person')}${team.map(s=>b(s.display_name,'staff',s.id,'person')).join('')}</div>` : empty('Нет доступных специалистов'));
  }
  if (page === 'time') {
    const data = await api.get(`/client/slots?service_id=${chosen.id}&target_date=${selectedDate}${selectedStaff ? `&staff_id=${selectedStaff}` : ''}`);
    const end = new Date(`${ctx.today}T12:00:00Z`); end.setUTCDate(end.getUTCDate() + ctx.booking_horizon_days);
    return header(chosen.title,'Когда удобно?') + `<form id="date-form">${f('Дата','date',selectedDate,'date',`min="${ctx.today}" max="${end.toISOString().slice(0,10)}" required`)}<button class="secondary">Показать время</button></form><p class="sub">Часовой пояс студии: ${e(ctx.project.timezone)}</p>` +
      (data.slots.length ? `<div class="times">${data.slots.map(slot=>b(slot.slice(11,16),'slot',slot,'slot')).join('')}</div>` : empty('На эту дату свободного времени нет. Выберите другой день.'));
  }
  if (page === 'confirm') {
    return header('Время зарезервировано','Всё верно?') + bookingCard(hold) + `<form id="confirm-form">${f('Телефон','phone',ctx.user.phone || '','tel','required autocomplete="tel"')}<label class="check"><input name="policy" type="checkbox" required><span>Согласен с условиями отмены. ${ctx.cancel_policy_hours} ч — срок из настроек студии. Внесённая предоплата при отмене не возвращается.</span></label><button class="primary">${Number(hold.deposit) ? 'Подтвердить и получить реквизиты' : 'Подтвердить запись'}</button></form>`;
  }
  if (page === 'payment') {
    const data = await api.get(`/client/appointments/${id}/payment`); hold = data.appointment;
    const p = hold.payment[0];
    return header('Предоплата',p.status === 'SUBMITTED' ? 'Чек на проверке' : 'Время за вами') + bookingCard(hold) + (p.rejection_reason ? notice(p.rejection_reason) : '') +
      `<div class="summary">${Object.entries({bank_name:'Банк',bank_card_number:'Карта / телефон',bank_recipient_name:'Получатель'}).map(([key,label])=>`<div class="summary-row"><span>${label}</span><strong>${e(data.requisites[key] || '')}</strong></div>`).join('')}</div>` +
      (p.status === 'PENDING' || p.status === 'REJECTED' ? `${notice('Прикрепите фото чека: JPEG или PNG, до 8 МБ. Мастер проверит предоплату.')}<form id="proof-form"><label class="field">Чек<input id="proof-file" name="file" type="file" accept="image/jpeg,image/png" required></label><img id="proof-preview" class="proof-preview" alt="Предпросмотр чека" hidden><progress id="upload-progress" max="100" value="0" hidden></progress><button class="primary">Отправить чек</button></form>` : '') + b('Мои записи','nav','bookings','secondary');
  }
  if (page === 'bookings') {
    const rows = await api.get('/client/appointments');
    return header('Ваши визиты','Мои записи') + (rows.length ? rows.map(a=>bookingCard(a) +
      (!a.policy_agreed && a.status === 'WAITING_PAYMENT' ? b('Продолжить подтверждение','resume',a.id,'secondary') : a.payment.length && ['WAITING_PAYMENT','PAYMENT_PROOF_SENT'].includes(a.status) ? b('Предоплата / чек','payment',a.id,'secondary') : '') +
      (a.cancel_allowed ? b('Отменить запись','cancel',a.id,'link') : '')).join('') : empty('Пока нет записей'));
  }
  if (page === 'contact') return contacts();
  if (page === 'dashboard') {
    const rows = await api.get(`/master/appointments?target_date=${selectedDate}`);
    return header(ctx.project.name,'Расписание') + `<form id="dashboard-date-form">${f('Дата','date',selectedDate,'date','required')}<button class="secondary">Показать записи</button></form>` +
      (rows.length ? rows.map(a=>bookingCard(a,true)+b('Открыть запись','appointment',a.id,'secondary')).join('') : empty('На эту дату записей нет'));
  }
  if (page === 'appointment') {
    const a = await api.get(`/master/appointments/${id}`);
    return header('Карточка записи',a.client) + bookingCard(a,true) + `<p>${e(a.notes || '')}</p>` + a.payment.map(p => `<h3>Предоплата ${rub(p.amount)}</h3><p>${e(p.status)}</p>` + p.proofs.map(proof=>`<a class="secondary proof-link" href="/api/miniapp/proofs/${proof.id}">Скачать чек</a>`).join('') +
      (ctx.capabilities.can_edit_project && p.status === 'SUBMITTED' ? `${b('Подтвердить чек','approve',p.id)}<form id="reject-form" data-id="${p.id}">${f('Причина отклонения','reason','','text','required maxlength="255"')}<button class="secondary">Отклонить чек</button></form>` : '')).join('');
  }
  if (page === 'clients') {
    const rows = await api.get(`/master/clients?search=${encodeURIComponent(id || '')}`);
    return header(ctx.project.name,'Клиенты') + `<form id="search-form">${f('Поиск по имени','search',id || '')}<button class="secondary">Найти</button></form>` +
      (rows.length ? rows.map(r=>`<button class="service" data-action="client" data-id="${r.id}"><span class="detail"><strong>${e(r.name)}</strong><small>${e(r.phone || '')}</small></span>›</button>`).join('') : empty('Клиенты не найдены'));
  }
  if (page === 'client') {
    const card = await api.get(`/master/clients/${id}`);
    return header('Клиент',card.name) + `<p>${e(card.phone || '')}</p><p>Визитов: ${card.total_bookings} · ${rub(card.total_spent)}</p><form id="notes-form" data-id="${id}">${f('Заметки','notes',card.notes || '', 'text','maxlength="2000"')}<button class="primary">Сохранить заметку</button></form><h3>История</h3>` + card.history.map(a=>bookingCard(a,true)).join('');
  }
  if (page === 'settings') return header(ctx.project.name,'Управление') + [['manage-services','Услуги'],['team','Команда'],['schedule','График и отдельные даты'],['contacts-edit','Контакты'],['requisites','Предоплата и реквизиты'],['booking-settings','Настройки записи']].map(([p,l])=>b(l,'nav',p,'settings-row')).join('');
  if (['contacts-edit','requisites','booking-settings'].includes(page)) {
    const data = await api.get('/master/settings');
    const fields = page === 'contacts-edit' ? {studio_address:'Адрес',studio_phone:'Телефон',whatsapp_phone:'WhatsApp',working_hours_text:'Режим работы',contacts_intro_text:'Вступление',telegram_username:'Telegram username',vk_profile:'ВКонтакте',about_text:'О студии'} : page === 'requisites' ? {bank_name:'Банк',bank_card_number:'Карта / телефон',bank_recipient_name:'Получатель'} : {booking_horizon_days:'Горизонт записи, дней',hold_duration_minutes:'Резервирование, минут',cancel_policy_hours:'Срок отмены, часов',min_advance_hours:'Минимум до визита, часов',grid_step_minutes:'Шаг слотов, минут',default_buffer_minutes:'Буфер, минут'};
    return header(ctx.project.name,page === 'requisites' ? 'Реквизиты' : page === 'contacts-edit' ? 'Контакты' : 'Настройки записи') + `<form id="settings-form">${Object.entries(fields).map(([key,label])=>f(label,key,data[key] ?? '',typeof data[key] === 'number'?'number':'text')).join('')}${page==='booking-settings'?['reminder_24h_enabled','reminder_3h_enabled'].map((key,index)=>`<label class="check"><input name="${key}" type="checkbox" ${data[key]?'checked':''}>Напоминание за ${index?3:24} ч</label>`).join(''):''}<button class="primary">Сохранить</button></form>`;
  }
  if (page === 'manage-services') {
    services = await api.get('/master/services');
    return header(ctx.project.name,'Услуги') + b('+ Добавить услугу','service-edit','new') + serviceRows(services,'service-edit');
  }
  if (page === 'service-edit') {
    const s = services.find(s=>s.id===Number(id)) || {title:'',price:0,duration_min:60,buffer_min:15,deposit_type:'FIXED',deposit_value:0,is_active:true};
    return header('Услуга',id==='new'?'Новая услуга':s.title) + `<form id="service-form" data-id="${e(id)}">${f('Название','title',s.title,'text','required maxlength="255"')}${f('Описание','description',s.description || '')}${f('Цена, ₽','price',s.price,'number','min="0" step="0.01" required')}${f('Длительность, мин','duration_min',s.duration_min,'number','min="1" required')}${f('Буфер, мин','buffer_min',s.buffer_min,'number','min="0" required')}<label class="field">Тип предоплаты<select name="deposit_type"><option value="FIXED" ${s.deposit_type==='FIXED'?'selected':''}>Сумма</option><option value="PERCENT" ${s.deposit_type==='PERCENT'?'selected':''}>Процент</option></select></label>${f('Предоплата','deposit_value',s.deposit_value,'number','min="0" step="0.01" required')}<label class="check"><input name="is_active" type="checkbox" ${s.is_active?'checked':''}>Услуга активна</label><button class="primary">Сохранить</button></form>`;
  }
  if (page === 'team') {
    team = await api.get('/master/staff');
    return header(ctx.project.name,'Команда') + (team.length ? team.map(s=>`<button class="settings-row" data-action="staff-edit" data-id="${s.id}"><span>${e(s.display_name)}<small>${s.is_active?'Активен':'Неактивен'}</small></span>›</button>`).join('') : empty('Нет сотрудников')) + notice('Добавление и приглашение сотрудника доступны в Telegram-админке проекта.');
  }
  if (page === 'staff-edit') {
    const s = team.find(s=>s.id===Number(id)); services = await api.get('/master/services');
    return header('Сотрудник',s.display_name) + `<form id="staff-form" data-id="${id}">${f('Имя','display_name',s.display_name,'text','required')}${f('Специализация','specialization',s.specialization || '')}<label class="check"><input name="is_active" type="checkbox" ${s.is_active?'checked':''}>Активен</label><h3>Услуги</h3>${notice('Если назначения пустые, сотрудник оказывает все активные услуги — правило текущего ZapisFlow.')}${services.map(row=>`<label class="check"><input name="service_ids" value="${row.id}" type="checkbox" ${s.service_ids.includes(row.id)?'checked':''}>${e(row.title)}</label>`).join('')}<button class="primary">Сохранить</button></form>`;
  }
  if (page === 'schedule') {
    team = await api.get('/master/staff');
    if (!team.length) return empty('Добавьте сотрудника в Telegram-админке');
    const staffId = Number(id) || team[0].id;
    const data = await api.get(`/master/schedule?staff_id=${staffId}`);
    const weekdays = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
    return header('График','Рабочее время') + `<form id="schedule-staff-form"><label class="field">Сотрудник<select name="staff_id">${team.map(s=>`<option value="${s.id}" ${s.id===staffId?'selected':''}>${e(s.display_name)}</option>`).join('')}</select></label><button class="secondary">Показать график</button></form>` +
      data.weekly.map(r=>`<p>${weekdays[r.weekday]}: ${r.is_day_off?'выходной':`${e(r.work_start)}–${e(r.work_end)}`}</p>`).join('') +
      `<form id="schedule-form" data-staff="${staffId}"><label class="field">День недели<select name="weekday"><option value="">Отдельная дата</option>${weekdays.map((w,i)=>`<option value="${i}">${w}</option>`).join('')}</select></label>${f('Или отдельная дата','target_date',ctx.today,'date')}<label class="check"><input type="checkbox" name="is_day_off">Выходной</label>${f('Начало','work_start','10:00','time','required')}${f('Конец','work_end','19:00','time','required')}${f('Перерывы (10:30-11:00, 13:00-14:00)','breaks','')}<button class="primary">Сохранить день</button></form><h3>Отдельные даты</h3>` +
      data.dates.map(r=>`<p>${r.scope==='project'?'Студия · ':''}${e(r.target_date)}: ${r.is_day_off?'выходной':`${e(r.work_start)}–${e(r.work_end)}`}</p>`).join('');
  }
  if (page === 'manual') {
    services = await api.get('/master/services'); team = await api.get('/master/staff');
    const clients = await api.get('/master/clients');
    return header('Расписание','Добавить запись') + (clients.length ? `<form id="manual-form">${[['master_client_id','Клиент',clients.map(c=>[c.id,c.name])],['service_id','Услуга',services.filter(s=>s.is_active).map(s=>[s.id,s.title])],['staff_id','Сотрудник',team.filter(s=>s.is_active).map(s=>[s.id,s.display_name])]].map(([key,label,rows])=>`<label class="field">${label}<select name="${key}">${rows.map(([v,l])=>`<option value="${v}">${e(l)}</option>`).join('')}</select></label>`).join('')}${f('Дата','date',ctx.today,'date','required')}${f('Время (по часовому поясу студии)','time','10:00','time','required')}${f('Телефон клиента (необязательно)','phone','','tel')}${f('Заметки','notes','')}<button class="primary">Проверить время и создать</button></form>` : empty('Сначала добавьте клиента через Telegram-админку'));
  }
  return empty('Экран недоступен');
}

async function run(action) {
  if (busy) return; busy = true;
  root.querySelectorAll('button').forEach(button=>button.disabled=true);
  try { await action(); pendingAction = null; }
  catch (error) { pendingAction = error.code === 'NETWORK' || error.status === 503 ? action : null; showError(error); }
  finally { busy = false; root.querySelectorAll('button').forEach(button=>button.disabled=false); }
}
root.addEventListener('click', event => {
  const target = event.target.closest('[data-action]'); if (!target) return;
  const {action,id} = target.dataset;
  const retryAction = pendingAction;
  run(async()=>{
    if (action === 'nav') return route(id);
    if (action === 'retry') return retryAction ? retryAction() : ctx ? route(...lastRoute) : start();
    if (action === 'resume') { hold = (await api.get('/client/appointments')).find(a => a.id === Number(id)); if (!hold) throw new Error('Резерв недоступен'); return route('confirm'); }
    if (action === 'mode') { if (id==='master' && !canManage(ctx)) return; mode=id; selectedDate=ctx.today; return route(id==='master'?'dashboard':'home'); }
    if (action === 'choose-service') { if (!ctx.can_book) return route('contact'); chosen=services.find(s=>s.id===Number(id)); return route('staff'); }
    if (action === 'staff') { selectedStaff=id==='any'?null:Number(id); selectedDate=ctx.today; return route('time'); }
    if (action === 'slot') { selectedSlot=id; hold=await api.mutate('/client/holds',{service_id:chosen.id,staff_id:selectedStaff,start_time:selectedSlot}); return route('confirm'); }
    if (action === 'payment') return route('payment',id);
    if (action === 'cancel') { if (!window.confirm('Отменить запись? Внесённая предоплата не возвращается.')) return; await api.mutate(`/client/appointments/${id}/cancel`,{}); return route('bookings'); }
    if (action === 'approve') { await api.mutate(`/master/payments/${id}/decision`,{approve:true}); return route('dashboard'); }
    if (['appointment','client','service-edit','staff-edit'].includes(action)) return route(action,id);
  });
});
root.addEventListener('change', event=>{
  if (event.target.id === 'theme') theme.set(event.target.value);
  if (event.target.id === 'proof-file' && event.target.files[0]) {
    const image=root.querySelector('#proof-preview'); if(image.src.startsWith('blob:')) URL.revokeObjectURL(image.src);
    image.src=URL.createObjectURL(event.target.files[0]); image.hidden=false;
  }
});
root.addEventListener('submit', event=>{
  event.preventDefault(); const form=event.target; const data=formValues(form);
  run(async()=>{
    if (form.id === 'date-form') { selectedDate=data.date; return route('time'); }
    if (form.id === 'dashboard-date-form') { selectedDate=data.date; return route('dashboard'); }
    if (form.id === 'confirm-form') {
      const result=await api.mutate('/client/appointments',{appointment_id:hold.id,phone:data.phone,policy_agreed:data.policy==='on'});
      return route(Number(result.deposit)?'payment':'bookings',result.id);
    }
    if (form.id === 'proof-form') {
      const file=form.querySelector('input[type=file]').files[0];
      if(!file || !['image/jpeg','image/png'].includes(file.type) || file.size>8*1024*1024) throw new Error('Выберите JPEG или PNG до 8 МБ');
      const progress=form.querySelector('progress');progress.hidden=false;
      await api.upload(`/client/appointments/${hold.id}/proof`,file,p=>progress.value=p); return route('bookings');
    }
    if (form.id === 'search-form') return route('clients',data.search);
    if (form.id === 'notes-form') { await api.mutate(`/master/clients/${form.dataset.id}`,{notes:data.notes || null},'PATCH'); return route('client',form.dataset.id); }
    if (form.id === 'reject-form') { await api.mutate(`/master/payments/${form.dataset.id}/decision`,{approve:false,reason:data.reason}); return route('dashboard'); }
    if (form.id === 'settings-form') {
      for (const input of form.querySelectorAll('input[type=number]')) data[input.name]=Number(input.value);
      for (const input of form.querySelectorAll('input[type=checkbox]')) data[input.name]=input.checked;
      for(const key of Object.keys(data)) if(data[key]==='') data[key]=null;
      await api.mutate('/master/settings',data,'PATCH');ctx=await api.get('/context');return route('settings');
    }
    if (form.id === 'service-form') {
      for(const key of ['duration_min','buffer_min'])data[key]=Number(data[key]);data.is_active=form.elements.is_active.checked;
      await api.mutate(`/master/services${form.dataset.id==='new'?'':`/${form.dataset.id}`}`,data,form.dataset.id==='new'?'POST':'PUT');return route('manage-services');
    }
    if (form.id === 'staff-form') {
      data.is_active=form.elements.is_active.checked;data.service_ids=new FormData(form).getAll('service_ids').map(Number);
      await api.mutate(`/master/staff/${form.dataset.id}`,data,'PUT');return route('team');
    }
    if (form.id === 'schedule-staff-form') return route('schedule',data.staff_id);
    if (form.id === 'schedule-form') {
      const body={staff_id:Number(form.dataset.staff),is_day_off:form.elements.is_day_off.checked,work_start:data.work_start,work_end:data.work_end,
        breaks:data.breaks?data.breaks.split(',').map(item=>item.trim().split('-').map(s=>s.trim())):[]};
      if(data.weekday!=='')body.weekday=Number(data.weekday);else body.target_date=data.target_date;
      await api.mutate('/master/schedule',body,'PUT');return route('schedule',body.staff_id);
    }
    if (form.id === 'manual-form') {
      // Browser timezone never decides the studio instant: obtain matching
      // authoritative backend slot, then reuse normal booking service.
      const slots=await api.get(`/client/slots?service_id=${data.service_id}&staff_id=${data.staff_id}&target_date=${data.date}`);
      const start=slots.slots.find(s=>s.slice(11,16)===data.time);if(!start)throw new Error('Это время недоступно. Выберите свободный слот');
      await api.mutate('/master/appointments',{service_id:Number(data.service_id),staff_id:Number(data.staff_id),master_client_id:Number(data.master_client_id),start_time:start,phone:data.phone || null,notes:data.notes || null});return route('dashboard');
    }
  });
});
async function start() {
  shell('<p role="status">Проверяем вход через Telegram…</p>');
  try {
    tg=bootstrap();theme=setupTheme(tg);const botId=botIdFromPath(location.pathname);
    await api.auth(botId,tg.initData);ctx=await api.get('/context');selectedDate=ctx.today;
    tg.BackButton.onClick(()=>route(mode==='client'?'home':'dashboard'));
    await route('home');
  } catch(error) { showError(error); }
}
start();
