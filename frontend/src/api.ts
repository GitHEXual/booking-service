/**
 * Обращения к сервису и типы ответов.
 *
 * Всё, что знает интерфейс о сервисе, описано здесь: ни один компонент не
 * строит адрес запроса сам.
 */

/** Эксперт, вошедший в панель. Соответствует `GET /auth/me`. */
export interface Эксперт {
  id: number;
  login: string;
  email: string;
  display_name: string;
  role: "organizer" | "admin";
}

/** Вид встречи в панели эксперта. */
export interface ВидВстречи {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  duration_minutes: number;
  active: boolean;
  /** Путь публичной ссылки, который отдаёт этот гостю. */
  public_path: string;
}

/** Заявка гостя в панели эксперта. */
export interface Заявка {
  id: number;
  /** К какой встрече относится: по нему заявка попадает в нужный список. */
  event_type_id: number;
  name: string;
  email: string;
  status: "pending" | "confirmed";
  start_at: string;
  end_at: string;
  timezone: string;
}

/** Страница гостя: то, на что он записывается. */
export interface СтраницаВстречи {
  name: string;
  description: string | null;
  duration_minutes: number;
  owner_name: string;
  timezone: string;
}

/** Один слот в сетке. Время приходит в UTC с меткой пояса. */
export interface Слот {
  start_at: string;
  end_at: string;
  is_taken: boolean;
  can_request: boolean;
}

/**
 * Кто вошёл.
 *
 * Возвращает `null`, если человек не вошёл: это обычное состояние, а не поломка,
 * и страница входа показывается без сообщения об ошибке. Всё остальное
 * считается поломкой и приходит исключением с текстом для показа.
 */
export async function кто_я(): Promise<Эксперт | null> {
  let ответ: Response;
  try {
    ответ = await fetch("/auth/me", { credentials: "same-origin" });
  } catch {
    throw new Error("Сервис недоступен. Проверьте, что он запущен.");
  }

  if (ответ.status === 401) {
    return null;
  }
  if (!ответ.ok) {
    throw new Error("Не удалось узнать, кто вошёл");
  }
  return (await ответ.json()) as Эксперт;
}

/** Выйти из панели. */
export async function выйти(): Promise<void> {
  await fetch("/auth/logout", {
    method: "POST",
    credentials: "same-origin",
  });
}

/** Начать вход через Яндекс: уводим человека с этого адреса. */
export function начать_вход(): void {
  window.location.href = "/auth/yandex";
}

/** Ответ сервиса с текстом ошибки, пригодным для показа. */
export class ОшибкаСервиса extends Error {}

async function запрос(адрес: string, настройки?: RequestInit): Promise<Response> {
  let ответ: Response;
  try {
    ответ = await fetch(адрес, {
      credentials: "same-origin",
      ...настройки,
    });
  } catch {
    throw new ОшибкаСервиса("Сервис недоступен. Попробуйте ещё раз.");
  }
  if (!ответ.ok) {
    const тело = (await ответ.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new ОшибкаСервиса(тело?.detail ?? "Что-то пошло не так");
  }
  return ответ;
}

/** Виды встреч эксперта. */
export async function моиВидыВстреч(): Promise<ВидВстречи[]> {
  return (await запрос("/api/panel/event-types")).json();
}

/** Заявки гостей, ждущие решения. */
export async function моиЗаявки(): Promise<Заявка[]> {
  return (await запрос("/api/panel/bookings")).json();
}

/** Создать вид встречи вместе с его расписанием. */
export async function создатьВидВстречи(тело: СозданиеВида): Promise<ВидВстречи> {
  const ответ = await запрос("/api/panel/event-types", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(тело),
  });
  return (await ответ.json()) as ВидВстречи;
}

/**
 * Вписать ссылку на встречу руками.
 *
 * Нужна, пока у приложения нет прав на API Телемоста: встреча всё равно должна
 * состояться, а ссылка может быть любой, где эксперт её уже создал.
 */
export async function задатьСсылку(bookingId: number, joinUrl: string) {
  await запрос(`/api/panel/bookings/${bookingId}/join-url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ join_url: joinUrl }),
  });
}

/** Что эксперт задаёт при создании вида встречи. */
export interface СозданиеВида {
  name: string;
  slug: string;
  description?: string;
  duration_minutes: number;
  schedule: {
    weekdays: number[];
    start_time: string;
    end_time: string;
  };
}

/** Страница гостя по публичной ссылке. */
export async function страницаВстречи(
  owner: string,
  slug: string,
): Promise<СтраницаВстречи> {
  return (await запрос(`/api/${owner}/${slug}`)).json();
}

/** Сетка слотов на указанное число дней. */
export async function слотыВстречи(
  owner: string,
  slug: string,
  дней = 7,
): Promise<{ timezone: string; slots: Слот[] }> {
  return (await запрос(`/api/${owner}/${slug}/slots?дней=${дней}`)).json();
}

/** Отправить заявку. */
export async function отправитьЗаявку(
  owner: string,
  slug: string,
  тело: ЗаявкаВвода,
): Promise<{ id: number; status: string }> {
  const ответ = await запрос(`/api/${owner}/${slug}/bookings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(тело),
  });
  return (await ответ.json()) as { id: number; status: string };
}

/** То, что гость оставляет в форме. */
export interface ЗаявкаВвода {
  start_at: string;
  name: string;
  email: string;
  timezone: string;
  consent: boolean;
}
