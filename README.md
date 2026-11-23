# Інструкція із запуску дослідницького конвеєра (Reproducibility Guide)

Цей документ містить вимоги до програмного середовища, специфікацію досліджуваних релізних зрізів ($T_0 \rightarrow T_1$) та покрокову інструкцію відтворення повного циклу збору, оброблення й статистичного аналізу даних.

---

## 1. Вимоги до програмного середовища

Для повного відтворення конвеєра на локальній робочій станції (macOS / Linux / Windows WSL) мають бути встановлені такі інструменти та бібліотеки:

1. **Системні утиліти:**
   * **Python 3.11+** (протестовано на `Python 3.13`)
   * **Git CLI 2.40+** (із підтримкою параметрів `--no-merges` та `--follow`)
   * **PMD CPD 7.x** (виконуваний файл `pmd` має бути доданий до системної змінної середовища `PATH`; потрібен лише для етапу повторного сканування сирого коду з нуля)

2. **Залежності Python (`requirements.txt`):**
   * `pandas >= 2.0.0` — оброблення, очищення та агрегація табличних даних
   * `numpy >= 1.24.0` — логарифмічні перетворення змінних ($\ln(x + 1)$) та числові операції
   * `statsmodels >= 0.14.0` — побудова узагальнених лінійних моделей від'ємної біноміальної регресії (*Negative Binomial GLM*, метод MLE та робастні похибки `HC3`)
   * `scipy >= 1.11.0` — розрахунок рангових кореляцій Спірмена, критеріїв Манна–Уітні та Краскела–Уолліса
   * `matplotlib >= 3.7.0` — побудова підсумкової візуалізації результатів

### Налаштування віртуального середовища

```bash
python3 -m venv .venv
source .venv/bin/activate  # для Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 2. Опис досліджуваних репозиторіїв та релізних тегів ($T_0 \rightarrow T_1$)

Аналіз проводиться за лонгітюдним дизайном із часовими вікнами: на базовому зрізі **$T_0$** вимірюється щільність синтаксичних клонів та системні метрики модулів, а в інтервалі **$T_0 \rightarrow T_1$** фіксується частота комітів із виправленням дефектів (`BugFixFreq_T1`).

| Проєкт (GitHub URL) | Мова | Цільова директорія коду (`--src`) | Базовий тег $T_0$ (дата релізу) | Кінцевий тег $T_1$ (дата релізу) | Тривалість вікна, діб | Кількість модулів |
| :--- | :---: | :--- | :--- | :--- | :---: | :---: |
| `https://github.com/google/gson` | Java | `gson/src/main/java` | `gson-parent-2.14.0` (23.04.2026) | `gson-parent-2.15.0` (24.10.2026) | 184 | 86 |
| `https://github.com/apache/commons-io` | Java | `src/main/java` | `rel/commons-io-2.21.0` (06.11.2025) | `rel/commons-io-2.22.0` (23.04.2026) | 168 | 268 |
| `https://github.com/Textualize/rich` | Python | `rich` | `v14.2.0` (09.10.2025) | `v15.0.0` (12.04.2026) | 185 | 77 |

*Примітка: під час збору даних автоматично виключаються директорії тестів (`src/test/`, `tests/`), тестові файли (`*Test.java`, `test_*.py`), службові файли ініціалізації (`__init__.py`) та модулі з нульовим обсягом коду (`LOC = 0`).*

---

## 3. Покрокова інструкція запуску конвеєра

### Крок 1. Клонування цільових Git-репозиторіїв

Створіть робочу директорію для локальних копій репозиторіїв та завантажте їхню повну історію комітів і тегів:

```bash
mkdir -p repos
git clone https://github.com/google/gson.git repos/gson
git clone https://github.com/apache/commons-io.git repos/commons-io
git clone https://github.com/Textualize/rich.git repos/rich
```

### Крок 2. Екстракція метрик клонів і Git-історії (`collect_metrics.py`)

Для кожного репозиторію скрипт автоматично перемикається на базовий тег $T_0$, запускає детектор дублікатів **PMD CPD 7** у трьох каскадних режимах (Type-1, Type-2, Type-3), обчислює `PhysicalLOC` та коваріати до $T_0$, після чого аналізує `bug-fix` коміти в інтервалі $T_0 \rightarrow T_1$ і додає результати до спільного CSV-файлу:

```bash
rm -f data/full_dataset.csv

# 1. Збір даних для google/gson (86 модулів)
python scripts/collect_metrics.py \
  --project "google/gson" \
  --repo repos/gson \
  --tag0 "gson-parent-2.14.0" \
  --tag1 "gson-parent-2.15.0" \
  --src "gson/src/main/java" \
  --lang java \
  --out data/full_dataset.csv

# 2. Збір даних для apache/commons-io (268 модулів)
python scripts/collect_metrics.py \
  --project "apache/commons-io" \
  --repo repos/commons-io \
  --tag0 "rel/commons-io-2.21.0" \
  --tag1 "rel/commons-io-2.22.0" \
  --src "src/main/java" \
  --lang java \
  --out data/full_dataset.csv

# 3. Збір даних для Textualize/rich (77 модулів)
python scripts/collect_metrics.py \
  --project "Textualize/rich" \
  --repo repos/rich \
  --tag0 "v14.2.0" \
  --tag1 "v15.0.0" \
  --src "rich" \
  --lang python \
  --out data/full_dataset.csv
```

### Крок 3. Верифікація цілісності сформованого датасету

Переконайтеся, що підсумковий файл `data/full_dataset.csv` містить рівно **431** унікальний рядок без дублікатів заголовків і пропущених значень:

```bash
python -c "
import pandas as pd
df = pd.read_csv('data/full_dataset.csv')
assert len(df) == 431, f'Очікувалося 431 рядок, отримано {len(df)}'
assert df.isna().sum().sum() == 0, 'Виявлено пропущені значення!'
print('Перевірку пройдено успішно. Розподіл за проєктами:', df['project'].value_counts().to_dict())
"
```

### Крок 4. Статистичне моделювання та тести стійкості (`run_analysis.py`)

Запустіть скрипт статистичного аналізу, який обчислює описову статистику, рангові кореляції Спірмена, моделі від'ємної біноміальної регресії (*Negative Binomial GLM* із MLE та робастними похибками `HC3`), непараметричні тести Краскела–Уолліса й Манна–Уітні за чотирма інтервалами $\text{CD}_{\text{Type-2/3}}$, а також перевірки стійкості для $N = 404$ та Java-підвибірки $N = 354$:

```bash
python scripts/run_analysis.py
```

Результати зберігаються у директорії `results/` (`descriptive_stats.csv` та `regression_summary.txt`).

### Крок 5. Генерація підсумкової візуалізації (`plot_figures.py`)

Для побудови двопанельного графіка (динаміка частки дефектних модулів та історичного `CodeChurn` за інтервалами $\text{CD}_{\text{Type-2/3}}$, а також лісовий графік коефіцієнтів $\beta$ із 95% довірчими інтервалами `HC3`) виконайте:

```bash
python scripts/plot_figures.py
```

Згенероване зображення зберігається у файл `results/figure1_results.png`.