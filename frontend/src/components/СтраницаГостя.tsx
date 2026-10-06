import { useEffect, useMemo, useState } from "react";

import {
  отправитьЗаявку,
  слотыВстречи,
  страницаВстречи,
  type Слот,
  type СтраницаВстречи,
} from "../api";

function капитализировать(текст: string): string {
  return текст.charAt(0).toUpperCase() + текст.slice(1);
}

/** Часовой пояс гостя: слоты показываем в его времени, а не в зоне сервера. */
function мой_пояс(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/**
 * Ключ дня в зоне гостя, например `2026-10-15`.
 *
 * Дата берётся именно в поясе гостя, а не в UTC: слот в 23:30 по гостю
 * относится к следующим его суткам, и при группировке по UTC он уехал бы
 * в чужой день.
 */
function ключДня(момент: Date, пояс: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: пояс,
  }).format(момент);
}

function заголовокДня(ключ: string, пояс: string): string {
  const момент = new Date(`${ключ}T12:00:00`);
  return new Intl.DateTimeFormat("ru-RU", {
    weekday: "long",
    day: "numeric",
    month: "long",
    timeZone: пояс,
  }).format(момент);
}

function времяСлота(слот: Слот, пояс: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: пояс,
  }).format(new Date(слот.start_at));
}

function названиеМесяца(год: number, месяц: number): string {
  // Месяц форматируем отдельно от года: `Intl` с `year` добавляет «г.»,
  // а в заголовке календаря это лишнее слово.
  const месяцНазвание = new Intl.DateTimeFormat("ru-RU", {
    month: "long",
  }).format(new Date(год, месяц, 1));
  return `${капитализировать(месяцНазвание)} ${год}`;
}

/** Дни месяца с ведущими пустыми клетками, чтобы сетка начиналась с понедельника. */
function ячейкиМесяца(год: number, месяц: number): (number | null)[] {
  const сколько = new Date(год, месяц + 1, 0).getDate();
  // getDay отдаёт 0 для воскресенья, а недель�� у нас начинается с понедельника.
  const сдвиг = (new Date(год, месяц, 1).getDay() + 6) % 7;
  return [
    ...Array.from({ length: сдвиг }, () => null),
    ...Array.from({ length: сколько }, (_, i) => i + 1),
  ];
}

