import { useEffect, useRef, useState } from 'react';
import './web-booking.css';

type Service = { id: number; title: string; price: string; duration_min: number };
type Staff = { id: number; display_name: string };
type Day = { date: string; available: boolean; reason: string | null };
type Booking = { status?: string; id: number; service: string; staff: string; start_time: string; status_label: string; price: string; deposit: string; cancel_allowed: boolean; bot_public_id?: string };
type Account = { first_name: string; phone: string | null; csrf_token: string; intent: { bot_public_id: string; service_id: number; staff_id: number; start_time: string } };
type Business = { project: { name: string; about: string; timezone: string }; bot_username: string; cancel_policy_hours: number };

function bookingDate(value: string) {
  // Appointment DTO is already rendered in the project's timezone by the backend.
  return `${value.slice(8,10)}.${value.slice(5,7)}.${value.slice(0,4)} ${value.slice(11,16)}`;
}

function civilDate(value: Date, timezone: string) {
  const parts = new Intl.DateTimeFormat('en', {timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(value);
  const part = (kind: string) => parts.find(p => p.type === kind)!.value;
  return `${part('year')}-${part('month')}-${part('day')}`;
}

function event(name: string) { window.dispatchEvent(new CustomEvent('zapisflow:event', { detail: { event: name } })); }

export function WebBooking() {
  const isAccount = window.location.pathname === '/account/bookings';
  const requestedAppointment = Number(new URLSearchParams(window.location.search).get("appointment"));
  const publicId = window.location.pathname.split('/')[2] || '';
  const base = `/api/web-booking/${encodeURIComponent(publicId)}`;
  const [business, setBusiness] = useState<Business | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [services, setServices] = useState<Service[]>([]);
  const [staff, setStaff] = useState<Staff[]>([]);
  const [serviceId, setServiceId] = useState<number>(0);
  const [staffId, setStaffId] = useState<number>(0);
  const [month, setMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  const [days, setDays] = useState<Day[]>([]);
  const [date, setDate] = useState('');
  const [slots, setSlots] = useState<string[]>([]);
  const [slot, setSlot] = useState('');
  const [phone, setPhone] = useState('');
  const [agreed, setAgreed] = useState(false);
  const [booking, setBooking] = useState<Booking | null>(null);
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [requisites, setRequisites] = useState<Record<string, string> | null>(null);
  const [proofSent, setProofSent] = useState(false);
  const operations = useRef<Record<string, string>>({});
  const errorBox = useRef<HTMLParagraphElement>(null);
  useEffect(() => { if (error) errorBox.current?.focus(); }, [error]);
  function operationKey(intent: unknown) {
    const key = JSON.stringify(intent);
    return operations.current[key] ||= crypto.randomUUID();
  }

  async function api(path: string, body?: unknown, key?: string, optionalAuth = false) {
    let response: Response;
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 15000);
    try { response = await fetch(path, { credentials: 'same-origin', method: body ? 'POST' : 'GET',
      signal: controller.signal,
      headers: body ? { 'Content-Type': 'application/json', 'X-CSRF-Token': account?.csrf_token || '', 'Idempotency-Key': key || crypto.randomUUID() } : {},
      body: body ? JSON.stringify(body) : undefined }); }
    catch { throw new Error('Нет соединения. Проверьте интернет и повторите запрос.'); }
    finally { window.clearTimeout(timer); }
    if (optionalAuth && response.status === 401) return null;
    let result;
    try { result = await response.json(); }
    catch { throw new Error('Запись временно недоступна. Повторите запрос позже.'); }
    if (!response.ok) throw new Error(result.message || 'Не удалось выполнить действие. Попробуйте ещё раз.');
    return result;
  }
  async function run(work: () => Promise<void>) {
    setError(''); setBusy(true);
    try { await work(); } catch (e) { setError(e instanceof Error ? e.message : 'Нет соединения'); event('booking_failed'); }
    finally { setBusy(false); }
  }
  useEffect(() => {
    let active = true;
    run(async () => {
      const user: Account | null = await api('/api/auth/me', undefined, undefined, true);
      if (user) {
        if (!active) return;
        setAccount(user); setPhone(user.phone || '');
        event('telegram_login_completed');
        if (user.intent?.bot_public_id === publicId && !requestedAppointment) {
          setServiceId(user.intent.service_id || 0); setStaffId(user.intent.staff_id || 0);
          setSlot(user.intent.start_time || ''); setDate(user.intent.start_time?.slice(0, 10) || '');
          if (user.intent.start_time) { const restored = new Date(user.intent.start_time); setMonth(new Date(restored.getFullYear(), restored.getMonth(), 1)); }
        }
      }
      if (isAccount) { if (user) setBookings(await api('/api/web-booking/account/bookings')); return; }
      const appointmentId = Number(new URLSearchParams(window.location.search).get('appointment'));
      if (user && Number.isSafeInteger(appointmentId) && appointmentId > 0) {
        const list = await api(`${base}/appointments`);
        const existing = list.find((a: Booking) => a.id === appointmentId);
        if (existing && active) {
          setBooking(existing); setProofSent(existing.status === 'PAYMENT_PROOF_SENT');
          if (Number(existing.deposit) > 0 && ['WAITING_PAYMENT','PAYMENT_PROOF_SENT'].includes(existing.status)) setRequisites((await api(`${base}/appointments/${existing.id}/payment`)).requisites);
          // Existing private booking survives expired subscription/channel off.
          setBusiness({project:{name:'Ваша запись',about:'',timezone:'UTC'},bot_username:'',cancel_policy_hours:0});
          return;
        }
        throw new Error('Запись не найдена. Откройте раздел «Мои записи».');
      }
      const [info, list] = await Promise.all([api(`${base}/context`), api(`${base}/services`)]);
      if (active) {
        setBusiness(info); setServices(list); event('web_booking_view');
        const restore = user?.intent?.bot_public_id === publicId && user.intent.start_time;
        const target = civilDate(restore ? new Date(user!.intent.start_time) : new Date(), info.project.timezone);
        setMonth(new Date(Number(target.slice(0,4)), Number(target.slice(5,7))-1, 1));
        if (restore) setDate(target);
      }
    });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!serviceId) return;
    let active = true;
    run(async () => { const list = await api(`${base}/staff?service_id=${serviceId}`); if (active) setStaff(list); });
    return () => { active = false; };
  }, [serviceId]);
  useEffect(() => {
    if (!serviceId || !staffId) return;
    let active = true;
    run(async () => { const calendar = await api(`${base}/availability/calendar?service_id=${serviceId}&staff_id=${staffId}&year=${month.getFullYear()}&month=${month.getMonth()+1}`); if (active) setDays(calendar.days); });
    return () => { active = false; };
  }, [serviceId, staffId, month]);
  useEffect(() => {
    if (!date || !staffId || !serviceId) return;
    let active = true;
    run(async () => { const result = await api(`${base}/slots?service_id=${serviceId}&staff_id=${staffId}&target_date=${date}`); if (active) setSlots(result.slots); });
    return () => { active = false; };
  }, [date, staffId, serviceId]);

  function login() {
    event('telegram_login_started');
    const query = new URLSearchParams();
    if (!isAccount) {
      query.set('bot_public_id', publicId);
      if (serviceId) query.set('service_id', String(serviceId));
      if (staffId) query.set('staff_id', String(staffId));
      if (slot) query.set('start_time', slot);
    }
    window.location.assign(`/api/auth/telegram/login?${query}`);
  }
  const selectedService = services.find(s => s.id === serviceId);
  const selectedStaff = staff.find(s => s.id === staffId);
  async function confirm() {
    await run(async () => {
      const selection = { service_id: serviceId, staff_id: staffId, start_time: slot };
      const hold = await api(`${base}/holds`, selection, operationKey(['hold', selection]));
      const body = { appointment_id: hold.id, phone, policy_agreed: agreed };
      const result = await api(`${base}/confirm`, body, operationKey(['confirm', body]));
      setBooking(result); event('booking_confirmed');
      if (Number(result.deposit) > 0) setRequisites((await api(`${base}/appointments/${result.id}/payment`)).requisites);
    });
  }
  const firstWeekday = (new Date(month.getFullYear(), month.getMonth(), 1).getDay() + 6) % 7;
  function bookingCard(item: Booking) {
    return <article key={item.id} className="wb-card"><h3>{item.service}</h3><p>{item.staff} · {bookingDate(item.start_time)}</p><p>{item.status_label} · {item.price} ₽</p>
      {isAccount && Number(item.deposit) > 0 && ['WAITING_PAYMENT','PAYMENT_PROOF_SENT'].includes(item.status || '') && <a href={`/book/${item.bot_public_id}?appointment=${item.id}`}>Предоплата и чек</a>}
      {item.cancel_allowed && <button disabled={busy} onClick={() => { if (window.confirm('Отменить запись? Внесённая предоплата может не возвращаться.')) run(async () => {
        const cancelled = await api(`/api/web-booking/${item.bot_public_id || publicId}/appointments/${item.id}/cancel`, {});
        if (booking?.id === item.id) setBooking(cancelled);
        setBookings(await api('/api/web-booking/account/bookings'));
      }); }}>Отменить запись</button>}</article>;
  }
  return <div className="wb"><header><a href="/">ZapisFlow</a><a href="/account/bookings">Мои записи</a></header><main>
    <h1>{isAccount ? 'Мои записи' : business?.project.name || 'Запись к мастеру'}</h1>
    {business?.project.about && <p>{business.project.about}</p>}
    <div aria-live="polite">{busy && <p role="status">Загрузка…</p>}{error && <p ref={errorBox} tabIndex={-1} className="wb-error" role="alert">{error}</p>}</div>
    {isAccount ? !account ? <button onClick={login}>Войти через Telegram</button> : <>
      <h2>Предстоящие</h2>{bookings.filter(b => new Date(b.start_time) >= new Date()).map(bookingCard)}
      <h2>Прошедшие</h2>{bookings.filter(b => new Date(b.start_time) < new Date()).map(bookingCard)}
      <button onClick={() => run(async () => { await api('/api/auth/logout', {}); setAccount(null); setBookings([]); })}>Выйти</button>
    </> : booking ? <section><h2>{window.location.search.includes("appointment=") ? "Ваша запись" : "Запись оформлена"}</h2>{bookingCard(booking)}
      {Number(booking.deposit) > 0 && ['WAITING_PAYMENT','PAYMENT_PROOF_SENT'].includes(booking.status || '') && <p>Требуется предоплата {booking.deposit} ₽.</p>}
      {requisites && <section><h3>Реквизиты мастера</h3><dl><dt>Банк</dt><dd>{requisites.bank_name}</dd><dt>Карта / телефон</dt><dd>{requisites.bank_card_number}</dd><dt>Получатель</dt><dd>{requisites.bank_recipient_name}</dd></dl>
        <p>После перевода загрузите изображение чека. Мастер проверит оплату вручную.</p>
        {proofSent ? <p role="status">Чек отправлен мастеру на проверку.</p> : <label>Подтверждение оплаты<input type="file" accept="image/jpeg,image/png" disabled={busy} onChange={e => {
          const file = e.target.files?.[0]; if (!file) return;
          run(async () => {
            if (file.size > 8 * 1024 * 1024) throw new Error('Файл слишком большой. Выберите изображение до 8 МБ.');
            const data = new FormData(); data.append('file', file);
            const controller = new AbortController();
            const timer = window.setTimeout(() => controller.abort(), 30000);
            let response: Response;
            try { response = await fetch(`${base}/appointments/${booking.id}/proof`, {method:'POST', credentials:'same-origin', signal:controller.signal, headers:{'X-CSRF-Token':account?.csrf_token || '', 'Idempotency-Key':operationKey(['proof',booking.id,file.name,file.size,file.lastModified])},body:data}); }
            catch { throw new Error('Не удалось отправить чек. Проверьте соединение и повторите запрос.'); }
            finally { window.clearTimeout(timer); }
            let result;
            try { result = await response.json(); } catch { throw new Error('Не удалось отправить чек. Свяжитесь с мастером.'); }
            if (!response.ok) throw new Error(result.message || 'Не удалось отправить чек. Свяжитесь с мастером.');
            if (result.appointment) setBooking(result.appointment);
            setProofSent(true);
          });
        }} /></label>}
      </section>}
      {business?.bot_username && <a href={`https://t.me/${business.bot_username}?start=web_booking`}>Получать напоминания в Telegram</a>}
    </section> : <>
      <section><h2>Услуга</h2><div className="wb-options">{services.map(s => <button key={s.id} aria-pressed={serviceId === s.id} onClick={() => {setServiceId(s.id); setStaffId(0); setDate(''); setSlot(''); event('service_selected');}}>{s.title}<span>{s.price} ₽ · {s.duration_min} мин</span></button>)}</div></section>
      {!!serviceId && <section><h2>Специалист</h2><div className="wb-options">{staff.map(s => <button key={s.id} aria-pressed={staffId === s.id} onClick={() => {setStaffId(s.id); setDate(''); setSlot(''); event('staff_selected');}}>{s.display_name}</button>)}</div></section>}
      {!!staffId && <section><h2>Дата</h2><div className="wb-month"><button aria-label="Предыдущий месяц" onClick={() => {setMonth(new Date(month.getFullYear(), month.getMonth()-1, 1)); setDays([]);}}>‹</button><strong>{month.toLocaleDateString('ru-RU', {month:'long', year:'numeric'})}</strong><button aria-label="Следующий месяц" onClick={() => {setMonth(new Date(month.getFullYear(), month.getMonth()+1, 1)); setDays([]);}}>›</button></div>
        <div className="wb-calendar">{['Пн','Вт','Ср','Чт','Пт','Сб','Вс'].map(d => <span key={d}>{d}</span>)}
          {Array.from({length:firstWeekday}, (_, i) => <span key={`empty-${i}`} />)}{days.map(d => <button key={d.date} aria-label={d.date} title={d.reason === "past" ? "Дата прошла" : d.reason === "horizon" ? "За горизонтом записи" : d.reason === "no_slots" ? "Нет свободного времени" : "Доступна"} aria-current={d.date === new Date().toLocaleDateString("sv-SE", {timeZone:business?.project.timezone || "UTC"}) ? "date" : undefined} aria-pressed={date === d.date} disabled={!d.available || busy} onClick={() => {setDate(d.date); setSlot(''); event('date_selected');}}>{Number(d.date.slice(-2))}</button>)}</div>
        {days.length > 0 && !days.some(d => d.available) && <p>У мастера нет свободных дат в этом месяце.</p>}</section>}
      {!!date && <section><h2>Время</h2><div className="wb-slots">{slots.map(time => <button key={time} aria-pressed={slot === time} onClick={() => {setSlot(time); event('slot_selected');}}>{new Date(time).toLocaleTimeString('ru-RU', {hour:'2-digit',minute:'2-digit',timeZone:business?.project.timezone || 'UTC'})}</button>)}</div>{!busy && slots.length === 0 && <p>На эту дату нет свободного времени. Выберите другую дату.</p>}</section>}
      {!!slot && <section><h2>Подтверждение</h2><p>{selectedService?.title} · {selectedStaff?.display_name}</p><p>{new Date(slot).toLocaleString('ru-RU', {timeZone:business?.project.timezone || 'UTC',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})} · {selectedService?.price} ₽ · {selectedService?.duration_min} мин</p>
        {!account ? <><p>Войдите через Telegram, чтобы оформить запись. До подтверждения время остаётся свободным.</p><button onClick={login}>Войти через Telegram</button></> : <form onSubmit={e => {e.preventDefault(); confirm();}}>
          <label>Телефон<input required type="tel" autoComplete="tel" value={phone} onChange={e => setPhone(e.target.value)} /></label>
          <label className="wb-agree"><input required type="checkbox" checked={agreed} onChange={e => setAgreed(e.target.checked)} />Согласен с условиями отмены: не позднее чем за {business?.cancel_policy_hours} ч. Внесённая предоплата может не возвращаться.</label>
          <button disabled={busy || !agreed}>Подтвердить запись</button></form>}</section>}
    </>}
  </main></div>;
}
