import type { Эксперт } from "../api";

/**
 * Страница профиля эксперта.
 *
 * Пока это только кто вошёл: виды встреч, расписание и заявки появятся позже.
 */
export function Профиль({ эксперт }: { эксперт: Эксперт }) {
  return (
    <main className="страница">
      <header className="шапка">
        <h1 className="заголовок">Панель эксперта</h1>
      </header>

      <section className="карточка">
        <dl className="список-пар">
          <dt>Имя</dt>
          <dd>{эксперт.display_name}</dd>
          <dt>Логин</dt>
          <dd className="моно">{эксперт.login}</dd>
          <dt>Почта</dt>
          <dd>{эксперт.email}</dd>
          <dt>Роль</dt>
          <dd>{роль_человеческим_языком(эксперт.role)}</dd>
        </dl>
      </section>
    </main>
  );
}

/** Роль словами, а не как в базе. */
function роль_человеческим_языком(роль: Эксперт["role"]): string {
  return роль === "admin" ? "Администратор" : "Эксперт";
}
