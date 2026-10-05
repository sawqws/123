# Таблица капитала

`capital_v7.xlsx` — актуальная таблица для Google Таблиц.

- У каждого месяца на всех листах есть колонка «Комментарий».
- Лист «КАПИТАЛ», строки 33–37: курсы подтягиваются сами на **29-е число месяца колонки, 10:00** (в феврале — последний день). До этого момента ячейки пустые.
  - Доллар, евро, бат — официальный курс ЦБ РФ (cbr.ru), за 1 единицу.
  - BTC, ETH — GOOGLEFINANCE (к USD) × курс доллара ЦБ. В сам день 29-го — текущая цена, со следующего дня — цена закрытия 29-го.
  - Время 10:00 считается по часовому поясу таблицы (Файл → Настройки).
- Формулы работают только в Google Таблицах.

`build_capital.py` — собирает `capital_v7.xlsx` из `capital_v5_original.xlsx`.

## Формулы для ручной вставки (колонка C, русская локаль с `;`)

Для колонок G и K — скопировать ячейку, ссылки сдвинутся сами.

**C32 — дата курса**

```
=DATE(YEAR(C$1);MONTH(C$1);MIN(29;DAY(EOMONTH(C$1;0))))+TIME(10;0;0)
```

**C33 — Доллар**

```
=IF(OR(C$32="";NOW()<C$32);"";IFERROR(IMPORTXML("https://www.cbr.ru/scripts/XML_daily.asp?date_req="&TEXT(C$32;"dd/mm/yyyy");"translate(//Valute[CharCode='USD']/Value,',','')")/10000/IMPORTXML("https://www.cbr.ru/scripts/XML_daily.asp?date_req="&TEXT(C$32;"dd/mm/yyyy");"//Valute[CharCode='USD']/Nominal");""))
```

**C34 — Евро**

```
=IF(OR(C$32="";NOW()<C$32);"";IFERROR(IMPORTXML("https://www.cbr.ru/scripts/XML_daily.asp?date_req="&TEXT(C$32;"dd/mm/yyyy");"translate(//Valute[CharCode='EUR']/Value,',','')")/10000/IMPORTXML("https://www.cbr.ru/scripts/XML_daily.asp?date_req="&TEXT(C$32;"dd/mm/yyyy");"//Valute[CharCode='EUR']/Nominal");""))
```

**C35 — Бат**

```
=IF(OR(C$32="";NOW()<C$32);"";IFERROR(IMPORTXML("https://www.cbr.ru/scripts/XML_daily.asp?date_req="&TEXT(C$32;"dd/mm/yyyy");"translate(//Valute[CharCode='THB']/Value,',','')")/10000/IMPORTXML("https://www.cbr.ru/scripts/XML_daily.asp?date_req="&TEXT(C$32;"dd/mm/yyyy");"//Valute[CharCode='THB']/Nominal");""))
```

**C36 — ВТС**

```
=IF(OR(C$32="";NOW()<C$32;N(C33)=0);"";IFERROR(IF(INT(C$32)=TODAY();GOOGLEFINANCE("CURRENCY:BTCUSD");INDEX(GOOGLEFINANCE("CURRENCY:BTCUSD";"price";C$32);2;2))*C33;""))
```

**C37 — ETH**

```
=IF(OR(C$32="";NOW()<C$32;N(C33)=0);"";IFERROR(IF(INT(C$32)=TODAY();GOOGLEFINANCE("CURRENCY:ETHUSD");INDEX(GOOGLEFINANCE("CURRENCY:ETHUSD";"price";C$32);2;2))*C33;""))
```
