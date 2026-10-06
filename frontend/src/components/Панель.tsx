import { useCallback, useEffect, useRef, useState } from "react";

import {
  выйти,
  моиВидыВстреч,
  моиЗаявки,
  создатьВидВстречи,
  type Заявка,
  type Эксперт,
  type ВидВстречи,
} from "../api";

/**
 * Панель эксперта, всё на одной странице.
 *
 * Слева список встреч, справа заявки выбранной. Формы новой встречи тоже
 * справа: отдельный экран и модальное окно заставили бы эксперта держать
 * в голове, где он находится.
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

/** Часовой пояс гостя, чтобы показывать время в привычном виде. */
function мой_пояс(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** Время заявки. Пояс задаётся явно: без него результат зависит от машины. */
function когда(начало: string, пояс: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    weekday: "short",
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: пояс,
  }).format(new Date(начало));
}

export function Панель({ эксперт }: { эксперт: Эксперт }) {
  const [виды, setВиды] = useState<ВидВстречи[]>([]);
  const [заявки, setЗаявки] = useState<Заявка[]>([]);
  const [выбрана, setВыбрана] = useState<number | null>(null);
  const [создаём, setСоздаём] = useState(false);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [загружаем, setЗагружаем] = useState(true);

  const обновить = useCallback(async () => {
    try {
      const [списокВидов, списокЗаявок] = await Promise.all([
        моиВидыВстреч(),
        моиЗаявки(),
      ]);
      setВиды(списокВидов);
      setЗаявки(списокЗаявок);
      setВыбрана((была) =>
        была !== null && списокВидов.some((вид) => вид.id === была)
          ? была
          : (списокВидов[0]?.id ?? null),
      );
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

  const активная = виды.find((в) => в.id === выбрана) ?? null;
  const заявки_встречи = активная
    ? заявки.filter((з) => з.event_type_id === активная.id)
    : [];

  return (
    <div className="оболочка">
      <header className="шапка">
        <span className="шапка__название">Запись на встречи</span>
        <МенюПользователя эксперт={эксперт} onВыход={выход} />
      </header>

      <div className="тело">
        <nav className="колонка">
          <button
            className="кнопка кнопка--вся"
            onClick={() => {
              setСоздаём(true);
              setВыбрана(null);
            }}
          >
            Создать встречу
          </button>

          {виды.length > 0 && (
            <ul className="встречи">
              {виды.map((вид) => (
                <li key={вид.id}>
                  <button
                    className="встреча"
                    aria-current={вид.id === выбрана && !создаём}
                    onClick={() => {
                      setСоздаём(false);
                      setВыбрана(вид.id);
                    }}
                  >
                    <span className="встреча__имя">{вид.name}</span>
                    <span className="встреча__путь">{вид.public_path}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </nav>

        <main className="содержимое">
          {ошибка && <p className="текст текст--ошибка">{ошибка}</p>}

          {создаём && (
            <НоваяВстреча onГотово={обновить} onОтмена={() => setСоздаём(false)} />
          )}

          {!создаём && загружаем && (
            <p className="текст текст--приглушенный">Загружаем</p>
          )}

          {!создаём && !загружаем && виды.length === 0 && (
            <div className="пусто">
              <h2 className="подзаголовок">Пока нет ни одной встречи</h2>
              <p className="текст текст--приглушенный">
                Создайте первую, получите ссылку и отправьте её гостям.
              </p>
            </div>
          )}

          {!создаём && активная && (
            <ЗаявкиВстречи
              встреча={активная}
              заявки={заявки_встречи}
              пояс={мой_пояс()}
            />
          )}
        </main>
      </div>
    </div>
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
        <span className="меню__подпись">{эксперт.role === "admin" ? "admin" : "эксперт"}</span>
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

function ЗаявкиВстречи({
  встреча,
  заявки,
  пояс,
}: {
  встреча: ВидВстречи;
  заявки: Заявка[];
  пояс: string;
}) {
  const [скопировано, setСкопировано] = useState(false);
  const ссылка = `${location.origin}${встреча.public_path}`;

  async function скопировать() {
    try {
      await navigator.clipboard.writeText(ссылка);
      setСкопировано(true);
      setTimeout(() => setСкопировано(false), 2000);
    } catch {
      // Буфер обмена может быть недоступен: ссылка и так на экране,
      // её можно выделить и скопировать руками.
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
          <div className="строка">
            <dt className="мелкий текст--приглушенный">Мест в слоте</dt>
            <dd>{встреча.max_guests}</dd>
          </div>
        </dl>

        <div>
          <button className="кнопка" onClick={скопировать}>
            {скопировано ? "Ссылка скопирована" : "Скопировать ссылку"}
          </button>
        </div>
      </div>

      <div className="разделитель" />

      <section className="группа">
        <h2 className="подзаголовок">Заявки</h2>

        {заявки.length === 0 ? (
          <p className="текст текст--приглушенный">
            Пока никто не записался. Отправьте ссылку гостям.
          </p>
        ) : (
          <ul>
            {заявки.map((заявка) => (
              <li className="строка" key={заявка.id}>
                <div>
                  <p className="строка__имя">{заявка.name}</p>
                  <p className="строка__почта">{заявка.email}</p>
                </div>
                <p className="строка__когда">{когда(заявка.start_at, пояс)}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

function НоваяВстреча({
  onГотово,
  onОтмена,
}: {
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
        max_guests: 1,
        schedule: { weekdays: дни, start_time: начало, end_time: конец },
      });
      await onГотово();
      setИмя("");
      setАдрес("");
    } catch (ошибка) {
      setОшибка(
        ошибка instanceof Error ? ошибка.message : "Не удалось создать встречу",
      );
    } finally {
      setЗанято(false);
    }
  }

  return (
    <form className="группа" onSubmit={отправить}>
      <h1 className="заголовок">Новая встреча</h1>

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
        <button
          type="button"
          className="кнопка"
          onClick={onОтмена}
          disabled={занято}
        >
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