export function СтраницаГостя({
  owner,
  slug,
}: {
  owner: string;
  slug: string;
}) {
  const [встреча, setВстреча] = useState<СтраницаВстречи | null>(null);
  const [поДням, setПоДням] = useState<Record<string, Слот[]>>({});
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [выбранныйДень, setВыбранныйДень] = useState<string | null>(null);
  const [выбранныйСлот, setВыбранныйСлот] = useState<Слот | null>(null);
  const [видимыйМесяц, setВидимыйМесяц] = useState(() => {
    const сейчас = new Date();
    return сейчас.getFullYear() * 12 + сейчас.getMonth();
  });

  const пояс = useMemo(мой_пояс, []);

  useEffect(() => {
    let отменено = false;

    (async () => {
      try {
        const [страница, сетка] = await Promise.all([
          страницаВстречи(owner, slug),
          слотыВстречи(owner, slug, 60),
        ]);
        if (отменено) return;

        const группы: Record<string, Слот[]> = {};
        for (const слот of сетка.slots) {
          const ключ = ключДня(new Date(слот.start_at), пояс);
          (группы[ключ] ??= []).push(слот);
        }
        setВстреча(страница);
        setПоДням(группы);

        // Сразу открываем ближайший день со свободным временем: пустой
        // календарь без подсказки выглядит как поломка.
        const ближайший = Object.keys(группы).sort()[0] ?? null;
        setВыбранныйДень(ближайший);
      } catch (ошибка) {
        if (!отменено) {
          setОшибка(
            ошибка instanceof Error
              ? ошибка.message
              : "Не удалось загрузить страницу",
          );
        }
      }
    })();

    return () => {
      отменено = true;
    };
  }, [owner, slug, пояс]);

  const год = Math.floor(видимыйМесяц / 12);
  const месяц = видимыйМесяц % 12;

  // При переходе между месяцами выбранный день обязан оказаться в видимом
  // месяце, иначе под календарём висит время вчерашнего месяца. Берём первый
  // день с свободным временем внутри месяца, а если такого нет, сбрасываем
  // выбор: показывать пустоту честнее, чем чужую дату.
  useEffect(() => {
    const префикс = `${год}-${String(месяц + 1).padStart(2, "0")}`;
    setВыбранныйДень((былый) => {
      if (былый && былый.startsWith(префикс)) {
        return былый;
      }
      const вЭтомМесяце = Object.keys(поДням)
        .filter((ключ) => ключ.startsWith(префикс))
        .sort();
      return вЭтомМесяце[0] ?? null;
    });
  }, [год, месяц, поДням]);

  if (ошибка) {
    return (
      <main className="узкая">
        <h1 className="заголовок">Ссылка не работает</h1>
        <p className="текст текст--приглушенный">{ошибка}</p>
      </main>
    );
  }

  if (!встреча) {
    return (
      <main className="узкая">
        <p className="текст текст--приглушенный">Загружаем</p>
      </main>
    );
  }

  return (
    <div className="оболочка">
      <main className="страница-гостя">
        <div className="карточка-записи">
          <aside className="карточка-записи__слева">
            <p className="подзаголовок">{встреча.owner_name}</p>
            <h1 className="заголовок">{встреча.name}</h1>
            <p className="мелкий текст--приглушенный">
              {встреча.duration_minutes} мин
            </p>
            {встреча.description && (
              <p className="текст">{встреча.description}</p>
            )}
          </aside>

          <div className="карточка-записи__справа">
            {Object.keys(поДням).length === 0 ? (
              <div className="пусто">
                <p className="текст">Свободного времени пока нет.</p>
                <p className="мелкий текст--приглушенный">
                  Загляните позже, расписание может измениться.
                </p>
              </div>
            ) : (
              <>
                <Календарь
                  год={год}
                  месяц={месяц}
                  поДням={поДням}
                  выбранный={выбранныйДень}
                  onВыбрать={setВыбранныйДень}
                  onСдвиг={setВидимыйМесяц}
                />

                {выбранныйДень && (
                  <section className="группа">
                    <h2 className="подзаголовок">
                      {капитализировать(заголовокДня(выбранныйДень, пояс))}
                    </h2>
                    <div className="слоты">
                      {(поДням[выбранныйДень] ?? []).map((слот) => (
                        <button
                          key={слот.start_at}
                          className="слот"
                          disabled={!слот.can_request}
                          onClick={() => setВыбранныйСлот(слот)}
                        >
                          {времяСлота(слот, пояс)}
                        </button>
                      ))}
                    </div>
                  </section>
                )}
              </>
            )}

            {выбранныйСлот && (
              <ФормаЗаявки
                слот={выбранныйСлот}
                пояс={пояс}
                owner={owner}
                slug={slug}
                день={выбранныйДень}
                onНазад={() => setВыбранныйСлот(null)}
              />
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

function Календарь({
  год,
  месяц,
  поДням,
  выбранный,
  onВыбрать,
  onСдвиг,
}: {
  год: number;
  месяц: number;
  поДням: Record<string, Слот[]>;
  выбранный: string | null;
  onВыбрать: (ключ: string) => void;
  onСдвиг: (сдвиг: number) => void;
}) {
  const нормализованныйМесяц = ((месяц % 12) + 12) % 12;
  const нормализованныйГод = год + Math.floor(месяц / 12);
  // Абсолютный индекс `год * 12 + месяц`: именно его ждёт родитель при
  // переходе между месяцами. Номер месяца не годится: из него не восстановить
  // год, и календарь уезжал на год ноль.
  const абсолютныйМесяц = нормализованныйГод * 12 + нормализованныйМесяц;
  const сегодня = new Date();

  // Ключи в UTC собираем из календарной даты без смещения: `new Date(2026, 9, 15)`
  // уже полночь местного времени, и добавлять часы не нужно.
  const ключЧисла = (число: number) =>
    `${нормализованныйГод}-${String(нормализованныйМесяц + 1).padStart(2, "0")}-${String(число).padStart(2, "0")}`;

  return (
    <section className="группа">
      <div className="календарь__шапка">
        <h2 className="подзаголовок">
          {названиеМесяца(нормализованныйГод, нормализованныйМесяц)}
        </h2>
        <div className="календарь__переходы">
          <button
            className="кнопка кнопка--тихая"
            onClick={() => onСдвиг(абсолютныйМесяц - 1)}
            aria-label="Предыдущий месяц"
          >
            Назад
          </button>
          <button
            className="кнопка кнопка--тихая"
            onClick={() => onСдвиг(абсолютныйМесяц + 1)}
            aria-label="Следующий месяц"
          >
            Далее
          </button>
        </div>
      </div>

      <div className="календарь">
        {["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((день) => (
          <span className="календарь__название" key={день}>
            {день}
          </span>
        ))}

        {ячейкиМесяца(нормализованныйГод, нормализованныйМесяц).map(
          (число, индекс) => {
            if (число === null) {
              return <span key={`пусто-${индекс}`} />;
            }
            const ключ = ключЧисла(число);
            const слоты = поДням[ключ] ?? [];
            const естьВремя = слоты.some((слот) => слот.can_request);
            const прошел =
              сегодня.getFullYear() > нормализованныйГод ||
              (сегодня.getFullYear() === нормализованныйГод &&
                сегодня.getMonth() > нормализованныйМесяц) ||
              (сегодня.getFullYear() === нормализованныйГод &&
                сегодня.getMonth() === нормализованныйМесяц &&
                сегодня.getDate() > число);

            return (
              <button
                key={ключ}
                className="календарь__день"
                disabled={!естьВремя}
                aria-pressed={ключ === выбранный}
                onClick={() => onВыбрать(ключ)}
                data-прошел={прошел ? "да" : undefined}
              >
                {число}
              </button>
            );
          },
        )}
      </div>

    </section>
  );
}

function ФормаЗаявки({
  слот,
  пояс,
  owner,
  slug,
  день,
  onНазад,
}: {
  слот: Слот;
  пояс: string;
  owner: string;
  slug: string;
  день: string | null;
  onНазад: () => void;
}) {
  const [имя, setИмя] = useState("");
  const [почта, setПочта] = useState("");
  const [согласие, setСогласие] = useState(false);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [занято, setЗанято] = useState(false);
  const [принято, setПринято] = useState(false);

  const когда = день ? `${заголовокДня(день, пояс)}, ${времяСлота(слот, пояс)}` : "";

  async function отправить(event: React.FormEvent) {
    event.preventDefault();
    setЗанято(true);
    setОшибка(null);
    try {
      await отправитьЗаявку(owner, slug, {
        start_at: слот.start_at,
        name: имя,
        email: почта,
        timezone: пояс,
        consent: согласие,
      });
      setПринято(true);
    } catch (ошибка) {
      setОшибка(
        ошибка instanceof Error ? ошибка.message : "Не удалось отправить заявку",
      );
    } finally {
      setЗанято(false);
    }
  }

  if (принято) {
    return (
      <section className="группа">
        <div className="разделитель" />
        <h2 className="подзаголовок">Заявка отправлена</h2>
        <p className="текст">
          {капитализировать(когда)}. Эксперт увидит заявку и напишет, когда сможет
          встретиться.
        </p>
        <div>
          <button className="кнопка" onClick={onНазад}>
            Выбрать другое время
          </button>
        </div>
      </section>
    );
  }

  return (
    <form className="группа" onSubmit={отправить}>
      <div className="разделитель" />
      <div>
        <button type="button" className="кнопка кнопка--тихая" onClick={onНазад}>
          Назад
        </button>
        <h2 className="подзаголовок">{капитализировать(когда)}</h2>
      </div>

      <label className="поле">
        <span>Имя</span>
        <input
          value={имя}
          onChange={(e) => setИмя(e.target.value)}
          required
          maxLength={200}
          autoComplete="name"
        />
      </label>

      <label className="поле">
        <span>Почта</span>
        <input
          type="email"
          value={почта}
          onChange={(e) => setПочта(e.target.value)}
          required
          autoComplete="email"
        />
      </label>

      <label className="поле поле--галочка">
        <input
          type="checkbox"
          checked={согласие}
          onChange={(e) => setСогласие(e.target.checked)}
          required
        />
        <span className="мелкий">Согласен на обработку персональных данных</span>
      </label>

      {ошибка && <p className="текст текст--ошибка">{ошибка}</p>}

      <button className="кнопка кнопка--главная" disabled={занято}>
        {занято ? "Отправляем" : "Отправить заявку"}
      </button>
    </form>
  );
}
