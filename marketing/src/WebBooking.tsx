import { brandColors, safeAsset, type Brand } from "./brand";
import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import "./web-booking.css";

type Service = {
  id: number;
  title: string;
  price: string;
  duration_min: number;
  description?: string;
};
type Staff = { id: number; display_name: string };
type Day = { date: string; available: boolean; reason: string | null };
type Booking = {
  status?: string;
  id: number;
  service: string;
  staff: string;
  start_time: string;
  status_label: string;
  price: string;
  deposit: string;
  cancel_allowed: boolean;
  bot_public_id?: string;
};
type Account = {
  first_name: string;
  phone: string | null;
  csrf_token: string;
  intent: {
    bot_public_id: string;
    service_id: number;
    staff_id: number;
    start_time: string;
  };
};
type Business = {
  branding?: Brand;
  contacts?: Record<string, string | null>;
  project: { name: string; about: string; timezone: string };
  bot_username: string;
  cancel_policy_hours: number;
};

function bookingDate(value: string) {
  // Appointment DTO is already rendered in the project's timezone by the backend.
  return `${value.slice(8, 10)}.${value.slice(5, 7)}.${value.slice(0, 4)} ${value.slice(11, 16)}`;
}

function civilDate(value: Date, timezone: string) {
  const parts = new Intl.DateTimeFormat("en", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(value);
  const part = (kind: string) => parts.find((p) => p.type === kind)!.value;
  return `${part("year")}-${part("month")}-${part("day")}`;
}

function event(name: string) {
  window.dispatchEvent(
    new CustomEvent("zapisflow:event", { detail: { event: name } }),
  );
}

export function WebBooking() {
  const [themeChoice, setThemeChoice] = useState<
    "system" | "light" | "dark" | null
  >(() => {
    try {
      const saved = localStorage.getItem("zf-web-theme");
      return ["system", "light", "dark"].includes(saved || "")
        ? (saved as "system" | "light" | "dark")
        : null;
    } catch {
      return null;
    }
  });
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia?.("(prefers-color-scheme: dark)").matches || false,
  );
  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!media) return;
    const changed = () => setSystemDark(media.matches);
    media.addEventListener?.("change", changed);
    return () => media.removeEventListener?.("change", changed);
  }, []);
  const isAccount = window.location.pathname === "/account/bookings";
  const requestedAppointment = Number(
    new URLSearchParams(window.location.search).get("appointment"),
  );
  const publicId = window.location.pathname.split("/")[2] || "";
  const base = `/api/web-booking/${encodeURIComponent(publicId)}`;
  const [business, setBusiness] = useState<Business | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [sectionError, setSectionError] = useState("");
  const [reviews, setReviews] = useState<
    { rating: number; comment: string; date: string }[]
  >([]);
  const [portfolio, setPortfolio] = useState<
    { id: number; title: string; caption: string; image_url: string }[]
  >([]);
  const [services, setServices] = useState<Service[]>([]);
  const [staff, setStaff] = useState<Staff[]>([]);
  const [serviceId, setServiceId] = useState<number>(0);
  const [staffId, setStaffId] = useState<number>(0);
  const [month, setMonth] = useState(
    () => new Date(new Date().getFullYear(), new Date().getMonth(), 1),
  );
  const [days, setDays] = useState<Day[]>([]);
  const [date, setDate] = useState("");
  const [slots, setSlots] = useState<string[]>([]);
  const [slot, setSlot] = useState("");
  const [phone, setPhone] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [booking, setBooking] = useState<Booking | null>(null);
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [requisites, setRequisites] = useState<Record<string, string> | null>(
    null,
  );
  const [proofSent, setProofSent] = useState(false);
  const operations = useRef<Record<string, string>>({});
  const errorBox = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (error) errorBox.current?.focus();
  }, [error]);
  function operationKey(intent: unknown) {
    const key = JSON.stringify(intent);
    return (operations.current[key] ||= crypto.randomUUID());
  }

  async function api(
    path: string,
    body?: unknown,
    key?: string,
    optionalAuth = false,
  ) {
    let response: Response;
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 15000);
    try {
      response = await fetch(path, {
        credentials: "same-origin",
        method: body ? "POST" : "GET",
        signal: controller.signal,
        headers: body
          ? {
              "Content-Type": "application/json",
              "X-CSRF-Token": account?.csrf_token || "",
              "Idempotency-Key": key || crypto.randomUUID(),
            }
          : {},
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch {
      throw new Error("Нет соединения. Проверьте интернет и повторите запрос.");
    } finally {
      window.clearTimeout(timer);
    }
    if (optionalAuth && response.status === 401) return null;
    let result;
    try {
      result = await response.json();
    } catch {
      throw new Error("Запись временно недоступна. Повторите запрос позже.");
    }
    if (!response.ok)
      throw new Error(
        result.message || "Не удалось выполнить действие. Попробуйте ещё раз.",
      );
    return result;
  }
  async function run(work: () => Promise<void>) {
    setError("");
    setBusy(true);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Нет соединения");
      event("booking_failed");
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    let active = true;
    run(async () => {
      const user: Account | null = await api(
        "/api/auth/me",
        undefined,
        undefined,
        true,
      );
      if (user) {
        if (!active) return;
        setAccount(user);
        setPhone(user.phone || "");
        event("telegram_login_completed");
        if (user.intent?.bot_public_id === publicId && !requestedAppointment) {
          setServiceId(user.intent.service_id || 0);
          setStaffId(user.intent.staff_id || 0);
          setSlot(user.intent.start_time || "");
          setDate(user.intent.start_time?.slice(0, 10) || "");
          if (user.intent.start_time) {
            const restored = new Date(user.intent.start_time);
            setMonth(new Date(restored.getFullYear(), restored.getMonth(), 1));
          }
        }
      }
      if (isAccount) {
        if (user) setBookings(await api("/api/web-booking/account/bookings"));
        return;
      }
      const appointmentId = Number(
        new URLSearchParams(window.location.search).get("appointment"),
      );
      if (user && Number.isSafeInteger(appointmentId) && appointmentId > 0) {
        const list = await api(`${base}/appointments`);
        const existing = list.find((a: Booking) => a.id === appointmentId);
        if (existing && active) {
          setBooking(existing);
          setProofSent(existing.status === "PAYMENT_PROOF_SENT");
          if (
            Number(existing.deposit) > 0 &&
            ["WAITING_PAYMENT", "PAYMENT_PROOF_SENT"].includes(existing.status)
          )
            setRequisites(
              (await api(`${base}/appointments/${existing.id}/payment`))
                .requisites,
            );
          // Existing private booking survives expired subscription/channel off.
          setBusiness({
            project: { name: "Ваша запись", about: "", timezone: "UTC" },
            bot_username: "",
            cancel_policy_hours: 0,
          });
          return;
        }
        throw new Error("Запись не найдена. Откройте раздел «Мои записи».");
      }
      const [info, list] = await Promise.all([
        api(`${base}/context`),
        api(`${base}/services`),
      ]);
      if (active) {
        setBusiness(info);
        setServices(list);
        event("web_booking_view");
        const restore =
          user?.intent?.bot_public_id === publicId && user.intent.start_time;
        const target = civilDate(
          restore ? new Date(user!.intent.start_time) : new Date(),
          info.project.timezone,
        );
        setMonth(
          new Date(
            Number(target.slice(0, 4)),
            Number(target.slice(5, 7)) - 1,
            1,
          ),
        );
        if (restore) setDate(target);
      }
    });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!business?.branding) return;
    let active = true;
    Promise.all([
      business.branding.show_reviews ? api(`${base}/reviews`) : [],
      business.branding.show_portfolio ? api(`${base}/portfolio`) : [],
    ])
      .then(([r, p]) => {
        if (active) {
          setReviews(r);
          setPortfolio(p);
        }
      })
      .catch(() => {
        if (active)
          setSectionError(
            "Не удалось загрузить портфолио и отзывы. Запись остаётся доступной.",
          );
      });
    return () => {
      active = false;
    };
  }, [business]);
  useEffect(() => {
    if (!serviceId) return;
    let active = true;
    run(async () => {
      const list = await api(`${base}/staff?service_id=${serviceId}`);
      if (active) setStaff(list);
    });
    return () => {
      active = false;
    };
  }, [serviceId]);
  useEffect(() => {
    if (!serviceId || (!staffId && business?.branding?.show_staff !== false))
      return;
    let active = true;
    run(async () => {
      const calendar = await api(
        `${base}/availability/calendar?service_id=${serviceId}${staffId ? `&staff_id=${staffId}` : ""}&year=${month.getFullYear()}&month=${month.getMonth() + 1}`,
      );
      if (active) setDays(calendar.days);
    });
    return () => {
      active = false;
    };
  }, [serviceId, staffId, month]);
  useEffect(() => {
    if (
      !date ||
      (!staffId && business?.branding?.show_staff !== false) ||
      !serviceId
    )
      return;
    let active = true;
    run(async () => {
      const result = await api(
        `${base}/slots?service_id=${serviceId}${staffId ? `&staff_id=${staffId}` : ""}&target_date=${date}`,
      );
      if (active) setSlots(result.slots);
    });
    return () => {
      active = false;
    };
  }, [date, staffId, serviceId]);

  function login() {
    event("telegram_login_started");
    const query = new URLSearchParams();
    if (!isAccount) {
      query.set("bot_public_id", publicId);
      if (serviceId) query.set("service_id", String(serviceId));
      if (staffId) query.set("staff_id", String(staffId));
      if (slot) query.set("start_time", slot);
    }
    window.location.assign(`/api/auth/telegram/login?${query}`);
  }
  const selectedService = services.find((s) => s.id === serviceId);
  const selectedStaff = staff.find((s) => s.id === staffId);
  async function confirm() {
    await run(async () => {
      const selection = {
        service_id: serviceId,
        staff_id: staffId || null,
        start_time: slot,
      };
      const hold = await api(
        `${base}/holds`,
        selection,
        operationKey(["hold", selection]),
      );
      const body = { appointment_id: hold.id, phone, policy_agreed: agreed };
      const result = await api(
        `${base}/confirm`,
        body,
        operationKey(["confirm", body]),
      );
      setBooking(result);
      event("booking_confirmed");
      if (Number(result.deposit) > 0)
        setRequisites(
          (await api(`${base}/appointments/${result.id}/payment`)).requisites,
        );
    });
  }
  const firstWeekday =
    (new Date(month.getFullYear(), month.getMonth(), 1).getDay() + 6) % 7;
  function bookingCard(item: Booking) {
    return (
      <article key={item.id} className="wb-card">
        <h3>{item.service}</h3>
        <p>
          {item.staff} · {bookingDate(item.start_time)}
        </p>
        <p>
          {item.status_label} · {item.price} ₽
        </p>
        {isAccount &&
          Number(item.deposit) > 0 &&
          ["WAITING_PAYMENT", "PAYMENT_PROOF_SENT"].includes(
            item.status || "",
          ) && (
            <a href={`/book/${item.bot_public_id}?appointment=${item.id}`}>
              Предоплата и чек
            </a>
          )}
        {item.cancel_allowed && (
          <button
            disabled={busy}
            onClick={() => {
              if (
                window.confirm(
                  "Отменить запись? Внесённая предоплата может не возвращаться.",
                )
              )
                run(async () => {
                  const cancelled = await api(
                    `/api/web-booking/${item.bot_public_id || publicId}/appointments/${item.id}/cancel`,
                    {},
                  );
                  if (booking?.id === item.id) setBooking(cancelled);
                  setBookings(await api("/api/web-booking/account/bookings"));
                });
            }}
          >
            Отменить запись
          </button>
        )}
      </article>
    );
  }
  const activeTheme = themeChoice || business?.branding?.theme_mode || "system";
  const resolvedTheme =
    activeTheme === "system" ? (systemDark ? "dark" : "light") : activeTheme;
  return (
    <div
      className="wb"
      data-theme={resolvedTheme}
      data-appearance={business?.branding?.appearance_preset || "clean"}
      style={brandColors(business?.branding?.accent_color) as CSSProperties}
    >
      <header>
        <a href="/">ZapisFlow</a>
        <a href="/account/bookings">Мои записи</a>
      </header>
      <main>
        <label className="wb-theme">
          Тема
          <select
            value={activeTheme}
            onChange={(e) => {
              const theme = e.target.value as "system" | "light" | "dark";
              setThemeChoice(theme);
              try {
                localStorage.setItem("zf-web-theme", theme);
              } catch {
                /* Storage can be disabled. */
              }
            }}
          >
            <option value="system">Система</option>
            <option value="light">Светлая</option>
            <option value="dark">Тёмная</option>
          </select>
        </label>
        {safeAsset(business?.branding?.cover_url) && (
          <img
            className="wb-cover"
            src={safeAsset(business?.branding?.cover_url)}
            alt=""
            loading="lazy"
          />
        )}
        {safeAsset(business?.branding?.logo_url) && (
          <img
            className="wb-logo"
            src={safeAsset(business?.branding?.logo_url)}
            alt=""
          />
        )}
        <h1>
          {isAccount
            ? "Мои записи"
            : business?.project.name || "Запись к мастеру"}
        </h1>
        {business?.branding?.tagline && <p>{business.branding.tagline}</p>}
        {!isAccount && !booking && (
          <a className="wb-cta" href="#web-services">
            {business?.branding?.booking_cta_label || "Записаться"}
          </a>
        )}
        {business?.branding?.welcome_text && (
          <p>{business.branding.welcome_text}</p>
        )}
        {(business?.branding?.description || business?.project.about) && (
          <p>{business?.branding?.description || business?.project.about}</p>
        )}
        <div aria-live="polite">
          {busy && <p role="status">Загрузка…</p>}
          {error && (
            <p ref={errorBox} tabIndex={-1} className="wb-error" role="alert">
              {error}
            </p>
          )}
        </div>
        {isAccount ? (
          !account ? (
            <button onClick={login}>Войти через Telegram</button>
          ) : (
            <>
              <h2>Предстоящие</h2>
              {bookings
                .filter((b) => new Date(b.start_time) >= new Date())
                .map(bookingCard)}
              <h2>Прошедшие</h2>
              {bookings
                .filter((b) => new Date(b.start_time) < new Date())
                .map(bookingCard)}
              <button
                onClick={() =>
                  run(async () => {
                    await api("/api/auth/logout", {});
                    setAccount(null);
                    setBookings([]);
                  })
                }
              >
                Выйти
              </button>
            </>
          )
        ) : booking ? (
          <section>
            <h2>
              {window.location.search.includes("appointment=")
                ? "Ваша запись"
                : "Запись оформлена"}
            </h2>
            {bookingCard(booking)}
            {Number(booking.deposit) > 0 &&
              ["WAITING_PAYMENT", "PAYMENT_PROOF_SENT"].includes(
                booking.status || "",
              ) && <p>Требуется предоплата {booking.deposit} ₽.</p>}
            {requisites && (
              <section>
                <h3>Реквизиты мастера</h3>
                <dl>
                  <dt>Банк</dt>
                  <dd>{requisites.bank_name}</dd>
                  <dt>Карта / телефон</dt>
                  <dd>{requisites.bank_card_number}</dd>
                  <dt>Получатель</dt>
                  <dd>{requisites.bank_recipient_name}</dd>
                </dl>
                <p>
                  После перевода загрузите изображение чека. Мастер проверит
                  оплату вручную.
                </p>
                {proofSent ? (
                  <p role="status">Чек отправлен мастеру на проверку.</p>
                ) : (
                  <label>
                    Подтверждение оплаты
                    <input
                      type="file"
                      accept="image/jpeg,image/png"
                      disabled={busy}
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (!file) return;
                        run(async () => {
                          if (file.size > 8 * 1024 * 1024)
                            throw new Error(
                              "Файл слишком большой. Выберите изображение до 8 МБ.",
                            );
                          const data = new FormData();
                          data.append("file", file);
                          const controller = new AbortController();
                          const timer = window.setTimeout(
                            () => controller.abort(),
                            30000,
                          );
                          let response: Response;
                          try {
                            response = await fetch(
                              `${base}/appointments/${booking.id}/proof`,
                              {
                                method: "POST",
                                credentials: "same-origin",
                                signal: controller.signal,
                                headers: {
                                  "X-CSRF-Token": account?.csrf_token || "",
                                  "Idempotency-Key": operationKey([
                                    "proof",
                                    booking.id,
                                    file.name,
                                    file.size,
                                    file.lastModified,
                                  ]),
                                },
                                body: data,
                              },
                            );
                          } catch {
                            throw new Error(
                              "Не удалось отправить чек. Проверьте соединение и повторите запрос.",
                            );
                          } finally {
                            window.clearTimeout(timer);
                          }
                          let result;
                          try {
                            result = await response.json();
                          } catch {
                            throw new Error(
                              "Не удалось отправить чек. Свяжитесь с мастером.",
                            );
                          }
                          if (!response.ok)
                            throw new Error(
                              result.message ||
                                "Не удалось отправить чек. Свяжитесь с мастером.",
                            );
                          if (result.appointment)
                            setBooking(result.appointment);
                          setProofSent(true);
                        });
                      }}
                    />
                  </label>
                )}
              </section>
            )}
            {business?.bot_username && (
              <a
                href={`https://t.me/${business.bot_username}?start=web_booking`}
              >
                Получать напоминания в Telegram
              </a>
            )}
          </section>
        ) : (
          <>
            <section>
              <h2 id="web-services">Услуга</h2>
              <div className="wb-options">
                {services.map((s) => (
                  <button
                    key={s.id}
                    aria-pressed={serviceId === s.id}
                    onClick={() => {
                      setServiceId(s.id);
                      setStaffId(0);
                      setDate("");
                      setSlot("");
                      event("service_selected");
                    }}
                  >
                    {s.title}
                    {s.description && <span>{s.description}</span>}
                    <span>
                      {s.price} ₽ · {s.duration_min} мин
                    </span>
                  </button>
                ))}
              </div>
            </section>
            {!!serviceId && business?.branding?.show_staff !== false && (
              <section>
                <h2>Специалист</h2>
                <div className="wb-options">
                  {staff.map((s) => (
                    <button
                      key={s.id}
                      aria-pressed={staffId === s.id}
                      onClick={() => {
                        setStaffId(s.id);
                        setDate("");
                        setSlot("");
                        event("staff_selected");
                      }}
                    >
                      {s.display_name}
                    </button>
                  ))}
                </div>
              </section>
            )}
            {(!!staffId ||
              (!!serviceId && business?.branding?.show_staff === false)) && (
              <section>
                <h2>Дата</h2>
                <div className="wb-month">
                  <button
                    aria-label="Предыдущий месяц"
                    onClick={() => {
                      setMonth(
                        new Date(month.getFullYear(), month.getMonth() - 1, 1),
                      );
                      setDays([]);
                    }}
                  >
                    ‹
                  </button>
                  <strong>
                    {month.toLocaleDateString("ru-RU", {
                      month: "long",
                      year: "numeric",
                    })}
                  </strong>
                  <button
                    aria-label="Следующий месяц"
                    onClick={() => {
                      setMonth(
                        new Date(month.getFullYear(), month.getMonth() + 1, 1),
                      );
                      setDays([]);
                    }}
                  >
                    ›
                  </button>
                </div>
                <div className="wb-calendar">
                  {["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((d) => (
                    <span key={d}>{d}</span>
                  ))}
                  {Array.from({ length: firstWeekday }, (_, i) => (
                    <span key={`empty-${i}`} />
                  ))}
                  {days.map((d) => (
                    <button
                      key={d.date}
                      aria-label={d.date}
                      title={
                        d.reason === "past"
                          ? "Дата прошла"
                          : d.reason === "horizon"
                            ? "За горизонтом записи"
                            : d.reason === "no_slots"
                              ? "Нет свободного времени"
                              : "Доступна"
                      }
                      aria-current={
                        d.date ===
                        new Date().toLocaleDateString("sv-SE", {
                          timeZone: business?.project.timezone || "UTC",
                        })
                          ? "date"
                          : undefined
                      }
                      aria-pressed={date === d.date}
                      disabled={!d.available || busy}
                      onClick={() => {
                        setDate(d.date);
                        setSlot("");
                        event("date_selected");
                      }}
                    >
                      {Number(d.date.slice(-2))}
                    </button>
                  ))}
                </div>
                {days.length > 0 && !days.some((d) => d.available) && (
                  <p>У мастера нет свободных дат в этом месяце.</p>
                )}
              </section>
            )}
            {!!date && (
              <section>
                <h2>Время</h2>
                <div className="wb-slots">
                  {slots.map((time) => (
                    <button
                      key={time}
                      aria-pressed={slot === time}
                      onClick={() => {
                        setSlot(time);
                        event("slot_selected");
                      }}
                    >
                      {new Date(time).toLocaleTimeString("ru-RU", {
                        hour: "2-digit",
                        minute: "2-digit",
                        timeZone: business?.project.timezone || "UTC",
                      })}
                    </button>
                  ))}
                </div>
                {!busy && slots.length === 0 && (
                  <p>
                    На эту дату нет свободного времени. Выберите другую дату.
                  </p>
                )}
              </section>
            )}
            {!!slot && (
              <section>
                <h2>Подтверждение</h2>
                <p>
                  {selectedService?.title} ·{" "}
                  {selectedStaff?.display_name || "Любой специалист"}
                </p>
                <p>
                  {new Date(slot).toLocaleString("ru-RU", {
                    timeZone: business?.project.timezone || "UTC",
                    year: "numeric",
                    month: "2-digit",
                    day: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}{" "}
                  · {selectedService?.price} ₽ · {selectedService?.duration_min}{" "}
                  мин
                </p>
                {!account ? (
                  <>
                    <p>
                      Войдите через Telegram, чтобы оформить запись. До
                      подтверждения время остаётся свободным.
                    </p>
                    <button onClick={login}>Войти через Telegram</button>
                  </>
                ) : (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      confirm();
                    }}
                  >
                    <label>
                      Телефон
                      <input
                        required
                        type="tel"
                        autoComplete="tel"
                        value={phone}
                        onChange={(e) => setPhone(e.target.value)}
                      />
                    </label>
                    <label className="wb-agree">
                      <input
                        required
                        type="checkbox"
                        checked={agreed}
                        onChange={(e) => setAgreed(e.target.checked)}
                      />
                      Согласен с условиями отмены: не позднее чем за{" "}
                      {business?.cancel_policy_hours} ч. Внесённая предоплата
                      может не возвращаться.
                    </label>
                    <button disabled={busy || !agreed}>
                      Подтвердить запись
                    </button>
                  </form>
                )}
              </section>
            )}
          </>
        )}
        {sectionError && <p role="status">{sectionError}</p>}
        {portfolio.length > 0 && (
          <section aria-label="Портфолио">
            <h2>Портфолио</h2>
            <div className="wb-options">
              {portfolio.map((item) => (
                <article key={item.id}>
                  {/^\/api\/web-booking\/[a-f0-9-]{36}\/portfolio\/\d+\/image$/i.test(
                    item.image_url,
                  ) && (
                    <img
                      className="wb-portfolio"
                      src={item.image_url}
                      alt={item.title || "Работа мастера"}
                      loading="lazy"
                    />
                  )}
                  <h3>{item.title}</h3>
                  <p>{item.caption}</p>
                </article>
              ))}
            </div>
          </section>
        )}
        {reviews.length > 0 && (
          <section aria-label="Отзывы">
            <h2>Отзывы</h2>
            {reviews.map((review, i) => (
              <article className="wb-card" key={i}>
                <p>Оценка {review.rating} из 5</p>
                <p>{review.comment}</p>
              </article>
            ))}
          </section>
        )}
        {business?.branding?.show_contacts !== false && business?.contacts && (
          <section aria-label="Контакты">
            <h2>Контакты</h2>
            {Object.entries({
              studio_phone: "Телефон",
              whatsapp_phone: "WhatsApp",
              studio_address: "Адрес",
              working_hours_text: "Режим работы",
              telegram_username: "Telegram",
              vk_profile: "ВКонтакте",
            })
              .filter(([key]) => business.contacts?.[key])
              .map(([key, label]) => (
                <p key={key}>
                  <strong>{label}: </strong>
                  {business.contacts?.[key]}
                </p>
              ))}
          </section>
        )}
        <footer className="wb-powered">Работает на ZapisFlow</footer>
      </main>
    </div>
  );
}
