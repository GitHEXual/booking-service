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

/**
 * Отказ сервиса.
 *
 * Отличать «не вошёл» от «сервис сломался» приходится по коду ответа, а не по
 * тексту: текст сервис меняет, код 401 остаётся.
 */
export class ОшибкаЗапроса extends Error {
  constructor(
    readonly код: number,
    сообщение: string,
  ) {
    super(сообщение);
    this.name = "ОшибкаЗапроса";
  }
}

/** Человек не вошёл или сессия закончилась. */
export function это_отказ_в_доступе(ошибка: unknown): boolean {
  return ошибка instanceof ОшибкаЗапроса && ошибка.код === 401;
}

/**
 * Кто вошёл.
 *
 * Возвращает `null`, если человек не вошёл: это обычное состояние, а не поломка,
 * и страница входа показывается без сообщения об ошибке.
 */
export async function кто_я(): Promise<Эксперт | null> {
  let ответ: Response;
  try {
    ответ = await fetch("/auth/me", { credentials: "same-origin" });
  } catch {
    throw new ОшибкаЗапроса(0, "Сервис недоступен. Проверьте, что он запущен.");
  }

  if (ответ.status === 401) {
    return null;
  }
  if (!ответ.ok) {
    throw new ОшибкаЗапроса(ответ.status, "Не удалось узнать, кто вошёл");
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
