# Курсы валют в таблице капитала

`capital_v6.xlsx` — таблица с формулами, которые сами подтягивают курсы на 1-е число (лист «КАПИТАЛ», строки 33–37, дата берётся из строки 32).

- Доллар, евро, бат — официальный курс ЦБ РФ (cbr.ru), за 1 единицу.
- BTC, ETH — GOOGLEFINANCE (к USD) × курс доллара ЦБ.
- Будущие месяцы пустые, пока дата не наступит. Работает только в Google Таблицах.

`add_rates.py` — скрипт, который вставляет эти формулы в xlsx.

## Формулы для ручной вставки (колонка C, русская локаль с `;`)

Для колонок F и I — скопировать ячейку, ссылки сдвинутся сами.

**C33 — Доллар**

```
=IF(OR(C$32="";C$32>TODAY());"";IFERROR(IMPORTXML("https://www.cbr.ru/scripts/XML_daily.asp?date_req="&TEXT(C$32;"dd/mm/yyyy");"translate(//Valute[CharCode='USD']/Value,',','')")/10000/IMPORTXML("https://www.cbr.ru/scripts/XML_daily.asp?date_req="&TEXT(C$32;"dd/mm/yyyy");"//Valute[CharCode='USD']/Nominal");""))
```

**C34 — Евро**

```
=IF(OR(C$32="";C$32>TODAY());"";IFERROR(IMPORTXML("https://www.cbr.ru/scripts/XML_daily.asp?date_req="&TEXT(C$32;"dd/mm/yyyy");"translate(//Valute[CharCode='EUR']/Value,',','')")/10000/IMPORTXML("https://www.cbr.ru/scripts/XML_daily.asp?date_req="&TEXT(C$32;"dd/mm/yyyy");"//Valute[CharCode='EUR']/Nominal");""))
```

**C35 — Бат**

```
=IF(OR(C$32="";C$32>TODAY());"";IFERROR(IMPORTXML("https://www.cbr.ru/scripts/XML_daily.asp?date_req="&TEXT(C$32;"dd/mm/yyyy");"translate(//Valute[CharCode='THB']/Value,',','')")/10000/IMPORTXML("https://www.cbr.ru/scripts/XML_daily.asp?date_req="&TEXT(C$32;"dd/mm/yyyy");"//Valute[CharCode='THB']/Nominal");""))
```

**C36 — ВТС**

```
=IF(OR(C$32="";C$32>TODAY();N(C33)=0);"";IFERROR(INDEX(GOOGLEFINANCE("CURRENCY:BTCUSD";"price";C$32);2;2)*C33;""))
```

**C37 — ETH**

```
=IF(OR(C$32="";C$32>TODAY();N(C33)=0);"";IFERROR(INDEX(GOOGLEFINANCE("CURRENCY:ETHUSD";"price";C$32);2;2)*C33;""))
```
