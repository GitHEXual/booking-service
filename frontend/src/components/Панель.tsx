import { useCallback, useEffect, useRef, useState } from "react";

import {
  выйти,
  моиВидыВстреч,
  моиЗаявки,
  сохранитьНастройки,
  удалитьЗаявку,
  создатьВидВстречи,
  type Заявка,
  type Эксперт,
  type ВидВстречи,
} from "../api";

/**
 * Панель эксперта, всё на одной странице.
 *
 * Слева навигация: главная со ближайшими встречами и список видов встреч.
 * Справа содержимое выбранного раздела. Часового пояса в интерфейсе нет:
 * время всюду показывается в поясе эксперта, а рядом стоит подпись вида
 * `UTC+7`, чтобы не приходилось держать смещение в голове.
 */

/** Дни недели, где 1 это понедельник. */
const ДНИ = [
  { номер: 1, коротко: "Пн" },
  { номер: 2, коротко: "Вт" },
  { номер: 3, коротко: "Ср" },
  { номер: 4, коротко: "Чт" },
  { номер: 5, коротко: "Пт" },
  { номер: 6, коротко: "Сб" },
  { номер: 7, коротко: "Вс" },
];

/** Подпись смещения пояса, например `UTC+7`. */
function смещениеUTC(пояс: string): string {
  try {
    const части = new Intl.DateTimeFormat("en-US", {
      timeZone: пояс,
      timeZoneName: "shortOffset",
    }).formatToParts(new Date());
    const имя = части.find((часть) => часть.type === "timeZoneName")?.value ?? "";
    const найдено = /GMT([+-]\d{1,2}(?::\d{2})?)?/.exec(имя);
    return `UTC${найдено?.[1] ?? ""}`;
  } catch {
    return "UTC";
  }
}

/** Время встречи. Пояс задаётся явно: без него результат зависит от машины. */
function время(начало: string, пояс: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: пояс,
  }).format(new Date(начало));
}

/** Интервал встречи, например `15:00–15:30`. */
function интервал(заявка: Заявка, пояс: string): string {
  return `${время(заявка.start_at, пояс)}–${время(заявка.end_at, пояс)}`;
}

/** Короткая дата, например `6 окт`. */
function короткаяДата(начало: string, пояс: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "short",
    timeZone: пояс,
  }).format(new Date(начало));
}

function капитализировать(текст: string): string {
  return текст.charAt(0).toUpperCase() + текст.slice(1);
}

