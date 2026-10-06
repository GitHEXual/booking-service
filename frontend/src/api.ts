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
