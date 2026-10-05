import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WebBooking } from '../src/WebBooking';

const date = '2026-10-10';
const slot = date + 'T10:00:00+00:00';
let signedIn = false;
let occupied = false;
let deposit = '0';
let restored = false;
let hasBookings = false;
const requests: {path: string; init?: RequestInit}[] = [];

beforeEach(() => {
  signedIn = false; occupied = false; deposit = '0'; restored = false; hasBookings = false; requests.length = 0;
  window.history.replaceState({}, '', '/book/test-public');
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    requests.push({path, init});
    let status = 200;
    let data: unknown = {};
    if (path === '/api/auth/me') {
      status = signedIn ? 200 : 401;
      data = {first_name:'Клиент',phone:'+79991234567',csrf_token:'test-csrf',intent:restored ? {bot_public_id:'test-public',service_id:1,staff_id:2,start_time:slot} : {}};
    } else if (path.endsWith('/context')) data = {project:{name:'Студия',about:'Стрижки',timezone:'UTC'},bot_username:'test_bot',cancel_policy_hours:24};
    else if (path.endsWith('/services')) data = [{id:1,title:'Стрижка',price:'499',duration_min:60}];
    else if (path.includes('/staff?')) data = [{id:2,display_name:'Мастер'}];
    else if (path.includes('/availability/calendar?')) data = {days:[{date,available:true,reason:null}]};
    else if (path.includes('/slots?')) data = {slots:[slot]};
    else if (path.endsWith('/holds')) {status = occupied ? 409 : 200; data = occupied ? {message:'Это время только что заняли. Выберите другое свободное время.'} : {id:3};}
    else if (path.endsWith('/confirm')) data = {status:deposit === '0' ? 'CONFIRMED' : 'WAITING_PAYMENT',id:3,service:'Стрижка',staff:'Мастер',start_time:slot,status_label:'Подтверждена',price:'499',deposit,cancel_allowed:true};
    else if (path.endsWith('/payment')) data = {requisites:{bank_name:'Тестовый банк',bank_card_number:'Тестовые реквизиты',bank_recipient_name:'Мастер'}};
    else if (path === '/api/web-booking/account/bookings') data = hasBookings ? [{id:3,service:'Стрижка',staff:'Мастер',start_time:slot,status_label:'Подтверждена',price:'499',deposit:'0',cancel_allowed:true,bot_public_id:'test-public'}] : [];
    return new Response(JSON.stringify(data), {status,headers:{'Content-Type':'application/json'}});
  }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function selectSlot() {
  render(<WebBooking />);
  fireEvent.click(await screen.findByRole('button', {name:/Стрижка/}));
  fireEvent.click(await screen.findByRole('button', {name:'Мастер'}));
  fireEvent.click(await screen.findByRole('button', {name:date}));
  fireEvent.click(await screen.findByRole('button', {name:/10:00/}));
}

describe('Website booking transport', () => {
  it('allows anonymous selection and only then offers Login; no hold before Login', async () => {
    await selectSlot();
    expect(await screen.findByRole('button', {name:'Войти через Telegram'})).toBeTruthy();
    expect(requests.some(r => r.path.endsWith('/holds'))).toBe(false);
  });
  it('uses semantic calendar navigation and selected date', async () => {
    await selectSlot();
    expect(screen.getByRole('button', {name:date}).getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', {name:'Следующий месяц'}));
    await waitFor(() => expect(requests.filter(r => r.path.includes('/calendar?')).length).toBeGreaterThan(1));
    expect(screen.getByRole('button', {name:'Предыдущий месяц'})).toBeTruthy();
  });
  it('prefills stored phone and sends shared hold/confirmation with CSRF', async () => {
    signedIn = true;
    await selectSlot();
    expect((screen.getByLabelText('Телефон') as HTMLInputElement).value).toBe('+79991234567');
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', {name:'Подтвердить запись'}));
    expect(await screen.findByRole('heading', {name:'Запись оформлена'})).toBeTruthy();
    const hold = requests.find(r => r.path.endsWith('/holds'))!;
    expect(JSON.parse(hold.init?.body as string)).toEqual({service_id:1,staff_id:2,start_time:slot});
    expect(hold.init?.headers).toHaveProperty('X-CSRF-Token','test-csrf');
    expect(screen.getByRole('link', {name:'Получать напоминания в Telegram'}).getAttribute('href')).toBe('https://t.me/test_bot?start=web_booking');
  });
  it('shows occupied slot without creating confirmation', async () => {
    signedIn = true; occupied = true;
    await selectSlot();
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', {name:'Подтвердить запись'}));
    expect((await screen.findByRole('alert')).textContent).toMatch(/Выберите другое/);
    expect(requests.some(r => r.path.endsWith('/confirm'))).toBe(false);
  });
  it('account is protected and provides Login', async () => {
    window.history.replaceState({}, '', '/account/bookings');
    render(<WebBooking />);
    expect(await screen.findByRole('button', {name:'Войти через Telegram'})).toBeTruthy();
  });
  it('restores server-side booking intent after Login', async () => {
    signedIn = true; restored = true;
    render(<WebBooking />);
    expect(await screen.findByRole('button', {name:'Подтвердить запись'})).toBeTruthy();
    await waitFor(() => expect(screen.getByRole('button', {name:/Стрижка/}).getAttribute('aria-pressed')).toBe('true'));
    expect((screen.getByLabelText('Телефон') as HTMLInputElement).value).toBe('+79991234567');
    expect(requests.some(r => r.path.endsWith('/holds'))).toBe(false);
  });
  it('shows manual requisites and uploads proof with session CSRF', async () => {
    signedIn = true; deposit = '50';
    await selectSlot();
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', {name:'Подтвердить запись'}));
    expect(await screen.findByText('Тестовый банк')).toBeTruthy();
    const file = new File(['test-image'], 'receipt.png', {type:'image/png'});
    fireEvent.change(screen.getByLabelText('Подтверждение оплаты'), {target:{files:[file]}});
    expect(await screen.findByText('Чек отправлен мастеру на проверку.')).toBeTruthy();
    const upload = requests.find(r => r.path.endsWith('/proof'))!;
    expect(upload.init?.body).toBeInstanceOf(FormData);
    expect(upload.init?.headers).toHaveProperty('X-CSRF-Token','test-csrf');
  });
  it('account separates bookings and requires cancellation confirmation', async () => {
    signedIn = true; hasBookings = true;
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    window.history.replaceState({}, '', '/account/bookings');
    render(<WebBooking />);
    fireEvent.click(await screen.findByRole('button', {name:'Отменить запись'}));
    await waitFor(() => expect(requests.some(r => r.path.endsWith('/cancel'))).toBe(true));
    expect(window.confirm).toHaveBeenCalledOnce();
    expect(screen.getByRole('heading', {name:'Предстоящие'})).toBeTruthy();
    expect(screen.getByRole('heading', {name:'Прошедшие'})).toBeTruthy();
  });
});