/** Пояс браузера. Нужен только как запасной, пока пояс не сохранён на сервере. */
function мойПояс(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** Идёт ли встреча прямо сейчас. */
function идётСейчас(заявка: Заявка, сейчас: number): boolean {
  return (
    new Date(заявка.start_at).getTime() <= сейчас &&
    сейчас <= new Date(заявка.end_at).getTime()
  );
}

type Раздел = "главная" | "встреча" | "новая";

export function Панель({ эксперт }: { эксперт: Эксперт }) {
  const [виды, setВиды] = useState<ВидВстречи[]>([]);
  const [заявки, setЗаявки] = useState<Заявка[]>([]);
  const [раздел, setРаздел] = useState<Раздел>("главная");
  const [выбрана, setВыбрана] = useState<number | null>(null);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [загружаем, setЗагружаем] = useState(true);

  // Редактора пояса в интерфейсе нет: время показываем в поясе эксперта, а
  // рядом пишем смещение. Если пояс всё ещё по умолчанию, один раз подставляем
  // пояс браузера, иначе часы приёма остались бы в UTC.
  const пояс = эксперт.timezone === "UTC" ? мойПояс() : эксперт.timezone;

  useEffect(() => {
    const изБраузера = мойПояс();
    if (эксперт.timezone === "UTC" && изБраузера !== "UTC") {
      void сохранитьНастройки(изБраузера).catch(() => {});
    }
  }, [эксперт.timezone]);

  const обновить = useCallback(async () => {
    try {
      const [списокВидов, списокЗаявок] = await Promise.all([
        моиВидыВстреч(),
        моиЗаявки(),
      ]);
      setВиды(списокВидов);
      setЗаявки(списокЗаявок);
      setОшибка(null);
    } catch (ошибка) {
      setОшибка(
        ошибка instanceof Error ? ошибка.message : "Не удалось загрузить панель",
      );
    } finally {
      setЗагружаем(false);
    }
  }, []);

  useEffect(() => {
    void обновить();
  }, [обновить]);

  const выход = useCallback(async () => {
    await выйти();
    location.href = "/";
  }, []);

  const активная = виды.find((вид) => вид.id === выбрана) ?? null;

  function открытьГлавную() {
    setРаздел("главная");
    setВыбрана(null);
  }

  function открытьВстречу(id: number) {
    setРаздел("встреча");
    setВыбрана(id);
  }

  function открытьСоздание() {
    setРаздел("новая");
    setВыбрана(null);
  }

  return (
    <div className="оболочка">
      <header className="шапка">
        <div className="шапка__слева">
          <span className="шапка__название">Запись на встречи</span>
          <span className="значок-пояса" title="Время на странице в вашем поясе">
            {смещениеUTC(пояс)}
          </span>
        </div>
        <МенюПользователя эксперт={эксперт} onВыход={выход} />
      </header>

      <div className="тело">
        <nav className="колонка">
          <button
            className="кнопка кнопка--вся кнопка--тихая"
            aria-current={раздел === "главная"}
            onClick={открытьГлавную}
          >
            Главная
          </button>

          <button className="кнопка кнопка--главная кнопка--вся" onClick={открытьСоздание}>
            + Создать встречу
          </button>

          {виды.length > 0 && (
            <div className="навигация__группа">
              <span className="навигация__подпись">Ваши встречи</span>
              <ul className="встречи">
                {виды.map((вид) => (
                  <li key={вид.id}>
                    <button
                      className="встреча"
                      aria-current={раздел === "встреча" && вид.id === выбрана}
                      onClick={() => открытьВстречу(вид.id)}
                    >
                      <span className="встреча__имя">{вид.name}</span>
                      <span className="встреча__путь">{вид.public_path}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </nav>

        <main className="содержимое">
          {ошибка && <p className="текст текст--ошибка">{ошибка}</p>}

          {загружаем ? (
            <p className="текст текст--приглушенный">Загружаем</p>
          ) : раздел === "главная" ? (
            <Главная
              виды={виды}
              заявки={заявки}
              пояс={пояс}
              onОбновить={обновить}
              onСоздать={открытьСоздание}
            />
          ) : раздел === "новая" ? (
            <НоваяВстреча
              пояс={пояс}
              onГотово={async () => {
                await обновить();
                открытьГлавную();
              }}
              onОтмена={открытьГлавную}
            />
          ) : активная ? (
            <Встреча
              встреча={активная}
              заявки={заявки.filter((з) => з.event_type_id === активная.id)}
              пояс={пояс}
              onОбновить={обновить}
            />
          ) : (
            <p className="текст текст--приглушенный">Встреча не найдена</p>
          )}
        </main>
      </div>
    </div>
  );
}

/** Главная: сводка и ближайшие встречи одним списком. */
function Главная({
  виды,
  заявки,
  пояс,
  onОбновить,
  onСоздать,
}: {
  виды: ВидВстречи[];
  заявки: Заявка[];
  пояс: string;
  onОбновить: () => Promise<void>;
  onСоздать: () => void;
}) {
  const сейчас = Date.now();
  const предстоящие = заявки
    .filter((з) => new Date(з.end_at).getTime() >= сейчас)
    .sort(
      (а, б) =>
        new Date(а.start_at).getTime() - new Date(б.start_at).getTime(),
    );
  const прошедшие = заявки
    .filter((з) => new Date(з.end_at).getTime() < сейчас)
    .sort(
      (а, б) =>
        new Date(б.start_at).getTime() - new Date(а.start_at).getTime(),
    );

  return (
    <div className="дашборд">
      <div className="метрики">
        <Метрика число={виды.length} подпись="виды встреч" />
        <Метрика число={предстоящие.length} подпись="предстоят" />
        <Метрика число={заявки.length} подпись="всего заявок" />
      </div>

      <section className="группа">
        <div className="раздел__шапка">
          <h2 className="подзаголовок">Ближайшие встречи</h2>
          <span className="часовой-значок">время в {смещениеUTC(пояс)}</span>
        </div>

        {предстоящие.length === 0 ? (
          <div className="пусто">
            <p className="текст">Пока никто не записался</p>
            <p className="мелкий текст--приглушенный">
              {виды.length === 0
                ? "Создайте встречу и отправьте гостям ссылку."
                : "Отправьте гостям ссылку на встречу, и заявки появятся здесь."}
            </p>
            <div>
              <button className="кнопка" onClick={onСоздать}>
                Создать встречу
              </button>
            </div>
          </div>
        ) : (
          <ul className="карточки">
            {предстоящие.map((заявка) => (
              <КарточкаЗаявки
                key={заявка.id}
                заявка={заявка}
                пояс={пояс}
                сейчас={сейчас}
                onОбновить={onОбновить}
              />
            ))}
          </ul>
        )}
      </section>

      {прошедшие.length > 0 && (
        <section className="группа">
          <div className="раздел__шапка">
            <h2 className="подзаголовок">Прошедшие</h2>
          </div>
          <ul className="карточки карточки--тихие">
            {прошедшие.map((заявка) => (
              <КарточкаЗаявки
                key={заявка.id}
                заявка={заявка}
                пояс={пояс}
                сейчас={сейчас}
                onОбновить={onОбновить}
              />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function Метрика({ число, подпись }: { число: number; подпись: string }) {
  return (
    <div className="метрика">
      <span className="метрика__число">{число}</span>
      <span className="метрика__подпись">{подпись}</span>
    </div>
  );
}

/** Карточка одной заявки: кто, когда, ссылка и удаление. */
function КарточкаЗаявки({
  заявка,
  пояс,
  сейчас,
  onОбновить,
}: {
  заявка: Заявка;
  пояс: string;
  сейчас: number;
  onОбновить: () => Promise<void>;
}) {
  const [удаляем, setУдаляем] = useState(false);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const идёт = идётСейчас(заявка, сейчас);

  async function убрать() {
    if (!window.confirm(`Убрать заявку гостя ${заявка.name}?`)) return;
    setУдаляем(true);
    setОшибка(null);
    try {
      await удалитьЗаявку(заявка.id);
      await onОбновить();
    } catch (ошибка) {
      setОшибка(
        ошибка instanceof Error ? ошибка.message : "Не удалось убрать заявку",
      );
      setУдаляем(false);
    }
  }

  return (
    <li className="карточка-встречи" data-идёт={идёт ? "да" : undefined}>
      <div className="карточка-встречи__когда">
        <span className="карточка-встречи__дата">
          {капитализировать(короткаяДата(заявка.start_at, пояс))}
        </span>
        <span className="карточка-встречи__час">{интервал(заявка, пояс)}</span>
        {идёт && <span className="метка метка--идёт">идёт сейчас</span>}
      </div>

      <div className="карточка-встречи__кто">
        <p className="строка__имя">{заявка.name}</p>
        <p className="строка__почта">{заявка.email}</p>
        <span className="метка метка--тихая">{заявка.event_type_name}</span>
      </div>

      <div className="карточка-встречи__действия">
        {заявка.join_url ? (
          <a
            className="кнопка кнопка--главная кнопка--тихая"
            href={заявка.join_url}
            target="_blank"
            rel="noreferrer"
          >
            Подключиться
          </a>
        ) : заявка.conference_status === "failed" ? (
          <span className="метка метка--ошибка">Встреча не создалась</span>
        ) : (
          <span className="метка">Готовим ссылку</span>
        )}
        <button
          className="кнопка кнопка--тихая кнопка--опасная"
          onClick={убрать}
          disabled={удаляем}
        >
          {удаляем ? "Убираем" : "Удалить"}
        </button>
      </div>

      {ошибка && <p className="мелкий текст--ошибка">{ошибка}</p>}
    </li>
  );
}

/** Экран одного вида встречи: ссылка, расписание и заявки. */
function Встреча({
  встреча,
  заявки,
  пояс,
  onОбновить,
}: {
  встреча: ВидВстречи;
  заявки: Заявка[];
  пояс: string;
  onОбновить: () => Promise<void>;
}) {
  const [скопировано, setСкопировано] = useState(false);
  const ссылка = `${location.origin}${встреча.public_path}`;
  const сейчас = Date.now();
  const предстоящие = [...заявки].sort(
    (а, б) => new Date(а.start_at).getTime() - new Date(б.start_at).getTime(),
  );

  async function скопировать() {
    try {
      await navigator.clipboard.writeText(ссылка);
      setСкопировано(true);
      setTimeout(() => setСкопировано(false), 2000);
    } catch {
      // Буфер обмена может быть недоступен: ссылку можно выделить руками.
    }
  }

  return (
    <>
      <div className="группа">
        <div>
          <h1 className="заголовок">{встреча.name}</h1>
          {встреча.description && (
            <p className="текст текст--приглушенный">{встреча.description}</p>
          )}
        </div>

        <dl className="свойства">
          <div className="строка">
            <dt className="мелкий текст--приглушенный">Ссылка для гостей</dt>
            <dd className="моно">{ссылка}</dd>
          </div>
          <div className="строка">
            <dt className="мелкий текст--приглушенный">Длительность</dt>
            <dd>{встреча.duration_minutes} мин</dd>
          </div>
        </dl>

        <div className="пара">
          <button className="кнопка кнопка--главная" onClick={скопировать}>
            {скопировано ? "Ссылка скопирована" : "Скопировать ссылку"}
          </button>
          <a
            className="кнопка"
            href={встреча.public_path}
            target="_blank"
            rel="noreferrer"
          >
            Открыть страницу гостя
          </a>
        </div>
      </div>

      <div className="разделитель" />

      <section className="группа">
        <div className="раздел__шапка">
          <h2 className="подзаголовок">Записались</h2>
          {заявки.length > 0 && <span className="счётчик">{заявки.length}</span>}
        </div>

        {предстоящие.length === 0 ? (
          <div className="пусто">
            <p className="текст">Пока никто не записался</p>
            <p className="мелкий текст--приглушенный">
              Отправьте ссылку гостям, и заявки появятся здесь.
            </p>
          </div>
        ) : (
          <ul className="карточки">
            {предстоящие.map((заявка) => (
              <КарточкаЗаявки
                key={заявка.id}
                заявка={заявка}
                пояс={пояс}
                сейчас={сейчас}
                onОбновить={onОбновить}
              />
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

/** Микроменю в шапке: имя эксперта и выход из панели. */
function МенюПользователя({
  эксперт,
  onВыход,
}: {
  эксперт: Эксперт;
  onВыход: () => Promise<void>;
}) {
  const [открыто, setОткрыто] = useState(false);
  const обёртка = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!открыто) return;

    function закрыть(событие: MouseEvent) {
      if (!обёртка.current?.contains(событие.target as Node)) {
        setОткрыто(false);
      }
    }
    document.addEventListener("mousedown", закрыть);
    return () => document.removeEventListener("mousedown", закрыть);
  }, [открыто]);

  return (
    <div className="меню" ref={обёртка}>
      <button
        className="меню__кнопка"
        onClick={() => setОткрыто((б) => !б)}
        aria-expanded={открыто}
      >
        {эксперт.display_name}
        <span className="меню__подпись">
          {эксперт.role === "admin" ? "admin" : "эксперт"}
        </span>
      </button>

      {открыто && (
        <div className="меню__список">
          <p className="меню__почта">{эксперт.email}</p>
          <button
            className="меню__пункт"
            onClick={() => {
              setОткрыто(false);
              void onВыход();
            }}
          >
            Выйти
          </button>
        </div>
      )}
    </div>
  );
}

function НоваяВстреча({
  пояс,
  onГотово,
  onОтмена,
}: {
  пояс: string;
  onГотово: () => Promise<void>;
  onОтмена: () => void;
}) {
  const [имя, setИмя] = useState("");
  const [адрес, setАдрес] = useState("");
  const [длительность, setДлительность] = useState(30);
  const [начало, setНачало] = useState("09:00");
  const [конец, setКонец] = useState("18:00");
  const [дни, setДни] = useState<number[]>([1, 2, 3, 4, 5]);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [занято, setЗанято] = useState(false);

  function переключитьДень(номер: number) {
    setДни((было) =>
      было.includes(номер)
        ? было.filter((д) => д !== номер)
        : [...было, номер].sort(),
    );
  }

  async function отправить(event: React.FormEvent) {
    event.preventDefault();
    setЗанято(true);
    setОшибка(null);
    try {
      await создатьВидВстречи({
        name: имя,
        slug: адрес,
        duration_minutes: длительность,
        schedule: { weekdays: дни, start_time: начало, end_time: конец },
      });
      await onГотово();
    } catch (ошибка) {
      setОшибка(
        ошибка instanceof Error ? ошибка.message : "Не удалось создать встречу",
      );
      setЗанято(false);
    }
  }

  return (
    <form className="группа" onSubmit={отправить}>
      <h1 className="заголовок">Новая встреча</h1>
      <p className="текст текст--приглушенный">
        Часы приёма указываются в вашем поясе. Сейчас это {смещениеUTC(пояс)}.
      </p>

      <label className="поле">
        <span>Название</span>
        <input
          value={имя}
          onChange={(e) => setИмя(e.target.value)}
          required
          maxLength={100}
          placeholder="Консультация по проекту"
        />
      </label>

      <label className="поле">
        <span>Адрес ссылки</span>
        <input
          value={адрес}
          onChange={(e) =>
            setАдрес(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))
          }
          required
          maxLength={64}
          placeholder="konsultaciya"
        />
      </label>

      <label className="поле">
        <span>Длительность, минут</span>
        <input
          type="number"
          value={длительность}
          onChange={(e) => setДлительность(Number(e.target.value))}
          min={5}
          max={480}
          step={5}
        />
      </label>

      <fieldset className="поле">
        <span>Дни приёма</span>
        <div className="дни">
          {ДНИ.map((день) => (
            <button
              key={день.номер}
              type="button"
              className="день"
              aria-pressed={дни.includes(день.номер)}
              onClick={() => переключитьДень(день.номер)}
            >
              {день.коротко}
            </button>
          ))}
        </div>
      </fieldset>

      <div className="пара">
        <label className="поле">
          <span>С часов</span>
          <input
            type="time"
            value={начало}
            onChange={(e) => setНачало(e.target.value)}
            required
          />
        </label>
        <label className="поле">
          <span>До часов</span>
          <input
            type="time"
            value={конец}
            onChange={(e) => setКонец(e.target.value)}
            required
          />
        </label>
      </div>

      {ошибка && <p className="текст текст--ошибка">{ошибка}</p>}

      <div className="пара">
        <button type="button" className="кнопка" onClick={onОтмена} disabled={занято}>
          Отмена
        </button>
        <button
          className="кнопка кнопка--главная"
          disabled={занято || дни.length === 0}
        >
          {занято ? "Создаём" : "Создать"}
        </button>
      </div>
    </form>
  );
}
