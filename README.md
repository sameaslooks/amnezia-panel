<h1 align="center">
  🚀 Amnezia VPN Panel
  <br>
  <span style="font-size: 0.8em; color: #666;">Control with ease.</span>
</h1>

<p align="center">
  <img src="https://github.com/user-attachments/assets/976a165a-9418-450f-9eff-070c1ec10fba" alt="Amnezia VPN Panel Screenshot" width="800"/>
</p>

<p align="center">
  <strong>✨ Open-source панель управления для Docker-контейнера с запущенным amnezia-awg2</strong><br>
  <em>Легко. Быстро. Для коммерции и личного использования.</em>
</p>

<p align="center">
  <a href="#-о-проекте">О проекте</a> •
  <a href="#-возможности">Возможности</a> •
  <a href="#-быстрый-старт">Быстрый старт</a> •
  <a href="#-дорожная-карта">Дорожная карта</a> •
  <a href="#-архитектура">Архитектура</a> •
  <a href="#-лицензия">Лицензия</a>
</p>

<hr>

<h2>📌 О проекте</h2>

<p>
  <strong>Amnezia VPN Panel</strong> — это готовая к использованию веб-панель для управления 
  Docker-контейнером <code>amnezia-awg2</code>. Создана для тех, кто хочет предоставлять 
  VPN-доступ своим пользователям: от небольших команд до коммерческих проектов.
</p>

<table>
  <tr>
    <td>✅ <strong>Уже работает</strong></td>
    <td>Установка одной командой, управление локальным сервером "из коробки"</td>
  </tr>
  <tr>
    <td>✅ <strong>Актуальность</strong></td>
    <td>Поддерживает текущую версию AmneziaWG (февраль 2026)</td>
  </tr>
  <tr>
    <td>✅ <strong>Open Source</strong></td>
    <td>Код открыт, можно изучать, улучшать и использовать бесплатно</td>
  </tr>
</table>

<p>
  ❗❗❗ <strong>Открыт к Pull Request и вопросам/запросам в Issues!</strong>
</p>

<hr>

<h2>✨ Возможности</h2>

<h3>✅ Полностью реализовано</h3>

<table>
  <thead>
    <tr>
      <th>Функция</th>
      <th>Описание</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><strong>Лимиты трафика</strong></td>
      <td>Динамическая блокировка/разблокировка при превышении или отсутствии лимита</td>
    </tr>
  </tbody>
</table>

<h3>🟠 Частично реализовано</h3>

<table>
  <thead>
    <tr>
      <th>Функция</th>
      <th>Статус</th>
      <th>Описание</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><strong>Управление пользователями</strong></td>
      <td>Частично</td>
      <td>Создание, массовое удаление/создание, просмотр конфигов и базовой статистики</td>
    </tr>
    <tr>
      <td><strong>AmneziaWG-конфиги</strong></td>
      <td>Частично</td>
      <td>Выдача конфигураций, QR-коды для AWG в проработке</td>
    </tr>
    <tr>
      <td><strong>vpn:// ссылки для AmneziaVPN</strong></td>
      <td>Частично</td>
      <td>Генерация ссылок для импорта в AmneziaVPN (в набросках, требуется интеграция)</td>
    </tr>
    <tr>
      <td><strong>QR-коды для AmneziaVPN</strong></td>
      <td>Не реализовано</td>
      <td>Требуется исследование механизма создания</td>
    </tr>
    <tr>
      <td><strong>Статистика использования</strong></td>
      <td>Частично</td>
      <td>Базовая текстовая статистика RX/TX доступна, графики в разработке</td>
    </tr>
  </tbody>
</table>

<h3>❌ В планах</h3>

<table>
  <thead>
    <tr>
      <th>Функция</th>
      <th>Плановая дата</th>
      <th>Описание</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><strong>Лимитированный датированный доступ</strong></td>
      <td>01.03.2026</td>
      <td>Недельные, месячные, годовые подписки для бизнеса</td>
    </tr>
    <tr>
      <td><strong>Подробная статистика</strong></td>
      <td>20.03.2026</td>
      <td>Графики, детализация по устройствам, привязанным к аккаунту</td>
    </tr>
    <tr>
      <td><strong>Управление группой серверов</strong></td>
      <td>01.04.2026</td>
      <td>Автоматическое развёртывание через SSH, единое управление, Prometheus-статистика</td>
    </tr>
    <tr>
      <td><strong>Telegram-бот и email-уведомления</strong></td>
      <td>01.04.2026</td>
      <td>Уведомления об окончании подписки, лимитах трафика</td>
    </tr>
    <tr>
      <td><strong>Интеграция с биллингом через API</strong></td>
      <td>По запросу</td>
      <td>Автоматическое продление доступа с внешними биллинг-системами</td>
    </tr>
    <tr>
      <td><strong>Смена протоколов на лету</strong></td>
      <td>01.05.2026</td>
      <td>Быстрое переключение между протоколами (XRay Reality и др.) при блокировках</td>
    </tr>
  </tbody>
</table>

<hr>

<h2>⚡ Быстрый старт (Docker)</h2>

<pre><code># Клонируем репозиторий
git clone https://github.com/sameaslooks/amnezia-panel
cd amnezia-panel

# Запускаем контейнеры
docker compose up -d
</code></pre>

<p>
  <strong>Готово!</strong> Панель будет доступна по адресу <code>http://&lt;ip-адрес-сервера&gt;:8000</code>
</p>

<p>
  <em>Примечание: проект позиционируется как Docker-контейнер, но теоретически возможен запуск и через Python напрямую.</em>
</p>

<hr>

<h2>🏗 Архитектура</h2>

<p>
  Проект построен с разделением на бэкенд и фронтенд:
</p>

<pre>
amnezia-panel/
├── backend/         # Python API
├── frontend/        # HTML/JS интерфейс
├── docker/          # Конфигурации Docker
└── docs/            # Документация
</pre>

<hr>

<h2>🤝 Открыт к сотрудничеству</h2>

<p>
  Открыт к предложениям, идеям и поправкам кода:
</p>

<ul>
  <li>🐛 Нашли баг? Создайте <a href="#">Issue!</a></li>
  <li>💡 Есть предложение? Напишите в <a href="#">Issues!</a></li>
  <li>🔧 Хотите помочь с кодом? Создавайте <a href="#">Pull Request!</a></li>
  <li>Всегда рад дополнить своё решение и помочь с вопросами!:)</li>
</ul>

<hr>

<h2>📄 Лицензия</h2>

<p>
  Проект распространяется под лицензией <strong>GPL-3.0</strong>.
</p>

<p>
  <em>
    Подробнее в файле <a href="LICENSE">LICENSE</a>.
  </em>
</p>

<hr>

<p align="center">
  <strong>⭐ Если проект вам полезен, поставьте звезду на GitHub!</strong><br>
  <sub>© 2026 Amnezia VPN Panel</sub>
</p>
